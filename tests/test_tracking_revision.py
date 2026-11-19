"""Tracking edits preserve measurements; live crops and visual effects coexist."""
import unittest
from copy import deepcopy
import numpy as np
import cv2
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
import test_guided_workspace as fixture
from tracking_data_edit import delete_samples
from tracking_preview import point_preview
from tracking_render import fade_alphas, RecordedTrackPath
from viewer_processing import apply_grade, default_grade


class TrackingDataDeletionTests(unittest.TestCase):
    def track(self):
        return dict(frames=np.array([3,7,12,18]), points=np.arange(8,dtype=float).reshape(4,2),
            scores=np.arange(4,dtype=float), angles=np.arange(4,dtype=float),
            t_frames=np.array([3,12]), t_points=np.array([[0.,1.],[4.,5.]]), t_angles=np.array([0.,2.]),
            notes={0:'anchor',2:'impact',3:'keep'},origin_frame=12,
            hybrid_candidates={i:dict(point=(i,i)) for i in (3,7,8,12,18)},
            hybrid_notes_by_frame={3:'anchor',12:'impact',18:'keep'},
            confidence_components={i:dict(score=i) for i in (3,7,8,12,18)},
            tumbling_observations={i:dict(mask=np.array([[i]],np.uint8)) for i in (3,7,8,12,18)})

    def test_range_is_inclusive_and_prunes_hidden_candidates_notes_masks_templates(self):
        original=self.track();edited,count=delete_samples(original,[7,12],(7,12))
        self.assertEqual(count,2);np.testing.assert_array_equal(edited['frames'],[3,18])
        self.assertEqual(edited['notes'],{0:'anchor',1:'keep'})
        for field in ('hybrid_candidates','hybrid_notes_by_frame','confidence_components','tumbling_observations'):
            self.assertEqual(set(edited[field]),{3,18})
        np.testing.assert_array_equal(edited['t_frames'],[3]);np.testing.assert_array_equal(edited['points'],original['points'][[0,3]])
        self.assertEqual(edited['origin_frame'],12)  # explicit missing-reference status, never change zero silently
        self.assertEqual(len(original['frames']),4);self.assertIn(8,original['hybrid_candidates'])

    def test_single_delete_reseeds_templates_and_preserves_other_samples(self):
        original=self.track();edited,count=delete_samples(original,[3,12])
        self.assertEqual(count,2);np.testing.assert_array_equal(edited['t_frames'],[7])
        np.testing.assert_array_equal(edited['t_points'],original['points'][[1]])
        self.assertIn(8,edited['hybrid_candidates']);self.assertEqual(edited['notes'],{1:'keep'})
        self.assertIsNone(delete_samples(original,original['frames'])[0])

    def test_fast_crop_matches_original_full_frame_process_for_mono_color_and_edges(self):
        rng=np.random.default_rng(13);grade=default_grade();grade['gamma']=1.8
        for dtype,channels in ((np.uint8,()),(np.uint16,()),(np.uint8,(3,))):
            image=rng.integers(0,180,(80,100)+channels,dtype=dtype)
            for point in ((0,0),(99,79),(50,40)):
                graded=apply_grade(image,grade)
                padded=cv2.copyMakeBorder(graded,10,10,10,10,cv2.BORDER_CONSTANT,value=[0,0,0])
                expected=cv2.resize(padded[point[1]:point[1]+20,point[0]:point[0]+20],(108,108),interpolation=cv2.INTER_CUBIC)
                np.testing.assert_array_equal(point_preview(image,point,40,108,grade),expected)

    def test_zero_radius_transparency_applies_whole_path_even_without_current_measurement(self):
        result=fade_alphas(np.array([[0.,0.],[100.,100.]]),np.empty((0,2)),0,60)
        np.testing.assert_array_equal(result,[102,102])


