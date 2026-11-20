"""Exercise the production virtual-pool interval table without initializing Metal."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NVN = ROOT / 'shared/third_party/d2r_gfx/gfx/nvn'
GAME_NVN = ROOT / 'games/totk/third_party/d2r_gfx/gfx/nvn'


class VpoolMappingTable(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'requires Darwin Mach VM')
    def test_dynamic_partial_alias_lifecycle_and_checked_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'vpool-mapping'
            subprocess.run([
                'xcrun', 'clang', '-std=gnu11', '-O1', '-DGAME_BOTW=1',
                '-I' + str(GAME_NVN), '-I' + str(NVN), '-I' + str(NVN / 'include'),
                str(ROOT / 'tests/vpool_mapping_cpu_probe.c'), str(NVN / 'totk_vpool.c'),
                '-o', str(binary),
            ], check=True, timeout=30)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('PASS vpool mapping metadata CPU probe:', run.stdout)


if __name__ == '__main__':
    unittest.main()
