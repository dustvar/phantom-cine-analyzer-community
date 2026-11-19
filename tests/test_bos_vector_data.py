import csv
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from bos_processing import BOSField, remove_camera_translation
from bos_vector_data import save_vector_csv


class VectorCSVTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'measurements.csv'
        grid = np.array([[.25, -.5], [np.nan, .0]], dtype=np.float32)
        field = BOSField((10, 20, 8, 8), np.array([12, 16]), np.array([22, 26]), grid,
                         grid.copy(), np.isfinite(grid), np.full(grid.shape, .9),
                         4, np.zeros(grid.shape, dtype=np.uint8))
        stable = remove_camera_translation(field, dict(reliable=True, dx=.125, dy=.25))
        arrows = SimpleNamespace(rows=np.array([0, 0]), cols=np.array([0, 1]),
                                 saturated=np.array([False, True]))
        self.result = dict(field=field, display_field=stable, vector_samples=arrows,
                           first=-15, index=4, count=6, reference=2, time_us=-12.34)
        self.settings = dict(path=str(self.path.with_suffix('.cine')), position=4,
                             reference_count=8, method='optical_flow', motion_compensation=True)

    def test_native_coordinates_numeric_units_raw_and_stabilized(self):
        self.assertEqual(save_vector_csv(self.path, self.result, self.settings), 3)
        with self.path.open(newline='') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([(r['x_px'], r['y_px']) for r in rows],
                         [('12', '22'), ('16', '22'), ('16', '26')])
        row = rows[0]
        self.assertEqual((row['cine_frame'], row['reference_first_frame'], row['reference_last_frame']),
                         ('-11', '-13', '-10'))
        self.assertEqual(float(row['time_from_trigger_us']), -12.34)
        self.assertEqual(tuple(float(row[k]) for k in ('raw_dx_px','dx_px','dy_px','magnitude_px')),
                         (.25, .125, 0., .125))
        self.assertEqual(rows[1]['arrow_length_capped'], '1')
        self.assertLess(float(rows[1]['dx_px']), 0.)
        self.assertEqual(rows[2]['selected_for_arrow'], '0')

    def test_stale_or_unreliable_field_and_write_failure_preserve_previous_export(self):
        self.path.write_text('earlier measurement')
        for bad in (dict(self.result, index=0), dict(self.result, display_field=None)):
            with self.assertRaises(ValueError):
                save_vector_csv(self.path, bad, self.settings)
        with patch('bos_vector_data.os.replace', side_effect=OSError('Disk unavailable')):
            with self.assertRaises(OSError):
                save_vector_csv(self.path, self.result, self.settings)
        self.assertEqual(self.path.read_text(), 'earlier measurement')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
