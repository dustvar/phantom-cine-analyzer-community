from concurrent.futures import CancelledError
import unittest

import numpy as np

from event_detection import scan_events


class EventDetectionTests(unittest.TestCase):
    @staticmethod
    def image(frame, shape=(65, 81)):
        y, x = np.indices(shape)
        texture = 1200 + 185*((x % 13) < 6) + 220*((y % 9) < 3)
        noise = np.random.default_rng(901+frame).normal(0, 3, shape)
        return np.rint(texture+noise).astype(np.uint16)

    def test_four_frame_bubble_is_localized_at_native_indices(self):
        first, last = -7600, -7276
        on, off = -7481, -7478
        calls = []
        updates = []
        def read(index):
            calls.append(index)
            image = self.image(index-first)
            if on <= index <= off:
                # A small sign-changing edge distorts six native pixels while
                # leaving total ROI brightness unchanged.
                delta = (20, 36, 25, 19)[index-on]
                image[31:34, 51] -= delta
                image[31:34, 52] += delta
            return image
        scan = scan_events(read, first, last, progress=lambda *progress: updates.append(progress))
        self.assertEqual(scan.frame_count, last-first+1)
        self.assertEqual(scan.total_events, 1)
        hit, = scan.events
        self.assertEqual((hit.first, hit.peak, hit.last), (on, on+1, off))
        self.assertEqual(hit.reason, 'Localized pixel change')
        self.assertEqual(calls[-scan.frame_count:], list(range(first, last+1)))
        self.assertEqual(updates[0], (0, scan.frame_count))
        self.assertEqual(updates[-1], (scan.frame_count, scan.frame_count))

    def test_events_at_both_ends_and_ranking(self):
        last = 93
        def read(i):
            image = self.image(i)
            if i < 4:
                image[1:6, 0:5] -= 39
            if i == last:
                image += 40
            return image
        scan = scan_events(read, 0, last)
        self.assertEqual([(e.first, e.last) for e in scan.events], [(93, 93), (0, 3)])
        self.assertEqual(scan.events[0].reason, 'Region brightened')
        self.assertGreater(scan.events[0].score, scan.events[1].score)

    def test_stationary_noise_and_static_sensor_defect(self):
        def read(i):
            image = self.image(i, shape=(24, 28))
            image[3, 15] = 4095
            return image
        scan = scan_events(read, 0, 2800)
        self.assertEqual(scan.events, ())
        self.assertEqual(scan.total_events, 0)

    def test_groups_across_read_batches_and_reports_independent_unit_scale(self):
        base = np.zeros((8, 16), np.uint8)
        base[:, :3] = 120
        def read(i):
            image = base.copy()
            if 1022 <= i <= 1026:
                image[3:6, 6:10] += 45
            if i == 1030:
                image[3:6, 6:10] += 25
            return image
        for func in (read, lambda i: read(i).astype(np.float32)/255.):
            scan = scan_events(func, 0, 1100, sensitivity='low')
            self.assertEqual([(e.first, e.peak, e.last) for e in scan.events],
                             [(1022, 1022, 1026), (1030, 1030, 1030)])

    def test_cancels_during_scan_and_reports_source_errors(self):
        n = 0
        def read(i):
            nonlocal n
            n += 1
            return np.ones((2, 5), np.uint16)
        with self.assertRaises(CancelledError):
            scan_events(read, 0, 200, cancelled=lambda: n >= 75)
        self.assertEqual(n, 75)
        with self.assertRaisesRegex(ValueError, 'size'):
            scan_events(lambda i: np.ones((6, 7 if i == 16 else 6)), 0, 99)
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            scan_events(lambda i: np.full((5, 4), np.nan), 0, 15)
        with self.assertRaisesRegex(ValueError, 'sensitivity'):
            scan_events(read, 0, 200, sensitivity='other')

    def test_spilled_scores_keep_strongest_events_and_total_count(self):
        # Exercise disk spill, chunk boundaries, and the explicit ranked-results cap.
        still = np.zeros((2, 3), np.uint8)
        bright = np.full_like(still, 40)
        scan = scan_events(lambda i: bright if i % 31 == 0 else still, 0, 44_999)
        self.assertEqual(scan.total_events, 1+(44_999//31))
        self.assertEqual(len(scan.events), 1000)
        self.assertEqual((scan.events[0].peak, scan.events[-1].peak), (0, 999*31))