class TrackingRevisionUITests(unittest.TestCase):
    setUpClass=classmethod(fixture.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.GuidedWorkspaceTests.tearDownClass.__func__)
    setUp=fixture.GuidedWorkspaceTests.setUp
    tearDown=fixture.GuidedWorkspaceTests.tearDown
    wait_worker=fixture.GuidedWorkspaceTests.wait_worker

    def seed(self):
        w=self.w;w.track_tool.click();tool=w.vm.active_tool;tool.track_select=True
        tool.collect_points((150.,120.),0)
        track=w.vm.track_data[0]
        track.update(frames=np.arange(12),points=np.column_stack((np.full(12,150.),np.arange(12)+120.)),
                     scores=np.ones(12),angles=np.zeros(12),notes={5:'impact'})
        w.vm.track_fig_calculations();w.vm.new_track_data.emit(w.vm.track_data,0);w.vm.redraw_cb()
        return track

    def test_idle_auto_click_does_not_modify_points_or_erase_heatmap(self):
        track=self.seed();before=deepcopy(track['points']);items=list(self.w.graph.scene().items())
        self.w.vm.graph_click_cb((40.,50.))
        np.testing.assert_array_equal(self.w.vm.track_data[0]['points'],before)
        self.assertEqual(self.w.graph.scene().items(),items)

    def test_live_point_views_update_during_playback_without_changing_source(self):
        self.seed();w=self.w;w.details_buttons[2].click();self.app.processEvents()
        w._sync_object_preview_grid();self.assertTrue(w._preview_tiles[0].isVisible())
        w._playback_active=True
        try:
            for level in (45,170):
                image=np.full((240,320),level,np.uint8)
                w.on_draw_frame('main_graph',image,8,0)
                pixmap=w._preview_tiles[0].graph._image_item.pixmap()
                self.assertEqual(pixmap.toImage().pixelColor(54,54).red(),level)
                self.assertTrue(np.all(image==level))
        finally:w._playback_active=False

    def test_path_panel_does_not_disable_transparency_and_heatmap_survives_zoom(self):
        self.seed();w=self.w
        w.path_fade_button.click();w.path_transparency_enabled.setChecked(True)
        w.path_fade_radius.setValue(0);w.path_fade_transparency.setValue(60)
        w.path_heat_map.setChecked(True);w.fig_dropdown.setCurrentText('Y-Displacement')
        w.path_fade_button.click();self.assertTrue(w._path_fade_enabled);self.assertTrue(w._heat_map_enabled)
        w.vm.redraw_cb()
        paths=[item for item in w.graph.scene().items() if isinstance(item,RecordedTrackPath)]
        self.assertTrue(paths)
        transform=w.graph.transform();w.graph.zoom_at(w.graph.viewport().rect().center(),3)
        self.assertNotEqual(w.graph.transform(),transform)
        self.assertIn(paths[0],w.graph.scene().items())
        self.assertTrue(w._path_fade_enabled);self.assertTrue(w._heat_map_enabled)
        w.path_fade_button.click();self.assertTrue(w.path_transparency_enabled.isChecked())

    def test_primary_tracking_button_and_removed_pan_hide_buttons(self):
        self.seed();w=self.w
        self.assertIs(w.viewer_overlay_controls.layout().itemAt(0).widget(),w.add_object_button)
        self.assertIn('background:',w.add_object_button.styleSheet())
        self.assertFalse(w.viewer_pan_button.isVisible());self.assertFalse(w.workspace_panes[0].hide_button.isVisible())

    def test_delete_undo_and_new_cine_calibration_are_independent(self):
        track=self.seed();w=self.w;original=deepcopy(track)
        self.assertTrue(w.delete_graph_tracking_samples(0,0,[4,5,6],(4,6)))
        self.assertEqual(len(w.vm.track_data[0]['frames']),9);self.assertEqual(w.vm.track_data[0]['notes'],{})
        self.assertTrue(w.undo_graph_tracking_delete())
        np.testing.assert_array_equal(w.vm.track_data[0]['points'],original['points'])
        self.assertEqual(w.vm.track_data[0]['notes'],{5:'impact'})
        w.vm.image_scale=.05;w.vm.length_units='in';w.vm.sync_active_workspace_context()
        job=self.root/'calibrated.pcajob';w.save_job_path(job)
        another=self.root/'other.cine';fixture.motion_cine(another)
        w.vm.load_cine_workspace_cb([str(another)])
        self.assertEqual(w.vm.length_units,'pix');self.assertEqual(w.vm.image_scale,1.)
        self.assertFalse(w.graph_units.isEnabled())
        w.open_job_path(job);self.assertEqual(w.vm.length_units,'in');self.assertEqual(w.vm.image_scale,.05)

    def test_undo_does_not_overwrite_later_settings_or_reused_object_id(self):
        self.seed();w=self.w
        self.assertTrue(w.delete_graph_tracking_samples(0,0,[3]))
        w.vm.track_data[0]['tpl_rng']=(17,19)
        self.assertFalse(w.undo_graph_tracking_delete())
        self.assertEqual(w.vm.track_data[0]['tpl_rng'],(17,19))
        self.assertTrue(w.delete_graph_tracking_samples(0,0,w.vm.track_data[0]['frames'].copy()))
        w.vm.active_tool.track_select=True;w.vm.active_tool.collect_points((80.,90.),5)
        self.assertFalse(w.undo_graph_tracking_delete())
        np.testing.assert_array_equal(w.vm.track_data[0]['points'],[[80.,90.]])


if __name__=='__main__':unittest.main()
