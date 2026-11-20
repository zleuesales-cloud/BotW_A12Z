// TOTK: NVN physical / virtual memory pools.
// A virtual pool is a host VM range whose 64 KiB pages alias pages of physical pools via mach_vm_remap
// (copy=FALSE), so CPU and GPU writes through either view hit the same physical pages.
// Physical pools marked CPU_NO_ACCESS cannot be observed except through virtual mappings, so they get
// 16 KiB-aligned host storage (game storage may be only 4/8 KiB aligned, which Darwin cannot remap).
// CPU-visible physical pools must alias the game storage itself and therefore must be 16 KiB aligned.
#include "totk_vpool.h"
#include "nvn_mtl.h"
#include "nvn_dirty.h"
#include "../../host/hle.h"
#include <mach/mach.h>
#include "mach_vm_compat.h"
#include <limits.h>
#include <pthread.h>
#include <stdlib.h>
#include <unistd.h>

#define F_CPU_NO_ACCESS NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS
#define F_GPU_NO_ACCESS NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS
#define F_PHYSICAL NVN_MEMORY_POOL_FLAGS_PHYSICAL
#define F_VIRTUAL NVN_MEMORY_POOL_FLAGS_VIRTUAL
#define POOL_MAGIC MAGIC('P', 'O', 'O', 'L')

static pthread_mutex_t g_map_mu = PTHREAD_MUTEX_INITIALIZER;
static int g_aliases_used;
int totk_vpool_aliases_used(void) { return __atomic_load_n(&g_aliases_used, __ATOMIC_ACQUIRE); }

typedef struct { uintptr_t lo, hi; const pool_t *owner; } vpool_range;
typedef struct { uintptr_t lo, hi; const pool_t *virtual_pool, *physical_pool; uint64_t physical_offset; } vmap_ent;
static vpool_range *g_vpools; static size_t g_nvpools, g_capvpools;
static vmap_ent *g_vmap, *g_vmap_scratch; static size_t g_nvmap, g_capvmap, g_capvmap_scratch;

