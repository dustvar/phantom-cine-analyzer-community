"""A drawn BOS arrow must correspond to a signed measurement at a stable source site."""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from bos_processing import BOSField, compute_bos, remove_camera_translation, render_bos
from bos_vectors import key_rect, sample_vectors


class VectorDrawingTests(unittest.TestCase):
    def setUp(self):
        x = np.arange(12, 292, 4, dtype=np.float32)
        y = np.arange(16, 184, 4, dtype=np.float32)
        shape = (len(y), len(x))
        self.field = BOSField((3, 5, 300, 190), x, y, np.full(shape, .2, np.float32),
                              np.full(shape, -.1, np.float32), np.ones(shape, bool),
                              np.ones(shape, np.float32), 4, np.zeros(shape, np.uint8))
        self.source = np.full((270, 320, 3), 106, dtype=np.uint8)

    def test_direction_magnitude_and_nearest_match_at_both_ends(self):
        f = self.field
        samples = sample_vectors(f, 1., 'balanced')
        self.assertGreater(len(samples.rows), 10)
        np.testing.assert_array_equal(samples.x, f.x[samples.cols])
        np.testing.assert_array_equal(samples.y, f.y[samples.rows])
        np.testing.assert_allclose(samples.draw_dx, samples.full_length*.2)
        np.testing.assert_allclose(samples.draw_dy, -samples.full_length*.1)
        self.assertFalse(samples.saturated.any())
        i = len(samples.rows)//2
        for t in (-.49, 0., .49):
            self.assertEqual(samples.nearest(samples.x[i]+t*samples.draw_dx[i], samples.y[i]+t*samples.draw_dy[i]),
                             (samples.rows[i], samples.cols[i]))
        self.assertIsNone(samples.nearest(-2,-2))

    def test_weak_and_invalid_values_leave_stable_holes_and_overrange_keeps_true_values(self):
        f = self.field
        before = sample_vectors(f, 1.)
        cells = [(before.rows[i], before.cols[i]) for i in range(7, 10)]
        f.dx[cells[0]], f.dy[cells[0]] = .016, .003
        f.valid[cells[1]] = False
        f.dx[cells[2]], f.dy[cells[2]] = 2., -.4
        samples = sample_vectors(f, 1.)
        kept = {(r,c) for r,c in zip(samples.rows,samples.cols)}
        self.assertTrue(kept.issubset(set(zip(before.rows,before.cols))))
        self.assertNotIn(cells[0], kept)
        self.assertNotIn(cells[1], kept)
        matches = np.flatnonzero((samples.rows == cells[2][0]) & (samples.cols == cells[2][1]))
        self.assertEqual(len(matches), 1)
        i = matches[0]
        self.assertTrue(samples.saturated[i])
        self.assertEqual(samples.dx[i], 2.)
        self.assertAlmostEqual(np.hypot(samples.draw_dx[i],samples.draw_dy[i]), samples.full_length, places=5)
        f.valid[:] = False
        base = render_bos(self.source, f, show_vectors=True)
        np.testing.assert_array_equal(base, self.source)

    def test_pinned_reference_and_drift_correction_use_the_same_signed_axes_for_both_methods(self):
        rng = np.random.default_rng(23)
        ref = cv2.GaussianBlur(rng.integers(0,256,(164,220),dtype=np.uint8),(3,3),.8)
        current = cv2.warpAffine(ref, np.float32([[1,0,1.25],[0,1,-.75]]), (220,164),
                                 flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT101)
        for method in ('optical_flow', 'cross_correlation'):
            with self.subTest(method=method):
                field = compute_bos(ref,current,(4,5,190,147),method=method,detail=True)
                corrected = remove_camera_translation(field,dict(reliable=True,dx=.5,dy=.25))
                samples = sample_vectors(corrected,2.)
                np.testing.assert_allclose([np.median(samples.dx),np.median(samples.dy)],[.75,-1.],atol=.15)
                np.testing.assert_array_equal(samples.dx, corrected.dx[samples.rows,samples.cols])
                np.testing.assert_allclose(field.dx[samples.rows,samples.cols]-.5, samples.dx)
                self.assertFalse(samples.saturated.any())

    def test_map_and_footage_share_glyph_scale_and_leave_base_and_raw_vectors_unmodified(self):
        f = self.field
        settings = dict(component='direction', palette='color', limit=.4)
        dx, dy = f.dx.copy(), f.dy.copy()
        original = self.source.copy()
        colored = render_bos(self.source, f, **settings)
        on_color = render_bos(self.source, f, show_vectors=True, vector_background='map', **settings)
        on_raw = render_bos(self.source, f, show_vectors=True, vector_background='footage', **settings)
        bypass = render_bos(self.source, f, show_vectors=True, opacity=0., **settings)
        np.testing.assert_array_equal(on_raw,bypass)
        self.assertTrue(np.any(on_color[:185] != colored[:185]))
        self.assertTrue(np.any(on_raw[:185] != self.source[:185]))
        self.assertTrue(np.any(on_color[:185] != on_raw[:185]))
        np.testing.assert_array_equal(dx,f.dx)
        np.testing.assert_array_equal(dy,f.dy)
        np.testing.assert_array_equal(original,self.source)

    def test_scale_key_avoids_stationary_patch_and_densities_order_the_lattice(self):
        f = self.field
        counts = [len(sample_vectors(f,.4,density).rows) for density in ('sparse','balanced','dense')]
        self.assertTrue(counts[0] <= counts[1] <= counts[2])
        sample = sample_vectors(f,.4)
        x,y,w,h = key_rect(320,270,f,sample)
        first = (x,y,w,h)
        second = key_rect(320,270,f,sample, first)
        self.assertNotEqual(first,second)
        self.assertGreaterEqual(second[1], f.roi[1]+f.roi[3])
        self.assertTrue(second[0] < first[0])
