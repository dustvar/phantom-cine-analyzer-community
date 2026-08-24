import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / 'app-source'
    / 'modules'
    / 'trackmeasure'
)
sys.path.insert(0, str(MODULE_PATH))

from PySide6.QtWidgets import QApplication, QWidget

import simplemeas_ui
import simplemeas_vm


class FakeConfig:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class FakeMetadata:
    FirstImageNo = -10
    ImageCount = 20
    TriggerTime_displ = 0.0
    Bright = 0
    Contrast = 1.0
    Gamma = 1.0


class FakeCineHandler:
    created = []

    def __init__(self):
        self.metadata = FakeMetadata()
        self.cine_loaded = False
        self.path = ''
        self.closed = False
        self.__class__.created.append(self)

    def load_cine(self, path):
        self.path = path
        self.cine_loaded = True

    def close(self):
        self.closed = True


class FakeReport:
    def __init__(self):
        self.cine_path = ''


class FakeReportHandler:
    def __init__(self):
        self.active_report = FakeReport()


class MultiCineWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.window = simplemeas_ui.MainWindow()

    def test_vm_keeps_tracking_state_isolated_per_cine(self):
        view = QWidget()
        original_handler = FakeCineHandler()
        with patch.object(simplemeas_vm, 'CineHandler', FakeCineHandler):
            vm = simplemeas_vm.SimpleMeasVM(
                view, object(), FakeReportHandler(), FakeConfig(), original_handler,
                {'cine_paths': ['/tmp/one.cine', '/tmp/two.cine']}
            )
            vm.draw_all = lambda *args, **kwargs: None
            self.assertTrue(vm.load_cine_workspace_cb(vm.initial_cine_paths))
            self.assertEqual(len(vm.workspace_contexts), 2)
            self.assertEqual(vm.active_cine_index, 0)

            vm.track_data[0] = {'name': 'Object in C1'}
            vm.active_frame = 7
            vm.image_scale = 2.0
            vm.activate_cine_cb(1)
            self.assertEqual(vm.track_data, {})
            self.assertEqual(vm.active_frame, 0)
            self.assertEqual(vm.image_scale, 1.0)

            vm.track_data[0] = {'name': 'Object in C2'}
            vm.active_frame = 4
            vm.image_scale = 3.0
            vm.activate_cine_cb(0)
            self.assertEqual(vm.track_data[0]['name'], 'Object in C1')
            self.assertEqual(vm.active_frame, 7)
            self.assertEqual(vm.image_scale, 2.0)

            vm.activate_cine_cb(1)
            self.assertEqual(vm.track_data[0]['name'], 'Object in C2')
            self.assertEqual(vm.active_frame, 4)
            self.assertEqual(vm.image_scale, 3.0)
            vm.close_workspace()

    def test_active_pane_switches_the_entire_vm_context(self):
        window = self.window

        class FakeVM:
            def __init__(self):
                self.workspace_contexts = [{}, {}]
                self.active_cine_index = 0
                self.cine_path = '/tmp/one.cine'
                self.activated = []

            def activate_cine_cb(self, index):
                self.active_cine_index = index
                self.cine_path = f'/tmp/{index + 1}.cine'
                self.activated.append(index)

        original_vm = window.vm
        try:
            window.vm = FakeVM()
            window.workspace_panes[1].setVisible(True)
            window.activate_cine_pane(1)
            self.assertEqual(window.vm.activated, [1])
            self.assertEqual(window.active_pane_index, 1)
            self.assertIs(window.graph, window.workspace_panes[1].graph)
            self.assertTrue(window.workspace_panes[1].property('activePane'))
            self.assertFalse(window.workspace_panes[0].property('activePane'))
        finally:
            window.vm = original_vm
            window.graph = window.workspace_panes[0].graph
            window._set_active_pane_style(0)

    def test_combined_graph_data_namespaces_objects_by_cine(self):
        window = self.window

        class IdentityCalibration:
            def point_transform(self, values):
                return np.asarray(values)

        class FakeVM:
            active_cine_index = 0
            cal = IdentityCalibration()
            track_data = {}
            active_object = None
            active_frame = 0
            n_diff = 4
            length_units = 'pix'
            time_units = 's'
            workspace_contexts = [
                {
                    'cine_path': '/tmp/alpha.cine',
                    'cal': IdentityCalibration(),
                    'track_data': {0: {
                        'name': 'Bolt', 'enabled': True, 'color': '#ff0000',
                        'points': np.array([[0, 0], [1, 1]]),
                        'frame_ts': np.array([0.0, 1.0]),
                        'frame_ts_trig': np.array([-1.0, 0.0]),
                        'Displacement': np.array([0.0, 1.0]),
                        'notes': {},
                    }},
                },
                {
                    'cine_path': '/tmp/beta.cine',
                    'cal': IdentityCalibration(),
                    'track_data': {0: {
                        'name': 'Bolt', 'enabled': True, 'color': '#00aaff',
                        'points': np.array([[0, 0], [2, 2]]),
                        'frame_ts': np.array([0.0, 1.0]),
                        'frame_ts_trig': np.array([0.0, 1.0]),
                        'Displacement': np.array([0.0, 2.0]),
                        'notes': {},
                    }},
                },
            ]

            def sync_active_workspace_context(self):
                pass

        original_vm = window.vm
        try:
            window.vm = FakeVM()
            combined = window.combined_track_data()
            self.assertEqual(len(combined), 2)
            self.assertEqual(combined[(0, 0)]['name'], 'C1 · alpha · Bolt')
            self.assertEqual(combined[(1, 0)]['name'], 'C2 · beta · Bolt')
            self.assertEqual(combined[(1, 0)]['_cine_index'], 1)

            window.fig_dropdown.setCurrentText('Displacement')
            window.track_canvas.redraw(window.vm.track_data)
            plotted_labels = {
                line.get_label() for line in window.track_canvas.ax.lines
                if line.get_label() != 'frame_pos'
            }
            self.assertEqual(plotted_labels, {'C1 · alpha · Bolt', 'C2 · beta · Bolt'})
            plotted_x = {
                line.get_label(): tuple(line.get_xdata())
                for line in window.track_canvas.ax.lines
                if line.get_label() != 'frame_pos'
            }
            self.assertEqual(plotted_x['C1 · alpha · Bolt'], (-1.0, 0.0))
            self.assertIn('Time from Trigger', window.track_canvas.ax.get_title())
        finally:
            window.vm = original_vm

    def test_theme_color_replaces_the_default_accent_palette(self):
        window = self.window
        original_stylesheet = self.app.styleSheet()
        try:
            window._base_stylesheet = (
                'QPushButton { border: 1px solid #f47efa; '
                'background: #9A4E9E; }'
            )
            window.apply_theme_color('#00aaff', persist=False)
            themed = self.app.styleSheet().lower()
            self.assertIn('#00aaff', themed)
            self.assertNotIn('#f47efa', themed)
        finally:
            self.app.setStyleSheet(original_stylesheet)


if __name__ == '__main__':
    unittest.main()
