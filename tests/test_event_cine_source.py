"""Verify sensor coordinates and precision match PCA's full Cine decoder."""
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from event_cine_source import EventCineSource
from test_cine_metadata import write_cine


def packed_fixture(path, compression, bits):
    width, height, count = 32, 24, 7
    write_cine(path, frames=count, width=width, height=height)
    data = bytearray(path.read_bytes())
    offset_table, = struct.unpack_from('<I', data, 32)
    del data[offset_table+count*8:]
    rng = np.random.default_rng(270)
    encoded_bits = {256: 10, 1024: 12}.get(compression, bits)
    nbytes = width*height*encoded_bits//8
    struct.pack_into('<HII', data, 58, encoded_bits, compression, nbytes)
    struct.pack_into('<I', data, 84+896, bits)
    struct.pack_into('<i', data, 84+5736, 2**bits-1)
    for i in range(count):
        struct.pack_into('<Q', data, offset_table+8*i, len(data))
        pixels = rng.integers(0, 256, nbytes, dtype=np.uint8)
        data.extend(struct.pack('<II',8,nbytes) + pixels.tobytes())
    path.write_bytes(data)


class EventCineSourceTests(unittest.TestCase):
    def test_roi_parity_through_packed_groups_and_bottom_up_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'test.cine'
            for compression,bits in ((0,8),(0,16),(256,12),(1024,12)):
                with self.subTest(compression=compression,bits=bits):
                    packed_fixture(path, compression, bits)
                    src = EventCineSource(path)
                    try:
                        self.assertEqual((src.first_frame, src.count), (-10,7))
                        for index in [0,4,6]:
                            full = src.reader.get_img(index)
                            self.assertEqual(src.preview(index).dtype, np.uint8)
                            for x,y,w,h in [(3,2,7,9),(31,23,1,1),(0,0,32,24)]:
                                patch = src.read_roi(index, (x,y,w,h))
                                self.assertEqual(patch.dtype, full.dtype)
                                np.testing.assert_array_equal(patch, full[y:y+h,x:x+w])
                    finally:
                        src.close()
                    self.assertEqual(patch.shape, (24,32))
