"""The shareable compass uses the displayed direction colors and source coordinates."""
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from bos_compass import CompassPlacement, compass_rect, compass_rgba, draw_compass
from bos_display import DIRECTION_COLORS
from bos_frames import default_bos_settings
from bos_preview import prepare_bos_preview, export_cached_bos
from test_bos_motion import texture, write_frames


class CompassExportTests(unittest.TestCase):
    def test_color_key_tracks_signed_image_axes_and_norm_coordinates(self):
        side = 300
        art = compass_rgba(side)
        for quadrant, hex_color in enumerate(DIRECTION_COLORS):
            theta = quadrant*math.pi/4
            x, y = round((.5+.17*math.cos(theta))*(side-1)), round((.447+.17*math.sin(theta))*(side-1))
            self.assertEqual(tuple(art[y, x]), (*bytes.fromhex(hex_color[1:]), 255))
        self.assertFalse(art.flags.writeable)
        self.assertEqual(art[0, 0, 3], 0)
        self.assertEqual(compass_rect(CompassPlacement(1, -1, .5), 801, 599), (501,0,300,300))
        self.assertEqual(compass_rect(CompassPlacement(.25, .3, .2), 300, 800), (75,240,60,60))

    def test_export_with_compass_uses_overlay_ranges_and_reliability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'dots.cine'
            imgs = np.array([np.roll(texture(), i%2, axis=1) for i in range(4)])
            imgs[2, 120:230, 10:120] = 128  # Hidden stationary anchor on the third frame only.
            write_frames(source, imgs)
            state = dict(default_bos_settings(), path=str(source), reference=0,
                         roi=[140,10,240,220], component='direction', palette='color',
                         motion_compensation=True, anchor_roi=[10,120,110,110])
            clip = prepare_bos_preview(state,0,3,root=root/'cache')
            self.addCleanup(clip.close)
            self.assertEqual([clip.compass_available(i) for i in range(4)], [True, True, False, True])
            placement = CompassPlacement(.63, .2, .32)
            mask = bytes([0,1,1,1])
            source.unlink()
            with patch('bos_preview.BOSFrames',side_effect=AssertionError('Unexpected Cine access')):
                for name, legend in [('plain', None), ('marked', placement)]:
                    export_cached_bos(clip, root/(name+'.mp4'), 240, overlay_mask=mask, compass=legend)
            x,y,side,_ = compass_rect(placement,clip.width,clip.height)
            with clip.reader() as reader:
                for name, on_frames in [('plain', set()), ('marked', {1,3})]:
                    movie = cv2.VideoCapture(str(root/(name+'.mp4')))
                    try:
                        self.assertAlmostEqual(movie.get(cv2.CAP_PROP_FPS),240)
                        for i in range(4):
                            ok, decoded = movie.read()
                            self.assertTrue(ok)
                            actual = cv2.cvtColor(decoded,cv2.COLOR_BGR2RGB)[y:y+side,x:x+side].astype(float)
                            raw = reader.read(i,overlay=mask[i])
                            predicted = raw.copy()
                            draw_compass(predicted, placement)
                            errors = [np.mean(np.abs(actual-im[y:y+side,x:x+side]))
                                      for im in (raw,predicted)]
                            self.assertEqual(int(np.argmin(errors)),int(i in on_frames))
                            self.assertGreater(abs(errors[0]-errors[1]),8)
                        self.assertFalse(movie.read()[0])
                    finally:
                        movie.release()
