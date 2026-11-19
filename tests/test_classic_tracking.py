"""Independent displacement and streaming regressions for Classic tracking."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from collections import Counter
from unittest.mock import patch
import numpy as np
from scipy.ndimage import gaussian_filter, fourier_shift

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from autotrackalgorithms import AutoTrackAlgorithms, AutoTrackException
from simplemeas_tools import AutoTrackTool, CLASSIC_TRACKING_METHOD


class Sink:
    def __init__(self): self.values=[]
    def emit(self,*args): self.values.append(args)


def worker_fixture(count=40, start=(96.3,96.2), width=193):
    image=gaussian_filter(np.random.default_rng(92).normal(100,25,(width,width)),.8).astype(np.float32)
    reads=[]
    def get_img(frame):
        reads.append(int(frame))
        return np.roll(image,int(frame)%7,axis=0)
    handler=SimpleNamespace(get_img=get_img,metadata=SimpleNamespace(
        ImWidth=width,ImHeight=width,CFA='MONO',RealBPP=12,ImageCount=count,FirstImageNo=-100))
    image_tools=SimpleNamespace(debayer=lambda image,*args,**kwargs:image)
    vm=SimpleNamespace(cine_handler=handler,image_tools=image_tools,abort_autotrack=False,
        update_progress=Sink(),update_status_text=Sink(),track_complete=Sink(),active_frame=0)
    track=dict(name='Classic target',points=np.array([start]),frames=np.array([0]),scores=np.array(['N/A'],object),
        t_points=np.array([start]),t_frames=np.array([0]),angles=np.zeros(1),t_angles=np.zeros(1),
        tracking_method=CLASSIC_TRACKING_METHOD,search_area_enable=True,search_area=(101,101),
        tpl_rng=(31,31),acceptable_score=.6,subpixel_size='1/10 pix',subpixel_type='cubic',
        update_template_enable=True,tpl_score=.8,template_offset=(0,0),rotation_allowed=False)
    return vm,track,reads


class ClassicTrackingTests(unittest.TestCase):
    def test_known_fractional_shifts_across_textures_without_half_pixel_bias(self):
        for seed in (4,73,701):
            original=gaussian_filter(np.random.default_rng(seed).normal(100,25,(101,101)),.8)
            template=original[35:66,35:66].astype(np.float32)
            for dx,dy in ((0,0),(.2,.3),(.5,-.5),(.8,.9),(5.3,-3.7),(-9.4,7.1)):
                shifted=np.fft.ifftn(fourier_shift(np.fft.fftn(original),(dy,dx))).real.astype(np.float32)
                result=AutoTrackAlgorithms.template_matcher(template,shifted,(150,200),(101,101),10,'cubic')
                np.testing.assert_allclose((result.x_pos,result.y_pos),(150+dx,200+dy),atol=.101)
                self.assertGreater(result.confid_val_ij,.96)

    def test_full_search_expansion_is_not_used_and_all_interpolation_choices_work(self):
        original=gaussian_filter(np.random.default_rng(7).normal(100,25,(201,201)),.8).astype(np.float32)
        template=original[63:94,124:155]
        with patch.object(AutoTrackAlgorithms,'interpolator',side_effect=AssertionError('Whole-search resize')):
            for mode in ('linear','quadratic','cubic','quartic','quintic'):
                result=AutoTrackAlgorithms.template_matcher(template,original,(100,100),(201,201),10,mode)
                np.testing.assert_allclose((result.x_pos,result.y_pos),(139,78),atol=.1)

    def test_flat_and_invalid_templates_are_rejected_and_bad_matches_stay_low(self):
        with self.assertRaises(AutoTrackException):
            AutoTrackAlgorithms.template_matcher(np.ones((15,15)),np.ones((31,31)),(15,15),(31,31),10,'cubic')
        rng=np.random.default_rng(44)
        result=AutoTrackAlgorithms.template_matcher(rng.normal(100,20,(31,31)),rng.normal(100,20,(101,101)),(50,50),(101,101),10,'cubic')
        self.assertLess(result.confid_val_ij,.3)

    def test_subpixel_axis_constraint_rejects_a_stronger_off_axis_target(self):
        rng=np.random.default_rng(38)
        tpl=gaussian_filter(rng.normal(100,30,(15,15)),.7).astype(np.float32)
        source=np.zeros((101,101),np.float32)
        source[53:68,43:58]=tpl+rng.normal(0,1,tpl.shape)
        source[33:48,63:78]=tpl
        constraint=dict(center=(50,50),size=(99,99),angle=0.,roi_size=(15,15),
                        motion_axis='y',anchor_point=(50.3,40),template_offset=(0,0))
        result=AutoTrackAlgorithms.template_matcher(tpl,source,(50,50),(101,101),10,'cubic',
                                                   constraint,template_center_offset=(.3,0))
        self.assertEqual(result.x_pos,50.3)
        self.assertAlmostEqual(result.y_pos,60,delta=.1)
        self.assertGreater(result.confid_val_ij,.95)

    def test_worker_reads_each_frame_once_and_preserves_fractional_seed(self):
        vm,track,reads=worker_fixture(count=45)
        result=AutoTrackTool(vm).track(0,44,track)
        self.assertEqual(reads,list(range(45)))
        self.assertEqual(len(result['frames']),45)
        np.testing.assert_allclose(result['points'][:,0],96.3,atol=.101)
        np.testing.assert_allclose(result['points'][:,1],96.2+np.arange(45)%7,atol=.101)
        self.assertEqual(vm.update_progress.values[-1],(100,))

    def test_worker_cancellation_stops_reads_between_frames(self):
        vm,track,reads=worker_fixture(count=20)
        original=vm.cine_handler.get_img
        def read(frame):
            value=original(frame)
            if frame==3:vm.abort_autotrack=True
            return value
        vm.cine_handler.get_img=read
        AutoTrackTool(vm).track(0,19,track)
        self.assertEqual(reads,[0,1,2,3])
        self.assertLess(len(track['frames']),20)

    def test_reverse_tracking_is_sequential_and_reference_patch_is_reused(self):
        vm,track,reads=worker_fixture(count=20,start=(96.3,101.2))
        track['frames'][:]=19;track['t_frames'][:]=19
        result=AutoTrackTool(vm).track(19,0,track)
        self.assertEqual(reads,list(range(19,-1,-1)))
        self.assertEqual(len(result['frames']),20)
        np.testing.assert_allclose(result['points'][:,0],96.3,atol=.101)
        np.testing.assert_allclose(result['points'][:,1],96.2+np.arange(20)%7,atol=.101)


if __name__=='__main__':unittest.main()
