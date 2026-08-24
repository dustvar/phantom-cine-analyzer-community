import unittest
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'launcher-source' / 'src'


class LauncherSourceTest(unittest.TestCase):
    def test_conda_path_fallback_is_not_reported_as_an_error(self):
        source = (SOURCE / 'main' / 'envManager.js').read_text(encoding='utf-8')
        self.assertIn("console.log('Conda is not on the launcher PATH", source)
        self.assertNotIn("console.error('Conda not found in PATH", source)

    def test_run_waits_for_environment_activation(self):
        source = (SOURCE / 'main' / 'envManager.js').read_text(encoding='utf-8')
        self.assertIn("conda activate ${env_name} && echo ${readySentinel}", source)
        self.assertIn('PCA environment \\"${env_name}\\" is ready.', source)

    def test_single_module_is_selected_automatically(self):
        source = (SOURCE / 'renderer' / 'renderer.js').read_text(encoding='utf-8')
        self.assertIn('if (modules.length === 1)', source)
        self.assertIn('li.classList.add("module_to_run")', source)

    def test_modified_launcher_has_distinct_window_title(self):
        source = (SOURCE / 'main' / 'windowManager.js').read_text(encoding='utf-8')
        self.assertIn("title: 'Phantom Cine Analyzer Additional Features v'", source)

    def test_modified_launcher_has_additional_features_product_name(self):
        package = json.loads((ROOT / 'launcher-source' / 'package.json').read_text(encoding='utf-8'))
        self.assertEqual(package['productName'], 'Phantom Cine Analyzer Additional Features')

    def test_launcher_accepts_and_limits_multi_cine_selection(self):
        window_source = (SOURCE / 'main' / 'windowManager.js').read_text(encoding='utf-8')
        renderer_source = (SOURCE / 'renderer' / 'renderer.js').read_text(encoding='utf-8')
        self.assertIn("properties: ['openFile', 'multiSelections']", window_source)
        self.assertIn('return filePaths.slice(0, 4)', window_source)
        self.assertIn('cine_paths: selectedCinePaths', renderer_source)
        self.assertIn('[...new Set(paths || [])].slice(0, 4)', renderer_source)


if __name__ == '__main__':
    unittest.main()
