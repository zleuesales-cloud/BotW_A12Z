// TOTK: NVN physical / virtual memory pools backed by Darwin VM aliasing.
#pragma once
#include <stdint.h>
#include "nvn_obj.h"

// Runtime-verified layout of NVNmappingRequest (0x28 bytes, main nvnMemoryPoolMapVirtual call).
typedef struct {
    const NVNmemoryPool *physical_pool;
    int64_t physical_offset;
    int64_t virtual_offset;
    uint64_t size;
    int32_t storage_class;
    uint32_t padding;
} totk_mapping_request;
_Static_assert(sizeof(totk_mapping_request) == 0x28, "NVNmappingRequest stride");

#define TOTK_VPOOL_PAGE 0x10000ull   // NVN virtual mapping granularity (64 KiB)

// Called by MemoryPoolInitialize for PHYSICAL or VIRTUAL pools. Returns 0 with a logged reason on failure.
int totk_vpool_init(pool_t *pool);
// Called by MemoryPoolFinalize. Returns 1 when it released the pool's host storage.
int totk_vpool_fini(pool_t *pool);
// All requests are validated before any mapping changes; returns 0 and leaves mappings intact on failure.
int totk_vpool_map(pool_t *virtual_pool, int count, const totk_mapping_request *requests);
typedef enum {
    TOTK_VPOOL_ADDRESS_NORMAL = 0,       // the full query range is outside registered virtual pools
    TOTK_VPOOL_ADDRESS_MAPPED = 1,       // requested range resolves to one physical-pool buffer span
    TOTK_VPOOL_ADDRESS_UNMAPPED = 2,     // starts in a virtual pool but includes an unmapped page
    TOTK_VPOOL_ADDRESS_NONCONTIGUOUS = 3,// mapped pages cross physical-pool/offset boundaries
    TOTK_VPOOL_ADDRESS_OVERFLOW = 4,     // va + len cannot be represented
    TOTK_VPOOL_ADDRESS_INVALID = 5       // empty query range
} totk_vpool_address_status;
typedef struct {
    uintptr_t backing;                  // physical pool CPU address corresponding to va
    uint64_t contiguous;                 // bytes in this same backing span from va
    const pool_t *physical_pool;         // stable under the NVN replay section
    uint64_t physical_offset;
} totk_vpool_address;
// Query metadata only; callers that then resolve a Metal pool must hold the NVN replay section
// across this query and pool lookup so MapVirtual/Finalize cannot replace the pool underneath it.
// NORMAL means the whole range is outside virtual pools; any partial intersection is NONCONTIGUOUS.
totk_vpool_address_status totk_vpool_resolve(uintptr_t va, uint64_t len, totk_vpool_address *out);
// Compatibility helper for diagnostics: true only when the full range maps contiguously.
int totk_vpool_backing(uintptr_t va, uint64_t len, uintptr_t *cpu);
// Once virtual aliases have existed, address-only write tracking is insufficient.
int totk_vpool_aliases_used(void);
