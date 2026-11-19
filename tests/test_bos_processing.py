"""Regression cases for subpixel BOS measurements, confidence and mapped overlays."""
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app-source/modules/trackmeasure'))
from bos_processing import compute_bos, probe_support, render_bos, reason_label


class BOSProcessingTests(unittest.TestCase):
    @staticmethod
    def dots():
        rng = np.random.default_rng(2918)
        texture = rng.normal(size=(180, 240)).astype(np.float32)
        texture = cv2.GaussianBlur(texture, (0, 0), 0.9)
        # A dim, narrow-range signal in a sensor container, below one uint8 DN
        # if naively displayed as the high byte of a 16-bit image.
        return np.rint(120 + 70*texture).clip(0, 255).astype(np.uint16)

    def test_subpixel_direction_units_and_roi_origin(self):
        ref = self.dots()
        expected = (1.375, -.6875)
        matrix = np.array([[1, 0, expected[0]], [0, 1, expected[1]]], dtype=np.float32)
        shifted = cv2.warpAffine(ref, matrix, ref.shape[::-1], flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REFLECT_101)
        shifted = 2*shifted + 45
        f = compute_bos(ref, shifted, (18, 22, 198, 137))
        self.assertGreater(f.valid.mean(), .95)
        self.assertTrue(np.all((f.x >= 18+14) & (f.x < 18+198-14)))
        self.assertTrue(np.all((f.y >= 22+14) & (f.y < 22+137-14)))
        np.testing.assert_allclose([np.nanmedian(f.dx), np.nanmedian(f.dy)], expected, atol=.07)

    def test_spatially_localized_deflection_does_not_cover_quiet_region(self):
        ref = self.dots()
        y, x = np.indices(ref.shape, dtype=np.float32)
        band = 1.8*np.exp(-0.5*((x-143)/12)**2)
        shifted = cv2.remap(ref, x-band, y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT_101)
        f = compute_bos(ref, shifted, (8, 9, 220, 160), window=15)
        front = np.abs(f.x-143) < 5
        quiet = f.x < 88
        self.assertGreater(np.nanmedian(f.dx[:, front]), 1.4)
        self.assertLess(np.nanmedian(np.abs(f.dy[:,front])), .1)
        self.assertLess(np.nanmax(np.abs(f.dx[:,quiet])), .06)

    def test_flash_illumination_does_not_create_apparent_flow(self):
        ref = self.dots().astype(np.float32)
        y, x = np.indices(ref.shape)
        light = 115*np.exp(-((x-116)**2 + (y-81)**2)/(2*34**2))
        flash = (ref + light).astype(np.float32)
        f = compute_bos(ref, flash, (10, 5, 210, 170), window=31)
        self.assertGreater(f.valid.mean(), .95)
        self.assertLess(np.nanpercentile(np.hypot(f.dx, f.dy), 95), .04)

    def test_bright_plume_keeps_unobscured_dim_dots_measurable(self):
        rng = np.random.default_rng(125)
        h, w = 200, 400
        texture = cv2.GaussianBlur(rng.normal(size=(h,w)).astype(np.float32), (0,0), 1.3)
        y, x = np.indices((h,w), dtype=np.float32)
        still = (370 + 120*texture/texture.std()).clip(0,1000) * (1 - .42*x/w)
        event = still.copy()
        plume = (x < 130) & (y > 40) & (y < 160)
        event[plume] = 2300 + rng.normal(0, 18, np.count_nonzero(plume))
        def sensor(image):
            return (image + rng.normal(0, 5, (h,w))).clip(0,4095).astype(np.uint16)
        f = compute_bos(sensor(still), sensor(event), (0,0,w,h), window=31, search=2)
        right = f.x > 300
        self.assertGreater(f.valid[:,right].mean(), .99)
        self.assertLess(np.nanpercentile(np.hypot(f.dx[:,right], f.dy[:,right]), 95), .04)
        core = np.ix_((f.y > 60) & (f.y < 139), f.x < 109)
        self.assertFalse(f.valid[core].any())
        self.assertIn(reason_label(np.median(f.reason[core])), (
            'Dot pattern differs from the reference', 'Too little usable texture'))

    def test_fine_measurements_resolve_narrow_front(self):
        ref = self.dots()
        y, x = np.indices(ref.shape, dtype=np.float32)
        band = 1.2*np.exp(-.5*((x-123)/3.5)**2)
        current = cv2.remap(ref, x-band, y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT_101)
        def profile(window, detail):
            field = compute_bos(ref, current, (0,0,240,180), window=window, search=2, detail=detail)
            return np.interp(np.arange(240), field.x, np.nanmedian(field.dx, axis=0))
        standard = profile(31, False)
        fine = profile(17, True)
        def fwhm(line):
            xs = np.flatnonzero(line >= line.max() * .5)
            return xs[-1] - xs[0]
        self.assertLess(fwhm(fine), fwhm(standard) * .7)
        self.assertLess(abs(np.argmax(fine)-123), 5)
        self.assertGreater(fine.max(), 1.5 * standard.max())
        self.assertLess(np.mean(np.abs(fine-band[0])), np.mean(np.abs(standard-band[0])))

    def test_uncorrelated_or_untextured_regions_have_missing_vectors(self):
        a = self.dots()
        b = np.roll(a[::-1], 47, axis=1)
        f = compute_bos(a, b, (0, 0, 240, 180))
        self.assertLess(f.valid.mean(), .05)
        self.assertTrue(np.isnan(f.dx[~f.valid]).all())
        flat = np.full_like(a, 210)
        self.assertFalse(compute_bos(flat, flat, (3, 5, 150, 129)).valid.any())

    def test_render_respects_region_measurement_support_and_zero(self):
        ref = self.dots()
        target = np.roll(ref, 1, axis=1)
        f = compute_bos(ref, target, (14, 8, 206, 159))
        base = np.full((*ref.shape, 3), 90, dtype=np.uint8)
        original = base.copy()
        a = render_bos(base, f, component='horizontal', limit=2, opacity=1, palette='gray')
        x, y = int(f.x[4]), int(f.y[4])
        self.assertAlmostEqual(int(a[y, x, 0]), 45, delta=4)
        self.assertTrue((a[:8] == 90).all())
        self.assertTrue((a[:, :14] == 90).all())
        np.testing.assert_array_equal(base, original)
        identical = compute_bos(ref, ref, (14, 8, 206, 159))
        np.testing.assert_array_equal(render_bos(base, identical), base)
        f.valid[4,4] = False
        self.assertEqual(render_bos(base, f, soften=True)[y, x, 0], 90)
        self.assertEqual(probe_support(f, x, y), 'weak')
        self.assertEqual(probe_support(f, 14, 8), 'outside')
        self.assertEqual(probe_support(f, f.x[3], f.y[3]), 'measured')
        # A nearest measured patch can still border a transparent interpolation gap.
        rendered = render_bos(base, f, component='horizontal', limit=2, opacity=1, palette='gray')
        for xx in range(x, x+f.step+1):
            self.assertEqual(probe_support(f, xx, y) == 'measured', rendered[y, xx, 0] < 90)

    def test_clip_or_reject_roi_at_image_boundary(self):
        a = self.dots()
        self.assertEqual(compute_bos(a, a, (-10,-8,120,99)).roi, (0, 0, 110, 91))
        with self.assertRaisesRegex(ValueError, 'larger'):
            compute_bos(a, a, (208,168,50,60))
