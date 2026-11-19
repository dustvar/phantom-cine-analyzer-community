import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

from bos_camera_labels import camera_lines
from bos_frames import default_bos_settings
from bos_preview import CachedBOSClip, prepare_bos_preview
from cine_metadata import CineHandler
from test_cine_metadata import write_cine
from test_cine_trim import write_trim_fixture


class CameraCaptionTests(unittest.TestCase):
    def test_sampled_exposures_stay_with_native_frames_offline(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'capture.cine'
            write_trim_fixture(source, width=104, height=80)
            state = dict(default_bos_settings(), path=str(source), reference=0, roi=[0, 0, 104, 80])
            clip = prepare_bos_preview(state, 1, 7, sample_step=4, root=root/'cache')
            self.addCleanup(clip.close)
            source.unlink()
            clip = CachedBOSClip(clip.directory)
            np.testing.assert_array_equal(clip.exposure_ticks, [3402, 3406, 3408])
            self.assertEqual([clip.source_frame(i) for i in range(3)], [-9, -5, -3])
            self.assertEqual(camera_lines(clip, {'cine_frame'}, 1), ('Cine frame -5',))
            for i, ticks in enumerate(clip.exposure_ticks):
                us = int(ticks)*1e6/2**32
                self.assertEqual(camera_lines(clip, {'resolution', 'exposure'}, i),
                                 ('104 × 80 px', f'Exposure {us:.3f} µs'))

    def test_old_combined_tag_aligns_to_subset_and_bounds_payload(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'old.cine'
            write_cine(path, frames=6)
            data = bytearray(path.read_bytes())
            pos = 84 + struct.unpack_from('<H', data, 84+142)[0]
            pad, = struct.unpack_from('<I', data, pos)
            count, offset = 25, 10
            struct.pack_into('<iI', data, 8, -10-offset, count)
            size = 8 + 12*count
            struct.pack_into('<IHH', data, pos, size, 1001, 0)
            struct.pack_into('<'+'I'*count, data, pos+8+8*count, *range(100,100+count))
            struct.pack_into('<IHH', data, pos+size, pad-size, 7777, 0)
            path.write_bytes(data)
            reader = CineHandler()
            try:
                reader.load_cine(path)
                ticks = reader.exposure_ticks(1,4,2)
            finally:
                reader.close()
            np.testing.assert_array_equal(ticks, [111,113,114])
            # A short exposure array must not borrow bytes from the following tag.
            struct.pack_into('<IHH', data, pos, 12, 1003, 0)
            struct.pack_into('<IHH', data, pos+12, pad-12, 7777, 0)
            path.write_bytes(data)
            try:
                reader.load_cine(path)
                self.assertIsNone(reader.exposure_ticks(0,5))
            finally:
                reader.close()

    def test_camera_name_resolution_and_setup_exposure_survive_source_removal(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'camera.cine'
            write_cine(source, length=11000, width=104, height=80)
            clip = prepare_bos_preview(dict(default_bos_settings(), path=str(source), reference=0,
                                            roi=[0,0,104,80]), 0, 0, root=root/'cache')
            self.addCleanup(clip.close)
            source.unlink()
            self.assertEqual(camera_lines(clip, {'camera_model','resolution','exposure'}, 0),
                             ('Phantom Test Camera','104 × 80 px','Exposure setting 416.666 µs'))
            legacy = SimpleNamespace(manifest={}, width=104, height=80)
            self.assertEqual(camera_lines(legacy, {'camera_model','exposure'}, 0),
                             ('Camera model unavailable','Exposure unavailable'))
            self.assertEqual(camera_lines(legacy, set(), 0), ())
