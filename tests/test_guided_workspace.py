from PySide6.QtCore import QCoreApplication, QEvent
"""Real Qt input, real Cine decoding, and styled guided-workspace regression tests."""
import os,sys,tempfile,unittest,struct,time
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
import numpy as np
import cv2
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
MODULE=Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'
sys.path.insert(0,str(MODULE))
from PySide6.QtCore import Qt,QPointF,QRectF,QPoint,QEventLoop,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import simplemeas_ui,simplemeas_vm,measmodel,reportmodel
from cine_metadata import CineHandler
from test_cine_metadata import write_cine
from test_multi_cine_workspace import FakeConfig
from tracking_setup_ui import CLASSIC,EDGE
from tumbling_tracker import TUMBLING_TRACKING_METHOD
from search_region_ui import SearchRegionItem
from tracking_geometry import corners,rotate,pose_at,search_pose,RegionSnapshot


def motion_cine(path,frames=30):
    write_cine(path,frames=frames,width=320,height=240)
    data=bytearray(path.read_bytes());start=len(data)-frames*(320*240+8)
    for frame in range(frames):
        image=np.full((240,320),25,np.uint8);y=120+round(10*np.sin(frame/8))
        cv2.circle(image,(150,y),12,220,-1);cv2.rectangle(image,(144,y-5),(150,y),110,-1)
        begin=start+frame*(320*240+8)+8;data[begin:begin+320*240]=image[::-1].tobytes()
    path.write_bytes(data)

class GuidedWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);cls.old_style=cls.app.styleSheet()
        cls.app.setStyleSheet((MODULE/'styles.css').read_text())
    @classmethod
    def tearDownClass(cls):cls.app.setStyleSheet(cls.old_style)
    def setUp(self, load=True):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'motion.cine';motion_cine(self.source)
        self.errors=[];self.hook=patch('sys.excepthook',lambda *e:self.errors.append(e));self.hook.start()
        self.w=simplemeas_ui.MainWindow();self.w.vm=simplemeas_vm.SimpleMeasVM(self.w,measmodel.MeasModel(),reportmodel.ReportHandler(),FakeConfig(),CineHandler(),{})
        self.w.connect_events();self.w.initialize_theme();self.w.resize(1280,820);self.w.show()
        if load:
            self.assertTrue(self.w.vm.load_cine_workspace_cb([str(self.source)]))
        QTest.qWait(30)
    def tearDown(self):
        self.w._stop_playback()
        if self.w._tracking_busy():
            self.w.process_autotrack('Cancel');self.wait_worker()
        self.w.overlay_panel.shutdown();self.w.comparator_panel.shutdown();self.w.vm.close_workspace();self.w.hide();self.w.deleteLater(); QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents()
        self.hook.stop();self.temp.cleanup()
        if self.errors:
            import traceback
            self.fail(''.join(''.join(traceback.format_exception(*error)) for error in self.errors))
    def wait_worker(self):
        # exec() releases the GIL like the real application. Repeated qWait()
        # calls starve a Python worker and exaggerate matching time by100×.
        loop=QEventLoop();poll=QTimer();poll.setInterval(20)
        poll.timeout.connect(lambda:loop.quit() if not self.w._tracking_busy() else None)
        timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
        poll.start();timeout.start(30000);loop.exec();poll.stop();timeout.stop()
        self.assertFalse(self.w._tracking_busy());QTest.qWait(80)
    def begin(self,method=EDGE,region=False):
        w=self.w
        if w.current_tool is not w.track_tool:w.track_tool.click()
        with patch.object(w,'choose_tracking_method',return_value=method) as chooser:w.add_object_button.click()
        chooser.assert_called_once();self.assertTrue(w.tracking_guide.isVisible());self.assertTrue(w.main_tab.isHidden())
        if region:
            w.tracking_guide.target.setCurrentIndex(1);self.drag(QPointF(132,102),QPointF(168,138))
        else:
            QTest.mouseClick(w.graph.viewport(),Qt.LeftButton,pos=w.graph.mapFromScene(QPointF(150,120)));QTest.qWait(20)
        self.assertEqual(w._guide['step'],1);return w._guide_track()
    def drag(self,start,end):
        g=self.w.graph
        QTest.mousePress(g.viewport(),Qt.LeftButton,pos=g.mapFromScene(start));QTest.mouseMove(g.viewport(),g.mapFromScene(end),20)
        QTest.mouseRelease(g.viewport(),Qt.LeftButton,pos=g.mapFromScene(end));QTest.qWait(30)
    def test_video_first_layout_and_side_only_inspector(self):
        w=self.w
        self.assertGreater(w.graph.height(),550);self.assertTrue(w.viewer_controls.isHidden());self.assertTrue(w.viewer_side_tools.isVisible())
        self.assertTrue(w.main_tab.tabBar().isHidden());self.assertTrue(w.main_tab.isHidden())
        w.timeline_toggle.click();QTest.qWait(20);self.assertTrue(w.section_timeline.isVisible());self.assertGreater(w.graph.height(),330)
        w.timeline_toggle.click();w.track_tool.click();w.details_buttons[2].click();QTest.qWait(30)
        for _ in range(3):w._sync_tracking_setup();w.vm.new_track_data.emit(w.vm.track_data,w.vm.active_object)
        self.assertTrue(w.tracking_setup.isHidden());self.assertTrue(w.track_pages.widget(1).isVisible());self.assertTrue(w.track_pages.tabBar().isHidden())
        w.details_close.click();self.assertTrue(w.main_tab.isHidden())
        self.assertTrue(w.status_toolbar.isHidden())
        w._set_graphs_open(True);QTest.qWait(10)
        original=w.video_plot_splitter.sizes();w.video_plot_splitter.setSizes([original[0]-40,original[1]+40]);self.assertGreater(w.status_toolbar.height(),original[1])
        w.transport_toggle.click();self.assertTrue(w.playback_card.isHidden());w.transport_toggle.click();self.assertTrue(w.playback_card.isVisible())

    def test_review_draft_survives_navigation_and_cancel(self):
        self.begin();w=self.w;p=w.tracking_guide
        w._guide_step(1);w._guide_step(1)
        p.name.setText('Paper bottom');p.first.setValue(-10);p.last.setValue(14);p.confidence.setValue(.55)
        p.reference.setCurrentIndex(p.reference.findData('pixel'));p.ref_x.setValue(200);p.ref_y.setValue(210)
        w._guide_step(-1);w._guide_step(1)
        self.assertEqual((p.name.text(),p.last.value(),p.confidence.value()),('Paper bottom',14,.55))
        self.assertEqual((p.reference.currentData(),p.ref_x.value(),p.ref_y.value()),('pixel',200,210))
        p.cancel.click();self.assertFalse(w.vm.track_data)

    def test_handle_click_is_noop_and_geometry_edit_returns_to_setup(self):
        track=self.begin();w=self.w;size=track['tpl_rng'];point=track['points'].copy()
        button=w.tracking_guide.pick_point
        self.assertTrue(w.tracking_guide.rect().contains(button.mapTo(w.tracking_guide,button.rect().bottomRight())))
        self.assertFalse(w.track_type_man.isEnabled());self.assertTrue(w.track_type_man.isHidden())
        box=next(i for i in w.graph.scene().items() if isinstance(i,simplemeas_ui.HybridRegionItem))
        pos=w.graph.mapFromScene(box.scale_handle.mapToScene(QPointF()))
        QTest.mouseClick(w.graph.viewport(),Qt.LeftButton,pos=pos);QTest.qWait(30)
        self.assertEqual(track['tpl_rng'],size);np.testing.assert_array_equal(track['points'],point)
        w.frame_slider.setValue(8);w.tracking_guide.roi_w.setValue(41);QTest.qWait(30)
        self.assertEqual(w.vm.active_frame,track['anchor_frame']);self.assertEqual(track['tpl_rng'][0],41)
        self.assertTrue(any(isinstance(i,simplemeas_ui.HybridRegionItem) for i in w.graph.scene().items()))

    def test_default_edge_tracks_user_range_without_tuning(self):
        track=self.begin();w=self.w;w._guide_step(1);w._guide_step(1)
        w.tracking_guide.last.setValue(9)  # zero-based frames0..19
        w.tracking_guide.next.click();self.wait_worker()
        self.assertGreaterEqual(len(track['frames']),19)
        self.assertEqual(int(max(track['frames'])),19)
        self.assertGreater(len(track.get('confidence_components',{})),10)
    def test_independent_search_mouse_resize_rotate_and_roi_center(self):
        track=self.begin(region=True);w=self.w;self.assertEqual(track['tracking_target'],'region_center')
        original=track['points'].copy();roi=pose_at(track,0)[0].copy();size=track['tpl_rng']
        w._guide_step(1);QTest.qWait(20)
        box=next(i for i in w.graph.scene().items() if isinstance(i,SearchRegionItem))
        start=box.mapToScene(QPointF(box.rect().right(),0));self.drag(start,start+QPointF(16,7))
        np.testing.assert_array_equal(track['points'],original);np.testing.assert_allclose(pose_at(track,0)[0],roi)
        self.assertEqual(track['tpl_rng'],size);self.assertGreater(np.linalg.norm(track['search_offset']),10)
        box=next(i for i in w.graph.scene().items() if isinstance(i,SearchRegionItem))
        handle=box.rotate_handle;start=handle.mapToScene(QPointF(0,0));center=box.pos();self.drag(start,center+QPointF(60,0))
        self.assertGreater(abs(track['search_angle']),45)
        w._commit_search_region(0,(200,180),(11,11),35)
        c,a,_=search_pose(track,roi,0);local=rotate(corners(roi,size,0)-c,-a)
        self.assertTrue(np.all(np.max(abs(local),axis=0)<=(np.array(track['search_area'])-1)/2))
        np.testing.assert_array_equal(track['points'],original)
    def test_selected_point_and_fixed_reference_are_independent_and_saved(self):
        track=self.begin();w=self.w
        center=pose_at(track,0)[0].copy();w._guide_pick('point')
        QTest.mouseClick(w.graph.viewport(),Qt.LeftButton,pos=w.graph.mapFromScene(QPointF(180,100)));QTest.qWait(30)
        np.testing.assert_allclose(pose_at(track,0)[0],center,atol=.01)
        self.assertGreater(np.linalg.norm(track['template_offset']),20)
        w._guide_step(1);w._guide_step(1);w.tracking_guide.reference.setCurrentIndex(2);w._guide_pick('reference')
        QTest.mouseClick(w.graph.viewport(),Qt.LeftButton,pos=w.graph.mapFromScene(QPointF(200,200)));QTest.qWait(30)
        with patch.object(w,'process_autotrack') as process:w.tracking_guide.next.click()
        process.assert_called_once_with('Process');self.assertIsNone(w._guide)
        np.testing.assert_allclose(track['reference_pixel'],(200,200),atol=.8)
        w.vm.track_fig_calculations(obj=track);path=self.root/'job.pcajob';w.save_job_path(path);w.open_job_path(path)
        self.assertEqual(w.vm.track_data[0]['motion_filter']['mode'],'local_polynomial')
        np.testing.assert_allclose(w.vm.track_data[0]['reference_pixel'],track['reference_pixel'])
    def test_cancel_new_and_cancel_edit_restore_data(self):
        self.begin();self.w.tracking_guide.cancel.click();self.assertFalse(self.w.vm.track_data)
        track=self.begin();w=self.w
        w._guide_step(1);w._guide_step(1)
        with patch.object(w,'process_autotrack'):w.tracking_guide.next.click()
        original=deepcopy(track);w.edit_object_button.click();w.tracking_guide.roi_w.setValue(61);w.tracking_guide.cancel.click()
        restored=w.vm.track_data[0];self.assertEqual(restored['tpl_rng'],original['tpl_rng']);np.testing.assert_array_equal(restored['points'],original['points'])
        self.assertTrue(w.tracking_setup.isHidden());self.assertTrue(w.main_tab.isHidden())
    def test_background_pans_but_geometry_still_edits(self):
        self.begin();w=self.w;g=w.graph;g.zoom_at(g.viewport().rect().center(),4)
        old=(g.horizontalScrollBar().value(),g.verticalScrollBar().value())
        self.drag(QPointF(95,80),QPointF(105,90))
        new=(g.horizontalScrollBar().value(),g.verticalScrollBar().value());self.assertNotEqual(old,new)
        original=w._guide_track()['tpl_rng'];box=next(i for i in g.scene().items() if isinstance(i,simplemeas_ui.HybridRegionItem))
        start=box.scale_handle.mapToScene(QPointF(0,0));self.drag(start,start+QPointF(8,8))
        self.assertNotEqual(w._guide_track()['tpl_rng'],original)
    def test_complete_tracks_actual_cine_and_never_shows_setup_over_results(self):
        track=self.begin();w=self.w;track.update(rotation_allowed=False,smart_frames=False,acceptable_score=.2,anchor_refinement_enabled=False)
        w._guide_step(1);w.tracking_guide.motion.setCurrentIndex(1);w._guide_step(1);w.tracking_guide.confidence.setValue(.2)
        w.tracking_guide.next.click();self.wait_worker()
        self.assertGreaterEqual(len(track['points']),25);np.testing.assert_allclose(track['points'][:,0],track['anchor_point'][0],atol=.01)
        self.assertLess(np.max(np.abs(track['points'][:,1]-(120+np.round(10*np.sin(track['frames']/8))))),1)
        w.details_buttons[2].click();QTest.qWait(20);self.assertTrue(w.tracking_setup.isHidden());self.assertTrue(w.track_pages.widget(1).isVisible())
        w._tracking_finished_ui();self.assertTrue(w.track_pages.widget(1).isVisible())
        self.assertTrue(w.main_tab.tabBar().isHidden())
    def test_classic_and_tumbling_both_enter_guided_setup(self):
        for method in (CLASSIC,TUMBLING_TRACKING_METHOD):
            track=self.begin(method);self.assertEqual(track['tracking_method'],method)
            self.assertTrue(self.w.playback_card.isVisible());self.w.tracking_guide.cancel.click();self.assertFalse(self.w.vm.track_data)
    def test_switching_modes_cancels_draft_and_save_requires_completed_setup(self):
        self.begin()
        with self.assertRaisesRegex(ValueError,'setup'):self.w.capture_job()
        self.w.viewer_tool.click();self.assertIsNone(self.w._guide);self.assertFalse(self.w.vm.track_data)
        self.assertFalse(self.w.tracking_guide.isVisible())

if __name__=='__main__':unittest.main()
