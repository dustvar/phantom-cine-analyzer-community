import unittest

import numpy as np

from camera_monitor import LINEAR_CURVE, MonitorCurve, MonitorLevels, active_curve, needs_shadow_precision


class CameraMonitorTests(unittest.TestCase):
    def test_editing_auto_preserves_current_mapping_and_sensor_data(self):
        ramp = np.linspace(0, 4000, 420, dtype=np.uint16)[None, :]
        original = ramp.copy()
        monitor = MonitorLevels()
        automatic = monitor.render(ramp, 'auto', now=0)
        base = active_curve('auto', monitor.gain, LINEAR_CURVE)
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
        output = levels.render(pixels, 'auto', now=0.)
        self.assertGreater(len(np.unique(output)), 4)
        self.assertEqual(output[0, 0], 0)
        self.assertEqual(levels.signal, 'Low light')
        np.testing.assert_array_equal(levels.render(pixels, 'linear', now=.1), pixels >> 8)
        np.testing.assert_array_equal(pixels, before)

    def test_ordinary_images_monitor_range_and_saturation(self):
        levels = MonitorLevels()
        ramp = np.arange(256, dtype='u1')[None, :]
        standard = levels.render(ramp, 'standard', now=0.)[0]
        self.assertEqual((int(standard[0]), int(standard[-1])), (0, 255))
        self.assertTrue(np.all(np.diff(standard.astype(int)) >= 0))
        self.assertEqual(int(standard[64]), 137)
        np.testing.assert_array_equal(levels.render(ramp, 'linear'), ramp)
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

    def test_gain_changes_smoothly_after_small_light_change(self):
        levels = MonitorLevels()
        pixels = np.full((32, 32), 10, dtype='u1')
        levels.render(pixels, now=10.)
        old = levels.gain
        levels.render(pixels + 35, now=10.1)
        self.assertEqual(levels.gain, old)
        levels.render(pixels + 35, now=10.2)
        self.assertLess(levels.gain, old)
        self.assertGreater(levels.gain, 4.)
