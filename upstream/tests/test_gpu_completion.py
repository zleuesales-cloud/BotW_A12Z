"""Check the shared completion path and real classic Metal submission."""
from pathlib import Path
import subprocess
import tempfile
import unittest

NVN = Path(__file__).resolve().parents[1] / "games/totk/third_party/d2r_gfx/gfx/nvn"

class GPUCompletion(unittest.TestCase):
    def test_callback_order_snapshot_and_gpu_readback(self):
        source = r'''
#import "nvn_completion.h"
#include <assert.h>
#include <string.h>
int main(void) { @autoreleasepool {
    __block int order = 0;
    NSError *error = [NSError errorWithDomain:@"first-error" code:7 userInfo:nil];
    nvn_gpu_complete(@[
        ^(nvn_gpu_result r) { assert(order++ == 0); assert(r.error == error); assert(r.GPUStartTime == 1); },
        ^(nvn_gpu_result r) { assert(order++ == 1); assert(r.GPUEndTime == 2); }
    ], (nvn_gpu_result){1, 2, error});
    assert(order == 2);
    id<MTLDevice> device = MTLCreateSystemDefaultDevice(); assert(device);
    id<MTLCommandQueue> queue = [device newCommandQueue]; assert(queue);
    id<MTLBuffer> output = [device newBufferWithLength:4096 options:MTLResourceStorageModeShared]; assert(output);
    memset(output.contents, 0, output.length);
    id<MTLCommandBuffer> externally_enqueued = [queue commandBuffer]; assert(externally_enqueued);
    [externally_enqueued enqueue];
    assert(!nvn_submit_classic(externally_enqueued, @[])); // External enqueue order is not assigned a helper sequence.
    [externally_enqueued commit]; // Rejected helper submissions still need the caller to drain their queue slot.
    id<MTLCommandBuffer> cb = [queue commandBuffer]; assert(cb && cb.retainedReferences);
    assert([cb conformsToProtocol:@protocol(MTLCommandBuffer)]);
    if ([device supportsFamily:MTLGPUFamilyMetal4]) {
        id<MTL4CommandBuffer> modern = [device newCommandBuffer]; assert(modern);
        assert(![modern conformsToProtocol:@protocol(MTLCommandBuffer)]);
    }
    id<MTLSharedEvent> gate = [device newSharedEvent]; assert(gate);
    [cb encodeWaitForEvent:gate value:1];
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder]; assert(blit);
    [blit fillBuffer:output range:NSMakeRange(0, output.length / 2) value:0x5a]; [blit endEncoding];
    id<MTLCommandBuffer> cb2 = [queue commandBuffer]; assert(cb2);
    id<MTLBlitCommandEncoder> blit2 = [cb2 blitCommandEncoder]; assert(blit2);
    [blit2 fillBuffer:output range:NSMakeRange(output.length / 2, output.length / 2) value:0xa5]; [blit2 endEncoding];
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    dispatch_semaphore_t done2 = dispatch_semaphore_create(0);
    __block int completed = 0, queue_order = 0;
    NSMutableArray *handlers = [NSMutableArray arrayWithObjects:
        ^(nvn_gpu_result r) { assert(!r.error && r.GPUEndTime >= r.GPUStartTime); assert(queue_order == 0); queue_order = 1; completed++; },
        ^(nvn_gpu_result r) { assert(completed == 1); dispatch_semaphore_signal(done); }, nil];
    NSArray *handlers2 = @[
        ^(nvn_gpu_result r) { assert(!r.error && queue_order == 1); queue_order = 2; dispatch_semaphore_signal(done2); }
    ];
    assert(!nvn_submit_classic(nil, handlers));
    assert(nvn_submit_classic(cb, handlers));
    assert(!nvn_submit_classic(cb, handlers));
    assert(nvn_submit_classic(cb2, handlers2));
    [handlers removeAllObjects]; // Submission owns immutable callback snapshots.
    gate.signaledValue = 1;
    assert(!dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10*NSEC_PER_SEC)));
    assert(!dispatch_semaphore_wait(done2, dispatch_time(DISPATCH_TIME_NOW, 10*NSEC_PER_SEC)));
    assert(queue_order == 2);
    assert(cb.status == MTLCommandBufferStatusCompleted);
    assert(cb2.status == MTLCommandBufferStatusCompleted);
    const unsigned char *bytes = output.contents;
    for (NSUInteger i=0; i<output.length / 2; i++) assert(bytes[i] == 0x5a);
    for (NSUInteger i=output.length / 2; i<output.length; i++) assert(bytes[i] == 0xa5);
} }
'''
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = work / "probe.m"
            probe.write_text(source)
            binary = work / "probe"
            subprocess.run(["xcrun", "clang", "-fobjc-arc", "-I"+str(NVN), str(probe), str(NVN/"nvn_completion.m"), "-framework", "Foundation", "-framework", "Metal", "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=20)

if __name__ == "__main__":
    unittest.main()
