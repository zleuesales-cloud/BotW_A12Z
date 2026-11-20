/* probe-gpu-copy-alias: production buffer copies through Metal buffers that reach the same memory -- two no-copy buffers over one
 * CPU allocation, and a virtual pool's remapped view of a physical pool (as source and destination, across encoder boundaries).
 * 256 dependent copies and fills over 64 KiB, checked byte by byte against the same steps done on the CPU. This is the GPU part
 * of the original port's copy probe (d309e62, Codex) without its copy-batch experiment. TOTK_PROBE_NEGATIVE=1 takes the alias
 * visibility away and must read stale bytes (2026-10-18 on an M4: byte 512 read 125 instead of 207, every run). */
#include "totk_vpool.h"
int mtl_probe_copy_alias(void) {
    mtl_init();
    if (nogpu()) MDIE("copy alias probe requires real GPU submission; TOTK_GPU_NOCOMMIT=1 disables it");
    int checked = 0, apple5 = g_present_apple5;
    const char *ne = getenv("TOTK_PROBE_NEGATIVE");
    if (apple5 && ne && atoi(ne) == 1) MDIE("TOTK_PROBE_NEGATIVE is only defined for the noncanonical wrapper probe");
    g_copy_probe_no_alias = !apple5 && ne && atoi(ne) == 1;
    enum { SZ = 65536 };
    void *mem = NULL; int alloc_rc = posix_memalign(&mem, 16384, SZ);
    if (alloc_rc || !mem) MDIE("copy alias probe memory: posix_memalign rc=%d ptr=%p", alloc_rc, mem);
    if (apple5) {
        if (!nvn_replay_enter || !nvn_replay_leave) MDIE("Apple5 copy alias probe requires the NVN replay section");
        nvn_replay_enter();
    }
    // A virtual view of this physical pool has different CPU and GPU VAs.
    pool_t physical = {.magic = MAGIC('P','O','O','L'), .flags = NVN_MEMORY_POOL_FLAGS_PHYSICAL, .mem = mem, .size = SZ};
    pool_t virtual_pool = {.magic = MAGIC('P','O','O','L'), .flags = NVN_MEMORY_POOL_FLAGS_VIRTUAL, .size = SZ};
    if (!totk_vpool_init(&physical) || !totk_vpool_init(&virtual_pool)) MDIE("copy alias probe virtual pool");
    if (apple5) { mtl_pool_add(mem, SZ); nvn_dirty_add_range(mem, SZ); }
    totk_mapping_request mapping = {.physical_pool = (const NVNmemoryPool *)&physical, .size = SZ};
    if (!totk_vpool_map(&virtual_pool, 1, &mapping)) MDIE("copy alias probe virtual mapping");
    id<MTLBuffer> x = nil, alias = nil;
    if (!apple5) {
        x = [g_dev newBufferWithBytesNoCopy:mem length:SZ options:MTLResourceStorageModeShared deallocator:nil];
        alias = [g_dev newBufferWithBytesNoCopy:mem length:SZ options:MTLResourceStorageModeShared deallocator:nil];
        if (!x || !alias) MDIE("copy alias probe no-copy buffers x=%p alias=%p", x, alias);
        res_add(x); res_add(alias);
    }
    void *vb; uint64_t vo;
    if (!mtl_addr((uint64_t)(uintptr_t)virtual_pool.mem, SZ, &vb, &vo)) MDIE("copy alias probe virtual address");
    id<MTLBuffer> virtual_buffer = (__bridge id<MTLBuffer>)vb;
    if (apple5) {
        void *pb; uint64_t po;
        if (!mtl_addr((uint64_t)(uintptr_t)mem, SZ, &pb, &po)) MDIE("copy alias probe physical address");
        if (pb != vb) MDIE("Apple5 copy alias probe did not canonicalize physical/virtual pools: physical=%p virtual=%p", pb, vb);
    }
    uint8_t *expected = malloc(SZ); if (!expected) MDIE("copy alias probe expected");
    op_hdr end = {OP_END, 8};
    for (int aliases = 0; aliases < 2; aliases++) for (int run = 0; run < 4; run++) {
        for (int i = 0; i < SZ; i++) ((uint8_t *)mem)[i] = (uint8_t)(i * 17 + (i / 256) * 13 + run);
        if (memcmp(virtual_pool.mem, mem, SZ)) MDIE("copy alias probe CPU mapping is not shared");
        memcpy(expected, mem, SZ);
        mtl_sub_begin(); mtl_exec((const uint8_t *)&end);
        uint32_t rng = 1234;
        for (int i = 0; i < 256; i++) {
            rng = rng * 1664525 + 1013904223; unsigned from = ((rng >> 16) % 128) * 256;
            rng = rng * 1664525 + 1013904223; unsigned to = ((rng >> 16) % 128) * 256;
            unsigned n = 256;
            id<MTLBuffer> dest = apple5 ? virtual_buffer : (aliases && i % 7 == 0 ? virtual_buffer : (i & 1 ? alias : x));
            id<MTLBuffer> source = apple5 ? virtual_buffer : (aliases && i % 11 == 0 ? virtual_buffer : x);
            NSUInteger source_offset = from + (source == virtual_buffer ? vo : 0), dest_offset = to + (dest == virtual_buffer ? vo : 0);
            uint64_t source_addr = (uint64_t)(uintptr_t)((aliases && i % 11 == 0) ? virtual_pool.mem : mem) + from;
            uint64_t dest_addr = (uint64_t)(uintptr_t)((aliases && i % 7 == 0) ? virtual_pool.mem : mem) + to;
            if (i % 5 == 0) {
                unsigned char value = (unsigned char)(i + run);
                id e;
                if (apple5) {
                    void *fill_buf; uint64_t fill_off;
                    if (!mtl_addr(dest_addr, n, &fill_buf, &fill_off)) MDIE("Apple5 copy alias probe fill address %#llx", (unsigned long long)dest_addr);
                    e = blit(); [e fillBuffer:(__bridge id<MTLBuffer>)fill_buf range:NSMakeRange((NSUInteger)fill_off, n) value:value];
                } else {
                    e = blit_buffer(nil, 0, dest, dest_offset, n); [e fillBuffer:dest range:NSMakeRange(dest_offset, n) value:value];
                }
                memset(expected + to, value, n);
            } else if (from != to) {
                if (apple5) mtl_sub_copy(source_addr, dest_addr, n);
                else enc_b_copy_buffer(blit_buffer(source, source_offset, dest, dest_offset, n), source, source_offset, dest, dest_offset, n);
                memcpy(expected + to, expected + from, n);
            }
            if (i == 127) (void)blit();                 // an ordinary copy-encoder user in between
            if (i == 63 || i == 191) blit_close();      // alias visibility survives encoder boundaries
        }
        uint64_t fence = mtl_sub_commit();
        if (apple5) {
            if (!mtl_wait(fence, 10000000000ull)) MDIE("copy alias probe GPU wait timed out at fence %llu", (unsigned long long)fence);
        } else mtl_finish();
        if (mtl_last_commit_error()) MDIE("copy alias probe GPU command failed at fence %llu", (unsigned long long)fence);
        for (int i = 0; i < SZ; i++) {
            if (((uint8_t *)mem)[i] != expected[i])
                MDIE("copy alias GPU mismatch aliases%d run%d byte%d got%d want%d", aliases, run, i, ((uint8_t *)mem)[i], expected[i]);
            checked++;
        }
    }
    nvn_dirty_remove_range(virtual_pool.mem); mtl_pool_del(virtual_pool.mem); totk_vpool_fini(&virtual_pool); virtual_buffer = nil;
    if (apple5) { nvn_dirty_remove_range(mem); mtl_pool_del(mem); }
    totk_vpool_fini(&physical);
    if (apple5) nvn_replay_leave();
    g_copy_probe_no_alias = 0;
    if (!apple5) { res_remove(alias); res_remove(x); }
    alias = nil; x = nil; free(mem); free(expected);
    return checked;
}
