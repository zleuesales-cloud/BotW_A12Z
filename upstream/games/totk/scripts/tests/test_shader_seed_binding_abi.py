import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_shader_seed as seed


class BindingAbiSeedTests(unittest.TestCase):
    def test_service_and_seed_share_one_idempotent_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'botw190'
            discrete = Path(tmp) / 'botw190.discrete-v1'
            self.assertEqual(seed.binding_output_directory(root, 'ab'), root)
            self.assertEqual(seed.binding_output_directory(root, 'discrete-v1'), discrete)
            self.assertEqual(seed.binding_output_directory(discrete, 'discrete-v1'), discrete)
            with self.assertRaisesRegex(ValueError, 'reserved for the discrete-v1'):
                seed.binding_output_directory(discrete, 'ab')

    def test_abi_is_part_of_seed_and_translation_cache_identity(self):
        common = dict(translator_code_sha256='translator', spirv_cross_sha256='spvc')
        ab = seed.translation_identity('ab', **common)
        discrete = seed.translation_identity('discrete-v1', **common)
        self.assertNotEqual(ab, discrete)
        baseline_ab = dict(schema=seed.TRANSLATIONS_SCHEMA, chain=seed.CHAIN,
                           translator_code_sha256='translator', spirv_cross_sha256='spvc',
                           request_sha256=ab['request_sha256'])
        self.assertEqual(ab, baseline_ab)
        self.assertNotIn('binding_abi', ab)
        self.assertEqual(discrete['binding_abi'], 'discrete-v1')

        seed_kwargs = dict(version='1.9.0', game='botw', source_romfs_sha256='romfs',
                           translator_code_sha256='translator', spirv_cross_sha256='spvc',
                           preparation_sha256='prepare', shader_list_sha256='list')
        old_seed = seed.seed_identity('ab', **seed_kwargs)
        baseline_seed = dict(schema=1, chain=seed.CHAIN, **seed_kwargs)
        self.assertEqual(old_seed, baseline_seed)
        self.assertEqual(seed.seed_identity('discrete-v1', **seed_kwargs)['binding_abi'], 'discrete-v1')

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'TOTK_TRANSLATION_CACHE': tmp}):
                ab_folder, _ = seed.translations_folder(Path(tmp) / 'ab.translations', ab)
                discrete_folder, _ = seed.translations_folder(Path(tmp) / 'discrete.translations', discrete)
            self.assertNotEqual(ab_folder, discrete_folder)

    def test_default_service_argv_stays_compatible(self):
        ab = seed.service_arguments('translator', 'spirv-cross', 'out', seed.CHAIN, 'ab')
        discrete = seed.service_arguments('translator', 'spirv-cross', 'out', seed.CHAIN, 'discrete-v1')
        self.assertNotIn('--binding-abi', ab)
        self.assertEqual(discrete[-2:], ['--binding-abi', 'discrete-v1'])


if __name__ == '__main__':
    unittest.main()
