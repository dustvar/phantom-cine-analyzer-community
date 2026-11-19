"""Display cleanup must preserve raw BOS vectors, genuine fronts and occlusions."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from bos_display import overlay_vectors


class DisplayNoiseTests(unittest.TestCase):
    def test_reduces_scatter_without_mixing_across_a_mask_or_at_a_strong_front(self):
        rng = np.random.default_rng(6814)
        dx, dy = rng.normal(0,.025,(2,55,80)).astype('float32')
        valid = np.ones(dx.shape, bool)
        valid[:,20] = False
        dx[:,53:56] += .60
        dx[~valid] = dy[~valid] = np.nan
        field = SimpleNamespace(dx=dx.copy(),dy=dy.copy(),valid=valid,step=4)
        shown_x, shown_y = overlay_vectors(field, True)
        self.assertLess(shown_x[8:-8,27:42].std(), .7*dx[8:-8,27:42].std())
        np.testing.assert_array_equal(shown_x[:,53:56], dx[:,53:56])
        np.testing.assert_array_equal(shown_x[:,16:25], dx[:,16:25])
        np.testing.assert_array_equal(np.isnan(shown_y),~valid)
        np.testing.assert_array_equal(field.dx,dx)
        np.testing.assert_array_equal(field.dy,dy)
        self.assertIs(overlay_vectors(field, False)[0], field.dx)
    def test_flat_grid_preserves_valid_zero_and_missing_measurements(self):
        dx = np.zeros((12,12), dtype=np.float32)
        dx[0,0] = np.nan
        field = SimpleNamespace(dx=dx,dy=dx.copy(),valid=np.isfinite(dx),step=15)
        x,y = overlay_vectors(field)
        np.testing.assert_array_equal(x,dx)
        np.testing.assert_array_equal(y,dx)
