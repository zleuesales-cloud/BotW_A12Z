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
static int parse(NSDictionary *j) {
    nvn_refl r; char error[256];
    int failed = nvn_refl_from_json(j, &r, error, sizeof error);
    free(r.c1); return failed;
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