static int reserve_array(void **ptr, size_t *capacity, size_t needed, size_t item_size) {
    if (needed <= *capacity) return 1;
    size_t cap = *capacity ? *capacity : 16;
    while (cap < needed) {
        if (cap > SIZE_MAX / 2) { cap = needed; break; }
        cap *= 2;
    }
    if (cap > SIZE_MAX / item_size) return 0;
    void *next = realloc(*ptr, cap * item_size);
    if (!next) return 0;
    *ptr = next; *capacity = cap; return 1;
}
static int vmap_reserve_locked(size_t needed) {
    if (!reserve_array((void **)&g_vmap, &g_capvmap, needed, sizeof *g_vmap)) return 0;
    if (!reserve_array((void **)&g_vmap_scratch, &g_capvmap_scratch, needed, sizeof *g_vmap_scratch)) return 0;
    return 1;
}
static const vpool_range *vpool_find_address_locked(uintptr_t va) {
    size_t lo = 0, hi = g_nvpools;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (va < g_vpools[mid].lo) hi = mid;
        else if (va >= g_vpools[mid].hi) lo = mid + 1;
        else return &g_vpools[mid];
    }
    return NULL;
}
static int vpool_register_locked(const pool_t *owner) {
    uintptr_t lo = (uintptr_t)owner->mem;
    if (!owner->size || owner->size > UINTPTR_MAX - lo) return 0;
    uintptr_t hi = lo + (uintptr_t)owner->size;
    size_t at = 0;
    while (at < g_nvpools && g_vpools[at].lo < lo) at++;
    if ((at && g_vpools[at - 1].hi > lo) || (at < g_nvpools && g_vpools[at].lo < hi)) return 0;
    if (g_nvpools == SIZE_MAX || !reserve_array((void **)&g_vpools, &g_capvpools, g_nvpools + 1, sizeof *g_vpools)) return 0;
    memmove(g_vpools + at + 1, g_vpools + at, (g_nvpools - at) * sizeof *g_vpools);
    g_vpools[at] = (vpool_range){lo, hi, owner}; g_nvpools++; return 1;
}
static int vpool_remove_owner_locked(const pool_t *owner) {
    for (size_t i = 0; i < g_nvpools; i++) if (g_vpools[i].owner == owner) {
        memmove(g_vpools + i, g_vpools + i + 1, (g_nvpools - i - 1) * sizeof *g_vpools); g_nvpools--; return 1;
    }
    return 0;
}
static int vmap_compare(const void *a, const void *b) {
    const vmap_ent *x = a, *y = b;
    return x->lo < y->lo ? -1 : x->lo > y->lo ? 1 : 0;
}
static void vmap_merge_locked(void) {
    size_t out = 0;
    for (size_t i = 0; i < g_nvmap; i++) {
        vmap_ent e = g_vmap[i];
        if (out) {
            vmap_ent *prev = &g_vmap[out - 1];
            if (prev->hi == e.lo && prev->virtual_pool == e.virtual_pool && prev->physical_pool == e.physical_pool &&
                prev->physical_offset <= UINT64_MAX - (prev->hi - prev->lo) &&
                prev->physical_offset + (prev->hi - prev->lo) == e.physical_offset) {
                prev->hi = e.hi; continue;
            }
        }
        g_vmap[out++] = e;
    }
    g_nvmap = out;
}
static void vmap_replace_locked(const pool_t *virtual_pool, uintptr_t lo, uintptr_t hi, const pool_t *physical_pool, uint64_t physical_offset) {
    size_t out = 0;
    for (size_t i = 0; i < g_nvmap; i++) {
        vmap_ent e = g_vmap[i];
        if (e.hi <= lo || e.lo >= hi) { g_vmap_scratch[out++] = e; continue; }
        if (e.lo < lo) g_vmap_scratch[out++] = (vmap_ent){e.lo, lo, e.virtual_pool, e.physical_pool, e.physical_offset};
        if (e.hi > hi) g_vmap_scratch[out++] = (vmap_ent){hi, e.hi, e.virtual_pool, e.physical_pool, e.physical_offset + (uint64_t)(hi - e.lo)};
    }
    if (physical_pool) g_vmap_scratch[out++] = (vmap_ent){lo, hi, virtual_pool, physical_pool, physical_offset};
    qsort(g_vmap_scratch, out, sizeof *g_vmap_scratch, vmap_compare);
    vmap_ent *old = g_vmap; g_vmap = g_vmap_scratch; g_vmap_scratch = old;
    size_t old_cap = g_capvmap; g_capvmap = g_capvmap_scratch; g_capvmap_scratch = old_cap; g_nvmap = out;
    vmap_merge_locked();
}
static void vmap_remove_virtual_locked(const pool_t *virtual_pool) {
    size_t out = 0;
    for (size_t i = 0; i < g_nvmap; i++) if (g_vmap[i].virtual_pool != virtual_pool)
        g_vmap[out++] = g_vmap[i];
    g_nvmap = out;
}
static void vmap_remove_physical_locked(const pool_t *physical_pool) {
    size_t out = 0;
    for (size_t i = 0; i < g_nvmap; i++) if (g_vmap[i].physical_pool != physical_pool)
        g_vmap[out++] = g_vmap[i];
    g_nvmap = out;
}

static uint64_t host_page(void) { return (uint64_t)getpagesize(); }
static int round_up(uint64_t v, uint64_t a, uint64_t *out) {
    if (!a || v > UINT64_MAX - (a - 1)) return 0;
    *out = (v + a - 1) & ~(a - 1); return 1;
}

static uint8_t *vm_fresh(uint64_t span) {
    mach_vm_address_t a = 0;
    kern_return_t kr = mach_vm_allocate(mach_task_self(), &a, span, VM_FLAGS_ANYWHERE);
    if (kr != KERN_SUCCESS) { LOG("【红】vpool: mach_vm_allocate(%#llx) failed kr=%d", span, kr); return NULL; }
    return (uint8_t *)(uintptr_t)a;
}

