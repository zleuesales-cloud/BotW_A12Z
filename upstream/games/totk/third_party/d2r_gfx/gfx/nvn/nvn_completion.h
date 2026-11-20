#pragma once
#import <Metal/Metal.h>

typedef struct {
    double GPUStartTime, GPUEndTime;
    __unsafe_unretained NSError *error; // Valid for the duration of the completion callbacks.
} nvn_gpu_result;
typedef void (^nvn_gpu_completion)(nvn_gpu_result result);

void nvn_gpu_complete(NSArray *handlers, nvn_gpu_result result);
BOOL nvn_submit_classic(id<MTLCommandBuffer> buffer, NSArray *handlers);
