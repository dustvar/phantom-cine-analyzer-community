"""Averaging a stationary background must leave the event and display untouched."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from bos_frames import BOSFrames, default_bos_settings
from test_cine_metadata import write_cine
from bos_processing import compute_bos
import bos_processing


class BOSReferenceTests(unittest.TestCase):
    def test_prepared_reference_matches_stateless_results_and_invalidates_by_settings(self):
        rng = np.random.default_rng(112)
        reference = cv2.GaussianBlur(rng.integers(0,256,(120,180),dtype=np.uint8),(3,3),.6)
        current = cv2.warpAffine(reference,np.float32([[1,0,.4],[0,1,-.3]]),(180,120),borderMode=cv2.BORDER_REFLECT)
        cache = {}
        def check(ref, roi, method, window=17):
            options=dict(window=window,search=2,detail=True,method=method)
            expected=compute_bos(ref,current,roi,**options)
            actual=compute_bos(ref,current,roi,**options,_reference_cache=cache)
            for key in ('dx','dy','valid','confidence','reason'):
                np.testing.assert_array_equal(getattr(actual,key),getattr(expected,key))
            self.assertGreater(np.count_nonzero(actual.valid),100)
        for method in ('optical_flow','cross_correlation'):
            check(reference,(0,0,180,120),method)
            prepared=cache['normalized']
            with patch('bos_processing._gray_patch',wraps=bos_processing._gray_patch) as normalize:
                compute_bos(reference,current,(0,0,180,120),window=17,search=2,detail=True,method=method,_reference_cache=cache)
                self.assertEqual(normalize.call_count,1) # Only the changing image.
            self.assertIs(cache['normalized'],prepared)
            check(reference,(10,10,160,100),method)
            self.assertIsNot(cache['normalized'],prepared)
            check(reference,(10,10,160,100),method,window=21)
            check(reference.copy(),(10,10,160,100),method,window=21)

    def test_fixed_mean_and_cache_do_not_mix_event_frames_into_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'pulse.cine'
            pixels = write_cine(source, frames=10, width=80, height=64)
            session = BOSFrames(source)
            self.addCleanup(session.close)
            settings = dict(default_bos_settings(), position=7, reference=1,
                            reference_count=4, roi=[0,0,80,64])
            with patch.object(session.reader, 'get_img', wraps=session.reader.get_img) as read:
                result = session.render(settings)
                np.testing.assert_allclose(session.ref_pixels, np.mean(pixels[1:5], axis=0))
                np.testing.assert_array_equal(session.cur_pixels, pixels[7])
                ref = session.ref_pixels
                read.reset_mock()
                session.render(dict(settings, position=8))
                self.assertEqual(read.call_count, 1)
                self.assertIs(session.ref_pixels, ref)
                session.render(dict(settings, reference_count=1))
                np.testing.assert_array_equal(session.ref_pixels, pixels[1])
                replacement = session.render(settings)
                self.assertIsNot(replacement['field'], result['field'])
                self.assertEqual(replacement['image'].shape, result['image'].shape)

    def test_eof_uses_only_remaining_frames_even_when_pin_is_current(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'end.cine'
            pixels = write_cine(source, frames=6, width=80, height=64)
            session = BOSFrames(source)
            self.addCleanup(session.close)
            settings = dict(default_bos_settings(), position=4, reference=4, reference_count=8)
            session.render(settings)
            np.testing.assert_allclose(session.ref_pixels, np.mean(pixels[4:6], axis=0))
            np.testing.assert_array_equal(session.cur_pixels, pixels[4])
            session.render(dict(settings, reference=5))
            np.testing.assert_array_equal(session.ref_pixels, pixels[5])