int totk_vpool_init(pool_t *o) {
    if (!o) return 0;
    int phys = (o->flags & F_PHYSICAL) != 0, virt = (o->flags & F_VIRTUAL) != 0;
    o->game_mem = o->mem; o->host = NULL; o->span = 0;
    if (phys == virt) { LOG("【红】vpool: pool flags %#x must be exactly one of PHYSICAL/VIRTUAL", o->flags); return 0; }
    if (!o->size || o->size % TOTK_VPOOL_PAGE) { LOG("【红】vpool: pool size %#llx not a multiple of 64 KiB", o->size); return 0; }
    if (virt && o->mem) { LOG("【红】vpool: virtual pool given storage %p", (void *)o->mem); return 0; }
    if (phys && !o->mem) { LOG("【红】vpool: physical pool without storage"); return 0; }
    if (phys && !(o->flags & F_CPU_NO_ACCESS)) {
        uintptr_t base = (uintptr_t)o->mem;
        if (base % host_page() || o->size > UINTPTR_MAX - base) { LOG("【红】vpool: CPU-visible physical pool %p size %#llx is unaligned or overflows", (void *)o->mem, (unsigned long long)o->size); return 0; }
        return 1;   // aliases the game storage directly
    }
    if (!round_up(o->size, host_page(), &o->span) || !o->span) { LOG("【红】vpool: pool size %#llx cannot be rounded safely", (unsigned long long)o->size); return 0; }
    uint8_t *host = vm_fresh(o->span);
    if (!host) return 0;
    if (o->size > UINTPTR_MAX - (uintptr_t)host) { mach_vm_deallocate(mach_task_self(), (mach_vm_address_t)(uintptr_t)host, o->span); o->span = 0; LOG("【红】vpool: allocated pool range overflows"); return 0; }
    o->host = host;
    o->mem = o->host;
    if (virt) {
        pthread_mutex_lock(&g_map_mu);
        int registered = vpool_register_locked(o);
        pthread_mutex_unlock(&g_map_mu);
        if (!registered) { mach_vm_deallocate(mach_task_self(), (mach_vm_address_t)(uintptr_t)o->host, o->span); o->host = NULL; o->mem = o->game_mem; o->span = 0; LOG("【红】vpool: virtual range registry allocation/overlap failed"); return 0; }
    }
    return 1;
}

int totk_vpool_fini(pool_t *o) {
    if (!o) return 0;
    pthread_mutex_lock(&g_map_mu);
    if (o->flags & F_VIRTUAL) {
        if (vpool_remove_owner_locked(o)) vmap_remove_virtual_locked(o);
    }
    if (o->flags & F_PHYSICAL) vmap_remove_physical_locked(o);
    pthread_mutex_unlock(&g_map_mu);
    if (!o->host) return 0; // CPU-visible physical pools still had their reverse mappings removed above.
    mach_vm_deallocate(mach_task_self(), (mach_vm_address_t)(uintptr_t)o->host, o->span);
    o->host = NULL; o->mem = o->game_mem; o->span = 0;
    return 1;
}

static int check_request(const pool_t *v, const totk_mapping_request *r, int i) {
    if (!r || r->virtual_offset < 0 || r->physical_offset < 0 || !r->size) { LOG("【红】vpool: request %d negative/empty range", i); return 0; }
    uint64_t vo = (uint64_t)r->virtual_offset, po = (uint64_t)r->physical_offset;
    if ((vo | po | r->size) % TOTK_VPOOL_PAGE) { LOG("【红】vpool: request %d not 64 KiB aligned (v=%#llx p=%#llx n=%#llx)", i, (unsigned long long)vo, (unsigned long long)po, (unsigned long long)r->size); return 0; }
    if (vo > v->size || r->size > v->size - vo) { LOG("【红】vpool: request %d exceeds virtual pool (v=%#llx n=%#llx pool=%#llx)", i, (unsigned long long)vo, (unsigned long long)r->size, (unsigned long long)v->size); return 0; }
    uintptr_t vb = (uintptr_t)v->mem;
    if (vo > UINTPTR_MAX - vb || r->size > UINTPTR_MAX - (vb + (uintptr_t)vo)) { LOG("【红】vpool: request %d virtual address overflows", i); return 0; }
    if (!r->physical_pool) return 1;   // unmap
    const pool_t *p = (const pool_t *)(const void *)r->physical_pool;
    if (p->magic != POOL_MAGIC || !(p->flags & F_PHYSICAL) || !p->mem) { LOG("【红】vpool: request %d source %p is not a live physical pool", i, (const void *)p); return 0; }
    if (po > p->size || r->size > p->size - po) { LOG("【红】vpool: request %d exceeds physical pool (p=%#llx n=%#llx pool=%#llx)", i, (unsigned long long)po, (unsigned long long)r->size, (unsigned long long)p->size); return 0; }
    uintptr_t pb = (uintptr_t)p->mem;
    if (po > UINTPTR_MAX - pb || r->size > UINTPTR_MAX - (pb + (uintptr_t)po)) { LOG("【红】vpool: request %d physical address overflows", i); return 0; }
    return 1;
}

