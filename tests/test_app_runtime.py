import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'
sys.path.insert(0, str(MODULE))
import app_runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root/'profile'
        self.environment = patch.dict(os.environ, {'PCA_USER_DATA_DIR': str(self.profile)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.seed = self.root/'config.json'
        self.seed.write_text(json.dumps({'image_scale': .125, 'theme': 'dark'}))

    def test_migration_preserves_settings_without_mutating_bundle(self):
        original = self.seed.read_bytes()
        target = Path(app_runtime.settings_path(self.seed))
        self.assertEqual(target.parent, self.profile.resolve())
        self.assertEqual(json.loads(target.read_text())['image_scale'], .125)
        target.write_text('{"image_scale": 3}')
        self.assertEqual(json.loads(Path(app_runtime.settings_path(self.seed)).read_text())['image_scale'], 3)
        self.assertEqual(self.seed.read_bytes(), original)

    def test_corrupt_settings_are_retained_and_recovered(self):
        target = Path(app_runtime.settings_path(self.seed))
        target.write_text('{broken')
        app_runtime.settings_path(self.seed)
        self.assertEqual(json.loads(target.read_text())['theme'], 'dark')
        backups = list(self.profile.glob('settings.invalid-*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), '{broken')

    def test_documents_and_legacy_launch_config(self):
        self.assertEqual(app_runtime.launch_config(['-psn_123']), {})
        self.assertEqual(app_runtime.launch_config(['A.CINE', 'B.cine'])['cine_paths'],
                         [str(Path(p).resolve()) for p in ('A.CINE', 'B.cine')])
        self.assertEqual(app_runtime.launch_config(['saved.pcajob'])['job_path'], str(Path('saved.pcajob').resolve()))
        self.assertEqual(app_runtime.launch_config([self.seed])['image_scale'], .125)
        for arguments in (['a.cine']*5, ['a.pcajob', 'b.cine'], ['notes.txt']):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                app_runtime.launch_config(arguments)

    def test_frozen_temporary_font_cache_is_replaced_by_persistent_profile(self):
        env = dict(os.environ, PYTHONPATH=str(MODULE), MPLCONFIGDIR='/tmp/pyinstaller-font-cache',
                   NUMBA_CACHE_DIR='/tmp/old-numba-cache')
        script = ('import app_runtime, os, json; app_runtime.configure_runtime(); '
                  'print(json.dumps([os.environ["MPLCONFIGDIR"], os.environ["NUMBA_CACHE_DIR"]]))')
        result = subprocess.run([sys.executable, '-c', script], env=env,
                                capture_output=True, text=True, check=True)
        paths = [Path(p) for p in json.loads(result.stdout)]
        self.assertEqual(paths, [self.profile.resolve()/'cache/matplotlib',
                                 self.profile.resolve()/'cache/numba'])
        self.assertTrue(all(p.is_dir() for p in paths))

    def test_second_process_forwards_documents_and_finder_events_queue(self):
        # Test real local sockets, independently of the GUI suite's QApplication.
        script = '''
import json, subprocess, sys
from launch_app import DesktopApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFileOpenEvent
app = DesktopApplication([])
assert app.become_primary([])
app.event(QFileOpenEvent('/tmp/Finder.cine'))
assert app.pending_documents == ['/tmp/Finder.cine']
received = []
app.document_handler = lambda paths: (received.extend(paths), app.quit())
child = subprocess.Popen([sys.executable, '-c',
    'from launch_app import DesktopApplication; a=DesktopApplication([]); '
    'assert not a.become_primary(["/tmp/second video.cine"])'])
QTimer.singleShot(7000, app.quit)
app.exec()
assert child.wait(timeout=5) == 0
assert received == ['/tmp/second video.cine'], received
app.local_server.close()
print('Forwarding passed')
'''
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                   PYTHONPATH=str(MODULE)+os.pathsep+os.environ.get('PYTHONPATH', ''))
        result = subprocess.run([sys.executable, '-c', script], env=env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)


if __name__ == '__main__':
    unittest.main()
