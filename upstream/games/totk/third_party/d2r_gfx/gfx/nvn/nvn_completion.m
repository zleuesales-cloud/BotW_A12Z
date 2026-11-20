#import "nvn_completion.h"

void nvn_gpu_complete(NSArray *handlers, nvn_gpu_result result) {
    for (nvn_gpu_completion handler in handlers) {
        @autoreleasepool { handler(result); }
    }
}

BOOL nvn_submit_classic(id<MTLCommandBuffer> buffer, NSArray *handlers) {
    if (!buffer || buffer.status > MTLCommandBufferStatusEnqueued) return NO;
    NSArray *completion = [handlers copy];
    [buffer addCompletedHandler:^(id<MTLCommandBuffer> done) {
        nvn_gpu_complete(completion, (nvn_gpu_result){done.GPUStartTime, done.GPUEndTime, done.error});
    }];
    [buffer commit];
    return YES;
}
