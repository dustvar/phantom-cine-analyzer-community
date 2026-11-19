import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from cine_metadata import CineHandler
from multi_cine_timing import CaptureClock
from test_cine_metadata import write_cine


class CaptureTimingTests(unittest.TestCase):
    def clock(self, times, *, count=None, fps=1000, first=0, decimation=1):
        md=SimpleNamespace(ImageCount=count if count is not None else len(times),FrameRate=fps,
                           FirstImageNo=first,fDecimation=decimation)
        return CaptureClock.from_handler(SimpleNamespace(metadata=md,capture_timestamps_from_trigger=lambda:times))

    def test_different_rates_match_event_time_and_reject_gaps(self):
        a=self.clock(np.arange(-4,9)*.0005)
        b=self.clock(np.arange(-2,5)*.001)
        self.assertEqual(b.index_at(a.time_at(8)),4)
        gap=self.clock(np.array([0,.001,.002,.005,.006,.007]))
        self.assertIsNone(gap.index_at(.003));self.assertIsNone(gap.index_at(.004))
        sparse=self.clock(np.array([0.,.005]),fps=1000)
        self.assertIsNone(sparse.index_at(.002));self.assertEqual(sparse.index_at(.005),1)
        self.assertIsNone(b.index_at(-.003));self.assertIsNone(b.index_at(.005))
        self.assertEqual([b.index_at(a.time_at(i)) for i in range(a.count-1,-1,-1)],
                         list(reversed([b.index_at(a.time_at(i)) for i in range(a.count)])))

    def test_real_cines_align_by_trigger_not_recording_epoch_or_nominal_rate(self):
        with tempfile.TemporaryDirectory() as folder:
            clocks=[]
            for n,epoch in enumerate((1700000000,1750000000)):
                path=Path(folder)/f'{n}.cine';write_cine(path,frames=8,start_second=epoch)
                data=bytearray(path.read_bytes());struct.pack_into('<II',data,36,round(.001*2**32),epoch);path.write_bytes(data)
                handler=CineHandler()
                try:
                    handler.load_cine(str(path));clocks.append(CaptureClock.from_handler(handler))
                finally:handler.close()
            np.testing.assert_array_equal(clocks[0].times,clocks[1].times)
            self.assertAlmostEqual(clocks[0].time_at(2),0,places=8)
            self.assertEqual(clocks[1].index_at(clocks[0].time_at(5)),5)
            self.assertFalse(clocks[0].estimated)

    def test_missing_tags_respect_decimation_and_first_sensor_frame(self):
        clock=self.clock(None,count=4,fps=1000,first=-10,decimation=2)
        np.testing.assert_allclose(clock.times,[-.010,-.008,-.006,-.004])
        self.assertAlmostEqual(clock.period,.002);self.assertTrue(clock.estimated)
        self.assertIn('Estimated',clock.note)
        self.assertEqual(clock.index_at(-.007),1)

    def test_invalid_timing_is_not_silently_replaced(self):
        for times in ([0,0],[0,-1],[0,np.nan],[[0,1]]):
            with self.subTest(times=times),self.assertRaises(ValueError):self.clock(times,count=2)
        with self.assertRaises(ValueError):self.clock([0,1],count=3)
        with self.assertRaises(ValueError):self.clock(None,count=2,fps=0)
        with self.assertRaises(ValueError):self.clock(None,count=2,decimation=float('inf'))
        clock=self.clock([0,.001])
        for frame in (-1,2,.5,True):
            with self.assertRaises(ValueError):clock.time_at(frame)
        for time in (float('nan'),float('inf')):self.assertIsNone(clock.index_at(time))

    def test_single_frame_and_immutable_owned_clock(self):
        source=np.array([0.,.001]);clock=self.clock(source);source[0]=-99
        self.assertEqual(clock.time_at(0),0)
        with self.assertRaises(ValueError):clock.times[0]=42
        one=self.clock([.25],fps=0)
        self.assertEqual(one.index_at(.25),0);self.assertIsNone(one.index_at(.25001))


if __name__=='__main__':unittest.main()
