from PySide6.QtCore import QCoreApplication, QEvent
"""Connected UI setup, measurements, failure handling and job persistence."""
import os,sys,time,tempfile,unittest,threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cv2
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication,QTabWidget,QTextBrowser
import simplemeas_ui,simplemeas_vm,measmodel
from tumbling_tracker import TUMBLING_TRACKING_METHOD,TrackerSettings
from tracking_setup_ui import CLASSIC,EDGE
from test_workspace_interactions import ImageCineHandler
from test_multi_cine_workspace import FakeConfig,FakeReportHandler
from test_tumbling_tracker import scene
from workspace_job import save_job,read_job


class TumblingCine(ImageCineHandler):
    def load_cine(self,path):
        super().load_cine(path)
        self.metadata.ImageCount=18;self.metadata.RealBPP=8
    def get_img(self,index=0):
        image,mask=scene(index,False)
        y,x=np.mgrid[:240,:320]
        gray=(180+20*np.sin(x/8+index*.1)+15*np.cos(y/7)).astype(np.uint8)
        gray[:180,:260][mask>0]=35
        return gray


class TumblingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.errors=[];self.hook=patch.object(sys,'excepthook',lambda *e:self.errors.append(e));self.hook.start()
        self.w=simplemeas_ui.MainWindow();report=FakeReportHandler();report.update_active_report=lambda kind:None
        self.vm=simplemeas_vm.SimpleMeasVM(self.w,measmodel.MeasModel(),report,FakeConfig(),TumblingCine(),{})
        self.w.vm=self.vm;self.vm._apply_image_tools=lambda img:img;self.w.connect_events()
        with patch.object(simplemeas_vm,'CineHandler',TumblingCine):self.vm.load_cine_workspace_cb(['/tmp/tumbling.cine'])
        self.w.show();self.app.processEvents()
        with patch.object(self.w,'choose_tracking_method',return_value=None):self.w.track_tool.click()
        self.app.processEvents()
    def tearDown(self):
        if self.w._tracking_busy():self.vm.abort_autotrack=True;self.w._tracking_thread.join(10)
        self.w._stop_playback();self.w.overlay_panel.shutdown();self.w.comparator_panel.shutdown();self.vm.close_workspace()
        self.w.hide();self.w.deleteLater(); QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents();self.hook.stop()
        self.assertEqual(self.errors,[],str(self.errors))
    def create(self,method=TUMBLING_TRACKING_METHOD):
        self.w._start_tracking_method(method)
        self.w._guide_target_chosen(QPointF(60,88))
        self.w._finish_guide_layout()  # Engine integration tests start with committed setup.
        self.app.processEvents();return self.vm.track_data[self.vm.active_object]
    def test_three_methods_have_visible_settings_before_point_selection(self):
        p=self.w.tracking_setup
        for method,key in ((CLASSIC,'subpixel_size'),(EDGE,'edge_weight'),(TUMBLING_TRACKING_METHOD,'tumbling.adaptation')):
            p.methods[method].click();self.app.processEvents()
            self.assertEqual(self.vm.pending_tracking_method,method)
            self.assertFalse(p.controls[key].isHidden());self.assertFalse(p.run.isEnabled())
        p.controls['tumbling.adaptation'].setValue(.35)
        self.w.create_tumbling_track_at(QPointF(60,88));self.app.processEvents()
        self.assertEqual(self.vm.track_data[0]['tumbling']['adaptation'],.35)
        self.assertTrue(p.run.isEnabled())
    def test_tumbling_run_keeps_centroids_rejections_and_serializable_settings(self):
        track=self.create();self.assertTrue(np.isnan(track['angles']).all())
        self.w.process_autotrack('Process')
        deadline=time.monotonic()+20
        while self.w._tracking_busy():
            self.app.processEvents();time.sleep(.01)
            self.assertLess(time.monotonic(),deadline)
        self.app.processEvents();self.w._tracking_finished_ui()
        self.assertEqual(track['frames'].tolist(),list(range(18)))
        for frame,point in zip(track['frames'],track['points']):
            yy,xx=np.nonzero(scene(frame,False)[1]);self.assertLess(np.linalg.norm(point-[xx.mean(),yy.mean()]),1.5)
        self.assertTrue(np.isnan(track['angles']).all())
        self.assertTrue(self.w.tracking_setup.run.isEnabled())
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'tracking.pcajob';save_job(path,dict(contexts=[],active=-1,track=track))
            restored=read_job(path)['track']
        np.testing.assert_array_equal(restored['points'],track['points'])
        self.assertEqual(restored['tumbling_observations'],track['tumbling_observations'])
        self.assertEqual(restored['tumbling'],track['tumbling'])
    def test_parameters_are_per_object_and_preview_does_not_replace_measurements(self):
        a=self.create();original=a['points'].copy()
        p=self.w.tracking_setup;p.controls['tumbling.growth'].setValue(22);self.w._preview_tumbling_mask()
        np.testing.assert_array_equal(a['points'],original);self.assertTrue(a['tumbling_dirty'])
        b=self.create(CLASSIC)
        self.assertEqual(b['tracking_method'],CLASSIC);self.assertFalse(b['smart_frames'])
        self.w._select_tracking_object(a['id'])
        self.assertEqual(p.controls['tumbling.growth'].value(),22)
        self.assertEqual(a['tracking_method'],TUMBLING_TRACKING_METHOD)
    def test_worker_error_leaves_controls_usable_and_preserves_prior_measurements(self):
        track=self.create();original=track['points'].copy();track['start']=10;track['end']=-5
        self.w.process_autotrack('Process')
        self.w._tracking_thread.join(10);self.app.processEvents();self.w._tracking_finished_ui()
        self.assertFalse(self.w._tracking_busy());self.assertTrue(self.w.tracking_setup.run.isEnabled())
        np.testing.assert_array_equal(track['points'],original)
    def test_cancel_keeps_partial_result_and_restores_controls(self):
        from tumbling_tracker import AdaptiveSilhouetteTracker
        track=self.create();entered=threading.Event();release=threading.Event()
        original=AdaptiveSilhouetteTracker.step
        def slow_step(tracker,image,frame):
            entered.set();release.wait(5)
            return original(tracker,image,frame)
        with patch.object(AdaptiveSilhouetteTracker,'step',slow_step):
            self.w.process_autotrack('Process')
            self.assertTrue(entered.wait(5))
            self.assertTrue(self.w.tracking_setup.cancel.isEnabled())
            self.w.tracking_setup.cancel.click();release.set();self.w._tracking_thread.join(10)
        self.app.processEvents();self.w._tracking_finished_ui()
        self.assertTrue(track['tumbling_cancelled'])
        self.assertGreaterEqual(len(track['frames']),1);self.assertLess(len(track['frames']),18)
        self.assertTrue(self.w.analysis_tools.isEnabled());self.assertTrue(self.w.tracking_setup.run.isEnabled())

    def test_mask_preview_keeps_background_holes_unfilled(self):
        track=self.create()
        record=track['tumbling_observations'][0]
        record['contours']=[[[40,65],[85,65],[85,110],[40,110]],[[55,80],[70,80],[70,95],[55,95]]]
        self.vm.redraw_cb();self.app.processEvents()
        item=next(item for item in self.w.graph.scene().items() if item.data(0)=='tumbling_mask_0')
        self.assertTrue(item.path().contains(QPointF(45,70)))
        self.assertFalse(item.path().contains(QPointF(60,85)))

    def test_help_has_usage_and_technical_tabs(self):
        dialog=simplemeas_ui.TrackingHelpDialog(self.w);tabs=dialog.findChild(QTabWidget)
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())],['How to use','How it works'])
        text=dialog.findChild(QTextBrowser,'tracking_technical_text').toPlainText()
        self.assertIn('not a physical center of mass',text);self.assertIn('GrabCut',text)
        dialog.deleteLater()

if __name__=='__main__':unittest.main()
