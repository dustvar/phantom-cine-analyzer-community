from concurrent.futures import CancelledError
import unittest

import numpy as np

from event_detection import EventScanCache, sample_count, scan_events


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

    def test_stride_uses_only_the_grid_and_both_endpoints(self):
        first, last, step = -117, 1003, 9
        grid = list(range(first, last+1, step)) + [last]
        calls = []
        reference_calls = []
        def read(i):
            calls.append(i)
            return np.full((5, 6), 50 if i in (grid[-1], grid[-2], grid[6]) else 30, np.uint8)
        scan = scan_events(read, first, last, step=step,
                           begin_sequence=lambda: reference_calls.extend(calls))
        self.assertTrue(set(reference_calls).issubset(grid))
        self.assertEqual(calls[len(reference_calls):], grid)
        self.assertEqual(scan.frame_count, sample_count(first, last, step))
        self.assertEqual(sorted((e.first, e.last) for e in scan.events),
                         [(grid[6], grid[6]), (grid[-2], last)])
        for args, expected in [((0, 100, 5), 21), ((0, 0, 30), 1), ((-8, -6, 21), 2)]:
            self.assertEqual(sample_count(*args), expected)
        for invalid in (0, -3, 1.1, True):
            with self.assertRaisesRegex(ValueError, 'step'):
                scan_events(read, first, last, step=invalid)

    def test_mean_measures_average_and_retains_signed_reason(self):
        calls = []
        def read(i):
            calls.append(i)
            region = np.full((4, 4), 100, np.uint16)
            if i == 50:
                # Spatial change with exactly unchanged mean.
                region[0, 0] -= 50
                region[-1, -1] += 50
            if 70 <= i <= 72:
                region[1, 2] += 64
            if i == 100:
                region[1, 2] -= 96
            return region
        scan = scan_events(read, 0, 150, signal='mean')
        self.assertEqual(calls, list(range(151)))
        self.assertEqual([(h.first, h.last, h.reason) for h in scan.events],
                         [(100, 100, 'Mean darkened'), (70, 72, 'Mean brightened')])
        calls.clear()
        with self.assertRaises(CancelledError):
            scan_events(read, 0, 150, signal='mean', cancelled=lambda: len(calls) >= 21)
        self.assertEqual(calls, list(range(21)))

    def test_cache_reranks_without_reading_and_isolates_measurements(self):
        cache = EventScanCache()
        self.addCleanup(cache.clear)
        source_key = ('camera serial, slot, trigger', (7, 11, 4, 4), 'native')
        def read(i):
            region = np.full((4, 4), 120, np.uint8)
            region[2, 2] += 27 if i == 76 else 0
            return region
        first = scan_events(read, 0, 200, step=2, cache=cache, cache_key=source_key)
        self.assertTrue(cache.contains(source_key, 0, 200, 2))
        reranked = scan_events(lambda i: self.fail('cache hit read footage'), 0, 200, step=2,
                               sensitivity='high', cache=cache, cache_key=source_key,
                               begin_sequence=lambda: self.fail('cache hit initialized camera transfer'))
        fresh = scan_events(read, 0, 200, step=2, sensitivity='high')
        self.assertEqual(reranked, fresh)
        self.assertEqual(first.events[0].peak, 76)
        for args in [(source_key, 0, 199, 2), (source_key, 0, 200, 3),
                     (source_key, 0, 200, 2, 'mean'), ('other', 0, 200, 2)]:
            self.assertFalse(cache.contains(*args))

        with self.assertRaisesRegex(IOError, 'camera'):
            def failed(i):
                raise IOError('camera disconnected')
            scan_events(failed, 0, 200, cache=cache, cache_key='other')
        self.assertTrue(cache.contains(source_key, 0, 200, 2))
        scan_events(read, 0, 200, cache=cache, cache_key='other')
        self.assertFalse(cache.contains(source_key, 0, 200, 2))
        self.assertTrue(cache.contains('other', 0, 200, 1))
        cache.clear()
        self.assertFalse(cache.contains('other', 0, 200, 1))

    def test_cached_mean_supports_new_threshold_and_single_frame(self):
        cache = EventScanCache()
        self.addCleanup(cache.clear)
        def read(i):
            image = np.zeros((4, 4), np.uint8)
            if i == 82:
                # 5 / 16 = 0.3125 gray levels, between High and Balanced here.
                image[1, 2] = 5
            return image
        def no_read(i):
            self.fail('unexpected reread')
        kwargs = dict(cache=cache, cache_key='signed mean', signal='mean')
        self.assertEqual(scan_events(read, 0, 99, **kwargs).events, ())
        high = scan_events(no_read, 0, 99, sensitivity='high', **kwargs)
        self.assertEqual([h.peak for h in high.events], [82])
        one = scan_events(read, 82, 82, **kwargs)
        self.assertEqual(one.events, ())
        self.assertEqual(scan_events(no_read, 82, 82, **kwargs), one)
