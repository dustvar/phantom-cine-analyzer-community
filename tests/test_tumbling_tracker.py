"""Ground-truth segmentation and trajectory checks, independent of PCA/Qt."""
import sys
import unittest
from pathlib import Path
import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from tumbling_tracker import AdaptiveSilhouetteTracker, TrackerSettings


def scene(frame, textured=True, bright=False, missing=False):
    rng=np.random.default_rng(frame+21)
    y,x=np.mgrid[:180,:260]
    if textured:
        image=np.stack([55+16*np.sin(x/8+frame*.15),110+30*np.cos(y/10),70+10*np.sin((x+y)/5)],axis=2)
        image=np.clip(image+rng.normal(0,4,image.shape),0,255).astype(np.uint8)
    else:
        image=np.full((180,260,3),25 if bright else 225,np.uint8)
    mask=np.zeros((180,260),np.uint8)
    center=(60+4*frame,88+frame)
    if not missing:
        # Changing projection, in-plane rotation, and internal surface texture.
        cv2.ellipse(mask,center,(int(17+5*np.sin(frame*.5)),int(11+3*np.cos(frame*.3))),frame*17,0,360,1,-1)
        if textured:
            colors=np.stack([200+12*np.sin(x/4+frame*.3),35+10*np.cos(y/3),170+10*np.sin(y/4)],axis=2)
            image[mask>0]=colors.astype(np.uint8)[mask>0]
        else:image[mask>0]=220 if bright else 30
    return image,mask


class TumblingTrackerTests(unittest.TestCase):
    def test_textured_changing_background_and_tumbling_outline(self):
        tracker=AdaptiveSilhouetteTracker()
        image,mask=scene(0)
        first=tracker.initialize(image,(31,65,58,46),(60,88),0)
        self.assertTrue(first.accepted)
        for frame in range(1,18):
            image,mask=scene(frame)
            result=tracker.step(image,frame)
            self.assertTrue(result.accepted,(frame,result.reason,result.confidence))
            yy,xx=np.nonzero(mask)
            self.assertLess(np.linalg.norm(np.asarray(result.centroid)-[xx.mean(),yy.mean()]),1.5)
            self.assertGreater(result.area,100)

    def test_silhouette_dark_bright_and_high_bit_depth(self):
        for bright in (False,True):
            for high_bit in (False,True):
                with self.subTest(bright=bright,high_bit=high_bit):
                    tracker=AdaptiveSilhouetteTracker(TrackerSettings(mode='bright' if bright else 'dark'))
                    image,_=scene(0,False,bright)
                    if high_bit:image=image.astype(np.uint16)*200
                    tracker.initialize(image,(31,65,58,46),(60,88),0)
                    for frame in range(1,8):
                        image,mask=scene(frame,False,bright)
                        if high_bit:image=image.astype(np.uint16)*200
                        result=tracker.step(image,frame)
                        self.assertTrue(result.accepted,result.reason)
                        yy,xx=np.nonzero(mask)
                        self.assertLess(np.linalg.norm(np.asarray(result.centroid)-[xx.mean(),yy.mean()]),1.)

    def test_loss_never_invents_coordinates_or_updates_appearance(self):
        tracker=AdaptiveSilhouetteTracker()
        tracker.initialize(scene(0)[0],(31,65,58,46),(60,88),0)
        hist=tracker.hist.copy();center=tracker.center.copy()
        for frame in (1,2,3,4):
            result=tracker.step(scene(frame,missing=True)[0],frame)
            self.assertFalse(result.accepted)
            self.assertIsNone(result.centroid)
            np.testing.assert_array_equal(tracker.hist,hist)
            np.testing.assert_array_equal(tracker.center,center)
        self.assertTrue(tracker.stopped)

    def test_reverse_tracking_uses_independent_state(self):
        tracker=AdaptiveSilhouetteTracker()
        tracker.initialize(scene(10)[0],(71,75,58,46),(100,98),10)
        for frame in range(9,0,-1):
            result=tracker.step(scene(frame)[0],frame)
            self.assertTrue(result.accepted,(frame,result.reason))
        with self.assertRaises(ValueError):tracker.step(scene(2)[0],2)

    def test_user_confidence_gate_and_frozen_appearance_reference(self):
        strict=AdaptiveSilhouetteTracker(TrackerSettings(confidence=1.0))
        strict.initialize(scene(0)[0],(31,65,58,46),(60,88),0)
        result=strict.step(scene(1)[0],1)
        self.assertFalse(result.accepted);self.assertIsNone(result.centroid)
        frozen=AdaptiveSilhouetteTracker(TrackerSettings(adaptation=0.0))
        frozen.initialize(scene(0)[0],(31,65,58,46),(60,88),0)
        original=frozen.hist.copy()
        for frame in range(1,5):self.assertTrue(frozen.step(scene(frame)[0],frame).accepted)
        np.testing.assert_array_equal(frozen.hist,original)

    def test_motion_bound_rejects_implausible_jump(self):
        tracker=AdaptiveSilhouetteTracker(TrackerSettings(max_motion=1,growth=15))
        tracker.initialize(scene(0)[0],(31,65,58,46),(60,88),0)
        result=tracker.step(scene(8)[0],1)
        self.assertFalse(result.accepted);self.assertIsNone(result.centroid)

    def test_clipped_setup_and_invalid_inputs(self):
        with self.assertRaises(ValueError):TrackerSettings(confidence=float('nan'))
        with self.assertRaises(ValueError):TrackerSettings(max_motion=-1)
        tracker=AdaptiveSilhouetteTracker()
        with self.assertRaises(ValueError):tracker.initialize(scene(0)[0],(50,80,20,18),(60,88))
        with self.assertRaises(ValueError):tracker.initialize(np.zeros((50,50)),(10,10,20,20))

    def test_display_contours_retain_holes_and_centroid_uses_foreground_only(self):
        image=np.full((130,160),220,np.uint8)
        cv2.circle(image,(75,65),22,30,-1)
        cv2.circle(image,(81,65),8,220,-1)
        tracker=AdaptiveSilhouetteTracker(TrackerSettings(mode='dark',cleanup=0))
        result=tracker.initialize(image,(45,35,61,61),(60,65))
        yy,xx=np.nonzero(image==30)
        np.testing.assert_allclose(result.centroid,[xx.mean(),yy.mean()],atol=.01)
        self.assertEqual(len(result.record()['contours']),2)

    def test_records_are_compact_and_do_not_claim_rotation_or_mass(self):
        tracker=AdaptiveSilhouetteTracker()
        record=tracker.initialize(scene(0)[0],(31,65,58,46),(60,88)).record()
        self.assertIn('contours',record)
        self.assertNotIn('mask',record)
        self.assertNotIn('angle',record)


if __name__=='__main__':unittest.main()
