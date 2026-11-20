"""Exercise the production present-ring wait loop with pthread handshakes."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NVN = ROOT / 'games/totk/third_party/d2r_gfx/gfx/nvn'


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


HARNESS = r'''
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>

#define PRING_N 3
static pthread_mutex_t g_pq_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_pq_cv = PTHREAD_COND_INITIALIZER;
static uint64_t g_pring_tk[PRING_N], g_pq_read, g_pring_full;
static int g_pring_busy[PRING_N];
static _Atomic int g_present_discard;
static pthread_mutex_t handshake_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t handshake_cv = PTHREAD_COND_INITIALIZER;
static int helper_waiting;
static uint64_t waited_ticket;

static void test_cond_wait(pthread_cond_t *cv, pthread_mutex_t *mu) {
    pthread_mutex_lock(&handshake_mu);
    helper_waiting++;
    pthread_cond_broadcast(&handshake_cv);
    pthread_mutex_unlock(&handshake_mu);
    pthread_cond_wait(cv, mu);
}

static void wait_for_helper(void) {
    pthread_mutex_lock(&handshake_mu);
    while (!helper_waiting) pthread_cond_wait(&handshake_cv, &handshake_mu);
    pthread_mutex_unlock(&handshake_mu);
}

static void mtl_present_wait(uint64_t ticket) {
    pthread_mutex_lock(&g_pq_mu);
    waited_ticket = ticket;
    g_pq_read = ticket;
    pthread_mutex_unlock(&g_pq_mu);
}

#define pthread_cond_wait test_cond_wait
// PRODUCTION_HELPERS
#undef pthread_cond_wait

static void *reserve_thread(void *out) { *(int *)out = present_ring_reserve(); return NULL; }

static int pending_uses_oldest_ticket(void) {
    g_pq_read = 0; waited_ticket = 0;
    g_pring_tk[0] = 8; g_pring_tk[1] = 3; g_pring_tk[2] = 5;
    int ring = present_ring_reserve();
    if (ring != 1 || waited_ticket != 3 || g_pring_tk[ring] != UINT64_MAX) return 0;
    present_ring_release_reservation(ring);
    return g_pring_tk[ring] == g_pq_read;
}

static int gpu_busy_wakes_reserver(void) {
    g_pq_read = 9; helper_waiting = 0;
    for (int i=0; i<PRING_N; i++) { g_pring_tk[i] = (uint64_t)i + 1; g_pring_busy[i] = 1; }
    int result = -2; pthread_t th;
    if (pthread_create(&th, NULL, reserve_thread, &result)) return 0;
    wait_for_helper();
    pthread_mutex_lock(&g_pq_mu);
    g_pring_busy[1] = 0;
    pthread_cond_broadcast(&g_pq_cv);
    pthread_mutex_unlock(&g_pq_mu);
    if (pthread_join(th, NULL) || result != 1 || g_pring_tk[1] != UINT64_MAX) return 0;
    present_ring_release_reservation(result);
    return 1;
}

static int discard_wakes_reserver(void) {
    g_pq_read = 9; helper_waiting = 0; atomic_store(&g_present_discard, 0);
    for (int i=0; i<PRING_N; i++) { g_pring_tk[i] = (uint64_t)i + 1; g_pring_busy[i] = 1; }
    int result = -2; pthread_t th;
    if (pthread_create(&th, NULL, reserve_thread, &result)) return 0;
    wait_for_helper();
    pthread_mutex_lock(&g_pq_mu);
    atomic_store(&g_present_discard, 1);
    pthread_cond_broadcast(&g_pq_cv);
    pthread_mutex_unlock(&g_pq_mu);
    if (pthread_join(th, NULL) || result != -1) return 0;
    atomic_store(&g_present_discard, 0);
    return 1;
}

int main(void) {
    if (!pending_uses_oldest_ticket()) return 1;
    if (!gpu_busy_wakes_reserver()) return 2;
    if (!discard_wakes_reserver()) return 3;
    puts("PASS present ring reserve: oldest pending, GPU release, discard wake");
    return 0;
}
'''


class PresentRingReserve(unittest.TestCase):
    @unittest.skipUnless(shutil.which('clang') or shutil.which('cc'), 'requires a C compiler')
    def test_production_waits_and_wakeups(self):
        source = (NVN / 'nvn_mtl.m').read_text()
        helpers = '\n'.join(production_function(source, signature) for signature in (
            'static int present_ring_reserve(void) {',
            'static void present_ring_release_reservation(int ring) {',
        ))
        with tempfile.TemporaryDirectory() as folder:
            probe = Path(folder) / 'present_ring_reserve.c'
            binary = Path(folder) / 'present_ring_reserve'
            probe.write_text(HARNESS.replace('// PRODUCTION_HELPERS', helpers))
            compiler = shutil.which('clang') or shutil.which('cc')
            subprocess.run([compiler, '-std=c11', '-O1', '-pthread', str(probe), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
            self.assertIn('PASS present ring reserve: oldest pending, GPU release, discard wake', result.stdout)


if __name__ == '__main__':
    unittest.main()
