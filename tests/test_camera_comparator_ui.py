import os,sys,tempfile,time,unittest
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from camera_comparator_ui import CameraComparatorPanel
from camera_comparator_media import ComparisonSession
from test_cine_metadata import write_cine
from test_workspace_interactions import ImageCineHandler
from test_multi_cine_workspace import FakeConfig,FakeReportHandler
import simplemeas_ui,simplemeas_vm,measmodel


class ComparatorPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'source.cine'
        write_cine(self.path,frames=12,width=1280,height=128)
        self.errors=[];self.hook=patch.object(sys,'excepthook',lambda *e:self.errors.append(e));self.hook.start()
        self.panel=CameraComparatorPanel();self.panel.resize(1100,760);self.panel.show()
        self.panel.set_session(ComparisonSession(self.path))
    def settle(self):
        start=time.monotonic()
        while (self.panel.busy() or self.panel.debounce.isActive()) and time.monotonic()-start<20:
            self.app.processEvents();time.sleep(.005)
        self.app.processEvents();self.assertFalse(self.panel.busy())
    def ready(self):
        self.panel.source_model.setCurrentIndex(self.panel.source_model.findData('TMX7510'))
        self.panel.source_bin.setCurrentIndex(1)
        self.panel.target_model.setCurrentIndex(self.panel.target_model.findData('T2110'))
        self.panel.target_fps.setValue(1000)
        self.panel.mode.setCurrentIndex(self.panel.mode.findData('1280x128-b1'))
        self.panel.debounce.stop();self.panel.rebuild();self.settle()
    def tearDown(self):
        self.panel.pause();self.panel.debounce.stop();self.settle()
        self.assertTrue(self.panel.shutdown());self.panel.hide();self.panel.deleteLater();self.app.processEvents()
        self.hook.stop();self.temp.cleanup();self.assertFalse(self.errors,self.errors)
    def test_unknown_source_geometry_blocks_simulation_but_source_visible(self):
        self.assertIsNone(self.panel.comparison);self.assertFalse(self.panel.export_pair.isEnabled())
        self.assertIsNotNone(self.panel.left_view.image)
        self.assertIn('source binning',self.panel.notices.text().lower())
    def test_controls_rebuild_preview_timing_and_frames(self):
        self.ready();self.assertIsNotNone(self.panel.right_view.image)
        self.panel.slider.setValue(5);self.settle()
        self.assertIn('Finished',self.panel.right_info.text()) if len(self.panel.comparison.plan.indices)<=5 else None
        self.panel.playback_mode.setCurrentIndex(1);self.panel.debounce.stop();self.panel.rebuild();self.settle()
        self.assertIn('Synchronized',self.panel.readout.text())
        self.panel.phase.setValue(500);self.panel.debounce.stop();self.panel.rebuild();self.settle()
        self.assertEqual(self.panel.comparison.plan.indices.tolist(),[1,3,5,7,9,11])
        self.panel.measure.setChecked(True);self.panel._measure_point(10,10);self.panel._measure_point(13,14)
        self.assertEqual(self.panel.feature.value(),5)
    def test_ui_save_load_export_and_decode(self):
        self.ready();original=deepcopy(self.panel.comparison.settings)
        preset=self.path.with_suffix('.pcacompare')
        with patch('camera_comparator_ui.QFileDialog.getSaveFileName',return_value=(str(preset),'')):self.panel.save_preset()
        self.settle();self.panel.target_fps.setValue(500);self.panel.debounce.stop();self.panel.rebuild();self.settle()
        with patch('camera_comparator_ui.QFileDialog.getOpenFileName',return_value=(str(preset),'')):self.panel.load_preset()
        self.settle();self.assertEqual(self.panel.comparison.settings,original)
        output=self.path.with_suffix('.mp4')
        with patch('camera_comparator_ui.QFileDialog.getSaveFileName',return_value=(str(output),'MP4')):self.panel.export('target')
        self.settle();self.assertTrue(output.exists());self.assertIn('Saved:',self.panel.status.text())
        import cv2
        cap=cv2.VideoCapture(str(output));self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT),6);self.assertTrue(cap.read()[0]);cap.release()
    def test_rapid_settings_and_source_changes_finish_without_stale_frame(self):
        self.ready()
        for i in range(10):self.panel.slider.setValue(i)
        other=self.path.with_name('other.cine');write_cine(other,frames=4,width=1280,height=128)
        self.panel.open_source(other);self.settle()
        self.assertEqual(self.panel.session.path,str(other.resolve()));self.assertEqual(self.panel.last.value()-self.panel.first.value(),3)
        self.panel.open_source(self.path);self.settle()
        self.assertEqual(self.panel.source_bin.currentData(),1);self.assertIsNotNone(self.panel.comparison)


class ComparatorNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_return_to_editor_retains_cuts_frame_and_tracking(self):
        window=simplemeas_ui.MainWindow();report=FakeReportHandler();report.update_active_report=lambda _:None
        vm=simplemeas_vm.SimpleMeasVM(window,measmodel.MeasModel(),report,FakeConfig(),ImageCineHandler(),{})
        vm._apply_image_tools=lambda img:img;window.vm=vm;window.connect_events()
        with patch.object(simplemeas_vm,'CineHandler',ImageCineHandler):vm.load_cine_workspace_cb(['/tmp/one.cine'])
        window.viewer_tool.setChecked(True);window.current_tool=window.viewer_tool
        window.frame_slider.setValue(8)
        from viewer_timeline import split_at
        vm.viewer_edit=split_at(vm.viewer_edit,5);edit=deepcopy(vm.viewer_edit);frame=vm.active_frame
        with patch.object(window.comparator_panel,'add_sources'):
            window.comparator_tool.click();self.app.processEvents()
        self.assertIs(window.workflow_stack.currentWidget(),window.comparator_panel)
        self.assertFalse(window.toggle_playback_shortcut.isEnabled())
        self.assertFalse(window.split_section_shortcut.isEnabled())
        window.viewer_tool.click();self.app.processEvents()
        self.assertIs(window.workflow_stack.currentWidget(),window.tab_splitter)
        self.assertTrue(window.toggle_playback_shortcut.isEnabled())
        self.assertTrue(window.split_section_shortcut.isEnabled())
        self.assertIs(window.current_tool,window.viewer_tool);self.assertEqual(vm.active_frame,frame);self.assertEqual(vm.viewer_edit,edit)
        self.assertTrue(window.comparator_panel.shutdown());vm.close_workspace();window.hide();window.deleteLater();self.app.processEvents()

if __name__=='__main__':unittest.main()
