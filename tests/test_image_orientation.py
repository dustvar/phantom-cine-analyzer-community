"""Rotated presentation must preserve sensor coordinates and exported pixels."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
import numpy as np
from PySide6.QtCore import QPointF, QEventLoop, QTimer
from PySide6.QtGui import QImage
import test_guided_workspace as fixtures
from image_orientation import rotate_points
from viewer_effects import apply_effects, VisualTimeline
from viewer_timeline import default_edit, validate_edit
from viewer_track_fade import default_track_fade
from cine_metadata import CineHandler
from video_export import export_clip
from viewer_processing import default_grade
from test_viewer_export import SETTINGS
from test_cine_trim import write_trim_fixture


def pixels(graph):
    image = graph._image_item.pixmap().toImage().convertToFormat(QImage.Format_RGB888)
    return np.frombuffer(image.bits(), np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()*3].reshape(image.height(), image.width(), 3).copy()


class OrientationTests(unittest.TestCase):
    def test_rotation_matches_sensor_pixel_locations_and_keeps_annotations_upright(self):
        image = np.arange(80*100*3, dtype=np.uint8).reshape(80,100,3)
        for degrees in (0,90,180,270):
            edit = default_edit(2);edit['rotation'] = degrees
            result = apply_effects(image,edit,SimpleNamespace(),VisualTimeline(edit),0,0,30)[0]
            np.testing.assert_array_equal(result,np.rot90(image,-degrees//90))
            xy=rotate_points([[20,30]],degrees,100,80)[0].astype(int)
            np.testing.assert_array_equal(result[xy[1],xy[0]],image[30,20])
            edit['overlay'].update(enabled=True,fields=['playback'],size=5,x=.9,y=.9)
            marked,rects,_,_=apply_effects(image,edit,SimpleNamespace(),VisualTimeline(edit),0,0,30)
            x,y,w,h=rects['playback']
            self.assertGreater(w,h)  # Label is laid out on the upright output canvas.
            self.assertLessEqual(x+w,marked.shape[1]);self.assertLessEqual(y+h,marked.shape[0])

    def test_tracking_fade_transforms_only_displayed_points(self):
        image = np.zeros((80,100,3),np.uint8)
        tracks={0:dict(points=np.array([[20.,30.]]),frames=np.array([0]),color='#ff0000',name='Target')}
        original=deepcopy(tracks)
        for degrees,point in [(90,(49,20)),(180,(79,49)),(270,(30,79))]:
            edit=default_edit(2);edit.update(rotation=degrees,track_fade=dict(default_track_fade(),enabled=True,duration=0))
            result=apply_effects(image,edit,SimpleNamespace(),VisualTimeline(edit),0,0,30,tracks=tracks)[0]
            self.assertGreater(result[point[1],point[0],0],200)
            np.testing.assert_array_equal(tracks[0]['points'],original[0]['points'])
        for bad in (True,45,360,'90'):
            edit['rotation']=bad
            with self.assertRaises(ValueError):validate_edit(edit,2)

    def test_rendered_cine_is_rotated_and_raw_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.cine';write_trim_fixture(source,frames=3,width=64,height=48)
            before=source.read_bytes()
            base=Path(tmp)/'base.cine'
            export_clip(source,base,0,2,30,SETTINGS,default_grade())
            reader=CineHandler();reader.load_cine(base)
            expected=[reader.get_img(i).copy() for i in range(3)];reader.close()
            for degrees in (90,180,270):
                output=Path(tmp)/f'{degrees}.cine';edit=default_edit(3);edit['rotation']=degrees
                export_clip(source,output,0,2,30,SETTINGS,default_grade(),edit=edit)
                reader=CineHandler();reader.load_cine(output)
                try:
                    for i in range(3):np.testing.assert_array_equal(reader.get_img(i),np.rot90(expected[i],-degrees//90))
                finally:reader.close()
            self.assertEqual(source.read_bytes(),before)


class OrientationUITests(unittest.TestCase):
    setUpClass=classmethod(fixtures.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(fixtures.GuidedWorkspaceTests.tearDownClass.__func__)
    setUp=fixtures.GuidedWorkspaceTests.setUp
    tearDown=fixtures.GuidedWorkspaceTests.tearDown

    def test_adjust_buttons_preview_analysis_mapping_and_job_roundtrip(self):
        w=self.w;original=pixels(w.graph)
        w.details_buttons[1].click()
        self.assertTrue(w.image_rotation.isVisible())
        w.rotate_right.click()
        self.assertEqual(w.vm.viewer_edit['rotation'],90)
        np.testing.assert_array_equal(pixels(w.graph),np.rot90(original,-1))
        w.track_tool.click();w.vm.redraw_cb()
        np.testing.assert_array_equal(pixels(w.graph),original)
        self.assertEqual(w.graph._pca_rotation,90)
        point=QPointF(150,120)
        recovered=w.graph.mapToScene(w.graph.mapFromScene(point))
        self.assertLess((recovered-point).manhattanLength(),1)
        w.rotate_right.click();self.assertEqual(w.graph._pca_rotation,180)
        job=self.root/'orientation.pcajob';w.save_job_path(job)
        w.rotate_reset.click();self.assertEqual(w.graph._pca_rotation,0)
        w.open_job_path(job)
        self.assertEqual(w.image_rotation.currentData(),180)
        self.assertEqual(w.graph._pca_rotation,180)
        w.viewer_tool.click();w.vm.redraw_cb()
        np.testing.assert_array_equal(pixels(w.graph),np.rot90(original,2))
        self.assertEqual(w.graph._pca_rotation,0)

    def wait_group(self):
        group = self.w.group_playback
        loop = QEventLoop(); poll = QTimer(); poll.setInterval(5)
        deadline = time.monotonic() + 3
        def settled():
            return group.pending is None and not group.refresh_timer.isActive() and group.last_batch is not None
        poll.timeout.connect(lambda: loop.quit() if settled() or time.monotonic() > deadline else None)
        poll.start(); loop.exec(); poll.stop()
        self.assertTrue(settled(), 'Synchronized preview did not finish')

    def test_synchronized_viewer_keeps_per_source_rotation_and_zoom_when_toggled(self):
        w = self.w; original = pixels(w.graph)
        second = self.root / 'second.cine'; fixtures.motion_cine(second)
        w.vm.load_cine_workspace_cb([str(self.source), str(second)])
        self.wait_group()
        w._set_image_rotation(90); self.wait_group()
        first = w.workspace_panes[0].graph
        np.testing.assert_array_equal(pixels(first), np.rot90(original, -1))
        self.assertEqual(getattr(first, '_pca_rotation', 0), 0)
        w.activate_cine_pane(1)
        self.assertEqual(w.image_rotation.currentData(), 0)  # Before worker publication.
        w._set_image_rotation(270); self.wait_group()
        second_graph = w.graph
        np.testing.assert_array_equal(pixels(first), np.rot90(original, -1))
        np.testing.assert_array_equal(pixels(second_graph), np.rot90(original, 1))
        second_graph.zoom_at(second_graph.viewport().rect().center(), 4)
        transform = second_graph.transform(); zoom = second_graph._view_zoom
        w.group_playback_toggle.setChecked(False)
        np.testing.assert_array_equal(pixels(second_graph), np.rot90(original, 1))
        self.assertEqual(second_graph.transform(), transform)
        self.assertEqual(second_graph._view_zoom, zoom)
        w.group_playback_toggle.setChecked(True); self.wait_group()
        np.testing.assert_array_equal(pixels(second_graph), np.rot90(original, 1))
        self.assertEqual(second_graph.transform(), transform)
        self.assertEqual(second_graph._view_zoom, zoom)
        w.track_tool.click()
        self.assertEqual(second_graph._pca_rotation, 270)  # Also immediate for analysis.
        self.wait_group()
        np.testing.assert_array_equal(pixels(second_graph), original)

    def test_rotation_is_per_source_and_undoable(self):
        w=self.w;second=self.root/'second.cine';fixtures.motion_cine(second)
        w.vm.load_cine_workspace_cb([str(self.source),str(second)])
        w.rotate_right.click();self.assertEqual(w.image_rotation.currentData(),90)
        w.activate_cine_pane(1);self.assertEqual(w.image_rotation.currentData(),0)
        w.rotate_left.click();self.assertEqual(w.image_rotation.currentData(),270)
        w.activate_cine_pane(0);self.assertEqual(w.image_rotation.currentData(),90)
        w._undo_viewer_edit();self.assertEqual(w.image_rotation.currentData(),0)
        w.activate_cine_pane(1);self.assertEqual(w.image_rotation.currentData(),270)
