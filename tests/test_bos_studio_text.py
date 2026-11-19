import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from bos_studio_text import StudioText, capture_rate


class StudioTextTests(unittest.TestCase):
    def setUp(self):
        self.clip = SimpleNamespace(width=320, height=240, count=6, times=np.arange(6)*400.,
                                    manifest=dict(capture_fps=10000., decimation=4))
        self.options = dict(fields=('frame','playback','capture','slowdown','time','custom'),
                            first=2, last=3, size='medium', position='bottom_right', custom='Ignition')

    def test_caption_tracks_selected_section_and_cut_speed_with_decimated_cine(self):
        title = StudioText(self.clip, self.options, capture_rate(self.clip))
        self.assertIsNone(title.graphic(1, 50))
        field, (x,y,w,h) = title.graphic(2, 50)
        self.assertIn('Frame 3 of 6', title.lines(2, 50))
        self.assertIn('10,000 fps capture', title.lines(2, 50))
        self.assertIn('50× slower', title.lines(2, 50))
        self.assertIn('50 fps playback', title.lines(2, 50))
        self.assertIn('100× slower', title.lines(3, 25))
        self.assertIsNone(title.graphic(4, 25))
        base = np.zeros((240,320,3), dtype=np.uint8)
        result = title.composite(base, 2, 50)
        expected = ((field[:,:,:3] * field[:,:,3:].astype(np.uint16) + 127) // 255)
        np.testing.assert_array_equal(result[y:y+h,x:x+w], expected)
        self.assertFalse(result[:y].any())

    def test_old_cache_labels_measured_cadence_and_ignores_missing_timestamps(self):
        self.clip.manifest.clear()
        self.assertEqual(capture_rate(self.clip), 2500.)
        title = StudioText(self.clip,self.options,capture_rate(self.clip))
        self.assertIn('2,500 fps recorded (est.)', title.lines(2,50))
        self.clip.times[[0,2,5]] = np.nan
        self.assertEqual(capture_rate(self.clip), 2500.)
        self.clip.times[:] = np.nan
        self.assertIsNone(capture_rate(self.clip))

    def test_static_text_reuses_artwork_and_wraps_long_captions(self):
        self.options.update(fields=('custom',), custom=' '.join(['Lighter ignition']*8))
        title = StudioText(self.clip,self.options,10000.)
        self.assertGreater(len(title.caption), 1)
        self.assertIs(title.graphic(2,60), title.graphic(3,120))

    def test_dynamic_numerals_reuse_rasterized_glyphs(self):
        self.clip.count = 200
        self.clip.times = np.arange(200)*9.32
        self.options.update(first=0,last=199)
        title = StudioText(self.clip, self.options, 10000.)
        # Warm each type of numeral and separator in both variable lines.
        for frame in range(120):
            title.graphic(frame, 240)
        with patch('bos_studio_text.ImageDraw.Draw', side_effect=AssertionError('per-frame font draw')):
            sprites = [title.graphic(i, 240)[0] for i in (121,122,123)]
        self.assertFalse(np.array_equal(sprites[0], sprites[1]))
