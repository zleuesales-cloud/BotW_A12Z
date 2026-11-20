/* CPU-only regression for checked NVN virtual-pool mapping metadata. */
#include "totk_vpool.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PAGE TOTK_VPOOL_PAGE
#define POOL_MAGIC_TEST MAGIC('P', 'O', 'O', 'L')
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "vpool mapping CPU probe failed at line %d: %s\n", __LINE__, #c); return 1; } checks++; } while (0)

static int checks;

/* totk_vpool.c only needs these platform-layer hooks; the probe has no Metal device. */
void mtl_pool_add(const void *mem, uint64_t size) { (void)mem; (void)size; }
void mtl_pool_del(const void *mem) { (void)mem; }
void nvn_dirty_add_range(const void *mem, size_t size) { (void)mem; (void)size; }
void nvn_dirty_remove_range(const void *mem) { (void)mem; }

static int physical_init(pool_t *p, uint64_t size, uint8_t **storage) {
    memset(p, 0, sizeof *p);
    if (posix_memalign((void **)storage, PAGE, (size_t)size)) return 0;
    memset(*storage, 0x39, (size_t)size);
    p->magic = POOL_MAGIC_TEST;
    p->flags = NVN_MEMORY_POOL_FLAGS_PHYSICAL;
    p->mem = *storage;
    p->size = size;
    return totk_vpool_init(p);
}

static int virtual_init(pool_t *p, uint64_t size) {
    memset(p, 0, sizeof *p);
    p->magic = POOL_MAGIC_TEST;
    p->flags = NVN_MEMORY_POOL_FLAGS_VIRTUAL;
    p->size = size;
    return totk_vpool_init(p);
}

static int map_range(pool_t *v, const pool_t *p, uint64_t po, uint64_t vo, uint64_t size) {
    totk_mapping_request r = {(const NVNmemoryPool *)p, (int64_t)po, (int64_t)vo, size, 0, 0};
    return totk_vpool_map(v, 1, &r);
}

static int unmap_range(pool_t *v, uint64_t vo, uint64_t size) {
    totk_mapping_request r = {NULL, 0, (int64_t)vo, size, 0, 0};
    return totk_vpool_map(v, 1, &r);
}

static int expect_mapping(pool_t *v, uint64_t off, uint64_t len, totk_vpool_address_status want,
                          const pool_t *pool, uint64_t pool_off, uint64_t contiguous) {
    totk_vpool_address got = {0};
    totk_vpool_address_status status = totk_vpool_resolve((uintptr_t)v->mem + (uintptr_t)off, len, &got);
    if (status != want) { fprintf(stderr, "mapping status at %#llx: got %d want %d\n", (unsigned long long)off, status, want); return 0; }
    if (pool && (got.physical_pool != pool || got.physical_offset != pool_off || got.backing != (uintptr_t)pool->mem + (uintptr_t)pool_off)) {
        fprintf(stderr, "mapping backing at %#llx did not match physical pool+offset\n", (unsigned long long)off); return 0;
    }
    if (got.contiguous != contiguous) { fprintf(stderr, "mapping span at %#llx: got %#llx want %#llx\n", (unsigned long long)off, (unsigned long long)got.contiguous, (unsigned long long)contiguous); return 0; }
    return 1;
}

