"""Measurement and geometry regressions for the guided tracking workspace."""
import sys,unittest
from pathlib import Path
import numpy as np
import cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from tracking_kinematics import calculate_motion
from tracking_geometry import contain_region,corners,rotate,search_pose,search_bounds
from edge_tracking import EdgeAlgorithms,candidate_search_mask
from autotrackalgorithms import AutoTrackAlgorithms

class GuidedGeometryTests(unittest.TestCase):
    def test_rotated_outer_box_expands_to_contain_unchanged_inner_region(self):
        roi=(120,130);size=(55,23)
        outer=contain_region((160,100),(11,11),47,roi,size,-25)
        local=rotate(corners(roi,size,-25)-[160,100],-47)
        self.assertTrue(np.all(np.max(np.abs(local),axis=0)<(np.array(outer)-1)/2))
        self.assertEqual(roi,(120,130));self.assertEqual(size,(55,23))

    def test_search_geometry_follows_relative_fixture_rotation(self):
        track=dict(search_offset=(20,0),search_angle=15,template_angle=10,search_follow_rotation=True)
        center,angle,delta=search_pose(track,(100,100),100)
        np.testing.assert_allclose(center,[100,80]);self.assertEqual(angle,-75)
        tl,br,center,size=search_bounds((20,20),(101,31),45,(100,80))
        np.testing.assert_array_equal(np.array(center)-np.array(size)//2,tl)
        self.assertTrue(all(a>=0 for a in tl));self.assertLessEqual(br[0],100);self.assertLessEqual(br[1],80)

    def test_oriented_search_excludes_a_stronger_distractor(self):
        rng=np.random.default_rng(22);tpl=rng.integers(10,230,(15,15),dtype=np.uint8)
        source=np.zeros((100,100),np.uint8)
        source[13:28,73:88]=tpl
        source[53:68,53:68]=np.clip(tpl.astype(float)+rng.normal(0,3,tpl.shape),0,255)
        constraint=dict(center=(50,50),size=(85,29),angle=45,roi_size=(15,15),motion_axis='xy')
        for matcher,options in [(EdgeAlgorithms.hybrid_pattern_matcher,dict(rotation_range=0)),
                                (AutoTrackAlgorithms.template_matcher,dict(sub_pixel=1,sub_pixel_type='linear'))]:
            result=matcher(tpl,source,(50,50),(100,100),search_constraint=constraint,**options)
            np.testing.assert_allclose([result.x_pos,result.y_pos],[60,60],atol=.8)
            self.assertTrue(candidate_search_mask(result.x_pos,result.y_pos,0,constraint))

    def test_vertical_constraint_is_used_during_matching(self):
        rng=np.random.default_rng(18);tpl=rng.integers(10,230,(15,15),dtype=np.uint8)
        source=np.zeros((100,100),np.uint8);source[43:58,63:78]=tpl
        source[53:68,43:58]=np.clip(tpl.astype(float)+rng.normal(0,3,tpl.shape),0,255)
        constraint=dict(center=(50,50),size=(99,99),angle=0,roi_size=(15,15),motion_axis='y',anchor_point=(50,40),template_offset=(0,0))
        result=EdgeAlgorithms.hybrid_pattern_matcher(tpl,source,(50,50),(100,100),rotation_range=0,search_constraint=constraint)
        self.assertEqual(result.x_pos,50);self.assertAlmostEqual(result.y_pos,60,delta=.5)

class LocalMotionTests(unittest.TestCase):
    def track(self,points,mode='local_polynomial',window=21):
        return dict(frames=np.arange(len(points)),points=np.asarray(points),motion_filter=dict(mode=mode,window=window))

    def test_noisy_oscillation_recovers_signed_acceleration_without_moving_points(self):
        t=np.arange(500)/10000;omega=2*np.pi*100
        y=20*np.sin(omega*t);points=np.c_[np.full(len(t),50),y]
        measured=points+np.random.default_rng(40).normal(0,.25,points.shape)
        track=self.track(measured);original=track['points'].copy()
        fit=calculate_motion(track,{},t);track['motion_filter']['mode']='raw';raw=calculate_motion(track,{},t)
        true=-20*omega**2*np.sin(omega*fit['_acceleration_ts'])
        interior=slice(20,-20)
        error=lambda values:np.sqrt(np.mean((values[interior]-true[interior])**2))
        self.assertLess(error(fit['Y-Acceleration']),error(raw['Y-Acceleration'])*.3)
        self.assertLess(error(fit['Y-Acceleration'])/np.max(np.abs(true)),.07)
        np.testing.assert_array_equal(track['points'],original)
        np.testing.assert_array_equal(fit['Y-Displacement'],raw['Y-Displacement'])

    def test_irregular_capture_times_and_reference_do_not_change_acceleration(self):
        rng=np.random.default_rng(10);t=np.cumsum(rng.uniform(.0008,.0012,150))
        track=self.track(np.c_[5+3*t+4*t*t,20-5*t+9*t*t]);track['reference_pixel']=(200,400)
        result=calculate_motion(track,{},t)
        np.testing.assert_allclose(result['X-Acceleration'],8,atol=1e-7)
        np.testing.assert_allclose(result['Y-Acceleration'],18,atol=1e-7)

    def test_fits_do_not_bridge_missing_positions_or_large_timestamp_gaps(self):
        t=np.arange(60)*.001;points=np.c_[t,t*t];points[25]=np.nan
        track=self.track(points);result=calculate_motion(track,{},t)
        self.assertTrue(np.all(np.isnan(result['Acceleration'][17:26])))
        self.assertTrue(np.all(np.isfinite(result['Acceleration'][:10])))
        t[30:]+=.1;result=calculate_motion(track,{},t)
        self.assertTrue(np.all(np.isnan(result['Acceleration'][22:30])))

if __name__=='__main__':unittest.main()
