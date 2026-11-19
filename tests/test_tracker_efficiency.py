"""Optimized work must retain the established image samples and decisions."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from collections import Counter
import cv2,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from edge_tracking import EdgeAlgorithms,EdgeMatchCache
from tumbling_tracker import AdaptiveSilhouetteTracker,TrackerSettings
from tumbling_pca_adapter import run_tumbling_track
from simplemeas_tools import AutoTrackTool
from test_classic_tracking import Sink
from test_tumbling_tracker import scene


class TrackerEfficiencyTests(unittest.TestCase):
    def test_small_oriented_crop_equals_full_frame_warp_at_subpixel_centers(self):
        rng=np.random.default_rng(94)
        for dtype in (np.uint8,np.float32):
            image=rng.uniform(0,255,(240,320)).astype(dtype)
            for center,size in (((160.3,120.1),(31,31)),((4.1,5.6),(24,36)),((316.6,235.7),(61,19))):
                for angle in (.1,2.,37.7,90.,-162.2):
                    matrix=cv2.getRotationMatrix2D(center,angle,1.)
                    full=cv2.warpAffine(image,matrix,(320,240),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_REFLECT_101)
                    expected=cv2.getRectSubPix(full,size,center)
                    actual=EdgeAlgorithms.extract_oriented_patch(image,center,size,angle)
                    np.testing.assert_array_equal(actual,expected)

    def test_edge_template_cache_is_exact_bounded_and_invalidated_by_pixels(self):
        rng=np.random.default_rng(19);image=rng.integers(0,255,(81,81),dtype=np.uint8);tpl=image[25:56,25:56].copy()
        cache=EdgeMatchCache(max_bytes=1_000_000,max_entries=15)
        options=dict(rotation_range=4,rotation_step=2)
        expected=EdgeAlgorithms.hybrid_pattern_matcher(tpl,image,(40,40),(81,81),**options)
        for _ in range(2):
            actual=EdgeAlgorithms.hybrid_pattern_matcher(tpl,image,(40,40),(81,81),template_cache=cache,**options)
            self.assertEqual((actual.x_pos,actual.y_pos,actual.angle_deg,actual.confid_val_ij),
                             (expected.x_pos,expected.y_pos,expected.angle_deg,expected.confid_val_ij))
            np.testing.assert_array_equal(actual.norm_xcorr_map,expected.norm_xcorr_map)
            self.assertLessEqual(cache.bytes,cache.max_bytes);self.assertLessEqual(len(cache.items),cache.max_entries)
        tpl[:]=image[15:46,35:66]
        changed=EdgeAlgorithms.hybrid_pattern_matcher(tpl,image,(40,40),(81,81),template_cache=cache,**options)
        self.assertNotEqual((changed.x_pos,changed.y_pos),(expected.x_pos,expected.y_pos))
        uncached=EdgeAlgorithms.hybrid_pattern_matcher(tpl,image,(40,40),(81,81),**options)
        self.assertEqual(changed.confid_val_ij,uncached.confid_val_ij)

    def test_tumbling_cropped_feature_search_matches_full_frame_coordinates(self):
        small,_=scene(0)
        for mono in (False,True):
            image=np.full((480,640,3),120,np.uint8);image[150:330,200:460]=small
            if mono:image=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
            tracker=AdaptiveSilhouetteTracker()
            cv2.setRNGSeed(67);tracker.initialize(image,(231,215,58,46),(260,238),0)
            expected=cv2.goodFeaturesToTrack(tracker.gray,80,.01,4,mask=tracker.mask,blockSize=5)
            tracker._prediction(tracker.gray,1)
            np.testing.assert_array_equal(tracker._feature_points,expected)
            self.assertEqual(tracker._image(image)[0].ndim,2 if mono else 3)
            with patch('cv2.goodFeaturesToTrack',side_effect=AssertionError('Repeated unchanged feature extraction')):
                tracker._prediction(tracker.gray,2)

    def test_tumbling_adapter_reads_anchor_once_and_keeps_both_directions_independent(self):
        images=[scene(i)[0] for i in range(12)];reads=[]
        def read(i):reads.append(i);return images[i]
        vm=SimpleNamespace(cine_handler=SimpleNamespace(get_img=read,metadata=SimpleNamespace(
                CFA='RGB',RealBPP=8,FirstImageNo=-10,ImageCount=12)),
            image_tools=SimpleNamespace(debayer=lambda image,*args,**kwargs:image),abort_autotrack=False,
            update_progress=Sink(),update_status_text=Sink())
        track=dict(anchor_frame=5,tumbling=TrackerSettings().to_dict(),tumbling_seed=(80,93),
                   tpl_rng=(61,61),template_offset=(0,0),start=-10,end=1,name='Tumbling',
                   t_points=np.array([[80,93]],float))
        result=run_tumbling_track(AutoTrackTool(vm),track)
        self.assertEqual(reads,[5,6,7,8,9,10,11,4,3,2,1,0])
        self.assertEqual(result['frames'].tolist(),list(range(12)))
        for frame,point in zip(result['frames'],result['points']):
            yy,xx=np.nonzero(scene(frame)[1]);self.assertLess(np.linalg.norm(point-[xx.mean(),yy.mean()]),1.5)


if __name__=='__main__':unittest.main()
