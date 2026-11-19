"""Real Cine pixels, capture-time synchronization, cancellation and editor isolation."""
import os,struct,time,unittest
from copy import deepcopy
from unittest.mock import patch
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtTest import QTest
from cine_metadata import CineHandler
from test_cine_metadata import write_cine
import test_guided_workspace as workspace
from viewer_timeline import append_video


def timed_cine(path,source,fps,count,first_seconds):
    epoch=1700000000+source*10000000
    write_cine(path,frames=count,width=64,height=48,start_second=epoch)
    handler=CineHandler();handler.load_cine(str(path))
    stamp=handler._timestamp_block_info['offset'];offsets=handler._image_offsets.copy();handler.close()
    data=bytearray(path.read_bytes());base=epoch<<32
    trigger=base+round(.02*2**32)
    struct.pack_into('<Q',data,36,trigger);struct.pack_into('<H',data,84,fps);struct.pack_into('<I',data,84+768,fps)
    for index,offset in enumerate(offsets):
        struct.pack_into('<Q',data,stamp+index*8,trigger+round((first_seconds+index/fps)*2**32))
        pixels=np.full((48,64),source*30,np.uint8);pixels[0,0]=index%256;pixels[0,1]=source
        data[int(offset)+8:int(offset)+8+pixels.size]=pixels[::-1].tobytes()
    path.write_bytes(data)


