"""Exercise the production native render/blit helpers with a bounded Metal GPU probe."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NVN = ROOT / 'games/totk/third_party/d2r_gfx/gfx/nvn'
SHARED_NVN = ROOT / 'shared/third_party/d2r_gfx/gfx/nvn'


def production_function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 0
    quote = None
    i = opening
    while i < len(source):
        if quote:
            if source[i] == '\\':
                i += 2
                continue
            if source[i] == quote:
                quote = None
        elif source.startswith('//', i):
            end = source.find('\n', i)
            i = len(source) if end < 0 else end
            continue
        elif source.startswith('/*', i):
            end = source.find('*/', i + 2)
            if end < 0:
                raise AssertionError(f'unterminated comment after {signature}')
            i = end + 2
            continue
        elif source[i] in ('"', "'"):
            quote = source[i]
        elif source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1
    raise AssertionError(f'unterminated function {signature}')


PROBE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "nvn_pool_index.h"
#include "totk_vpool.h"
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MDIE(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); abort(); } while (0)
static id<MTLDevice> g_dev;
static id<MTLCommandQueue> g_q;
static id g_cb;
static id<MTL4ComputeCommandEncoder> g_blit;
static id<MTL4RenderCommandEncoder> g_enc;
static id<MTLFence> g_classic_fence;
static id<MTL4ArgumentTable> g_vt, g_ft;
static int g_classic_fence_has_update;
static pthread_t g_classic_fence_owner;
static int g_classic_fence_owner_set;
static int g_present_apple5 = 1;
static int g_blit_alias, g_copy_probe_no_alias;
static uint64_t g_blit_opens, g_blit_splits;
static _Atomic int g_oot_bar_last;
static int g_oot_bar_beginning, g_oot_bar_mode, g_oot_bar_pass;
static int g_oot_bar_prev_writes, g_oot_bar_writes;
static id<MTL4RenderCommandEncoder> g_oot_bar_encoder;
static pool_t *g_test_physical_pool, *g_test_virtual_pool;
static pool_t *g_test_decoy_pool;
static id<MTLBuffer> g_test_physical_buffer, g_test_virtual_buffer, g_test_decoy_buffer;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static pidx g_pools;
static int g_replay_depth, g_pool_lookup_trace;

void nvn_replay_enter(void) __attribute__((weak));
void nvn_replay_leave(void) __attribute__((weak));
void nvn_replay_enter(void) { g_replay_depth++; }
void nvn_replay_leave(void) { assert(g_replay_depth > 0); g_replay_depth--; }
void mtl_pool_add(const void *mem, uint64_t size) { (void)mem; (void)size; }
void mtl_pool_del(const void *mem) { (void)mem; }
void nvn_dirty_add_range(const void *mem, size_t size) { (void)mem; (void)size; }
void nvn_dirty_remove_range(const void *mem) { (void)mem; }
static void pool_res(pidx_ent *entry, int origin) { (void)entry; (void)origin; assert(g_replay_depth > 0); }

static int range_has(uint64_t addr, uint64_t len, const pool_t *pool) {
    uintptr_t base = (uintptr_t)pool->mem;
    return len && addr >= base && addr - base <= pool->size && len <= pool->size - (addr - base);
}
static int pool_lookup(uint64_t addr, uint64_t len, void **buf, uint64_t *base, uint64_t *gpu) {
    assert(g_replay_depth > 0);
    if (g_pool_lookup_trace) fputs("POOL_LOOKUP_CALLED\n", stderr);
    if (g_test_decoy_pool && range_has(addr, len, g_test_decoy_pool)) {
        *buf = (__bridge void *)g_test_decoy_buffer;
        *base = (uintptr_t)g_test_decoy_pool->mem;
    } else if (g_test_physical_pool && range_has(addr, len, g_test_physical_pool)) {
        *buf = (__bridge void *)g_test_physical_buffer;
        *base = (uintptr_t)g_test_physical_pool->mem;
    } else if (g_test_virtual_pool && range_has(addr, len, g_test_virtual_pool)) {
        *buf = (__bridge void *)g_test_virtual_buffer;
        *base = (uintptr_t)g_test_virtual_pool->mem;
    } else return 0;
    *gpu = 0;
    return 1;
}

static void blit_note(void) {}
static void rp_end(void) {}
static void rs_sync(void) {}
static int rs_stream_pass(void) { return 0; }
static id rs_pass_begin(id desc, id vt, id ft, id cb, int mode, uint64_t visibility) {
    (void)desc; (void)vt; (void)ft; (void)cb; (void)mode; (void)visibility; abort();
}
static void ser_gate(void) {}
static void pt_mark(id cb, char kind, id desc) { (void)cb; (void)kind; (void)desc; }
static void dsp_note_host_op(void) {}
static void es_begin(id encoder) { (void)encoder; }
static int rs_using(id encoder) { (void)encoder; return 0; }
static void rs_pass_end(id encoder, int mode, uint64_t visibility) { (void)encoder; (void)mode; (void)visibility; abort(); }
static int bar_end_mode(void) { return 1; }
static int bar_start_mode(void) { return 1; }
static int bar_end_on(void) { return 1; }
static int blit_bar_on(void) { return 1; }
static MTL4VisibilityOptions bar_vis(void) { return MTL4VisibilityOptionDevice; }
static MTL4VisibilityOptions blit_vis(void) { return MTL4VisibilityOptionDevice; }
static inline void m4_end(id encoder);
static inline void blit_close(void);
// PRODUCTION_FUNCTIONS

static void native_clear(id<MTLTexture> texture, MTLClearColor color) {
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = texture;
    rp.colorAttachments[0].loadAction = MTLLoadActionClear;
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    rp.colorAttachments[0].clearColor = color;
    id<MTL4RenderCommandEncoder> render = enc_r_begin((MTL4RenderPassDescriptor *)rp);
    enc_r_end(render);
}

int main(int argc, char **argv) { @autoreleasepool {
    g_dev = MTLCreateSystemDefaultDevice(); if (!g_dev) return 77;
    g_q = [g_dev newCommandQueue]; assert(g_q);
    g_classic_fence = [g_dev newFence]; assert(g_classic_fence);
    g_classic_fence_owner = pthread_self(); g_classic_fence_owner_set = 1;
    g_cb = [g_q commandBuffer]; assert(g_cb);

    const NSUInteger w = 8, h = 8, row = w * 4, image = row * h;
    id<MTLBuffer> upload = [g_dev newBufferWithLength:image options:MTLResourceStorageModeShared]; assert(upload);
    uint8_t *in = upload.contents;
    for (NSUInteger i = 0; i < w * h; i++) { in[4*i]=0x11; in[4*i+1]=0x22; in[4*i+2]=0x33; in[4*i+3]=0xff; }
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
    td.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget; td.storageMode = MTLStorageModePrivate;
    id<MTLTexture> uploaded = [g_dev newTextureWithDescriptor:td]; assert(uploaded);
    id<MTLTexture> cleared = [g_dev newTextureWithDescriptor:td]; assert(cleared);
    id<MTLBuffer> upload_readback = [g_dev newBufferWithLength:image options:MTLResourceStorageModeShared]; assert(upload_readback);
    id<MTLBuffer> clear_readback = [g_dev newBufferWithLength:image options:MTLResourceStorageModeShared]; assert(clear_readback);
    id<MTLBuffer> alias_readback = [g_dev newBufferWithLength:image options:MTLResourceStorageModeShared]; assert(alias_readback);

    id upload_blit = blit_kind(0);
    enc_b_copy_buffer_texture(upload_blit, upload, 0, row, image, MTLSizeMake(w,h,1), uploaded, 0, 0, MTLOriginMake(0,0,0));
    blit_close();

    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = cleared;
    rp.colorAttachments[0].loadAction = MTLLoadActionClear;
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    rp.colorAttachments[0].clearColor = MTLClearColorMake(0.25, 0.5, 0.75, 1.0);
    id<MTL4RenderCommandEncoder> render = enc_r_begin((MTL4RenderPassDescriptor *)rp);
    enc_r_end(render);

    id read_blit = blit_kind(0);
    enc_b_copy_texture_buffer(read_blit, uploaded, 0, 0, MTLOriginMake(0,0,0), MTLSizeMake(w,h,1), upload_readback, 0, row, image);
    enc_b_copy_texture_buffer(read_blit, cleared, 0, 0, MTLOriginMake(0,0,0), MTLSizeMake(w,h,1), clear_readback, 0, row, image);
    blit_close();

    MTLTextureDescriptor *alias_desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
    alias_desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; alias_desc.storageMode = MTLStorageModePrivate;
    MTLSizeAndAlign alias_size = [g_dev heapTextureSizeAndAlignWithDescriptor:alias_desc]; assert(alias_size.size && alias_size.align);
    MTLHeapDescriptor *hd = [MTLHeapDescriptor new]; hd.storageMode = MTLStorageModePrivate;
    hd.hazardTrackingMode = MTLHazardTrackingModeUntracked; hd.size = alias_size.size * 2 + alias_size.align;
    id<MTLHeap> heap = [g_dev newHeapWithDescriptor:hd]; if (!heap) return 77;
    id<MTLTexture> alias_a = [heap newTextureWithDescriptor:alias_desc]; if (!alias_a) return 77;
    NSUInteger alias_offset = alias_a.heapOffset;
    native_clear(alias_a, MTLClearColorMake(1,0,0,1));
    [alias_a makeAliasable];
    id<MTLTexture> alias_b = [heap newTextureWithDescriptor:alias_desc]; if (!alias_b) return 77;
    if (alias_b.heapOffset != alias_offset || alias_b.hazardTrackingMode != MTLHazardTrackingModeUntracked) {
        fprintf(stderr, "Metal did not reuse the untracked heap alias slot\n"); return 77;
    }
    native_clear(alias_b, MTLClearColorMake(0.25,0.5,0.75,1));
    id alias_blit = blit_kind(0);
    enc_b_copy_texture_buffer(alias_blit, alias_b, 0, 0, MTLOriginMake(0,0,0), MTLSizeMake(w,h,1), alias_readback, 0, row, image);
    blit_close();
    assert(!g_blit && g_classic_fence_has_update);

    enum { ALIAS_BYTES = 3 * TOTK_VPOOL_PAGE, ALIAS_STEP = 256, ALIAS_STEPS = 4 };
    void *alias_memory = NULL; assert(posix_memalign(&alias_memory, 16384, ALIAS_BYTES) == 0);
    memset(alias_memory, 0, ALIAS_BYTES);
    uint8_t *expected = calloc(1, ALIAS_BYTES); assert(expected);
    for (NSUInteger i = 0; i < ALIAS_STEP; i++) expected[i] = (uint8_t)(i * 13 + 7);
    MTLResourceOptions untracked = MTLResourceStorageModeShared | MTLResourceHazardTrackingModeUntracked;
    pool_t physical_pool = {.magic = MAGIC('P','O','O','L'), .flags = NVN_MEMORY_POOL_FLAGS_PHYSICAL, .mem = alias_memory, .size = ALIAS_BYTES};
    pool_t decoy_pool = {.magic = MAGIC('P','O','O','L'), .flags = NVN_MEMORY_POOL_FLAGS_PHYSICAL,
        .mem = (uint8_t *)alias_memory + TOTK_VPOOL_PAGE, .size = 2 * TOTK_VPOOL_PAGE};
    pool_t virtual_pool = {.magic = MAGIC('P','O','O','L'), .flags = NVN_MEMORY_POOL_FLAGS_VIRTUAL, .size = ALIAS_BYTES};
    assert(totk_vpool_init(&physical_pool) && totk_vpool_init(&decoy_pool) && totk_vpool_init(&virtual_pool));
    totk_mapping_request map = {.physical_pool = (const NVNmemoryPool *)&physical_pool, .size = ALIAS_BYTES};
    assert(totk_vpool_map(&virtual_pool, 1, &map));
    id<MTLBuffer> physical_wrapper = [g_dev newBufferWithBytesNoCopy:alias_memory length:ALIAS_BYTES options:untracked deallocator:nil];
    id<MTLBuffer> decoy_wrapper = [g_dev newBufferWithBytesNoCopy:decoy_pool.mem length:decoy_pool.size options:untracked deallocator:nil];
    id<MTLBuffer> virtual_wrapper = [g_dev newBufferWithBytesNoCopy:virtual_pool.mem length:ALIAS_BYTES options:untracked deallocator:nil];
    if (!physical_wrapper || !decoy_wrapper || !virtual_wrapper) { fprintf(stderr, "Metal no-copy physical/virtual pool wrappers unavailable\n"); return 77; }
    g_test_physical_pool = &physical_pool; g_test_decoy_pool = &decoy_pool; g_test_virtual_pool = &virtual_pool;
    g_test_physical_buffer = physical_wrapper; g_test_decoy_buffer = decoy_wrapper; g_test_virtual_buffer = virtual_wrapper;
    pidx_ent physical_entry = {.lo = (uintptr_t)physical_pool.mem, .hi = (uintptr_t)physical_pool.mem + physical_pool.size,
        .base = (uintptr_t)physical_pool.mem, .buf = (__bridge void *)physical_wrapper, .key = physical_pool.mem};
    pidx_ent decoy_entry = {.lo = (uintptr_t)decoy_pool.mem, .hi = (uintptr_t)decoy_pool.mem + decoy_pool.size,
        .base = (uintptr_t)decoy_pool.mem, .buf = (__bridge void *)decoy_wrapper, .key = decoy_pool.mem};
    assert(pidx_insert(&g_pools, &physical_entry) && pidx_insert(&g_pools, &decoy_entry));
    void *physical_resolved = NULL, *virtual_resolved = NULL; uint64_t physical_offset = 0, virtual_offset = 0;
    assert(mtl_addr((uint64_t)(uintptr_t)physical_pool.mem, ALIAS_BYTES, &physical_resolved, &physical_offset));
    assert(mtl_addr((uint64_t)(uintptr_t)virtual_pool.mem, ALIAS_BYTES, &virtual_resolved, &virtual_offset));
    if (physical_resolved != virtual_resolved || physical_resolved == (__bridge void *)virtual_wrapper || physical_offset != virtual_offset) {
        fprintf(stderr, "Apple5 mtl_addr did not canonicalize virtual pool onto the physical MTLBuffer\n"); return 4;
    }
    void *decoy_resolved = NULL, *mapped_page_resolved = NULL; uint64_t decoy_offset = 0, mapped_page_offset = 0;
    assert(mtl_addr((uint64_t)(uintptr_t)decoy_pool.mem, TOTK_VPOOL_PAGE, &decoy_resolved, &decoy_offset));
    assert(mtl_addr((uint64_t)(uintptr_t)virtual_pool.mem + TOTK_VPOOL_PAGE, TOTK_VPOOL_PAGE, &mapped_page_resolved, &mapped_page_offset));
    if (decoy_resolved != (__bridge void *)decoy_wrapper || mapped_page_resolved != (__bridge void *)physical_wrapper ||
        mapped_page_resolved == decoy_resolved || mapped_page_offset != TOTK_VPOOL_PAGE) {
        fprintf(stderr, "Apple5 mapping did not preserve its physical-pool owner across overlapping pool ranges\n"); return 4;
    }
    id<MTLBuffer> alias_x = (__bridge id<MTLBuffer>)physical_resolved;
    id<MTLBuffer> alias_y = (__bridge id<MTLBuffer>)virtual_resolved;
    memcpy(alias_memory, expected, ALIAS_STEP);
    if (argc > 1 && (!strcmp(argv[1], "unmapped") || !strcmp(argv[1], "partial"))) {
        if (!strcmp(argv[1], "unmapped")) {
            totk_mapping_request unmap = {.virtual_offset = 0, .size = ALIAS_BYTES};
            assert(totk_vpool_map(&virtual_pool, 1, &unmap));
        }
        g_pool_lookup_trace = 1;
        void *bad = NULL; uint64_t bad_offset = 0;
        uint64_t query = !strcmp(argv[1], "partial") ? (uint64_t)(uintptr_t)virtual_pool.mem - 1 : (uint64_t)(uintptr_t)virtual_pool.mem;
        int unexpectedly_found = mtl_addr(query, !strcmp(argv[1], "partial") ? 2 : ALIAS_BYTES, &bad, &bad_offset);
        fprintf(stderr, "invalid Apple5 virtual range unexpectedly returned %d\n", unexpectedly_found);
        return 4;
    }

    /* Commit the clear/upload and one alias texture before the pool-copy chain. */
    id<MTLCommandBuffer> cb1 = g_cb;
    [cb1 commit];
    g_cb = [g_q commandBuffer]; assert(g_cb);
    id<MTLCommandBuffer> cb2 = g_cb;
    id copy0 = blit_kind(1);
    assert(![copy0 respondsToSelector:@selector(waitForFence:beforeStages:)]);
    assert(![copy0 respondsToSelector:@selector(updateFence:afterStages:)]);
    assert([copy0 respondsToSelector:@selector(waitForFence:)] && [copy0 respondsToSelector:@selector(updateFence:)]);
    enc_b_copy_buffer(copy0, alias_x, 0, alias_y, ALIAS_STEP, ALIAS_STEP);
    assert(!g_blit && !g_blit_alias && g_classic_fence_has_update);
    memcpy(expected + ALIAS_STEP, expected, ALIAS_STEP);
    [cb2 commit];
    g_cb = [g_q commandBuffer]; assert(g_cb);
    id<MTLCommandBuffer> cb3 = g_cb;
    id copy1 = blit_kind(1); enc_b_copy_buffer(copy1, alias_x, ALIAS_STEP, alias_y, ALIAS_STEP * 2, ALIAS_STEP);
    assert(!g_blit && !g_blit_alias);
    memcpy(expected + ALIAS_STEP * 2, expected + ALIAS_STEP, ALIAS_STEP);
    id copy2 = blit_kind(1); enc_b_copy_buffer(copy2, alias_y, ALIAS_STEP * 2, alias_x, ALIAS_STEP * 3, ALIAS_STEP);
    assert(!g_blit && !g_blit_alias);
    memcpy(expected + ALIAS_STEP * 3, expected + ALIAS_STEP * 2, ALIAS_STEP);
    id copy3 = blit_kind(1); enc_b_copy_buffer(copy3, alias_x, ALIAS_STEP * 3, alias_y, ALIAS_STEP * 4, ALIAS_STEP);
    assert(!g_blit && !g_blit_alias);
    memcpy(expected + ALIAS_STEP * 4, expected + ALIAS_STEP * 3, ALIAS_STEP);

    [cb3 commit]; [cb3 waitUntilCompleted];
    for (id<MTLCommandBuffer> cb in @[cb1, cb2, cb3]) if ([cb error]) { fprintf(stderr, "GPU error: %s\n", [cb error].localizedDescription.UTF8String); return 2; }
    const uint8_t *u = upload_readback.contents, *c = clear_readback.contents, *a = alias_readback.contents;
    for (NSUInteger i = 0; i < w*h; i++) {
        assert(u[4*i] == 0x11 && u[4*i+1] == 0x22 && u[4*i+2] == 0x33 && u[4*i+3] == 0xff);
        assert(c[4*i] == 191 && c[4*i+1] == 128 && c[4*i+2] == 64 && c[4*i+3] == 255);
    }
    for (NSUInteger i = 0; i < w*h; i++) assert(a[4*i] == 191 && a[4*i+1] == 128 && a[4*i+2] == 64 && a[4*i+3] == 255);
    if (memcmp(alias_memory, expected, ALIAS_BYTES)) {
        for (NSUInteger i = 0; i < ALIAS_BYTES; i++) if (((uint8_t *)alias_memory)[i] != expected[i]) {
            fprintf(stderr, "untracked no-copy alias chain byte %lu: got=%u expected=%u\n", (unsigned long)i,
                    ((uint8_t *)alias_memory)[i], expected[i]); return 3;
        }
    }
    alias_x = nil; alias_y = nil; physical_wrapper = nil; decoy_wrapper = nil; virtual_wrapper = nil;
    g_test_physical_buffer = nil; g_test_decoy_buffer = nil; g_test_virtual_buffer = nil;
    g_test_physical_pool = NULL; g_test_decoy_pool = NULL; g_test_virtual_pool = NULL;
    assert(totk_vpool_fini(&virtual_pool) == 1);
    assert(totk_vpool_fini(&decoy_pool) == 0);
    assert(totk_vpool_fini(&physical_pool) == 0);
    free(g_pools.e); free(g_pools.maxhi);
    free(expected); free(alias_memory);
    puts("PASS production native enc_r/enc_b: clear, upload/readback, canonical vpool buffer and cross-command-buffer alias fences");
    return 0;
} }
'''


class NativeMainEncoders(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'requires macOS Metal')
    def test_production_native_clear_upload_and_alias_fences(self):
        source = (NVN / 'nvn_mtl.m').read_text()
        signatures = (
            'static void classic_fence_owner_check(void) {',
            'static void classic_fence_wait(id cb, id encoder) {',
            'static void classic_fence_update(id encoder) {',
            'static inline void m4_end(id e) {',
            'static inline void m4_end_render(id e) {',
            'static inline void blit_close(void) {',
            'static inline id<MTL4RenderCommandEncoder> enc_r_begin(MTL4RenderPassDescriptor *d) {',
            'static inline void enc_r_end(id e) {',
            'static inline id enc_b_begin(id cb) {',
            'static inline void enc_b_end(id e) {',
            'static inline void enc_b_copy_buffer(id e, id<MTLBuffer> s, NSUInteger so, id<MTLBuffer> d, NSUInteger doff, NSUInteger n) {',
            'static inline void enc_b_copy_buffer_texture(id e, id<MTLBuffer> s, NSUInteger so, NSUInteger row, NSUInteger img, MTLSize z, id<MTLTexture> d, NSUInteger sl, NSUInteger lv, MTLOrigin o) {',
            'static inline void enc_b_copy_texture_buffer(id e, id<MTLTexture> s, NSUInteger sl, NSUInteger lv, MTLOrigin o, MTLSize z, id<MTLBuffer> d, NSUInteger doff, NSUInteger row, NSUInteger img) {',
            'static id<MTL4ComputeCommandEncoder> blit_kind(int buffer_alias) {',
            'static int pool_lookup_physical(const pool_t *owner, uint64_t offset, uint64_t len, void **buf, uint64_t *base) {',
            'int mtl_addr(uint64_t addr, uint64_t len, void **buf, uint64_t *off) {',
        )
        functions = '\n'.join(production_function(source, signature) for signature in signatures)
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = work / 'native_main.m'
            probe.write_text(PROBE.replace('// PRODUCTION_FUNCTIONS', functions))
            vpool_object = work / 'totk_vpool.o'
            subprocess.run(['xcrun', 'clang', '-std=gnu11', '-O1', '-DGAME_BOTW=1',
                            '-I' + str(NVN), '-I' + str(SHARED_NVN), '-I' + str(SHARED_NVN / 'include'),
                            '-c', str(SHARED_NVN / 'totk_vpool.c'), '-o', str(vpool_object)],
                           check=True, timeout=30)
            binary = work / 'native_main'
            subprocess.run(['xcrun', 'clang', '-std=gnu11', '-fobjc-arc', '-O1', '-Wno-incompatible-pointer-types',
                            '-I' + str(NVN), '-I' + str(SHARED_NVN), '-I' + str(SHARED_NVN / 'include'),
                            str(probe), str(vpool_object), '-framework', 'Foundation', '-framework', 'Metal', '-o', str(binary)],
                           check=True, timeout=30)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            if run.returncode == 77:
                self.skipTest(run.stderr.strip() or 'Metal GPU/no-copy alias probe unavailable')
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('PASS production native enc_r/enc_b', run.stdout)
            for scenario, status in (('unmapped', 2), ('partial', 3)):
                invalid = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=30)
                self.assertNotEqual(invalid.returncode, 0, invalid.stdout + invalid.stderr)
                self.assertIn(f'virtual-map status {status}', invalid.stderr)
                self.assertNotIn('POOL_LOOKUP_CALLED', invalid.stderr)


if __name__ == '__main__':
    unittest.main()
