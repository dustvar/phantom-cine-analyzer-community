"""Presentation-time behavior of centered ramps in analyzed BOS footage."""
import math
import unittest

import numpy as np

from bos_sampling import RangeSampling
from bos_studio_model import StudioEdit


class SpeedRampClockTests(unittest.TestCase):
    def test_integrated_linear_and_smooth_speed_up_and_slow_down(self):
        for curve in ('linear', 'smooth'):
            for a, b in ((200, 800), (800, 200)):
                with self.subTest(curve=curve, rates=(a, b)):
                    edit = StudioEdit(3000, a)
                    cut = edit.split_speed(1000)
                    edit.set_speed(cut, b)
                    edit.set_ramp(cut, 2, curve)
                    # Integrals through the midpoint of linear and smoothstep.
                    half = .125 if curve == 'linear' else .09375
                    before = 2*(a*.5 + (b-a)*half)
                    start = (1000-before)/a
                    end = start+2
                    covered = a+b
                    np.testing.assert_allclose(edit.ramp_bounds(cut), (start, end), atol=1e-12, rtol=0)
                    self.assertAlmostEqual(edit.duration, end+(2000-(covered-before))/b)
                    self.assertAlmostEqual(edit.time_at(1000), start+1)
                    for u in (0., .25, .5, .75, 1.):
                        weight = u if curve == 'linear' else u*u*(3-2*u)
                        integral = u*u/2 if curve == 'linear' else u**3-u**4/2
                        elapsed = start+2*u
                        self.assertAlmostEqual(edit.rate_at_time(elapsed), a+(b-a)*weight)
                        target = 1000-before + 2*(a*u+(b-a)*integral)
                        self.assertEqual(edit.frame_at(elapsed), math.floor(target))
                    self.assertAlmostEqual(edit.rate_at(1000), (a+b)/2)

    def test_sampling_and_fades_follow_same_clock_as_movie_and_prefetch(self):
        sampling = RangeSampling(2454, 7)  # last retained interval is shorter than seven frames
        edit = StudioEdit(sampling.count, 200, sampling=sampling)
        cut = edit.split_speed(130)
        edit.set_speed(cut, 800)
        edit.set_ramp(cut, 1.52, 'smooth')
        self.assertEqual([edit.frame_at(edit.time_at(i)) for i in range(edit.count)],
                         list(range(edit.count)))
        self.assertEqual(edit.output_count(60), math.ceil(edit.duration*60))
        self.assertEqual(tuple(edit.output_frames(60)),
                         tuple(edit.frame_at(i/60) for i in range(edit.output_count(60))))
        queue = set(edit.preview_indices(0, edit.count, 60))
        self.assertTrue(all(edit.preview_frame(i, 60) in queue for i in range(edit.count)))
        self.assertIn(edit.count-1, queue)
        self.assertIn(edit.speeds[cut].first, queue)
        edit.set_fades(0, 4., 1.25, layer='vectors')
        levels = list(edit.opacity_bytes('vectors'))
        last = edit.time_at(edit.count-1)
        expected = [round(255*min(1., edit.time_at(i)/4., (last-edit.time_at(i))/1.25))
                    for i in range(edit.count)]
        np.testing.assert_allclose(expected, levels, atol=0)

    def test_neighbors_fit_short_sections_and_move_with_the_cut(self):
        edit = StudioEdit(380, 20)
        first, second = edit.split_speed(100), edit.split_speed(180)
        edit.set_speed(first, 80)
        edit.set_ramp(first, 1.)
        maximum = edit.ramp_limit(second)
        self.assertAlmostEqual(maximum, (80-34.375)/34.375)
        edit.set_ramp(second, maximum+5)
        self.assertAlmostEqual(edit.speeds[second].ramp, maximum)
        self.assertEqual(edit.speeds[first].ramp, 1.)
        old = edit.copy()
        edit.move_speed_cut(second, 104)
        self.assertEqual(old.speeds[second].first, 180)
        self.assertEqual(edit.speeds[second].first, 104)
        self.assertLessEqual(edit.ramp_bounds(first)[1], edit.ramp_bounds(second)[0]+1e-10)
        self.assertTrue(0 < edit.speeds[first].ramp < 1.)
        self.assertTrue(0 < edit.speeds[second].ramp < maximum)
        self.assertEqual(len(set(edit.frame_at(edit.time_at(i)) for i in range(380))), 380)

    def test_a_split_inside_a_ramp_preserves_playback_and_zero_restores_hard_cut(self):
        edit = StudioEdit(3000, 200)
        first = edit.split_speed(1000)
        edit.set_speed(first, 800)
        edit.set_ramp(first, 2., 'linear')
        original = tuple(edit.output_frames(60))
        edit.split_speed(900)
        self.assertEqual(tuple(edit.output_frames(60)), original)
        self.assertEqual(edit.speeds[1].ramp, 0.)
        self.assertEqual(edit.speeds[2].ramp, 2.)
        edit.remove_speed_cut(1)
        self.assertEqual(tuple(edit.output_frames(60)), original)
        edit.set_ramp(1, 0.)
        self.assertEqual(edit.duration, 7.5)
        self.assertEqual(edit.output_count(60), 450)
        self.assertEqual(edit.rate_at_time(5.), 800.)
        self.assertEqual(edit.rate_at_time(5.-1e-9), 200.)
        for value in (-.2, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                edit.set_ramp(1, value)
        with self.assertRaises(ValueError):
            edit.set_ramp(0, .5)
        with self.assertRaises(ValueError):
            edit.set_ramp(1, .5, 'unrecognized')
