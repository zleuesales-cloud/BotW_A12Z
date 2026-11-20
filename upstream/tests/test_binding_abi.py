"""Exercise production reflection parsing without GPU execution."""
from pathlib import Path
import subprocess
import tempfile
import unittest

NVN = Path(__file__).resolve().parents[1] / "games/totk/third_party/d2r_gfx/gfx/nvn"

class BindingABI(unittest.TestCase):
    def test_legacy_and_explicit_abi_and_invalid_argument_ids(self):
        source = r'''
#import "nvn_refl.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
static int parse(NSDictionary *j) {
    nvn_refl r; char error[256];
    int failed = nvn_refl_from_json(j, &r, error, sizeof error);
    free(r.c1); return failed;
}
static int parse_mode(NSDictionary *j, int allow_discrete, nvn_refl *r, char *error) {
    return nvn_refl_from_json_mode(j, r, allow_discrete, error, 256);
}
static void expect_failure(NSDictionary *j, const char *needle) {
    nvn_refl r; char error[256] = {0};
    assert(parse_mode(j, 1, &r, error));
    assert(strstr(error, needle));
    free(r.c1);
}
int main(void) { @autoreleasepool {
    NSDictionary *binding = @{ @"kind": @"cbuf", @"reg": @0, @"arg_id": @0 };
    NSMutableDictionary *j = [@{ @"route": @"B", @"bindings": @[binding] } mutableCopy];
    assert(!parse(j));
    j[@"bind_model"] = @"ab"; assert(!parse(j));
    for (id model in @[@"discrete", @"tier1", @"unknown", @42, [NSNull null]]) {
        j[@"bind_model"] = model; assert(parse(j));
    }
    [j removeObjectForKey:@"bind_model"];
    for (id slot in @[@(-1), @(NVN_REFL_MAXB), @1.5, @"0", [NSNull null]]) {
        NSMutableDictionary *b = [binding mutableCopy]; b[@"arg_id"] = slot;
        j[@"bindings"] = @[b]; assert(parse(j));
    }
    NSMutableDictionary *missing = [binding mutableCopy]; [missing removeObjectForKey:@"arg_id"];
    j[@"bindings"] = @[missing]; assert(parse(j));
    j[@"bindings"] = @[binding, binding]; assert(parse(j));
    j[@"bindings"] = @[@42]; assert(parse(j));
    NSMutableDictionary *last = [binding mutableCopy]; last[@"arg_id"] = @(NVN_REFL_MAXB-1);
    j[@"bindings"] = @[binding, last]; assert(!parse(j));
    last[@"arg_id"] = @1.0; j[@"bindings"] = @[last]; assert(!parse(j));

    // Legacy parser remains AB-only; the explicit parser option is available to format tests and a guarded loader.
    NSArray *discrete_bindings = @[
        @{ @"kind": @"cbuf", @"reg": @0, @"metal_class": @"buffer", @"metal_slot": @0,
           @"msl_name": @"fp_c3", @"msl_type": @"constant float4*" },
        @{ @"kind": @"tbuf", @"reg": @7, @"metal_class": @"texture", @"metal_slot": @0,
           @"msl_name": @"fp_t_tcb_8", @"msl_type": @"texture_buffer<float, access::read>" },
        @{ @"kind": @"samp_tex", @"reg": @7, @"metal_class": @"sampler", @"metal_slot": @0,
           @"msl_name": @"fp_t_tcb_8Smplr", @"msl_type": @"sampler" },
        @{ @"kind": @"sbuf_ubo", @"reg": @3, @"off": @16, @"rw": @YES, @"metal_class": @"buffer", @"metal_slot": @1,
           @"msl_name": @"fp_s0", @"msl_type": @"device uint*" },
        @{ @"kind": @"drvcb", @"bank": @0, @"metal_class": @"buffer", @"metal_slot": @2,
           @"msl_name": @"fp_c0", @"msl_type": @"constant float4*" },
        @{ @"kind": @"c1", @"metal_class": @"buffer", @"metal_slot": @3,
           @"msl_name": @"fp_c1", @"msl_type": @"constant float4*" },
    ];
    NSMutableDictionary *d = [@{ @"route": @"B", @"bind_model": @"discrete-v1", @"stage": @"cs",
        @"bindings": discrete_bindings, @"c1": @"aabb", @"drv_reads": @{ @"0": @{}, @"2": @{} },
        @"threadgroup_size": @[@4,@2,@1], @"vertex_inputs": @[@{@"location": @3, @"msl_type": @"uint4"}] } mutableCopy];
    assert(parse(d));
    nvn_refl r; char error[256] = {0};
    assert(!parse_mode(d, 1, &r, error));
    assert(r.binding_model == NVN_BIND_MODEL_DISCRETE_V1 && r.abi == -1 && r.nb == 6);
    assert(r.b[0].kind == NVN_RB_CBUF && r.b[0].metal_class == NVN_METAL_CLASS_BUFFER && r.b[0].metal_slot == 0 && r.b[0].reg == 0);
    assert(r.b[1].kind == NVN_RB_TBUF && r.b[1].metal_class == NVN_METAL_CLASS_TEXTURE && r.b[1].metal_slot == 0 && r.b[1].reg == 7);
    assert(r.b[2].kind == NVN_RB_SAMPT && r.b[2].metal_class == NVN_METAL_CLASS_SAMPLER && r.b[2].metal_slot == 0 && r.b[2].reg == 7);
    assert(r.b[3].kind == NVN_RB_SBUFU && r.b[3].off == 16 && r.b[3].rw);
    assert(r.b[4].kind == NVN_RB_DRV0 && r.drv_read[0] && r.drv_read[2]);
    assert(r.b[5].kind == NVN_RB_C1 && r.has_c1 && r.c1_len == 2 && r.c1[0] == 0xaa && r.c1[1] == 0xbb);
    assert(r.tg[0] == 4 && r.tg[1] == 2 && r.tg[2] == 1);
    assert(r.nv == 1 && r.v[0].loc == 3 && r.v[0].ct == NVN_CT_UINT);
    free(r.c1);

    NSMutableDictionary *bad = [d mutableCopy];
    bad[@"bindings"] = @[discrete_bindings[0], discrete_bindings[0]];
    expect_failure(bad, "duplicate discrete Metal class");
    bad[@"bindings"] = @[[discrete_bindings[0] mutableCopy]];
    NSMutableDictionary *one = [bad[@"bindings"][0] mutableCopy];
    one[@"metal_class"] = @"texture"; one[@"msl_type"] = @"texture2d<float>"; bad[@"bindings"] = @[one];
    expect_failure(bad, "does not match NVN kind");
    one = [discrete_bindings[0] mutableCopy]; one[@"msl_type"] = @"texture2d<float>"; bad[@"bindings"] = @[one];
    expect_failure(bad, "does not match MSL type");
    one = [discrete_bindings[0] mutableCopy]; one[@"metal_slot"] = @31; bad[@"bindings"] = @[one];
    expect_failure(bad, "invalid discrete Metal slot");
    one = [discrete_bindings[1] mutableCopy]; one[@"metal_slot"] = @96; bad[@"bindings"] = @[one];
    expect_failure(bad, "invalid discrete Metal slot");
    one = [discrete_bindings[2] mutableCopy]; one[@"metal_slot"] = @16; bad[@"bindings"] = @[one];
    expect_failure(bad, "invalid discrete Metal slot");
    one = [discrete_bindings[0] mutableCopy]; one[@"metal_slot"] = @1.5; bad[@"bindings"] = @[one];
    expect_failure(bad, "invalid discrete Metal slot");
    one = [discrete_bindings[0] mutableCopy]; one[@"reg"] = @0.5; bad[@"bindings"] = @[one];
    expect_failure(bad, "invalid discrete NVN register");
    one = [discrete_bindings[3] mutableCopy]; one[@"off"] = @1.5; bad[@"bindings"] = @[one];
    expect_failure(bad, "invalid discrete storage-buffer offset");
    one = [discrete_bindings[4] mutableCopy]; one[@"bank"] = @1; bad[@"bindings"] = @[one];
    expect_failure(bad, "invalid discrete driver bank");
    one = [discrete_bindings[0] mutableCopy]; one[@"metal_class"] = @"image"; bad[@"bindings"] = @[one];
    expect_failure(bad, "invalid discrete Metal class");
    bad[@"bindings"] = [NSMutableArray arrayWithCapacity:NVN_REFL_MAXB+1];
    for (int i = 0; i <= NVN_REFL_MAXB; i++) [bad[@"bindings"] addObject:[NSNull null]];
    expect_failure(bad, "超上限");
} }
'''
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = work / "probe.m"
            probe.write_text(source)
            binary = work / "probe"
            subprocess.run(["xcrun", "clang", "-fobjc-arc", "-I"+str(NVN), "-I"+str(NVN/"include"), str(probe), str(NVN/"nvn_refl.m"), "-framework", "Foundation", "-framework", "Metal", "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

if __name__ == "__main__":
    unittest.main()
