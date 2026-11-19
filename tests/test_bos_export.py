import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from bos_frames import BOSFrames, default_bos_settings
from bos_export import export_bos_clip
from video_export import ExportCancelled
from test_cine_metadata import write_cine


class BOSExportTests(unittest.TestCase):
    def test_encoded_frames_follow_region_reference_and_inclusive_source_range(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'dots.cine'
            width, height, count = 144, 96, 5
            write_cine(source, frames=count, width=width, height=height)
            # Replace the fixture's uniform pixels with a native, translated dot pattern.
            pattern = cv2.GaussianBlur(np.random.default_rng(15).integers(
                0, 245, (height, width), dtype=np.uint8), (3, 3), .8)
            size = width * height
            with source.open('r+b') as stream:
                for index in range(count):
                    stream.seek(-(count-index)*(size+8)+8, os.SEEK_END)
                    stream.write(np.roll(pattern, index, axis=1)[::-1].tobytes())
            settings = dict(default_bos_settings(), path=str(source), reference=0,
                            position=2, roi=[14, 10, 118, 78], limit=2)
            dest = Path(directory)/'BOS.mp4'
            export_bos_clip(settings, dest, 1, 3, 24)
            reader = cv2.VideoCapture(str(dest))
            session = BOSFrames(source)
            try:
                self.assertAlmostEqual(reader.get(cv2.CAP_PROP_FPS), 24)
                for index in [1, 2, 3]:
                    ok, bgr = reader.read()
                    self.assertTrue(ok)
                    observed = bgr[:, :, ::-1].astype(float)
                    rendered = session.render(dict(settings, position=index))
                    self.assertGreater(np.mean(rendered['field'].valid), .85)
                    np.testing.assert_allclose(np.nanmedian(rendered['field'].dx), index, atol=.15)
                    self.assertLess(np.mean(np.abs(observed-rendered['image'].astype(float))), 5)
                self.assertFalse(reader.read()[0])
            finally:
                session.close()
                reader.release()
            committed = dest.read_bytes()
            cancel = threading.Event()
            with self.assertRaises(ExportCancelled):
                export_bos_clip(settings, dest, 1, 3, 24, cancel, lambda *_: cancel.set())
            self.assertEqual(dest.read_bytes(), committed)
            self.assertEqual(list(dest.parent.glob('.pca-export-*')), [])
