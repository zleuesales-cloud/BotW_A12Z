#pragma once
#import <Metal/Metal.h>

typedef struct {
    double GPUStartTime, GPUEndTime;
    __unsafe_unretained NSError *error; // Valid for the duration of the completion callbacks.
} nvn_gpu_result;
typedef void (^nvn_gpu_completion)(nvn_gpu_result result);

void nvn_gpu_complete(NSArray *handlers, nvn_gpu_result result);
BOOL nvn_submit_classic(id<MTLCommandBuffer> buffer, NSArray *handlers);
/* nvn_submit_classic accepts only NotEnqueued buffers; it owns enqueue/commit ordering. */

#ifdef NVN_COMPLETION_TESTING
void nvn_completion_test_deliver_classic(id<MTLCommandQueue> queue, uint64_t sequence, NSArray *handlers, nvn_gpu_result result);
#endif
