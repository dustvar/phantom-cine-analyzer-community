"""Edited RGB Cine must reopen with display pixels and editorial timing intact."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import imageio_ffmpeg
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from PySide6.QtWidgets import QApplication
from cine_render import write_rendered_cine, rendered_size
from cine_metadata import CineHandler
from pyphantom_image.imagetools import ImageTools
from test_viewer_export import SETTINGS
from test_cine_trim import write_trim_fixture
from video_export import export_clip, ExportCancelled
from video_export_ui import ExportDialog
from viewer_processing import render_cine_image, default_grade
from viewer_timeline import default_edit, append_video
from test_viewer_sequence import annotation


class RenderedCineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.out = self.folder / 'edited.cine'

    def test_asymmetric_rgb_and_timing_roundtrip_with_independent_decoder(self):
        width,height,count = 50,33,4  # Deliberately needs DIB row padding.
        yy,xx = np.indices((height,width))
        frames = [np.stack((yy*7,xx*4+i,xx+yy),axis=2).astype('uint8') for i in range(count)]
        write_rendered_cine(self.out, frames, width, height, 29.97, count, threading.Event())
        self.assertEqual(self.out.stat().st_size, rendered_size(width,height,count))
        c = CineHandler()
        self.addCleanup(c.close)
        c.load_cine(self.out)
        self.assertTrue(c.metadata.PcaRendered)
        self.assertEqual(c.metadata.FrameRate,29.97)
        np.testing.assert_allclose(c.capture_timestamps_from_trigger(), np.arange(count)/29.97, atol=1e-9)
        for i, frame in enumerate(frames):
            pixels = c.get_img(i)
            np.testing.assert_array_equal(pixels,frame)
            np.testing.assert_array_equal(render_cine_image(pixels,c.metadata,SETTINGS,ImageTools()),frame)
        data = subprocess.check_output([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i',str(self.out),
                                       '-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
        np.testing.assert_array_equal(np.frombuffer(data,np.uint8).reshape(count,height,width,3),frames)

    def test_movie_edit_text_baked_into_cine_and_cancel_preserves_existing_file(self):
        source = self.folder/'camera.cine'
        write_trim_fixture(source,frames=8, width=239,height=159)
        edit = default_edit(8)
        edit['texts'] = [annotation(text='Impact',start=0,duration=1,color='#ff6600')]
        export_clip(source,self.out,2,5,30,SETTINGS,default_grade(),edit=edit)
        handler = CineHandler()
        handler.load_cine(self.out)
        try:
            self.assertEqual(handler.metadata.ImageCount,4)
            rgb = handler.get_img(1)
            self.assertEqual(rgb.shape, (159,239,3))
            self.assertGreater(np.sum(rgb[:,:,0].astype(int)-rgb[:,:,2] > 40),15)
        finally:
            handler.close()
        before=self.out.read_bytes()
        cancel=threading.Event()
        with self.assertRaises(ExportCancelled):
            export_clip(source,self.out,0,5,30,SETTINGS,default_grade(),edit=edit,cancel=cancel,
                        progress=lambda *_: cancel.set())
        self.assertEqual(self.out.read_bytes(),before)
        self.assertFalse(list(self.folder.glob('.pca-render-*')))

    def test_original_trim_maps_single_copy_and_blocks_a_join_or_non_cine(self):
        from types import SimpleNamespace
        source = self.folder/'camera.cine'
        md=SimpleNamespace(FirstImageNo=-10,FrameRate=2000)
        edit = append_video(default_edit(8))
        dialog = ExportDialog(None,source,10,11,md,edit)
        dialog.format.setCurrentIndex(dialog.format.findData('original'))
        self.assertEqual(dialog.options(),dict(original=True,first=2,last=3,source=str(source)))
        self.assertEqual(dialog.extension(),'.cine')
        self.assertTrue(dialog.choose.isEnabled())
        self.assertFalse(dialog.form.isRowVisible(dialog.fps))
        for low,high,path in [(7,9,source),(10,11,self.folder/'video.mp4')]:
            edit['copies'][1]['source_path']=str(path)
            d=ExportDialog(None,source,low,high,md,edit)
            d.format.setCurrentIndex(d.format.findData('original'))
            self.assertFalse(d.choose.isEnabled())
            d.format.setCurrentIndex(d.format.findData('rendered'))
            self.assertTrue(d.choose.isEnabled())
            d.close()
        dialog.close()
