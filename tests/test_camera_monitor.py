import unittest

import numpy as np

from camera_monitor import LINEAR_CURVE, MonitorCurve, MonitorLevels, auto_curve, needs_shadow_precision


class CameraMonitorTests(unittest.TestCase):
    def test_editing_auto_preserves_current_mapping_and_sensor_data(self):
        ramp = np.linspace(0, 4000, 420, dtype=np.uint16)[None, :]
        original = ramp.copy()
        monitor = MonitorLevels()
        base = auto_curve(ramp)
        automatic = monitor.render(ramp, 'auto', now=0, curve=base)
        x = .14
        anchored = MonitorCurve(base.base_gain, ((0., 0.), (x, float(base.values(x))), (1., 1.)))
        np.testing.assert_array_equal(automatic, monitor.render(ramp, 'custom', now=1, curve=anchored))
        darker = MonitorCurve(base.base_gain, ((0., 0.), (x, .12), (1., 1.)))
        result = monitor.render(ramp, 'custom', now=2, curve=darker)
        self.assertLess(np.mean(result), np.mean(automatic))
        self.assertTrue(np.all(np.diff(result.astype(int)) >= 0))
        np.testing.assert_array_equal(ramp, original)

    def test_dark_sensor_detail_survives_display_and_linear_mode(self):
        pixels = np.array([[0, 16, 32, 48, 64, 128, 255]], dtype='<u2')
        before = pixels.copy()
        levels = MonitorLevels()
        output = levels.render(pixels, 'auto', now=0., curve=auto_curve(pixels))
        self.assertGreater(len(np.unique(output)), 4)
        self.assertEqual(output[0, 0], 0)
        self.assertEqual(levels.signal, 'Low light')
        np.testing.assert_array_equal(levels.render(pixels, now=.1), pixels >> 8)
        np.testing.assert_array_equal(pixels, before)

    def test_ordinary_images_monitor_range_and_saturation(self):
        levels = MonitorLevels()
        ramp = np.arange(256, dtype='u1')[None, :]
        standard = levels.render(ramp, 'standard', now=0.)[0]
        self.assertEqual((int(standard[0]), int(standard[-1])), (0, 255))
        self.assertTrue(np.all(np.diff(standard.astype(int)) >= 0))
        self.assertEqual(int(standard[64]), 137)
        np.testing.assert_array_equal(levels.render(ramp), ramp)
        levels.render(np.full((20, 20), 65024, dtype='uint16'))
        self.assertEqual(levels.signal, 'Highlights near white')

    def test_monitor_precision_switches_only_in_shadows(self):
        dim = np.full((60, 80), 9, dtype='u1')
        ordinary = np.full((60, 80), 100, dtype='u1')
        between = np.full((60, 80), 28 * 256, dtype='<u2')
        self.assertTrue(needs_shadow_precision(dim, False))
        self.assertTrue(needs_shadow_precision(between, True))
        self.assertFalse(needs_shadow_precision(between, False))
        self.assertFalse(needs_shadow_precision(ordinary, True))

    def test_auto_holds_across_lighting_and_depth_changes_until_metered_again(self):
        levels = MonitorLevels()
        dark = np.full((32, 32), 10, dtype='u1')
        bright = np.full_like(dark, 180)
        bright[0, 0] = dark[0, 0]
        held = auto_curve(dark)
        initial = levels.render(dark, 'auto', now=0., curve=held)[0, 0]
        after = levels.render(bright, 'auto', now=5., curve=held)[0, 0]
        self.assertEqual(after, initial)
        deep = bright.astype('uint16') * 257
        after = levels.render(deep, 'auto', now=10., curve=held)[0, 0]
        self.assertAlmostEqual(int(after), int(initial), delta=1)
        new_curve = auto_curve(deep)
        self.assertEqual(new_curve, auto_curve(bright))
        self.assertLess(levels.render(bright, 'auto', curve=new_curve)[0, 0], initial)

    def test_auto_handles_flat_frames_and_isolated_extremes(self):
        for dtype in (np.uint8, np.uint16):
            full = np.iinfo(dtype).max
            for level in (0, full):
                flat = np.full((60, 80), level, dtype=dtype)
                self.assertEqual(auto_curve(flat), LINEAR_CURVE)
            dim = np.full((60, 80), full // 32, dtype=dtype)
            original_curve = auto_curve(dim)
            dim[0, :2] = (0, full)
            self.assertEqual(auto_curve(dim), original_curve)
            values = original_curve.values(np.linspace(0., 1., 4096))
            np.testing.assert_allclose(values[[0, -1]], [0., 1.])
            self.assertTrue(np.all(np.diff(values) >= 0))

    def test_small_lit_subject_on_black_is_metered_across_five_percent(self):
        subject = np.linspace(5, 32, 1600, dtype=np.uint8).reshape(40, 40)
        for dtype in (np.uint8, np.uint16):
            scale = np.iinfo(dtype).max // 255
            image = np.zeros((200, 200), dtype=dtype)
            image[80:120, 80:120] = subject.astype(dtype) * scale
            one_subject = auto_curve(image)
            result = MonitorLevels().render(image, 'auto', curve=one_subject)
            self.assertGreater(result.max(), 100)
            self.assertEqual(result[0, 0], 0)
            image[:40, :40] = image[80:120, 80:120]
            self.assertEqual(auto_curve(image), one_subject)

    def test_sparse_hot_pixels_do_not_drive_auto(self):
        for shape, defect_count in (((200, 200), 10), ((1000, 1000), 64)):
            image = np.zeros(shape, dtype=np.uint8)
            image.flat[:defect_count] = 25
            self.assertEqual(auto_curve(image), LINEAR_CURVE)