int main(void) {
    uint8_t *a_mem = NULL, *b_mem = NULL;
    pool_t a, b, v;
    CHECK(physical_init(&a, 4 * PAGE, &a_mem));
    CHECK(physical_init(&b, 2 * PAGE, &b_mem));
    CHECK(virtual_init(&v, 4 * PAGE));
    uintptr_t old_v = (uintptr_t)v.mem;
    CHECK(expect_mapping(&v, 0, PAGE, TOTK_VPOOL_ADDRESS_UNMAPPED, NULL, 0, 0));
    CHECK(map_range(&v, &a, 0, 0, 4 * PAGE));
    CHECK(expect_mapping(&v, PAGE, 3 * PAGE, TOTK_VPOOL_ADDRESS_MAPPED, &a, PAGE, 3 * PAGE));
    CHECK(map_range(&v, &b, 0, PAGE, 2 * PAGE)); // partial overlap splits A's old interval
    CHECK(expect_mapping(&v, 0, 2 * PAGE, TOTK_VPOOL_ADDRESS_NONCONTIGUOUS, &a, 0, PAGE));
    CHECK(expect_mapping(&v, PAGE, 2 * PAGE, TOTK_VPOOL_ADDRESS_MAPPED, &b, 0, 2 * PAGE));
    CHECK(expect_mapping(&v, 3 * PAGE, PAGE, TOTK_VPOOL_ADDRESS_MAPPED, &a, 3 * PAGE, PAGE));
    CHECK(unmap_range(&v, 2 * PAGE, PAGE)); // partial unmap preserves the right-hand A interval
    CHECK(expect_mapping(&v, PAGE, 2 * PAGE, TOTK_VPOOL_ADDRESS_UNMAPPED, &b, 0, PAGE));
    CHECK(expect_mapping(&v, 3 * PAGE, PAGE, TOTK_VPOOL_ADDRESS_MAPPED, &a, 3 * PAGE, PAGE));
    CHECK(totk_vpool_resolve(UINTPTR_MAX - 7, 16, NULL) == TOTK_VPOOL_ADDRESS_OVERFLOW);
    CHECK(totk_vpool_resolve((uintptr_t)a.mem, 1, NULL) == TOTK_VPOOL_ADDRESS_NORMAL);
    CHECK(totk_vpool_resolve(old_v, 0, NULL) == TOTK_VPOOL_ADDRESS_INVALID);

    /* More than the former 512-entry ceiling; every other page remains unmapped. */
    enum { MAPS = 600 };
    pool_t many;
    CHECK(virtual_init(&many, (uint64_t)MAPS * 2 * PAGE));
    uintptr_t old_many = (uintptr_t)many.mem;
    totk_mapping_request *requests = calloc(MAPS, sizeof *requests);
    CHECK(requests != NULL);
    for (int i = 0; i < MAPS; i++) requests[i] = (totk_mapping_request){(const NVNmemoryPool *)&b, 0, (int64_t)((uint64_t)i * 2 * PAGE), PAGE, 0, 0};
    CHECK(totk_vpool_map(&many, MAPS, requests));
    for (int i = 0; i < MAPS; i++) {
        CHECK(expect_mapping(&many, (uint64_t)i * 2 * PAGE, PAGE, TOTK_VPOOL_ADDRESS_MAPPED, &b, 0, PAGE));
        CHECK(expect_mapping(&many, ((uint64_t)i * 2 + 1) * PAGE, PAGE, TOTK_VPOOL_ADDRESS_UNMAPPED, NULL, 0, 0));
    }
    free(requests);
    CHECK(totk_vpool_fini(&many) == 1);
    CHECK(totk_vpool_resolve(old_many, PAGE, NULL) == TOTK_VPOOL_ADDRESS_NORMAL);
    CHECK(virtual_init(&many, (uint64_t)MAPS * 2 * PAGE)); // pool object/address reuse cannot retain stale records
    if ((uintptr_t)many.mem != old_many) {
        fprintf(stderr, "test allocator did not reuse the freed vpool address\n"); return 1;
    }
    CHECK(expect_mapping(&many, 0, PAGE, TOTK_VPOOL_ADDRESS_UNMAPPED, NULL, 0, 0));
    CHECK(totk_vpool_fini(&many) == 1);

    /* Adjacent virtual pools may share physically contiguous pages but retain separate owners. */
    enum { ADJACENT_CANDIDATES = 128 };
    pool_t adjacent[ADJACENT_CANDIDATES];
    uintptr_t adjacent_base[ADJACENT_CANDIDATES];
    for (int i = 0; i < ADJACENT_CANDIDATES; i++) {
        CHECK(virtual_init(&adjacent[i], PAGE));
        adjacent_base[i] = (uintptr_t)adjacent[i].mem;
    }
    int left = -1, right = -1;
    for (int i = 0; i < ADJACENT_CANDIDATES && left < 0; i++) {
        int has_predecessor = 0;
        for (int k = 0; k < ADJACENT_CANDIDATES; k++)
            if (adjacent_base[k] <= UINTPTR_MAX - PAGE && adjacent_base[k] + PAGE == adjacent_base[i]) has_predecessor = 1;
        if (has_predecessor) continue;
        for (int j = 0; j < ADJACENT_CANDIDATES; j++)
            if (adjacent_base[i] <= UINTPTR_MAX - PAGE && adjacent_base[i] + PAGE == adjacent_base[j]) { left = i; right = j; break; }
    }
    CHECK(left >= 0);
    CHECK(map_range(&adjacent[left], &a, 0, 0, PAGE));
    CHECK(map_range(&adjacent[right], &a, PAGE, 0, PAGE));
    totk_vpool_address cross = {0};
    CHECK(adjacent_base[left] > 0);
    CHECK(totk_vpool_resolve(adjacent_base[left] - 1, 2, NULL) == TOTK_VPOOL_ADDRESS_NONCONTIGUOUS);
    CHECK(totk_vpool_resolve(adjacent_base[left], 2 * PAGE, &cross) == TOTK_VPOOL_ADDRESS_NONCONTIGUOUS);
    CHECK(cross.contiguous == PAGE);
    CHECK(totk_vpool_fini(&adjacent[left]) == 1);
    CHECK(totk_vpool_resolve(adjacent_base[left], PAGE, NULL) == TOTK_VPOOL_ADDRESS_NORMAL);
    CHECK(expect_mapping(&adjacent[right], 0, PAGE, TOTK_VPOOL_ADDRESS_MAPPED, &a, PAGE, PAGE));
    CHECK(totk_vpool_fini(&adjacent[right]) == 1);
    CHECK(totk_vpool_resolve(adjacent_base[right], PAGE, NULL) == TOTK_VPOOL_ADDRESS_NORMAL);
    for (int i = 0; i < ADJACENT_CANDIDATES; i++) if (i != left && i != right) CHECK(totk_vpool_fini(&adjacent[i]) == 1);

    /* Physical finalization removes reverse mappings even when no host allocation exists. */
    CHECK(totk_vpool_fini(&b) == 0);
    CHECK(expect_mapping(&v, PAGE, PAGE, TOTK_VPOOL_ADDRESS_UNMAPPED, NULL, 0, 0));
    CHECK(totk_vpool_fini(&v) == 1);
    CHECK(totk_vpool_resolve(old_v, PAGE, NULL) == TOTK_VPOOL_ADDRESS_NORMAL);
    CHECK(totk_vpool_fini(&a) == 0);
    free(a_mem); free(b_mem);
    printf("PASS vpool mapping metadata CPU probe: %d checks\n", checks);
    return 0;
}
