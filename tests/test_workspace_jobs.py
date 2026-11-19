import os
import sys
import tempfile
import unittest
import time
import shutil
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from test_cine_metadata import write_cine
from test_multi_cine_workspace import FakeConfig
from workspace_job import save_job,read_job,resolve_media
from video_overlay import default_overlay
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSignalBlocker
import simplemeas_ui,simplemeas_vm,measmodel,reportmodel
from cine_metadata import CineHandler
from viewer_timeline import split_at


class WorkspaceJobArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'a.cine';write_cine(self.source)
        self.payload=dict(contexts=[],active=-1,overlay=dict(default_overlay(),paths=[str(self.source),'']),
            data={4:np.array([1,2],dtype=np.int32),'nested':(float('nan'),np.array(['N/A',.75],dtype=object))})
    def test_typed_arrays_integer_keys_and_nonfinite_round_trip(self):
        path=self.root/'a.pcajob';save_job(path,self.payload);p=read_job(path)
        np.testing.assert_array_equal(p['data'][4],self.payload['data'][4]);self.assertEqual(p['data'][4].dtype,np.int32)
        self.assertIsInstance(p['data']['nested'],tuple);self.assertTrue(np.isnan(p['data']['nested'][0]))
        self.assertEqual(list(p['data']['nested'][1]),['N/A',.75])
    def test_moved_job_resolves_relative_media(self):
        save_job(self.root/'a.pcajob',self.payload)
        moved=self.root/'moved';moved.mkdir();shutil.move(self.source,moved/self.source.name)
        shutil.move(self.root/'a.pcajob',moved/'a.pcajob')
        p=resolve_media(read_job(moved/'a.pcajob'),moved/'a.pcajob')
        self.assertEqual(p['overlay']['paths'][0],str((moved/'a.cine').resolve()))
    def test_relink_validates_source_identity(self):
        path=self.root/'a.pcajob';save_job(path,self.payload)
        renamed=self.root/'renamed.cine';self.source.rename(renamed)
        with self.assertRaises(FileNotFoundError):resolve_media(read_job(path),path)
        p=resolve_media(read_job(path),path,lambda missing:renamed)
        self.assertEqual(p['overlay']['paths'][0],str(renamed.resolve()))
        renamed.write_bytes(b'wrong footage')
        with self.assertRaises(ValueError):resolve_media(read_job(path),path,lambda missing:renamed)
    def test_failed_save_preserves_previous_archive(self):
        path=self.root/'a.pcajob';save_job(path,self.payload);before=path.read_bytes()
        payload=deepcopy(self.payload);payload['bad']=object()
        with self.assertRaises(ValueError):save_job(path,payload)
        self.assertEqual(path.read_bytes(),before)


class WorkspaceJobUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.paths=[self.root/'a.cine',self.root/'b.cine']
        for p in self.paths:write_cine(p,frames=20,width=100,height=80)
        self.errors=[];self.hook=patch.object(sys,'excepthook',lambda *e:self.errors.append(e));self.hook.start()
        self.windows=[]
        self.w=self.make_window()
        self.w.vm.load_cine_workspace_cb([str(p) for p in self.paths])
    def make_window(self):
        w=simplemeas_ui.MainWindow();w.vm=simplemeas_vm.SimpleMeasVM(w,measmodel.MeasModel(),reportmodel.ReportHandler(),FakeConfig(),CineHandler(),{})
        w.connect_events();w.show();self.windows.append(w);self.app.processEvents();return w
    def settle(self,window=None):
        w=window or self.w;deadline=time.monotonic()+15
        while w.overlay_panel.busy() or w.overlay_panel.pending or w.overlay_panel.debounce.isActive() or w.comparator_panel.busy() or w.comparator_panel.debounce.isActive():
            self.app.processEvents();time.sleep(.01)
            if time.monotonic()>deadline:raise AssertionError('Workers did not finish')
        self.app.processEvents()
    def tearDown(self):
        for w in self.windows:
            w._stop_playback();w.overlay_panel.shutdown();w.comparator_panel.shutdown()
            deadline=time.monotonic()+15
            while w.overlay_panel.busy() or w.comparator_panel.busy():
                self.app.processEvents();time.sleep(.01)
                if time.monotonic()>deadline:break
            w.overlay_panel.shutdown();w.comparator_panel.shutdown();w.vm.close_workspace();w.hide();w.deleteLater()
        self.app.processEvents();self.hook.stop();self.temp.cleanup()
        self.assertEqual(self.errors,[])
    def track(self,w):
        w.vm.track_type='Manual';w.vm._update_track_type();w.current_tool=w.track_tool;w.track_tool.setChecked(True)
        w.vm.active_tool.track_select=True;w.vm.active_tool.collect_points((10,10),0)
        w.vm.active_tool.collect_points((20,20),3)
        w.vm.active_frame=3;w.vm.image_scale=.125;w.vm.length_units='mm'
        w.vm.track_data[0]['name']='Impact marker';w.vm.track_data[0]['notes'][3]='First contact'
        w.vm.track_fig_calculations();w.main_tab.setCurrentIndex(2)
    def test_whole_job_restores_tracking_edit_calibration_and_graph(self):
        w=self.w;self.track(w);w.fig_dropdown.setCurrentText('X-Displacement')
        w.vm.viewer_edit=split_at(w.vm.viewer_edit,5);w.vm.viewer_grade['gamma']=1.4
        expected=deepcopy(w.vm.track_data)
        w.vm.sync_active_workspace_context();w.activate_cine_pane(1);self.track(w)
        w.vm.image_scale=.75;w.activate_cine_pane(0);w.hide_cine_pane(1)
        w.overlay_panel.add_sources([str(p) for p in self.paths]);w.overlay_panel.offsets[1].setValue(2);self.settle()
        job=self.root/'demo.pcajob';w.save_job_path(job)
        restored=self.make_window();restored.open_job_path(job);self.settle(restored)
        self.assertEqual(len(restored.vm.workspace_contexts),2)
        np.testing.assert_array_equal(restored.vm.track_data[0]['points'],expected[0]['points'])
        np.testing.assert_array_equal(restored.vm.track_data[0]['frames'],expected[0]['frames'])
        self.assertEqual(restored.vm.track_data[0]['name'],'Impact marker')
        self.assertEqual(restored.vm.track_data[0]['notes'][3],'First contact')
        self.assertEqual(restored.vm.image_scale,.125);self.assertEqual(restored.vm.workspace_contexts[1]['_image_scale'],.75)
        self.assertEqual(restored.vm.active_frame,3);self.assertEqual(restored.hidden_cine_indices,{1})
        self.assertEqual(restored.fig_dropdown.currentText(),'X-Displacement');self.assertIs(restored.current_tool,restored.track_tool)
        self.assertEqual(len(restored.vm.viewer_edit['sections']),2);self.assertEqual(restored.vm.viewer_grade['gamma'],1.4)
        self.assertEqual(restored.overlay_panel.offsets[1].value(),2)
        # Continue manually tracking from stored points, rather than starting over.
        restored.vm.active_tool.collect_points((25,23),5)
        self.assertIn(5,restored.vm.track_data[0]['frames'])
    def test_missing_media_keeps_current_job_untouched(self):
        self.track(self.w);job=self.root/'demo.pcajob';self.w.save_job_path(job)
        points=self.w.vm.track_data[0]['points'].copy();handler=self.w.vm.cine_handler
        self.paths[1].rename(self.root/'moved.cine')
        with self.assertRaises(FileNotFoundError):self.w.open_job_path(job)
        self.assertIs(self.w.vm.cine_handler,handler);np.testing.assert_array_equal(self.w.vm.track_data[0]['points'],points)
    def test_bos_tool_job_round_trip_and_media_relink(self):
        window = self.w
        window.mode_actions[window.bos_tool].trigger()
        panel = window.bos_panel
        settings = dict(panel.settings(), path=str(self.paths[0]), reference=1, position=4,
                        roi=[5, 5, 80, 64], component='vertical', playback_fps=1000.)
        panel.apply_settings(settings)
        saved = self.root/'schlieren.pcajob'
        window.save_job_path(saved)
        window.viewer_tool.click()
        self.assertIs(window.workflow_stack.currentWidget(), window.tab_splitter)
        moved = self.root/'dots-renamed.cine'
        self.paths[0].rename(moved)
        other = self.make_window()
        other.open_job_path(saved, relink=lambda old: str(moved))
        self.assertIs(other.workflow_stack.currentWidget(), other.bos_panel)
        deadline = time.monotonic()+10
        while other.bos_panel.last_result is None or other.bos_panel.busy() or other.bos_panel.pending:
            self.app.processEvents()
            time.sleep(.005)
            self.assertLess(time.monotonic(), deadline, other.bos_panel.status.text())
        self.assertEqual(other.bos_panel.settings(), dict(settings, path=str(moved.resolve())))
        self.assertEqual(other.bos_panel.last_result['index'], 4)
        self.assertIsNotNone(other.bos_panel.last_result['field'])
        other.mode_menu.aboutToShow.emit()
        self.assertTrue(other.mode_actions[other.bos_tool].isChecked())
        other.viewer_tool.click()
        self.assertIs(other.workflow_stack.currentWidget(), other.tab_splitter)

    def test_overlay_navigation_keeps_tracking_and_shortcuts(self):
        self.track(self.w);expected=deepcopy(self.w.vm.track_data)
        self.w.overlay_tool.click();self.settle();self.assertIs(self.w.workflow_stack.currentWidget(),self.w.overlay_panel)
        self.w.comparator_tool.click();self.settle();self.w.overlay_tool.click();self.settle();self.w.track_tool.click()
        self.assertIs(self.w.workflow_stack.currentWidget(),self.w.tab_splitter)
        self.assertIs(self.w.current_tool,self.w.track_tool)
        np.testing.assert_array_equal(self.w.vm.track_data[0]['points'],expected[0]['points'])
        self.assertTrue(all(s.isEnabled() for s,enabled in getattr(self.w,'_comparator_saved_shortcuts',[]) if enabled))

    def test_overlay_only_job_replaces_analysis_workspace(self):
        # Save from an actual overlay-only workspace so pane identities match
        # its empty analysis workspace, including newly added view settings.
        source=self.make_window();source.overlay_tool.click()
        source.overlay_panel.add_sources([str(p) for p in self.paths]);self.settle(source)
        source.overlay_panel.fps.setValue(1000.)
        path=self.root/'overlay-only.pcajob';source.save_job_path(path)
        self.assertEqual(read_job(path)['view']['pane_order'],[])
        self.w.open_job_path(path);self.settle()
        self.assertEqual(self.w.vm.workspace_contexts,[])
        self.assertEqual(self.w.cine_path,'')
        self.assertIs(self.w.workflow_stack.currentWidget(),self.w.overlay_panel)
        self.assertIsNotNone(self.w.overlay_panel.last_result)
        self.assertEqual(self.w.overlay_panel.fps.value(),1000.)

    def test_invalid_job_preserves_existing_tracking(self):
        self.track(self.w);payload=self.w.capture_job();payload['overlay']['opacity']=[float('nan'),50]
        path=self.root/'invalid.pcajob';save_job(path,payload)
        original=self.w.vm.cine_handler;points=self.w.vm.track_data[0]['points'].copy()
        with self.assertRaises(ValueError):self.w.open_job_path(path)
        self.assertIs(self.w.vm.cine_handler,original)
        np.testing.assert_array_equal(self.w.vm.track_data[0]['points'],points)

    def test_hybrid_candidates_and_measurements_survive(self):
        from simplemeas_tools import HYBRID_TRACKING_METHOD,AutoTrackTool
        self.track(self.w);vm=self.w.vm;t=vm.track_data[0]
        t['tracking_method']=HYBRID_TRACKING_METHOD
        t['hybrid_candidates']={3:dict(point=(20.,20.),score=.812,angle=24.75)}
        t['confidence_components']={3:dict(edge=.9,intensity=.7)}
        vm.active_tool=AutoTrackTool(vm);vm.track_type='Auto';vm._active_track_tool=vm.active_tool
        vm.meas_data={'_curr_i':1,'Two Point Tool':{'ID':[0],'Distance':[12.5],'Units':['mm']}}
        path=self.root/'hybrid.pcajob';self.w.save_job_path(path)
        restored=self.make_window()
        with patch.object(simplemeas_vm.SimpleMeasVM,'autotrack_cb',side_effect=AssertionError('Should not retrack')):
            restored.open_job_path(path);self.settle(restored)
        self.assertEqual(restored.vm.track_data[0]['hybrid_candidates'],t['hybrid_candidates'])
        self.assertEqual(restored.vm.track_data[0]['confidence_components'],t['confidence_components'])
        self.assertEqual(restored.vm.meas_data,vm.meas_data)
        self.assertIsInstance(restored.vm.active_tool,AutoTrackTool)

    def test_tumbling_job_restores_masks_and_method_without_retracking(self):
        from tumbling_tracker import TUMBLING_TRACKING_METHOD,TrackerSettings
        from simplemeas_tools import AutoTrackTool
        self.track(self.w);vm=self.w.vm;t=vm.track_data[0]
        t.update(tracking_method=TUMBLING_TRACKING_METHOD,tumbling=TrackerSettings().to_dict(),
                 tumbling_seed=(10.,10.),tumbling_dirty=False,
                 measurement_kind='Projected silhouette centroid',
                 tumbling_observations={3:dict(frame=3,accepted=True,centroid=(20.,20.),confidence=.85,
                 area=80,reason='Accepted',contours=[[[17,17],[23,17],[23,23],[17,23]]],components={})})
        t['angles'][:]=np.nan;t['t_angles'][:]=np.nan
        vm.active_tool=AutoTrackTool(vm);vm.track_type='Auto';vm._active_track_tool=vm.active_tool
        path=self.root/'tumbling.pcajob';self.w.save_job_path(path)
        restored=self.make_window()
        with patch.object(simplemeas_vm.SimpleMeasVM,'autotrack_cb',side_effect=AssertionError('Should not retrack')):
            restored.open_job_path(path);self.settle(restored)
        actual=restored.vm.track_data[0]
        self.assertEqual(actual['tracking_method'],TUMBLING_TRACKING_METHOD)
        self.assertEqual(actual['tumbling_observations'],t['tumbling_observations'])
        self.assertTrue(np.isnan(actual['angles']).all())
        self.assertTrue(restored.tracking_setup.methods[TUMBLING_TRACKING_METHOD].isChecked())
        self.assertTrue(restored.tracking_setup.run.isEnabled())

    def test_comparator_source_settings_and_position_restore(self):
        self.w.comparator_tool.click();self.settle();c=self.w.comparator_panel
        s=c.settings();s['source'].update(binning=1,pitch_um=28);s['target_fps']=1000;c.apply_settings(s);self.settle()
        self.assertIsNotNone(c.comparison,c.notices.text())
        c.slider.setValue(4);self.settle()
        path=self.root/'comparison.pcajob';self.w.save_job_path(path)
        restored=self.make_window();restored.open_job_path(path);self.settle(restored)
        # Comparator is hidden from current navigation; retain old settings for
        # backwards compatibility without reopening its removed workspace.
        self.assertIs(restored.workflow_stack.currentWidget(),restored.tab_splitter)
        self.assertEqual(restored.comparator_panel.settings(),c.settings())
        self.assertEqual(restored.comparator_panel.slider.value(),4)
