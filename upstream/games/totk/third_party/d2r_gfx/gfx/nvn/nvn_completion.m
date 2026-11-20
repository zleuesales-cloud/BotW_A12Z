#import "nvn_completion.h"
#import <objc/runtime.h>

@interface NVNClassicCompletionState : NSObject {
@public
    uint64_t next_submit, next_deliver;
    dispatch_queue_t feedback_queue;
    NSMutableDictionary<NSNumber *, NSArray *> *pending;
}
@end

@implementation NVNClassicCompletionState
- (instancetype)init {
    if ((self = [super init])) {
        next_submit = next_deliver = 1;
        feedback_queue = dispatch_queue_create("nvn.classic-completion", DISPATCH_QUEUE_SERIAL);
        pending = [NSMutableDictionary dictionary];
        if (!feedback_queue || !pending) return nil;
    }
    return self;
}
@end

static char g_classic_completion_state_key;
static NVNClassicCompletionState *classic_completion_state(id<MTLCommandQueue> queue) {
    @synchronized (queue) {
        NVNClassicCompletionState *state = objc_getAssociatedObject(queue, &g_classic_completion_state_key);
        if (!state) {
            state = [NVNClassicCompletionState new];
            if (state) objc_setAssociatedObject(queue, &g_classic_completion_state_key, state, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        }
        return state;
    }
}

/* Called only on this queue's serial feedback queue. GPU callbacks can arrive
   out of order; retain later results until all earlier CPU effects have run. */
static void classic_completion_receive(NVNClassicCompletionState *state, uint64_t sequence, NSArray *payload) {
    @autoreleasepool {
        state->pending[@(sequence)] = payload;
        for (;;) {
            NSNumber *key = @(state->next_deliver);
            NSArray *entry = state->pending[key];
            if (!entry) break;
            [state->pending removeObjectForKey:key];
            state->next_deliver++;
            NSError *error = entry[3] == [NSNull null] ? nil : entry[3];
            nvn_gpu_complete(entry[0], (nvn_gpu_result){[entry[1] doubleValue], [entry[2] doubleValue], error});
        }
    }
}

void nvn_gpu_complete(NSArray *handlers, nvn_gpu_result result) {
    for (nvn_gpu_completion handler in handlers) {
        @autoreleasepool { handler(result); }
    }
}

BOOL nvn_submit_classic(id<MTLCommandBuffer> buffer, NSArray *handlers) {
    if (!buffer) return NO;
    id<MTLCommandQueue> queue = buffer.commandQueue;
    if (!queue) return NO;
    NVNClassicCompletionState *state = classic_completion_state(queue);
    if (!state) return NO;
    NSArray *completion = [handlers copy] ?: @[];
    @synchronized (queue) {
        /* Only this helper may enqueue: external enqueue order cannot be
           aligned with the callback sequence assigned at commit time. */
        if (buffer.status != MTLCommandBufferStatusNotEnqueued) return NO;
        uint64_t sequence = state->next_submit++;
        [buffer addCompletedHandler:^(id<MTLCommandBuffer> done) {
            @autoreleasepool {
                NSError *error = done.error;
                NSArray *payload = @[completion, @(done.GPUStartTime), @(done.GPUEndTime), error ?: [NSNull null]];
                dispatch_async(state->feedback_queue, ^{
                    @autoreleasepool { classic_completion_receive(state, sequence, payload); }
                });
            }
        }];
        [buffer commit];
    }
    return YES;
}

#ifdef NVN_COMPLETION_TESTING
void nvn_completion_test_deliver_classic(id<MTLCommandQueue> queue, uint64_t sequence,
                                         NSArray *handlers, nvn_gpu_result result) {
    @autoreleasepool {
        NVNClassicCompletionState *state = classic_completion_state(queue);
        if (!state) abort();
        NSError *error = result.error;
        NSArray *payload = @[[handlers copy] ?: @[], @(result.GPUStartTime), @(result.GPUEndTime), error ?: [NSNull null]];
        dispatch_sync(state->feedback_queue, ^{
            @autoreleasepool { classic_completion_receive(state, sequence, payload); }
        });
    }
}
#endif
