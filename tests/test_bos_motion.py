"""Stationary-anchor correction must remove only measured camera translation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from bos_frames import BOSFrames, default_bos_settings, validate_bos_settings
from bos_processing import camera_translation, compute_bos, remove_camera_translation
from bos_export import export_bos_clip
from test_cine_metadata import write_cine


def texture():
    image = np.random.default_rng(144).integers(5, 250, (240, 400), dtype=np.uint8)
    return cv2.GaussianBlur(image, (3, 3), .7)


def write_frames(path, frames):
    count, height, width = frames.shape
    write_cine(path, frames=count, width=width, height=height)
    with path.open('r+b') as out:
        for i, frame in enumerate(frames):
            out.seek(-(count-i)*(width*height+8)+8, os.SEEK_END)
            out.write(frame[::-1].tobytes())


class BOSMotionTests(unittest.TestCase):
    def test_anchor_removes_camera_translation_and_preserves_local_flow_and_raw_data(self):
        reference = texture()
        y, x = np.indices(reference.shape, dtype=np.float32)
        camera = (.75, -.5)
        front = 1.35*np.exp(-.5*((x-305)/13)**2)
        current = cv2.remap(reference, x-camera[0]-front, y-camera[1], cv2.INTER_CUBIC,
                            borderMode=cv2.BORDER_REFLECT_101)
        raw = compute_bos(reference, current, (0,0,400,240), window=17, detail=True)
        original = raw.dx.copy()
        drift = camera_translation(reference, current, (10,120,110,110))
        self.assertTrue(drift['reliable'])
        np.testing.assert_allclose([drift['dx'],drift['dy']],camera,atol=.06)
        corrected = remove_camera_translation(raw, drift)
        quiet = raw.x < 180
        front_cols = abs(raw.x-305) < 5
        self.assertLess(np.nanmedian(abs(corrected.dx[:,quiet])), .04)
        self.assertLess(np.nanmedian(abs(corrected.dy[:,quiet])), .04)
        self.assertGreater(np.nanmedian(corrected.dx[:,front_cols]), 1.1)
        np.testing.assert_array_equal(raw.dx,original)
        np.testing.assert_array_equal(raw.valid,corrected.valid)
        np.testing.assert_array_equal(raw.confidence,corrected.confidence)
        np.testing.assert_array_equal(np.isnan(raw.dx),np.isnan(corrected.dx))

    def test_unreliable_or_distorted_anchor_is_rejected(self):
        reference=texture()
        flat=np.full_like(reference,128)
        result=camera_translation(reference,flat,(10,120,110,110))
        self.assertFalse(result['reliable'])
        self.assertIsNone(result['dx'])
        with self.assertRaises(ValueError):
            remove_camera_translation(compute_bos(reference,reference,(0,0,400,240)),result)
        y,x=np.indices(reference.shape,dtype=np.float32)
        distorted=cv2.remap(reference,x-2*np.sin(y/12),y,cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT_101)
        self.assertFalse(camera_translation(reference,distorted,(10,120,110,110))['reliable'])

    def test_settings_are_opt_in_and_validate_anchor_geometry(self):
        state=validate_bos_settings({'path':'old.cine'})
        self.assertFalse(state['motion_compensation'])
        self.assertIsNone(state['anchor_roi'])
        for change in ({'motion_compensation':1},{'anchor_roi':[0,0,79,80]},{'anchor_roi':[-1,0,100,100]}):
            with self.assertRaises(ValueError):validate_bos_settings(dict(default_bos_settings(),**change))
        self.assertEqual(validate_bos_settings(dict(state,anchor_roi=(1,2,80,80)))['anchor_roi'],[1,2,80,80])

    def test_session_never_reuses_a_good_shift_for_an_obscured_anchor(self):
        reference=texture();moving=np.roll(reference,1,axis=1);obscured=moving.copy();obscured[120:230,10:120]=128
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'anchor.cine';write_frames(source,np.array([reference,moving,obscured]))
            session=BOSFrames(source);self.addCleanup(session.close)
            state=dict(default_bos_settings(),reference=0,position=1,roi=[135,0,265,240],
                       anchor_roi=[10,120,110,110],motion_compensation=True)
            good=session.render(state)
            self.assertTrue(good['drift']['reliable'])
            self.assertLess(abs(np.nanmedian(good['display_field'].dx)),.03)
            bad=session.render(dict(state,position=2))
            self.assertFalse(bad['drift']['reliable'])
            self.assertIsNone(bad['display_field'])
            self.assertGreater(bad['field'].valid.mean(),.9)
            np.testing.assert_array_equal(bad['image'],session.base)
            self.assertIn('showing original',bad['warning'])
            off=session.render(dict(state,position=2,motion_compensation=False))
            self.assertIsNone(off['drift'])
            self.assertIs(off['display_field'],off['field'])
            self.assertEqual(off['warning'],'')

    def test_anchor_cache_reuses_style_changes_but_not_frames_references_or_regions(self):
        frames=np.array([np.roll(texture(),i,axis=1) for i in range(4)])
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'cache.cine';write_frames(source,frames)
            session=BOSFrames(source);self.addCleanup(session.close)
            state=dict(default_bos_settings(),reference=0,position=1,roi=[135,0,265,240],
                       anchor_roi=[10,120,110,110],motion_compensation=True)
            import bos_frames
            with patch('bos_frames.camera_translation',wraps=bos_frames.camera_translation) as estimate:
                original=session.render(state)
                session.render(dict(state,limit=2,palette='color'))
                self.assertEqual(estimate.call_count,1)
                session.render(dict(state,position=2));self.assertEqual(estimate.call_count,2)
                session.render(dict(state,position=2,anchor_roi=[20,120,110,110]));self.assertEqual(estimate.call_count,3)
                session.render(dict(state,position=2,reference=1));self.assertEqual(estimate.call_count,4)
                missing=session.render(dict(state,anchor_roi=None))
                self.assertFalse(missing['drift']['reliable'])
                self.assertIsNone(missing['display_field'])
                self.assertIsNotNone(original['display_field'])

    def test_export_reports_original_only_frames_without_changing_return_contract(self):
        reference=texture();moving=np.roll(reference,1,axis=1);obscured=moving.copy();obscured[120:230,10:120]=128
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'export.cine';write_frames(source,np.array([reference,moving,obscured]))
            state=dict(default_bos_settings(),path=str(source),reference=0,position=1,roi=[135,0,265,240],
                       anchor_roi=[10,120,110,110],motion_compensation=True)
            report={};dest=Path(td)/'checked.mp4'
            result=export_bos_clip(state,dest,1,2,24,report=report)
            self.assertEqual(result,str(dest))
            self.assertEqual(report['warning_frame_count'],1)
            self.assertEqual(sum(report['warning_counts'].values()),1)
            self.assertEqual(len(report['warning_frames']),1)
            video=cv2.VideoCapture(str(dest))
            try:self.assertEqual(int(video.get(cv2.CAP_PROP_FRAME_COUNT)),2)
            finally:video.release()


if __name__=='__main__':unittest.main()
