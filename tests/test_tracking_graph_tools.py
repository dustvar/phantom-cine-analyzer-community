"""Graph units, actual Qt point inspection, pan/zoom and range selection."""
import os, sys, unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from PySide6.QtCore import Qt, QPoint, QEvent, QCoreApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QHBoxLayout,QComboBox
from matplotlib.backend_bases import MouseEvent
from tracking_graph import TrackingGraph
from tracking_graph_data import graph_series, normalize_length_unit


class GraphDataTests(unittest.TestCase):
    @staticmethod
    def track():
        return dict(name='Object',enabled=True,color='red',frames=np.arange(5),
                    frame_ts=np.arange(5,dtype=float),points=np.zeros((5,2)),notes={},
                    **{'Displacement':np.arange(5,dtype=float), 'Speed':np.full(4,3.), 'Acceleration':np.full(3,4.)})
    def test_inches_convert_to_mm_and_time_denominators_are_not_squared_with_length(self):
        t=self.track();cal=SimpleNamespace(point_transform=lambda a:np.asarray(a)*2.)
        for metric,expected in [('Displacement',np.arange(5)*50.8),('Speed',np.full(4,3*50.8*1000)),('Acceleration',np.full(3,4*50.8*1000000))]:
            series,omitted=graph_series({0:t},metric,1,target_length='mm',target_time_scale=1,
                default_length='in',default_cal=cal,default_time_scale=1000)
            self.assertFalse(omitted);np.testing.assert_allclose(series[0]['y'],expected)
        self.assertEqual(normalize_length_unit('inches'),'in')
        self.assertEqual(normalize_length_unit('µm'),'μm')
    def test_mixed_cine_calibration_converts_per_source_and_omits_unknown_pixels(self):
        t=self.track();cal=SimpleNamespace(point_transform=lambda a:np.asarray(a))
        contexts=[{'_length_units':'in','_time_scale':1,'cal':cal},
                  {'_length_units':'mm','_time_scale':1000,'cal':cal},
                  {'_length_units':'pix','_time_scale':1,'cal':cal}]
        second=deepcopy(t);second['frame_ts']*=1000
        series,omitted=graph_series({(0,0):t,(1,0):second,(2,0):t},'Displacement',1,
                                   target_length='mm',contexts=contexts)
        self.assertEqual(len(series),2);self.assertEqual(len(omitted),1)
        np.testing.assert_allclose(series[0]['y'],np.arange(5)*25.4)
        np.testing.assert_allclose(series[1]['y'],np.arange(5))
        np.testing.assert_allclose(series[0]['x'],series[1]['x'])
    def test_derivative_points_map_to_nearest_original_capture(self):
        t=self.track();t['frame_ts']=np.array([0.,.01,.03,.04,.09]);t['frames']=np.array([10,11,13,14,19])
        series,_=graph_series({0:t},'Speed',1)
        np.testing.assert_array_equal(series[0]['frames'],[10,11,13,14])
        before=t['Speed'].copy();series[0]['y'][0]=100
        np.testing.assert_array_equal(t['Speed'],before)


class InteractiveGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.host=QWidget();self.layout=QVBoxLayout(self.host)
        self.host.fig_dropdown=QComboBox();self.host.fig_dropdown.addItems(['Displacement','Speed','Acceleration','Angle'])
        t=GraphDataTests.track();t['frame_ts']=np.arange(5)*.1;t['Angle']=np.arange(5)*10
        self.t=t
        self.host.vm=SimpleNamespace(track_data={0:t},active_object=0,active_frame=2,active_cine_index=0,n_diff=1,
            length_units='pix',time_units='s',time_scale=1,workspace_contexts=[],cal=SimpleNamespace(point_transform=lambda a:np.asarray(a)*2))
        self.graph=TrackingGraph(self.host);self.layout.addWidget(self.graph)
        row=QHBoxLayout();row.addWidget(self.host.fig_dropdown);self.graph.install_units_control(row);self.layout.addLayout(row)
        self.host.resize(900,380);self.host.show();self.app.processEvents();self.graph.redraw({0:t});self.app.processEvents()
        self.deletes=[]
        self.host.delete_graph_tracking_samples=lambda *args,**kw:self.deletes.append((args,kw)) or True
    def tearDown(self):
        self.host.close();self.host.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents()
    def event_at(self,index,kind='motion_notify_event',series=None):
        s=series or self.graph._series[0];x,y=self.graph.ax.transData.transform((s['x'][index],s['y'][index]))
        return MouseEvent(kind,self.graph,x,y,button=1 if 'button' in kind else None)
    def test_zoom_pan_and_fit_scale_both_axes_and_keep_limits_during_playhead(self):
        g=self.graph;start=(g.ax.get_xlim(),g.ax.get_ylim())
        g.zoom_in_button.click();self.app.processEvents()
        zoom=(g.ax.get_xlim(),g.ax.get_ylim())
        for old,new in zip(start,zoom):self.assertLess(np.ptp(new),np.ptp(old))
        for frame in range(5):
            self.host.vm.active_frame=frame;g.draw_frame_pos()
            self.assertEqual(g.ax.get_xlim(),zoom[0]);self.assertEqual(g.ax.get_ylim(),zoom[1])
            self.assertAlmostEqual(g.frame_pos_line.get_xdata()[0],frame*.1)
        e=self.event_at(2,'button_press_event');g._on_press(e)
        moved=MouseEvent('motion_notify_event',g,e.x+35,e.y+20,button=1);g._on_motion(moved)
        g._on_release(MouseEvent('button_release_event',g,e.x+35,e.y+20,button=1));self.app.processEvents()
        self.assertNotEqual(g.ax.get_xlim(),zoom[0]);self.assertNotEqual(g.ax.get_ylim(),zoom[1])
        limits=(g.ax.get_xlim(),g.ax.get_ylim());g.redraw({0:self.t});self.assertEqual((g.ax.get_xlim(),g.ax.get_ylim()),limits)
        g.fit_button.click();self.assertAlmostEqual(np.ptp(g.ax.get_xlim()),np.ptp(start[0]))
        self.assertTrue(any(line.get_visible() for line in g.ax.get_ygridlines()))
    def test_hover_and_arrow_keys_select_only_same_line_then_delete_exact_source_frame(self):
        g=self.graph;g._on_motion(self.event_at(2));self.app.processEvents()
        self.assertEqual(g._selected,(0,0,2));self.assertEqual(g.selection_marker.get_markersize(),8)
        self.assertIn('X 0.2 s',g.value_label.text());self.assertIn('Y 2 pix',g.value_label.text())
        QTest.keyClick(g,Qt.Key_Right);self.assertEqual(g._selected,(0,0,3));self.assertEqual(self.host.vm.active_frame,3)
        QTest.keyClick(g,Qt.Key_Left);self.assertEqual(g._selected,(0,0,2))
        QTest.keyClick(g,Qt.Key_Delete)
        self.assertEqual(self.deletes[0],((0,0,[2]),{}))
    def test_range_rejects_other_line_confirms_inclusive_source_frames_and_cancel_is_noop(self):
        g=self.graph;t2=deepcopy(self.t);t2['name']='Other';t2['Displacement']+=.5;self.host.vm.track_data[1]=t2
        g.redraw(self.host.vm.track_data);g.begin_delete_range();g._pick_range((g._series[0],1))
        g._pick_range((g._series[1],4));self.assertIsNone(g._range['end']);self.assertFalse(g.confirm_range_button.isEnabled())
        g._pick_range((g._series[0],3));self.assertIn('3 points',g.value_label.text())
        g.confirm_range_button.click();self.assertEqual(self.deletes[0],((0,0,[1,2,3]),{'frame_range':(1,3)}))
        g.begin_delete_range();g._pick_range((g._series[0],0));g.cancel_range_button.click();self.assertEqual(len(self.deletes),1)
    def test_overlapping_curves_prefer_selected_object_and_range_line_only_when_coincident(self):
        g=self.graph;t2=deepcopy(self.t);t2['name']='Second';self.host.vm.track_data[1]=t2
        self.host.vm.active_object=1;g.redraw(self.host.vm.track_data)
        hit=g._nearest(self.event_at(2,series=g._series[0]));self.assertEqual(hit[0]['object_id'],1)
        g._select(hit);g.setFocus();QTest.keyClick(g,Qt.Key_Delete)
        self.assertEqual(self.deletes[-1],((0,1,[2]),{}))
        # Subpixel separation remains visually coincident, regardless of order.
        pixel=g.ax.transData.transform((g._series[0]['x'][2],g._series[0]['y'][2]))
        shifted=g.ax.transData.inverted().transform(pixel+[0,.3*g.device_pixel_ratio])
        g._series[1]['y'][2]=shifted[1]
        hit=g._nearest(self.event_at(2,series=g._series[0]));self.assertEqual(hit[0]['object_id'],1)
        # During a range operation its first line wins a coincidence even if
        # the object dropdown still identifies a different source object.
        g.begin_delete_range();g._pick_range((g._series[0],1))
        hit=g._nearest(self.event_at(2,series=g._series[0]));self.assertEqual(hit[0]['object_id'],0)
        g._pick_range(hit);g.confirm_delete_range()
        self.assertEqual(self.deletes[-1],((0,0,[1,2]),{'frame_range':(1,2)}))
        g._series[1]['y'][2]=g._series[0]['y'][2]+.2
        hit=g._nearest(self.event_at(2,series=g._series[0]));self.assertEqual(hit[0]['object_id'],0)

    def test_current_time_stays_visible_but_value_guide_hides_over_deleted_gap(self):
        g=self.graph;t=deepcopy(self.t)
        for key in ('frames','frame_ts','points','Displacement','Angle'):t[key]=np.delete(t[key],2,axis=0)
        self.host.vm.track_data={0:t};self.host.vm.active_frame=2
        self.host.vm.cine_handler=SimpleNamespace(get_timestamp=lambda frame:{'time_from_start':frame*.1})
        g.redraw({0:t});g.draw_frame_pos()
        self.assertAlmostEqual(g.frame_pos_line.get_xdata()[0],.2)
        self.assertTrue(g.frame_value_line is None or not g.frame_value_line.get_visible())
    def test_arrow_navigation_visits_distinct_derivative_dots_with_shared_source_frame(self):
        g=self.graph;s=g._series[0];s['frames'][2]=s['frames'][1]
        g._select((s,1));g.setFocus();QTest.keyClick(g,Qt.Key_Right)
        self.assertEqual(g._resolve_selected()[1],2)
        QTest.keyClick(g,Qt.Key_Right);self.assertEqual(g._resolve_selected()[1],3)

    def test_units_need_calibration_and_snapshot_csv_arrays_are_independent(self):
        g=self.graph;self.assertFalse(self.host.graph_units.isEnabled());self.assertEqual(g.get_units('Speed'),('s','pix/s'))
        self.host.vm.length_units='in';g.redraw({0:self.t});self.assertTrue(self.host.graph_units.isEnabled())
        self.host.graph_units.setCurrentIndex(self.host.graph_units.findData('mm'))
        np.testing.assert_allclose(g._series[0]['y'],np.arange(5)*50.8)
        snapshot=g.graph_export_snapshot();self.assertEqual(snapshot['y_unit'],'mm');self.assertEqual(snapshot['time_reference'],'start')
        snapshot['series'][0]['y'][0]=500;self.assertEqual(g._series[0]['y'][0],0)
        self.host.fig_dropdown.setCurrentText('Angle');g.redraw({0:self.t});self.assertFalse(self.host.graph_units.isEnabled())
        self.assertEqual(g.get_units('Angle'),('s','deg'))



class GraphWorkspaceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_guided_workspace import GuidedWorkspaceTests
        cls.fixture=GuidedWorkspaceTests;cls.fixture.setUpClass()
    @classmethod
    def tearDownClass(cls):cls.fixture.tearDownClass()
    def setUp(self):
        self.case=self.fixture('test_video_first_layout_and_side_only_inspector');self.case.setUp()
        self.w=self.case.w;w=self.w;w.track_tool.click();w.vm.track_type='Manual';w.vm._update_track_type()
        w.vm.active_tool.track_select=True
        for frame in range(12):w.vm.active_tool.collect_points((100.+frame,110.+frame*2),frame)
        w.vm.track_fig_calculations();w.vm.active_frame=4;w._set_graphs_open(True);w.track_canvas.redraw(w.vm.track_data)
        QApplication.processEvents()
    def tearDown(self):self.case.tearDown()
    def test_real_point_delete_undo_range_and_global_arrow_focus(self):
        w=self.w;g=w.track_canvas;original=deepcopy(w.vm.track_data[0]);g.zoom_graph(1.5)
        limits=(g.ax.get_xlim(),g.ax.get_ylim());g._select((g._series[0],4));g.setFocus()
        QTest.keyClick(g,Qt.Key_Right);self.assertEqual(g._selected,(0,0,5));self.assertEqual(w.vm.active_frame,5)
        QTest.keyClick(g,Qt.Key_Delete)
        np.testing.assert_array_equal(w.vm.track_data[0]['frames'],np.delete(original['frames'],5))
        self.assertEqual((g.ax.get_xlim(),g.ax.get_ylim()),limits);self.assertTrue(g.undo_button.isEnabled())
        g.undo_button.click();np.testing.assert_array_equal(w.vm.track_data[0]['points'],original['points'])
        g.begin_delete_range();g._pick_range((g._series[0],2));g._pick_range((g._series[0],7));g.confirm_range_button.click()
        np.testing.assert_array_equal(w.vm.track_data[0]['frames'],[0,1,8,9,10,11])
        self.assertEqual((g.ax.get_xlim(),g.ax.get_ylim()),limits)
        g.undo_button.click();np.testing.assert_array_equal(w.vm.track_data[0]['frames'],original['frames'])
        self.assertIsNone(w._graph_delete_undo)

if __name__=='__main__':unittest.main()