static int apply_request(pool_t *v, const totk_mapping_request *r) {
    uintptr_t va = (uintptr_t)v->mem + (uintptr_t)r->virtual_offset;
    mach_vm_address_t dst = (mach_vm_address_t)va;
    kern_return_t kr;
    if (!r->physical_pool) kr = mach_vm_allocate(mach_task_self(), &dst, r->size, VM_FLAGS_FIXED | VM_FLAGS_OVERWRITE);
    else {
        const pool_t *p = (const pool_t *)(const void *)r->physical_pool;
        uintptr_t backing = (uintptr_t)p->mem + (uintptr_t)r->physical_offset;
        vm_prot_t cur, max;
        kr = mach_vm_remap(mach_task_self(), &dst, r->size, 0, VM_FLAGS_FIXED | VM_FLAGS_OVERWRITE, mach_task_self(),
                           (mach_vm_address_t)backing, FALSE, &cur, &max, VM_INHERIT_NONE);
        if (kr == KERN_SUCCESS && (cur & (VM_PROT_READ | VM_PROT_WRITE)) != (VM_PROT_READ | VM_PROT_WRITE)) { LOG("【红】vpool: alias lost RW (prot=%d)", cur); return 0; }
    }
    if (kr != KERN_SUCCESS || dst != (mach_vm_address_t)va) {
        LOG("【红】vpool: remap at %#llx size %#llx failed kr=%d", (unsigned long long)dst, (unsigned long long)r->size, kr); return 0;
    }
    const pool_t *physical = r->physical_pool ? (const pool_t *)(const void *)r->physical_pool : NULL;
    vmap_replace_locked(v, va, va + (uintptr_t)r->size, physical, (uint64_t)r->physical_offset);
    return 1;
}

int totk_vpool_map(pool_t *v, int count, const totk_mapping_request *r) {
    if (!v || v->magic != POOL_MAGIC || !(v->flags & F_VIRTUAL) || !v->mem) { LOG("【红】vpool: MapVirtual target %p is not a virtual pool", (void *)v); return 0; }
    if (count < 0 || (count && !r)) { LOG("【红】vpool: invalid request count %d", count); return 0; }
    for (int i = 0; i < count; i++) if (!check_request(v, &r[i], i)) return 0;
    if (!count) return 1;
    pthread_mutex_lock(&g_map_mu);
    const vpool_range *range = vpool_find_address_locked((uintptr_t)v->mem);
    if (!range || range->owner != v || (size_t)count > (SIZE_MAX - g_nvmap) / 2 ||
        !vmap_reserve_locked(g_nvmap + (size_t)count * 2)) {
        pthread_mutex_unlock(&g_map_mu);
        LOG("【红】vpool: map metadata reservation/ownership check failed before VM mutation"); return 0;
    }
    // Reserve table memory before replacing the VM view or its Metal/dirty-page registrations.
    __atomic_store_n(&g_aliases_used, 1, __ATOMIC_RELEASE);
    nvn_dirty_remove_range(v->mem); mtl_pool_del(v->mem);
    int ok = 1;
    for (int i = 0; ok && i < count; i++) ok = apply_request(v, &r[i]);
    mtl_pool_add(v->mem, v->size); nvn_dirty_add_range(v->mem, v->size);
    pthread_mutex_unlock(&g_map_mu);
    if (!ok) die("vpool: mapping failed after validation; virtual pool %p is inconsistent", (void *)v);
    return 1;
}

