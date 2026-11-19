import os,sys,unittest,tempfile,json
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import cv2
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure')]
from viewer_timeline import (default_edit,append_video,split_at,move_cut,merge_next,validate_edit,
    Timeline,copy_options,copy_index,copy_count,copy_offset,total_frames,frame_label,is_video_join)
from viewer_effects import VisualTimeline,crop_rect,zoom_image,apply_effects
from viewer_processing import default_grade,render_cine_image
from viewer_media import open_media,media_descriptor,fit_canvas
from viewer_project import save_project,load_project
from video_export import export_clip,video_frame
from pyphantom_image.imagetools import ImageTools
from test_cine_metadata import write_cine
from test_viewer_export import SETTINGS
import test_workspace_interactions as fixtures
from PySide6.QtCore import Qt,QPointF,QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog
from viewer_transition_ui import TransitionDialog
import simplemeas_ui


def annotation(**changes):
    value=dict(id='a',text='Arc starts here',start=0.,duration=2.,enabled=True,
               x=.03,y=.9,size=4.,color='#ffffff',background=True,pinned=True)
    value.update(changes);return value


class SequenceModelTests(unittest.TestCase):
    def test_multiply_is_fresh_preserves_edits_and_one_click_appends_one_copy(self):
        edit=split_at(default_edit(20),7);edit['sections'][0]['fps']=80
        edit['sections'][1]['zoom']['factor']=3
        edit['overlay']['enabled']=True;edit['texts']=[annotation()]
        original=deepcopy(edit);result=append_video(edit)
        self.assertEqual(edit,original);self.assertEqual(len(result['copies']),2)
        self.assertEqual(result['sections'][:2],edit['sections'])
        self.assertEqual(result['sections'][-1]['start'],20)
        self.assertEqual(result['sections'][-1]['fps'],30)
        self.assertEqual(result['sections'][-1]['zoom']['factor'],1)
        self.assertFalse(result['copies'][1]['overlay']['enabled'])
        self.assertFalse(result['copies'][1]['camera_overlay']['enabled'])
        self.assertEqual(result['copies'][1]['texts'],[])
        self.assertEqual(validate_edit(result,20),result)
        self.assertEqual(len(append_video(result)['copies']),3)

    def test_multiply_preserves_trimmed_ramp_timing_before_replay(self):
        edit=split_at(default_edit(100),30)
        edit['sections'][0]['fps']=90;edit['sections'][1].update(fps=15,ramp=.8)
        before=Timeline(edit,40,78)
        repeated=append_video(edit,first=40,last=78);after=Timeline(repeated,0,199)
        for time in np.linspace(0,before.duration-1e-7,100):
            a,b=before.sample(time),after.sample(time)
            self.assertEqual(a[0],b[0]);self.assertAlmostEqual(a[1],b[1])
        self.assertEqual(after.sample(before.duration)[0],100)
        self.assertAlmostEqual(after.duration,before.duration+100/30)

    def test_new_video_different_length_and_multiplying_that_source(self):
        media=dict(frame_count=7,source_path='/tmp/new.cine',first_frame=-2,name='new.cine',playback_fps=30)
        edit=append_video(default_edit(20),media=media)
        edit['sections'][-1]['fps']=5;edit['copies'][1]['texts']=[annotation()]
        repeated=append_video(edit,template_index=1)
        self.assertEqual(total_frames(repeated),34)
        self.assertEqual(copy_offset(repeated,2),27)
        self.assertEqual(copy_index(repeated,26),1)
        self.assertEqual(copy_index(repeated,27),2)
        self.assertEqual(frame_label(repeated,28,-10),'V3 · -1')
        self.assertEqual(repeated['copies'][2]['source_path'],'/tmp/new.cine')
        self.assertEqual(repeated['copies'][2]['texts'],[])
        self.assertEqual(repeated['sections'][-1]['fps'],30)
        self.assertEqual(validate_edit(repeated,20),repeated)

    def test_join_stays_fixed_and_regular_cuts_stay_in_their_video(self):
        edit=append_video(default_edit(20));self.assertTrue(is_video_join(edit,1))
        self.assertEqual(move_cut(edit,1,15),edit);self.assertEqual(merge_next(edit,0),edit)
        edit=split_at(edit,25);self.assertFalse(is_video_join(edit,2))
        moved=move_cut(edit,2,-100)
        self.assertEqual(moved['sections'][2]['start'],21)
        self.assertEqual(validate_edit(moved,20),moved)

    def test_speed_ramp_works_across_new_video_join(self):
        edit=append_video(default_edit(60));edit['sections'][0]['fps']=90
        edit['sections'][1].update(fps=15,ramp=.5,ramp_place='center')
        plan=Timeline(edit,0,119);cut=plan.time_for_source(60)
        self.assertEqual(plan.sample(cut)[0],60)
        self.assertAlmostEqual(plan.sample(cut)[1],52.5)
        self.assertGreater(plan.sample(cut-.1)[1],plan.sample(cut+.1)[1])

    def test_zoom_interpolates_and_plain_split_keeps_the_original_animation(self):
        edit=default_edit(60);edit['sections'][0]['zoom'].update(factor=3,cx=.7,cy=.7,duration=1)
        visual=VisualTimeline(edit)
        np.testing.assert_allclose(visual.crop_at(0),[0,0,1,1])
        np.testing.assert_allclose(visual.crop_at(.5),(crop_rect(edit['sections'][0]['zoom'])+np.array([0,0,1,1]))/2)
        after=VisualTimeline(split_at(edit,10))
        for seconds in np.linspace(0,1.9,100):np.testing.assert_allclose(visual.crop_at(seconds),after.crop_at(seconds))
        split=split_at(edit,30);split['sections'][1]['zoom'].update(inherit=False,factor=1,duration=.5)
        back=VisualTimeline(split)
        np.testing.assert_allclose(back.crop_at(1.5),[0,0,1,1])
        self.assertLess(back.crop_at(1.1)[2],back.crop_at(1.4)[2])

    def test_crop_pixels_zoom_into_selected_region_without_mutating_source(self):
        image=np.zeros((120,200,3),np.uint8);image[60:,100:]=(220,40,10);original=image.copy()
        zoom=dict(factor=2,cx=.75,cy=.75)
        result=zoom_image(image,crop_rect(zoom))
        np.testing.assert_array_equal(result[30:-2,30:-2],np.broadcast_to([220,40,10],result[30:-2,30:-2].shape))
        np.testing.assert_array_equal(image,original)

    def test_text_timing_and_pinning_apply_independently_of_the_crop(self):
        edit=default_edit(90);edit['sections'][0]['zoom'].update(factor=3)
        edit['texts']=[annotation(start=.5,duration=1)]
        image=np.zeros((240,320,3),np.uint8);metadata=SimpleNamespace(FrameRate=1000)
        visual=VisualTimeline(edit)
        for seconds,visible in [(0,False),(.5,True),(1.49,True),(1.5,False)]:
            _,boxes,_,_=apply_effects(image,edit,metadata,visual,seconds,round(seconds*30),30)
            self.assertEqual('text:a' in boxes,visible)
        edit['texts'][0]['pinned']=False;visual=VisualTimeline(edit)
        out,boxes,_,_=apply_effects(image,edit,metadata,visual,.5,15,30)
        self.assertNotIn('text:a',boxes);self.assertFalse(out.any())

    def test_text_and_camera_data_do_not_leak_into_a_fresh_copy(self):
        edit=default_edit(30);edit['texts']=[annotation(duration=100)];edit['camera_overlay']['enabled']=True
        edit=append_video(edit);visual=VisualTimeline(edit)
        _,boxes,_,_=apply_effects(np.zeros((200,320,3),np.uint8),edit,SimpleNamespace(FrameRate=1000),visual,1.1,33,30)
        self.assertEqual(boxes,{})

    def test_invalid_sequence_effects_are_rejected_and_v2_migrates(self):
        for key,value in [('factor',0),('cx',float('nan')),('duration',-1)]:
            edit=default_edit(20);edit['sections'][0]['zoom'][key]=value
            with self.assertRaises(ValueError):validate_edit(edit,20)
        edit=default_edit(20);edit['texts']=[annotation(duration=-1)]
        with self.assertRaises(ValueError):validate_edit(edit,20)
        old=default_edit(20);old['version']=2
        for key in ('copies','source_count','selection','texts'):old.pop(key)
        old['sections'][0].pop('zoom')
        migrated=validate_edit(old,20)
        self.assertEqual(migrated,default_edit(20))


class SequenceInteractionsTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.WorkspaceInteractionsTest.setUpClass.__func__)
    setUp=fixtures.WorkspaceInteractionsTest.setUp
    click=fixtures.WorkspaceInteractionsTest.click
    assert_images_visible=fixtures.WorkspaceInteractionsTest.assert_images_visible
    def tearDown(self):
        self.window._close_sequence_readers()
        fixtures.WorkspaceInteractionsTest.tearDown(self)

    def multiply(self):
        with patch('viewer_sequence_mixin.render_cine_image',side_effect=lambda image,*args:image):
            self.click(self.window.multiply_button)

    def test_multiply_full_playback_scrub_undo_redo_and_analysis_isolation(self):
        self.window.clip_range.setValues(2,12);before=deepcopy(self.vm.viewer_edit)
        self.multiply()
        self.assertEqual(len(self.vm.viewer_edit['copies']),2)
        self.assertEqual(self.window.frame_slider.maximum(),39)
        self.assertEqual(self.window.frame_slider.value(),20)
        self.assertEqual(self.vm.active_frame,0)
        self.assertFalse(self.window.editor_panel.overlay_enabled.isChecked())
        self.click(self.window.timeline_undo_button)
        self.assertEqual(self.vm.viewer_edit,before)
        self.assertEqual((self.window.clip_range.lowerValue(),self.window.clip_range.upperValue()),(2,12))
        with patch('viewer_sequence_mixin.render_cine_image',side_effect=lambda image,*args:image):
            self.click(self.window.timeline_redo_button)
            self.window._timeline_seek(35)
        self.assertEqual(self.vm.active_frame,15)
        with patch.object(self.window,'choose_tracking_method',return_value=None):self.click(self.window.track_tool)
        self.assertEqual(self.window.frame_slider.maximum(),19)
        self.assertEqual(self.vm.active_frame,15)
        with patch('viewer_sequence_mixin.render_cine_image',side_effect=lambda image,*args:image):self.click(self.window.viewer_tool)
        self.assertEqual(self.window.frame_slider.value(),35)
        self.assert_images_visible()

    def test_zoom_buttons_crop_drag_and_one_undo(self):
        self.click(self.window.timeline_button);self.window.editor_panel.tabs.setCurrentIndex(2)
        self.click(self.window.editor_panel.zoom_presets[2]);self.app.processEvents()
        self.assertTrue(self.window.editor_panel.crop_edit_button.isChecked())
        graph=self.window.graph;rect=graph._crop_rect
        self.assertIsNotNone(rect);before=deepcopy(self.vm.viewer_edit);history=len(self.vm._viewer_edit_undo)
        start=graph.mapFromScene(rect.center());end=graph.mapFromScene(rect.center()+QPointF(35,-20))
        QTest.mousePress(graph.viewport(),Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(graph.viewport(),end)
        QTest.mouseRelease(graph.viewport(),Qt.MouseButton.LeftButton,pos=end);self.app.processEvents()
        self.assertGreater(self.vm.viewer_edit['sections'][0]['zoom']['cx'],.5)
        self.assertEqual(len(self.vm._viewer_edit_undo),history+1)
        self.click(self.window.timeline_undo_button);self.assertEqual(self.vm.viewer_edit,before)
        self.window._start_playback(1);self.assertFalse(self.window.editor_panel.crop_edit_button.isChecked())
        self.window._stop_playback();self.assert_images_visible()

    def test_custom_text_typing_duration_position_and_undo(self):
        self.click(self.window.fps_button);self.click(self.window.editor_panel.custom_text_button)
        panel=self.window.editor_panel;self.click(panel.add_text_button)
        QTest.keyClicks(panel.text_content,'A tiny lightning arc');panel.text_content.editingFinished.emit();self.app.processEvents()
        panel.text_start.setValue(.1);panel.text_duration.setValue(.4)
        self.assertEqual(self.vm.viewer_edit['texts'][0]['text'],'A tiny lightning arc')
        self.window._timeline_seek(6);self.app.processEvents()
        key='text:'+self.vm.viewer_edit['texts'][0]['id'];self.assertIn(key,self.window.graph._data_rects)
        before=deepcopy(self.vm.viewer_edit);graph=self.window.graph;rect=graph._data_rects[key]
        start=graph.mapFromScene(rect.center());end=graph.mapFromScene(rect.center()+QPointF(0,50))
        QTest.mousePress(graph.viewport(),Qt.MouseButton.LeftButton,pos=start);QTest.mouseMove(graph.viewport(),end)
        QTest.mouseRelease(graph.viewport(),Qt.MouseButton.LeftButton,pos=end);self.app.processEvents()
        self.assertGreater(self.vm.viewer_edit['texts'][0]['y'],before['texts'][0]['y'])
        self.click(self.window.timeline_undo_button);self.assertEqual(self.vm.viewer_edit,before)
        self.window._timeline_seek(17);self.assertNotIn(key,graph._data_rects)

    def test_timeline_zoom_fit_and_join_dialog(self):
        self.multiply();initial=self.window.section_timeline.width()
        self.click(self.window.timeline_zoom_in)
        self.assertGreater(self.window.section_timeline.width(),initial*1.8)
        self.assertGreater(self.window.timeline_viewport.horizontalScrollBar().maximum(),0)
        self.click(self.window.timeline_fit);self.assertEqual(self.window.timeline_viewport.factor,1)
        def inspect(dialog):
            self.assertIn('New video join',dialog.windowTitle())
            self.assertFalse(dialog.cut_frame.isEnabled());self.assertFalse(dialog.cut_slider.isEnabled())
            dialog.duration.setValue(.1)
            return QDialog.DialogCode.Accepted
        with patch.object(TransitionDialog,'exec',inspect),patch('viewer_sequence_mixin.render_cine_image',side_effect=lambda image,*args:image):
            self.click(self.window.section_timeline.transition_buttons[1])
        self.assertEqual(self.vm.viewer_edit['sections'][1]['ramp'],.1)

    def test_add_new_video_uses_its_own_frames_and_keeps_failed_import_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'new.cine';write_cine(source,frames=24,width=160,height=100)
            with patch.object(simplemeas_ui.QFileDialog,'getOpenFileName',return_value=(str(source),'')):
                self.click(self.window.add_video_button)
            self.assertEqual(self.window.frame_slider.maximum(),43)
            self.window._timeline_seek(42)
            self.assertEqual(self.window.frame_slider.value(),42)
            self.assertEqual(self.vm._viewer_frame,42)
            self.assertEqual(self.vm.active_frame,19)  # Analysis remains within the original Cine.
            self.assertEqual((self.window.graph.xMax,self.window.graph.yMax),(160,100))
            self.click(self.window.fps_button)
            self.assertTrue(self.vm.viewer_edit['copies'][1]['overlay']['enabled'])
            self.assertFalse(self.vm.viewer_edit['overlay']['enabled'])
            before=deepcopy(self.vm.viewer_edit)
            with patch.object(simplemeas_ui.QFileDialog,'getOpenFileName',return_value=(str(Path(folder)/'missing.cine'),'')),patch.object(simplemeas_ui.QMessageBox,'warning') as warning:
                self.click(self.window.add_video_button)
                warning.assert_called_once()
            self.assertEqual(self.vm.viewer_edit,before)
            self.window._close_sequence_readers()

    def test_replay_preview_crosses_join_and_preserves_per_cine_sequence(self):
        with patch('viewer_sequence_mixin.render_cine_image',side_effect=lambda image,*args:image):
            self.multiply();self.window._timeline_seek(0);self.window._start_playback(1)
            self.window._viewer_clock=SimpleNamespace(elapsed=lambda:1000)
            self.window._advance_playback()
            self.assertEqual(self.window.frame_slider.value(),30)
            self.assertEqual(self.vm.active_frame,10)
            self.window._stop_playback()
            self.window.activate_cine_pane(1);self.app.processEvents()
            self.assertEqual(self.window.frame_slider.maximum(),7)
            self.window.activate_cine_pane(0);self.app.processEvents()
            self.assertEqual(self.window.frame_slider.maximum(),39)
            self.assertEqual(self.window.frame_slider.value(),30)

    def test_data_and_levels_are_independent_for_each_video(self):
        self.click(self.window.fps_button);self.vm.viewer_grade['gamma']=2
        with patch('viewer_sequence_mixin.render_cine_image',side_effect=lambda image,*args:image):
            self.multiply()
            self.assertEqual(self.vm.viewer_edit['copies'][1]['grade']['gamma'],1)
            self.assertFalse(self.vm.viewer_edit['copies'][1]['overlay']['enabled'])
            self.click(self.window.fps_button)
            self.window.editor_panel.overlay_x.setValue(25)
            self.assertEqual(self.vm.viewer_edit['overlay']['x'],.96)
            self.assertEqual(self.vm.viewer_edit['copies'][1]['overlay']['x'],.25)
            grade=default_grade();grade['gamma']=1.5
            self.window._on_viewer_grade_changed(grade)
            self.assertEqual(self.vm.viewer_grade['gamma'],2)
            self.assertEqual(self.vm.viewer_edit['copies'][1]['grade']['gamma'],1.5)


class SequenceFileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'first.cine';self.second=self.root/'second.cine'
        write_cine(self.source,frames=8,width=320,height=200)
        write_cine(self.second,frames=5,width=160,height=160)

    def test_mixed_cine_export_sizes_repeats_zoom_and_text_match_render(self):
        reader=open_media(self.second);self.addCleanup(reader.close)
        edit=append_video(default_edit(8),SETTINGS,media=media_descriptor(self.second,reader))
        edit=append_video(edit,SETTINGS,template_index=1)
        edit['sections'][1]['zoom'].update(factor=2,duration=.1,cx=.7,cy=.3)
        edit['copies'][1]['texts']=[annotation(text='Second view',duration=1,size=6)]
        edit['copies'][1]['camera_overlay']['enabled']=True
        output=self.root/'sequence.mp4';plan=Timeline(edit,0,total_frames(edit)-1);visual=VisualTimeline(edit)
        export_clip(self.source,output,0,total_frames(edit)-1,30,SETTINGS,default_grade(),edit=edit)
        cap=cv2.VideoCapture(str(output));self.addCleanup(cap.release)
        self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT),18)
        first=open_media(self.source);self.addCleanup(first.close)
        for i,(frame,rate) in enumerate(plan.samples()):
            ci=copy_index(edit,frame);r=first if ci==0 else reader
            base=video_frame(render_cine_image(r.get_img(frame-copy_offset(edit,ci)),r.metadata,SETTINGS,ImageTools()))
            wanted,_,_,_=apply_effects(base,edit,r.metadata,visual,i/30,frame,rate)
            wanted=fit_canvas(wanted,320,200)
            ok,image=cap.read();self.assertTrue(ok)
            self.assertLess(np.abs(image[:,:,::-1].astype(float)-wanted).mean(),6)
        self.assertFalse(cap.read()[0])

    def test_standard_video_and_recipe_asset_validation(self):
        movie=self.root/'ordinary.mp4';export_clip(self.second,movie,0,4,25,SETTINGS,default_grade())
        reader=open_media(movie);self.addCleanup(reader.close)
        self.assertEqual(reader.metadata.ImageCount,5)
        self.assertEqual(reader.metadata.FrameRate,25)
        reader.metadata.FrameRate=240
        self.assertEqual(media_descriptor(movie,reader)['playback_fps'],240)
        reader.metadata.FrameRate=25
        edit=append_video(default_edit(8),media=media_descriptor(movie,reader))
        edit['copies'][1]['texts']=[annotation()]
        output=self.root/'mixed.mp4'
        export_clip(self.source,output,0,12,30,SETTINGS,default_grade(),edit=edit)
        cap=cv2.VideoCapture(str(output));self.addCleanup(cap.release)
        self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT),14)
        recipe=self.root/'mixed.pcaedit';save_project(recipe,self.source,0,12,edit,default_grade(),SETTINGS)
        self.assertEqual(load_project(recipe,self.source,8)['edit'],edit)
        with movie.open('ab') as stream:stream.write(b'changed')
        with self.assertRaisesRegex(ValueError,'changed'):load_project(recipe,self.source,8)
