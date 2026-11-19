import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from bos_frames import BOSFrames, default_bos_settings
from bos_preview import prepare_bos_preview, export_cached_bos, PreviewReadAhead, CacheReader
from bos_preview_ui import BOSPreviewWindow, _Progress, analyze_bos_range
from test_bos_motion import texture, write_frames
from video_export import ExportCancelled


class BOSPreviewCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'dots.cine'
        self.frames = np.array([np.roll(texture(), i%2, axis=1) for i in range(8)])
        write_frames(self.source, self.frames)
        self.settings = dict(default_bos_settings(),path=str(self.source),reference=0,
                             position=3,roi=[140,10,240,220],playback_fps=30.)

    def prepare(self, first=1, last=5, **kwargs):
        return prepare_bos_preview(self.settings,first,last,root=self.root/'cache',**kwargs)

    def test_inclusive_lossless_cache_is_independent_of_source_and_settings_changes(self):
        source_bytes = self.source.read_bytes()
        original = dict(self.settings)
        clip = self.prepare(progress=lambda *_: self.settings.update(limit=9,position=6))
        self.addCleanup(clip.close)
        self.assertEqual(clip.count,5)
        self.assertEqual(clip.source_frame(0),-9)
        self.assertEqual(clip.source_frame(4),-5)
        session = BOSFrames(self.source)
        try:
            with clip.reader() as reader:
                for local in range(5):
                    expected = session.render(dict(original,position=local+1))
                    np.testing.assert_array_equal(reader.read(local),expected['image'])
                    self.assertAlmostEqual(clip.times[local],expected['time_us'])
        finally:session.close()
        self.assertEqual(clip.manifest['settings']['limit'],original['limit'])
        self.assertEqual(self.source.read_bytes(),source_bytes)
        self.source.rename(self.root/'disconnected.cine')
        with patch('bos_preview.BOSFrames',side_effect=AssertionError('Source must not reopen')):
            with clip.reader() as reader:self.assertEqual(reader.read(4).shape,(240,400,3))
            destination=self.root/'cached.mp4'
            export_cached_bos(clip,destination,24)
            cap=cv2.VideoCapture(str(destination))
            try:
                self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),5)
                self.assertAlmostEqual(cap.get(cv2.CAP_PROP_FPS),24)
                for index in range(5):self.assertTrue(cap.read()[0])
                self.assertFalse(cap.read()[0])
            finally:cap.release()

    def test_color_compass_follows_vector_background_and_map_opacity(self):
        self.settings.update(component='direction', show_vectors=True)
        for background, opacity, expected in [('map', .85, True), ('footage', .85, False),
                                               ('map', 0., False)]:
            with self.subTest(background=background, opacity=opacity):
                self.settings.update(vector_background=background, opacity=opacity)
                clip = self.prepare(1, 1)
                try:
                    self.assertEqual(clip.has_direction_colors, expected)
                    self.assertEqual(clip.compass_available(0), expected)
                finally:
                    clip.close()

    def test_cancel_error_and_low_space_remove_partial_cache(self):
        cancel=threading.Event()
        with self.assertRaises(ExportCancelled):self.prepare(cancel=cancel,progress=lambda *_:cancel.set())
        self.assertEqual(list((self.root/'cache').glob('bos-*')),[])
        real=BOSFrames.render
        def fail(session,settings):
            if settings['position']==3:raise OSError('Read disconnected')
            return real(session,settings)
        with patch('bos_preview.BOSFrames.render',fail),self.assertRaisesRegex(OSError,'disconnected'):
            self.prepare()
        self.assertEqual(list((self.root/'cache').glob('bos-*')),[])
        import shutil
        usage=shutil.disk_usage(self.root)
        with patch('bos_preview.shutil.disk_usage',return_value=type(usage)(usage.total,usage.total-1,1)):
            with self.assertRaisesRegex(ValueError,'cache space'):self.prepare()
        self.assertEqual(list((self.root/'cache').glob('bos-*')),[])

    def test_unreliable_anchor_warning_survives_cache(self):
        self.frames[2,120:230,10:120]=128
        write_frames(self.source,self.frames)
        self.settings.update(motion_compensation=True,anchor_roi=[10,120,110,110])
        clip=self.prepare(1,3);self.addCleanup(clip.close)
        self.assertEqual(clip.manifest['anchor_failures'],1)
        self.assertEqual(sum(clip.manifest['warning_counts'].values()),1)
        self.assertEqual(clip.manifest['warning_examples'][0]['frame'],-8)
        session=BOSFrames(self.source)
        try:
            expected=session.render(dict(self.settings,position=2))
            with clip.reader() as reader:np.testing.assert_array_equal(reader.read(1),expected['image'])
        finally:session.close()

    def test_original_and_direction_layers_follow_inclusive_range_edits(self):
        self.settings.update(component='direction', palette='color')
        clip = self.prepare()
        self.addCleanup(clip.close)
        session = BOSFrames(self.source)
        try:
            expected = []
            for index in range(1, 6):
                rendered = session.render(dict(self.settings, position=index))
                expected.append((session.base.copy(), rendered['image']))
        finally:
            session.close()
        self.source.rename(self.root/'detached.cine')
        worker = PreviewReadAhead(clip)
        worker.start()
        try:
            for mask in (bytes([1]*5), bytes([0, 1, 1, 0, 0]), bytes([0]*5)):
                worker.set_overlay_mask(mask)
                worker.request(0)
                deadline = time.monotonic()+4
                while worker.ready_count(0) < 5 and time.monotonic() < deadline:
                    time.sleep(.002)
                for i, on in enumerate(mask):
                    np.testing.assert_array_equal(worker.get(i), expected[i][on])
        finally:
            worker.stop()
            worker.join(3)

    def test_read_ahead_memory_bound_and_backward_random_seek(self):
        clip=self.prepare(0,7);self.addCleanup(clip.close)
        worker=PreviewReadAhead(clip,memory_bytes=400*240*3*2)
        worker.start()
        try:
            for index in (0,6,2,7,0):
                worker.request(index)
                deadline=time.monotonic()+3
                while worker.get(index) is None and time.monotonic()<deadline:time.sleep(.002)
                self.assertIsNotNone(worker.get(index))
                with clip.reader() as reader:np.testing.assert_array_equal(worker.get(index),reader.read(index))
                with worker.condition:self.assertLessEqual(len(worker.frames),2)
            self.assertIsNone(worker.error)
        finally:worker.stop();worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_export_failure_and_cancellation_preserve_existing_file(self):
        clip=self.prepare();self.addCleanup(clip.close)
        dest=self.root/'keep.mp4';dest.write_bytes(b'keep')
        cancel=threading.Event()
        with self.assertRaises(ExportCancelled):
            export_cached_bos(clip,dest,24,cancel,lambda *_:cancel.set())
        self.assertEqual(dest.read_bytes(),b'keep')
        with self.assertRaisesRegex(ValueError,'source Cine'):
            export_cached_bos(clip,self.source,30)
        self.assertEqual(list(self.root.glob('.pca-export-*')),[])

    def test_gray_storage_is_smaller_but_color_channels_are_preserved(self):
        for palette, ndim in [('gray',2),('color',3)]:
            self.settings['palette']=palette
            clip=self.prepare(1,1);self.addCleanup(clip.close)
            payload=(clip.directory/'frames.bin').read_bytes()
            decoded=cv2.imdecode(np.frombuffer(payload,np.uint8),cv2.IMREAD_UNCHANGED)
            self.assertEqual(decoded.ndim,ndim)
            session=BOSFrames(self.source)
            try:
                expected=session.render(dict(self.settings,position=1))['image']
                with clip.reader() as reader:np.testing.assert_array_equal(reader.read(0),expected)
            finally:session.close()


class BOSPreviewWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'play.cine'
        small=cv2.resize(texture(),(160,96))
        write_frames(self.source,np.array([np.roll(small,i%2,axis=1) for i in range(90)]))
        self.settings=dict(default_bos_settings(),path=str(self.source),reference=0,position=5,
                           roi=[0,0,160,96],playback_fps=20.)
        self.clip=prepare_bos_preview(self.settings,0,89,root=self.root/'cache')
        self.window=BOSPreviewWindow(None,self.clip);self.window.resize(800,600);self.window.show()
        self.wait(lambda:self.window.pending_seek is None)

    def wait(self,predicate,timeout=5):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();time.sleep(.002)
        self.assertTrue(predicate())

    def tearDown(self):
        self.window.close();self.wait(lambda:self.window.cleaned)
        self.app.processEvents()

    def test_play_pause_seek_and_rate_changes_never_recompute_cine(self):
        self.source.rename(self.root/'unplugged.cine')
        with patch('bos_preview.BOSFrames',side_effect=AssertionError('Recomputed BOS')):
            QTest.mouseClick(self.window.play,Qt.MouseButton.LeftButton)
            self.wait(lambda:self.window.position>=8)
            self.assertTrue(self.window.playing)
            self.window.fps.setValue(60)
            self.wait(lambda:self.window.position>=16)
            QTest.mouseClick(self.window.play,Qt.MouseButton.LeftButton)
            paused=self.window.position
            QTest.qWait(100)
            self.assertEqual(self.window.position,paused)
            self.window.slider.setValue(3)
            self.wait(lambda:self.window.pending_seek is None and self.window.position==3)
            self.assertEqual(self.window.number.value(),-7)
            self.window.view.setFocus()
            QTest.keyClick(self.window,Qt.Key.Key_Right)
            self.wait(lambda:self.window.position==4)

    def test_speed_slider_and_overlay_range_editor(self):
        w = self.window
        w.speed_control.slider.setValue(240)
        self.assertEqual(w.fps.value(), 240)
        w.fps.setValue(119.88)
        self.assertEqual(w.speed_control.slider.value(), 120)
        self.assertFalse(w.studio.isVisible())
        w.studio_button.click()
        self.assertTrue(w.studio.isVisible())
        w.seek(3)
        self.wait(lambda: w.pending_seek is None)
        w.studio.split.click()
        w.studio.first.setValue(-4)
        self.assertEqual(list(w.overlay_mask()[:8]), [1,1,1,0,0,0,1,1])
        w.overlay.click()
        self.assertFalse(any(w.overlay_mask()))
        w.overlay.click()
        self.assertEqual(w.overlay_mask()[3:6], bytes(3))
        w.seek(3)
        self.wait(lambda: w.pending_seek is None)
        self.assertIn('Original', w.frame_info.text())

    def test_close_stops_decoder_and_removes_only_its_cache(self):
        directory=self.clip.directory
        kept=self.root/'cache'/'unrelated.txt';kept.write_text('keep')
        self.window.close();self.wait(lambda:self.window.cleaned)
        self.assertFalse(directory.exists())
        self.assertEqual(kept.read_text(),'keep')
        self.assertFalse(self.window.reader.is_alive())

    def test_progress_cancel_waits_for_worker_and_does_not_publish_partial_result(self):
        finished=threading.Event()
        def slow(cancel,progress):
            while not cancel.is_set():time.sleep(.002)
            time.sleep(.02);finished.set();raise ExportCancelled()
        dialog=_Progress(self.window,'Cancel test',10,slow)
        dialog.worker.start();QTimer.singleShot(25,dialog.reject)
        dialog.exec()
        self.assertTrue(finished.is_set())
        self.assertFalse(dialog.worker.isRunning())
        self.assertTrue(dialog.worker.cancelled)
        self.assertIsNone(dialog.worker.result)
        dialog.deleteLater()

    def test_late_end_still_loads_actual_final_frame(self):
        window=self.window
        window.playing=True;window.buffering=False
        window.clock=(time.monotonic()-10,0)
        original=window.reader.get
        with patch.object(window.reader,'get',side_effect=lambda i:None if i==89 else original(i)):
            window._tick()
        self.assertFalse(window.playing)
        self.assertEqual(window.pending_seek,89)
        self.wait(lambda:window.position==89 and window.pending_seek is None)

    def test_close_waits_for_modal_export_to_unwind_after_thread_finished(self):
        from unittest.mock import Mock
        worker=Mock();worker.isRunning.return_value=False
        self.window.export_worker=worker
        self.assertFalse(self.window.shutdown())
        self.assertFalse(self.window.cleaned)
        self.assertTrue(self.clip.directory.exists())
        self.window.export_worker=None
        self.wait(lambda:self.window.shutdown())

    def test_single_frame_memory_budget_still_advances_playback(self):
        window=self.window
        window.reader.stop();window.reader.join(3)
        window.reader=PreviewReadAhead(self.clip,memory_bytes=1)
        self.assertEqual(window.reader.capacity,1)
        window.reader.start();window.reader.request(0)
        window.toggle_play()
        self.wait(lambda:window.position>=4)
        self.assertTrue(window.playing)
        self.assertEqual(window.view.index,window.position)

    def test_pause_during_rewind_keeps_last_displayed_frame_and_labels(self):
        window=self.window
        window.seek(89);self.wait(lambda:window.pending_seek is None)
        with patch.object(window.reader,'ready_count',return_value=0):
            window.toggle_play()
            self.assertTrue(window.buffering)
            self.assertEqual(window.buffer_target,0)
            window.pause()
        self.assertEqual(window.position,89)
        self.assertEqual(window.slider.value(),89)
        self.assertEqual(window.view.index,89)
        self.assertEqual(window.number.value(),self.clip.source_frame(89))


if __name__=='__main__':unittest.main()
