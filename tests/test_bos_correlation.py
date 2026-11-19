"""Displacement and reliability tests for the direct BOS correlation search."""
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from bos_processing import compute_bos, reason_label


class CorrelationBOSTests(unittest.TestCase):
    def setUp(self):
        noise = np.random.default_rng(1141).normal(0, 1, (128, 192)).astype('f')
        self.texture = cv2.GaussianBlur(noise, (0, 0), 1.1)

    def measure(self, reference, current, search=4):
        h, w = reference.shape
        return compute_bos(reference, current, (0, 0, w, h), window=17, search=search,
                           detail=True, method='cross_correlation')

    def test_known_band_limited_fractional_shifts_and_gain(self):
        # Fourier shifts supply ground truth independent of the subpixel peak
        # estimator and avoid interpolation biases in synthetic cubic/bilinear warps.
        fy = np.fft.fftfreq(self.texture.shape[0])[:,None]
        fx = np.fft.fftfreq(self.texture.shape[1])[None,:]
        spectrum = np.fft.fft2(self.texture)
        for dx, dy in ((.125,-.375), (.4,.7), (-1.625,2.25), (2.25,-1.625)):
            with self.subTest(dx=dx,dy=dy):
                translated = np.fft.ifft2(spectrum*np.exp(-2j*np.pi*(fy*dy+fx*dx))).real
                current = (14.3*translated + 1000.).astype('f')
                f = self.measure(self.texture, current)
                self.assertGreater(f.valid.mean(), .97)
                np.testing.assert_allclose([np.nanmedian(f.dx), np.nanmedian(f.dy)], [dx,dy], atol=.045)
        same = self.measure(self.texture, self.texture)
        self.assertGreater(same.valid.mean(), .99)
        self.assertLess(np.nanmax(abs(same.dx)), .001)
        self.assertLess(np.nanmax(abs(same.dy)), .001)

    def test_occluded_region_and_displacements_outside_search(self):
        current = np.roll(self.texture, 1, axis=1)
        current[:, :70] = np.random.default_rng(14).normal(0, .2, (128,70))
        field = self.measure(self.texture, current)
        smoke = field.x < 45
        still = field.x > 100
        self.assertLess(field.valid[:,smoke].mean(), .01)
        self.assertTrue(np.isnan(field.dx[~field.valid]).all())
        self.assertGreater(field.valid[:,still].mean(), .98)
        np.testing.assert_allclose(np.nanmedian(field.dx[:,still]),1.,atol=.03)
        outside = self.measure(self.texture, np.roll(self.texture, 4, axis=1), search=2)
        self.assertLess(outside.valid.mean(), .04)

    def test_ambiguous_or_one_dimensional_pattern_is_masked(self):
        tile = np.random.default_rng(28).normal(size=(6,6)).astype('f')
        repeats = np.tile(tile,(20,29))
        field = self.measure(repeats, repeats, search=7)
        interior = np.ix_((field.y>30) & (field.y<90),(field.x>30) & (field.x<140))
        self.assertFalse(field.valid[interior].any())
        reason = int(np.median(field.reason[interior]))
        self.assertEqual(reason_label(reason), 'Multiple similar dot-pattern matches')
        stripes = np.tile(self.texture[0:1],(128,1))
        self.assertFalse(self.measure(stripes, np.roll(stripes, 1, axis=1)).valid.any())