class GroupPlaybackTests(unittest.TestCase):
    setUpClass=classmethod(workspace.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(workspace.GuidedWorkspaceTests.tearDownClass.__func__)
    tearDown=workspace.GuidedWorkspaceTests.tearDown
    wait_worker=workspace.GuidedWorkspaceTests.wait_worker
    def setUp(self):
        workspace.GuidedWorkspaceTests.setUp(self)
        self.paths=[]
        for source,(fps,count,start) in enumerate(((1000,100,-.01),(500,60,-.02),(2000,200,.01),(1000,10,.1))):
            path=self.root/f'{source}.cine';timed_cine(path,source,fps,count,start);self.paths.append(str(path))
        self.w.vm.load_cine_workspace_cb(self.paths)
        self.wait(lambda:self.w.group_playback.last_batch is not None and self.w.group_playback.pending is None)
    def wait(self,predicate,timeout=3000):
        loop=QEventLoop();timer=QTimer();timer.setInterval(5);start=time.monotonic()
        timer.timeout.connect(lambda:loop.quit() if predicate() or (time.monotonic()-start)*1000>timeout else None)
        timer.start();loop.exec();timer.stop();self.assertTrue(predicate())
    def delay(self,milliseconds):
        loop=QEventLoop();QTimer.singleShot(milliseconds,loop.quit);loop.exec()
    def seek(self,frame):
        g=self.w.group_playback;before=g.last_batch
        self.w.frame_slider.setValue(frame);g.seek(frame)
        self.wait(lambda:g.pending is None and g.last_batch is not before)
    def assert_frames(self,expected):
        w=self.w
        self.assertEqual([p.sync_frame_index for p in w.workspace_panes],expected)
        for index,frame in enumerate(expected):
            pane=w.workspace_panes[index]
            if frame is None:self.assertIn('No recorded frame',pane.sync_status.text());self.assertTrue(pane.sync_status.isVisible())
            else:
                self.assertFalse(pane.sync_status.isVisible())
                self.assertEqual(pane.graph._displayed_source_index,frame)
                raw=w.vm.workspace_contexts[index]['cine_handler'].img
                self.assertEqual((int(raw[0,0]),int(raw[0,1])),(frame%256,index))
    def test_four_sources_use_capture_time_and_actual_decoded_pixels(self):
        self.assertTrue(self.w.group_playback_toggle.isChecked())
        self.assert_frames([0,5,None,None])
        self.seek(30);self.assert_frames([30,20,20,None])
        self.seek(60);self.assert_frames([60,35,80,None])
        self.assertIn('difference',self.w.workspace_panes[1].graph.toolTip())
        self.assertTrue(all(b._capacity<=8 for b in self.w.group_playback.frames.buffers.values()))
        edit=deepcopy(self.w.vm.viewer_edit)
        self.w.playback_fps.setValue(60);self.w._sync_transport_fps()
        self.assertEqual(self.w._analysis_fps,60);self.assertEqual(self.w.playback_fps.value(),60)
        self.assertEqual(self.w.vm.viewer_edit,edit)
        self.assertIn('Synchronized',self.w.playback_fps.toolTip())
    def test_repeated_seek_pause_reverse_and_missing_master_roundtrip(self):
        w=self.w;g=w.group_playback
        for delay in range(40):QTimer.singleShot(delay,lambda:g.seek(30))
        self.delay(70);self.wait(lambda:g.pending is None and g.last_batch.master_frame==30)
        self.assert_frames([30,20,20,None])
        with patch.object(g.frames,'prepare',return_value=None):
            w.frame_slider.setValue(31);self.delay(20);w.pause_button.click()
            self.assertEqual(w.vm.active_frame,30);self.assertEqual(w.frame_slider.value(),30)
            self.assert_frames([30,20,20,None])
            self.assertNotIn('Buffering',w.active_frame_label.text())
        w.forward_play_button.click();self.delay(200);w.pause_button.click()
        held=[p.sync_frame_index for p in w.workspace_panes];active=w.vm.active_frame
        self.delay(100);self.assertEqual([p.sync_frame_index for p in w.workspace_panes],held)
        self.assertFalse(g.timer.isActive());self.assertEqual(w.vm.active_frame,active)
        w.reverse_play_button.click();self.delay(100);w.pause_button.click();self.assertLess(w.vm.active_frame,active)
        self.seek(30)
        for _ in range(4):
            w.activate_cine_pane(3);self.wait(lambda:g.pending is None and g.last_batch.master_frame==0)
            self.assert_frames([None,None,180,0])
            w.activate_cine_pane(0);self.wait(lambda:g.pending is None and g.last_batch.master_frame==30)
            self.assert_frames([30,20,20,None])
    def test_hidden_source_stops_decoding_and_new_workspace_discards_old_buffers(self):
        w=self.w;g=w.group_playback;self.seek(30)
        old_key=g.frames.key(w.vm.workspace_contexts[1]);w.hide_cine_pane(1)
        self.wait(lambda:g.pending is None and old_key not in g.frames.buffers)
        w.restore_cine_pane(1);self.wait(lambda:g.pending is None and old_key in g.frames.buffers)
        before=g.last_batch
        w.vm.load_cine_workspace_cb(self.paths[:2])
        self.wait(lambda:g.pending is None and g.last_batch is not before)
        self.assertEqual(len(g.frames.buffers),2);self.assertNotIn(old_key,g.frames.buffers)
    def test_adjustments_sync_without_reading_on_gui_and_edit_state_survives_toggle(self):
        w=self.w;g=w.group_playback;self.seek(30)
        old=g.last_batch;inactive=w.vm.workspace_contexts[1]['transformed_img'].copy()
        w.vm.image_brightness=10
        w.vm.redraw_cb()
        self.wait(lambda:g.pending is None and g.last_batch is not old)
        self.assertEqual(w.vm.workspace_contexts[0]['_image_brightness'],10)
        np.testing.assert_array_equal(w.vm.workspace_contexts[1]['transformed_img'],inactive)
        # Workers own other CineHandler instances. Foreground readers must not
        # decode anything while a group seek publishes prepared frames.
        from contextlib import ExitStack
        with ExitStack() as stack:
            for c in w.vm.workspace_contexts:stack.enter_context(patch.object(c['cine_handler'],'get_img',side_effect=AssertionError('GUI decoded Cine')))
            self.seek(60);self.assert_frames([60,35,80,None])
        w.group_playback_toggle.setChecked(False)
        original=w.vm.viewer_edit
        edit=append_video(original,w.vm.image_settings_snapshot(),0,99,None,0)
        edit['selection']=[5,190];w._apply_viewer_edit(edit);w.frame_slider.setValue(130)
        saved=deepcopy(w.vm.viewer_edit)
        w.group_playback_toggle.setChecked(True)
        self.wait(lambda:g.pending is None and w.frame_slider.maximum()==99)
        w.group_playback_toggle.setChecked(False);self.delay(30)
        self.assertEqual(w.vm.viewer_edit,saved);self.assertEqual(w.frame_slider.maximum(),199)
        self.assertEqual(w.frame_slider.value(),130)
        w.group_playback_toggle.setChecked(True);self.wait(lambda:g.pending is None)
        for index in (1,2,3):w.hide_cine_pane(index)
        self.delay(50)
        self.assertFalse(g.enabled());self.assertEqual(w.frame_slider.maximum(),199)
        self.assertTrue(all(not p.sync_status.isVisible() for p in w.workspace_panes))
        w.restore_cine_pane(1);self.wait(lambda:g.pending is None)
        w.timeline_button.click();self.assertFalse(w.group_playback_toggle.isChecked())

if __name__=='__main__':unittest.main()
