"""The exported modules work with arrays and no application imports."""
import ast
import sys
import unittest
from pathlib import Path
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app-source/modules/trackmeasure'))
from edge_tracking import EdgeAssistTracker,EdgeSettings,EdgeAlgorithms


def rigid(frame,angle=0):
    pattern=np.full((51,51),25,np.uint8)
    cv2.rectangle(pattern,(7,7),(40,13),230,-1)
    cv2.rectangle(pattern,(32,7),(40,41),230,-1)
    cv2.circle(pattern,(13,36),6,145,-1)
    rotated=EdgeAlgorithms._rotate_image_expanded(pattern,angle)
    image=np.full((200,260),25,np.uint8)
    center=np.array([90+frame*3,90+frame*2],float)
    h,w=rotated.shape;x=int(center[0])-w//2;y=int(center[1])-h//2
    image[y:y+h,x:x+w]=rotated
    return image,center


class PortableEngineTests(unittest.TestCase):
    def test_only_numpy_opencv_and_standard_library_imports(self):
        for name in ('edge_tracking.py','tumbling_tracker.py'):
            tree=ast.parse((ROOT/'app-source/modules/trackmeasure'/name).read_text())
            modules=[]
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):modules.extend(a.name.split('.')[0] for a in node.names)
                elif isinstance(node,ast.ImportFrom):modules.append(node.module.split('.')[0])
            # Caches may use standard-library containers and content hashes;
            # this boundary guards against PCA/UI or extra third-party imports.
            self.assertLessEqual(set(modules),set(sys.stdlib_module_names)|{'numpy','cv2'})

    def test_edge_translation_rotation_and_separate_point(self):
        tracker=EdgeAssistTracker(EdgeSettings(rotation_range=30,rotation_step=2,point_lock=False))
        image,center=rigid(0)
        offset=np.array([6.,-8.])
        tracker.initialize(image,center+offset,(51,51),region_center=center)
        for frame in range(1,6):
            angle=frame*7;image,center=rigid(frame,angle)
            result=tracker.step(image,frame)
            self.assertTrue(result.accepted,result)
            expected=center+tracker._rotate(offset,angle)
            self.assertLess(np.linalg.norm(np.asarray(result.point)-expected),1.5)
            self.assertAlmostEqual(result.angle,angle,delta=1.5)

    def test_edge_initial_rotation_and_reverse_sequence(self):
        tracker=EdgeAssistTracker(EdgeSettings(rotation_range=25,point_lock=False))
        image,center=rigid(5,45)
        tracker.initialize(image,center,(51,51),angle=45,frame=5)
        for frame in range(4,-1,-1):
            image,center=rigid(frame,frame*9)
            result=tracker.step(image,frame)
            self.assertTrue(result.accepted,result)
            self.assertLess(np.linalg.norm(np.asarray(result.point)-center),1.5)
            self.assertAlmostEqual(result.angle,frame*9,delta=1.5)

    def test_edge_loss_has_no_point_and_does_not_change_reference(self):
        tracker=EdgeAssistTracker();image,center=rigid(0)
        tracker.initialize(image,center,(51,51));reference=tracker.neighbor.copy()
        for frame in range(1,5):
            result=tracker.step(np.full_like(image,25),frame)
            self.assertFalse(result.accepted);self.assertIsNone(result.point)
            np.testing.assert_array_equal(tracker.neighbor,reference)
        with self.assertRaises(ValueError):tracker.step(image,3)

    def test_edge_rejects_invalid_or_featureless_setup(self):
        with self.assertRaises(ValueError):EdgeSettings(confidence=float('nan'))
        tracker=EdgeAssistTracker()
        with self.assertRaises(ValueError):tracker.initialize(np.zeros((100,100)),(50,50),(31,31))
        with self.assertRaises(ValueError):tracker.initialize(rigid(0)[0],(4,4),(51,51))

if __name__=='__main__':unittest.main()
