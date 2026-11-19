import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from tracking_kinematics import calculate_motion


class MotionTests(unittest.TestCase):
    def track(self, points, **options):
        return dict(points=np.asarray(points, float), frames=np.arange(len(points)),
                    origin_frame=None, relative_to=None, **options)

    def test_crossing_reference_does_not_create_false_speed_dip(self):
        times = np.arange(41)*.001
        points = np.column_stack((np.full(41, 200), 400+1000*(times-.02)))
        track = self.track(points, reference_pixel=(200, 400))
        data = calculate_motion(track, {}, times, 4)
        np.testing.assert_allclose(data['Speed'], 1000, rtol=1e-12)
        np.testing.assert_allclose(data['Y-Speed'], 1000, rtol=1e-12)
        self.assertLess(data['Y-Displacement'][0], 0)
        self.assertEqual(data['Y-Displacement'][20], 0)
        self.assertGreater(data['Y-Displacement'][-1], 0)
        # The former abs-before-difference calculation cancels at the crossing.
        folded = np.abs(points-np.array([200, 400]))
        old_speed = np.linalg.norm(folded[4:]-folded[:-4], axis=1)/.004
        self.assertLess(old_speed[18], 1e-8)

    def test_sinusoidal_motion_peaks_at_midstroke_in_each_cycle(self):
        times = np.linspace(0, 2, 2001)
        points = np.column_stack((np.full(len(times), 200), 400+50*np.sin(2*np.pi*times)))
        track = self.track(points)
        track['origin_frame'] = 0
        data = calculate_motion(track, {}, times, 4)
        for crossing in (.5, 1., 1.5):
            i = np.argmin(abs(data['_velocity_ts']-crossing))
            self.assertGreater(data['Speed'][i], 310)
        for endpoint in (.25, .75, 1.25, 1.75):
            i = np.argmin(abs(data['_velocity_ts']-endpoint))
            self.assertLess(data['Speed'][i], .01)

    def test_frame_and_fixed_reference_leave_velocity_unchanged(self):
        times = np.arange(12, dtype=float)*.1
        track = self.track(np.column_stack((5-3*times, 8+4*times)))
        initial = calculate_motion(track, {}, times, 2)
        track['origin_frame'] = 6
        frame = calculate_motion(track, {}, times, 2)
        np.testing.assert_array_equal([frame['X-Displacement'][6], frame['Y-Displacement'][6]], [0, 0])
        track['reference_pixel'] = (200, 400)
        fixed = calculate_motion(track, {}, times, 2)
        for data in (initial, frame, fixed):
            np.testing.assert_allclose(data['Speed'], 5)
            np.testing.assert_allclose(data['X-Speed'], -3)
            np.testing.assert_allclose(data['Y-Speed'], 4)

    def test_acceleration_uses_derivative_midpoint_times_and_single_division(self):
        times = np.array([0, .02, .05, .09, .14, .20, .27, .35, .44])
        track = self.track(np.column_stack((3*times**2, 4*times**2)))
        data = calculate_motion(track, {}, times, 2)
        np.testing.assert_allclose(data['X-Acceleration'], 6)
        np.testing.assert_allclose(data['Y-Acceleration'], 8)
        np.testing.assert_allclose(data['Acceleration'], 10)

    def test_missing_reference_samples_and_duplicate_timestamps_are_unavailable(self):
        track = self.track([[0, 0], [1, 0], [2, 0], [3, 0]])
        track['relative_to'] = 2
        other = dict(frames=np.array([0, 2, 3]), points=np.array([[0, 0], [0, 0], [0, 0]]))
        data = calculate_motion(track, {2: other}, np.arange(4), 1)
        self.assertTrue(np.isnan(data['X-Displacement'][1]))
        self.assertTrue(np.isnan(data['Speed'][:2]).all())
        track['relative_to'] = None
        data = calculate_motion(track, {}, np.array([0., 1., 1., 2.]), 1)
        self.assertTrue(np.isnan(data['Speed'][1]))

    def test_short_tracks_have_empty_derivatives(self):
        for count in range(1, 5):
            data = calculate_motion(self.track([[1, 2]]*count), {}, np.arange(count), 4)
            self.assertEqual(data['Speed'].size, 0)
            self.assertEqual(data['Acceleration'].size, 0)


if __name__ == '__main__': unittest.main()
