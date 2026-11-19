import os
import sys
import struct
import tempfile
import unittest
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from test_cine_metadata import write_cine
from video_overlay import OverlayPair,default_overlay,nearest_capture
from cine_metadata import CineHandler
from camera_comparator import display_frame


class VideoOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.a=Path(self.temp.name)/'a.cine';self.b=Path(self.temp.name)/'b.cine'
        write_cine(self.a,frames=8,start_second=1700000000)
        write_cine(self.b,frames=8,start_second=1750000000)
    def pair(self):
        p=OverlayPair([self.a,self.b]);self.addCleanup(p.close);return p
    def test_trigger_alignment_ignores_recording_date(self):
        p=self.pair();s=default_overlay();s['position']=3;r=p.render(s)
        self.assertEqual(r['frames'],[-7,-7]);self.assertAlmostEqual(r['time'],.0015,places=8)
        s['mode']='Difference';self.assertFalse(p.render(s)['image'].any())
    def test_offset_positive_moves_b_later_no_frozen_endpoints(self):
        p=self.pair();s=default_overlay();s['offsets'][1]=2;s['position']=0
        self.assertEqual(p.render(s)['frames'],[-10,None])
        s['position']=3;self.assertEqual(p.render(s)['frames'],[-7,-9])
        s['position']=9;self.assertEqual(p.render(s)['frames'],[None,-3])
    def test_negative_offset_and_custom_zero(self):
        p=self.pair();s=default_overlay();s['offsets'][1]=-2
        self.assertEqual(p.render(s)['frames'],[None,-10])
        s.update(offsets=[0,0],raw_t0=[False,False],t0_frames=[-8,-6],position=4)
        r=p.render(s);self.assertEqual(r['frames'],[-8,-6]);self.assertAlmostEqual(r['time'],0,places=8)
    def test_trigger_fraction_without_epoch_precision_loss(self):
        data=bytearray(self.a.read_bytes())
        # Header trigger TIME64 is at byte 36; set trigger two capture periods later.
        struct.pack_into('<II',data,36,round(.001*2**32),1700000000);self.a.write_bytes(data)
        h=CineHandler();self.addCleanup(h.close);h.load_cine(str(self.a))
        times=h.capture_timestamps_from_trigger()
        self.assertAlmostEqual(times[0],-.001,places=8);self.assertAlmostEqual(times[2],0,places=8)
    def test_different_capture_rates_use_event_time(self):
        p=self.pair();p.times[1]=np.arange(8)*.001;p.periods[1]=.001
        s=default_overlay();s['position']=4
        r=p.render(s);self.assertEqual(r['frames'],[-6,-8])
    def test_no_capture_across_timestamp_gap(self):
        self.assertIsNone(nearest_capture(np.array([0.,.001,.004]),.002,.001))
    def test_no_spatial_stretch_and_zero_opacity(self):
        p=self.pair();s=default_overlay();s['opacity']=[0,0]
        r=p.render(s);self.assertEqual(r['image'].shape,(24,32,3));self.assertFalse(r['image'].any())
        s['opacity']=[100,0];s['x']=10000
        self.assertTrue(p.render(s)['image'].any())
    def test_invalid_custom_zero_rejected(self):
        p=self.pair();s=default_overlay();s['raw_t0'][0]=False;s['t0_frames'][0]=999
        with self.assertRaises(ValueError):p.render(s)

    def test_cached_timeline_updates_for_alignment_and_source_changes(self):
        p=self.pair();s=default_overlay();initial=p.timeline(s)
        s['position']=4;s['opacity']=[75,25]
        self.assertIs(p.timeline(s),initial)
        s['offsets'][1]=2
        shifted=p.timeline(s);self.assertIsNot(shifted,initial)
        np.testing.assert_allclose(shifted[0][1],p.times[1]+2*p.periods[1])
        s['raw_t0'][1]=False;s['t0_frames'][1]=-8
        zeroed=p.timeline(s)
        self.assertAlmostEqual(zeroed[0][1][2],2*p.periods[1])
        p.times[1]=p.times[1]+.00001
        self.assertIsNot(p.timeline(s),zeroed)
        s['t0_frames'][1]=999
        with self.assertRaises(ValueError):p.timeline(s)

    def test_render_pixels_match_full_rgb_formula_without_modifying_cached_frames(self):
        rng=np.random.default_rng(582)
        for dimensions in ((2,2),(3,3),(2,3)):
            for sizes in (((23,31),(23,31)),((21,29),(17,19))):
                frames=[rng.random(shape if dim==2 else (*shape,3)).astype(np.float32)
                        for dim,shape in zip(dimensions,sizes)]
                for frame in frames:frame.setflags(write=False)
                p=OverlayPair.__new__(OverlayPair)
                p.sessions=[SimpleNamespace(metadata=SimpleNamespace(ImHeight=f.shape[0],ImWidth=f.shape[1],FirstImageNo=0,ImageCount=2,FrameRate=1000),frame=lambda i,f=f:f) for f in frames]
                p.times=[np.array([0.,.001]),np.array([0.,.001])];p.periods=[.001,.001];p.notes=['a','b']
                height=max(f.shape[0] for f in frames);width=max(f.shape[1] for f in frames)
                for x,y in ((0,0),(5,-3),(100,100)):
                    for mode in ('Blend','Difference'):
                        for opacity in ((50,50),(90,80),(0,0)):
                            s=default_overlay();s.update(x=x,y=y,mode=mode,opacity=opacity)
                            expected=[]
                            for n,f in enumerate(frames):
                                if f.ndim==2:f=np.repeat(f[:,:,None],3,axis=2)
                                h,w=f.shape[:2];xx=(width-w)//2+(x if n else 0);yy=(height-h)//2+(y if n else 0)
                                layer=np.zeros((height,width,3),np.float32)
                                left,top,right,bottom=max(0,xx),max(0,yy),min(width,xx+w),min(height,yy+h)
                                if right>left and bottom>top:layer[top:bottom,left:right]=f[top-yy:bottom-yy,left-xx:right-xx]
                                expected.append(layer)
                            if mode=='Difference':pixels=abs(expected[0]-expected[1])
                            else:
                                a,b=[float(v)/100 for v in opacity];pixels=(a*expected[0]+b*expected[1])/max(1,a+b)
                            with self.subTest(dimensions=dimensions,sizes=sizes,x=x,y=y,mode=mode,opacity=opacity):
                                np.testing.assert_array_equal(p.render(s)['image'],display_frame(pixels))
