"""Deterministically reverse classic completion arrival on two Metal queues."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

NVN = Path(__file__).resolve().parents[1] / 'games/totk/third_party/d2r_gfx/gfx/nvn'


PROBE = r'''
#import "nvn_completion.h"
#include <assert.h>
#include <stdio.h>

int main(void) { @autoreleasepool {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice(); assert(device);
    id<MTLCommandQueue> first_queue = [device newCommandQueue];
    id<MTLCommandQueue> second_queue = [device newCommandQueue];
    assert(first_queue && second_queue);
    NSError *first_error = [NSError errorWithDomain:@"first" code:1 userInfo:nil];
    NSError *second_error = [NSError errorWithDomain:@"second" code:2 userInfo:nil];
    __block int first_order = 0, second_order = 0;
    NSArray *first_handlers = @[
        ^(nvn_gpu_result r) { assert(first_order == 0 && r.error == first_error && r.GPUStartTime == 1); first_order = 1; },
        ^(nvn_gpu_result r) { assert(first_order == 1 && r.GPUEndTime == 2); first_order = 2; }
    ];
    NSArray *second_handlers = @[
        ^(nvn_gpu_result r) { assert(first_order == 2 && r.error == second_error); first_order = 3; }
    ];
    NSArray *other_queue_handler = @[
        ^(nvn_gpu_result r) { assert(!r.error && second_order == 0); second_order = 1; }
    ];
    nvn_completion_test_deliver_classic(first_queue, 2, second_handlers, (nvn_gpu_result){3, 4, second_error});
    assert(first_order == 0);
    nvn_completion_test_deliver_classic(second_queue, 1, other_queue_handler, (nvn_gpu_result){0});
    assert(second_order == 1 && first_order == 0);
    nvn_completion_test_deliver_classic(first_queue, 1, first_handlers, (nvn_gpu_result){1, 2, first_error});
    assert(first_order == 3);
    puts("PASS classic completion order: contiguous per queue after reversed arrival");
} }
'''


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'requires macOS Metal')
class ClassicCompletionOrder(unittest.TestCase):
    def test_reversed_callbacks_wait_for_the_contiguous_queue_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = work / 'completion_order.m'
            binary = work / 'completion_order'
            probe.write_text(PROBE)
            subprocess.run([
                'xcrun', 'clang', '-fobjc-arc', '-DNVN_COMPLETION_TESTING', '-I' + str(NVN),
                str(probe), str(NVN / 'nvn_completion.m'), '-framework', 'Foundation', '-framework', 'Metal',
                '-o', str(binary),
            ], check=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
            self.assertIn('PASS classic completion order: contiguous per queue after reversed arrival', result.stdout)


if __name__ == '__main__':
    unittest.main()