static size_t vmap_find_address_locked(uintptr_t va) {
    size_t lo = 0, hi = g_nvmap;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (va < g_vmap[mid].lo) hi = mid;
        else if (va >= g_vmap[mid].hi) lo = mid + 1;
        else return mid;
    }
    return g_nvmap;
}
static size_t vmap_find_start_locked(uintptr_t va) {
    size_t lo = 0, hi = g_nvmap;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (g_vmap[mid].lo < va) lo = mid + 1; else hi = mid;
    }
    return lo < g_nvmap && g_vmap[lo].lo == va ? lo : g_nvmap;
}
totk_vpool_address_status totk_vpool_resolve(uintptr_t va, uint64_t len, totk_vpool_address *out) {
    totk_vpool_address result = {0};
    if (out) *out = result;
    if (!len) return TOTK_VPOOL_ADDRESS_INVALID;
    if (len > UINTPTR_MAX - va) return TOTK_VPOOL_ADDRESS_OVERFLOW;
    uintptr_t end = va + (uintptr_t)len;
    pthread_mutex_lock(&g_map_mu);
    const vpool_range *range = vpool_find_address_locked(va);
    if (!range) { pthread_mutex_unlock(&g_map_mu); return TOTK_VPOOL_ADDRESS_NORMAL; }
    size_t i = vmap_find_address_locked(va);
    if (i == g_nvmap) { pthread_mutex_unlock(&g_map_mu); return TOTK_VPOOL_ADDRESS_UNMAPPED; }
    const vmap_ent *first = &g_vmap[i];
    if (first->virtual_pool != range->owner) { pthread_mutex_unlock(&g_map_mu); return TOTK_VPOOL_ADDRESS_UNMAPPED; }
    const pool_t *owner = first->physical_pool;
    uint64_t physical_offset = first->physical_offset + (uint64_t)(va - first->lo);
    uintptr_t physical_base = (uintptr_t)owner->mem;
    if (!owner->mem || physical_offset > UINTPTR_MAX - physical_base) { pthread_mutex_unlock(&g_map_mu); return TOTK_VPOOL_ADDRESS_OVERFLOW; }
    result.backing = physical_base + (uintptr_t)physical_offset;
    result.physical_pool = owner; result.physical_offset = physical_offset;
    uintptr_t first_hi = first->hi < range->hi ? first->hi : range->hi;
    uint64_t contiguous = (uint64_t)(first_hi - va);
    uintptr_t cursor = first_hi;
    uint64_t next_offset = first->physical_offset + (uint64_t)(first_hi - first->lo);
    totk_vpool_address_status blocked = TOTK_VPOOL_ADDRESS_UNMAPPED;
    while (cursor < end && cursor < range->hi) {
        size_t next = vmap_find_start_locked(cursor);
        if (next == g_nvmap) { blocked = TOTK_VPOOL_ADDRESS_UNMAPPED; break; }
        const vmap_ent *e = &g_vmap[next];
        if (e->virtual_pool != range->owner || e->physical_pool != owner || e->physical_offset != next_offset) { blocked = TOTK_VPOOL_ADDRESS_NONCONTIGUOUS; break; }
        uint64_t n = (uint64_t)(e->hi - e->lo);
        if (n > UINT64_MAX - contiguous || n > UINT64_MAX - next_offset) { blocked = TOTK_VPOOL_ADDRESS_OVERFLOW; break; }
        contiguous += n; next_offset += n; cursor = e->hi;
    }
    result.contiguous = contiguous;
    totk_vpool_address_status status;
    if (len <= contiguous) status = TOTK_VPOOL_ADDRESS_MAPPED;
    else if (cursor >= range->hi || end > range->hi) status = TOTK_VPOOL_ADDRESS_NONCONTIGUOUS;
    else status = blocked;
    pthread_mutex_unlock(&g_map_mu);
    if (out) *out = result;
    return status;
}
int totk_vpool_backing(uintptr_t va, uint64_t len, uintptr_t *cpu) {
    totk_vpool_address r;
    if (totk_vpool_resolve(va, len, &r) != TOTK_VPOOL_ADDRESS_MAPPED) return 0;
    if (cpu) *cpu = r.backing;
    return 1;
}
