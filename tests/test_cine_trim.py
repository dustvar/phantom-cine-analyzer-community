"""Native clips preserve recorded frame identities, tags and pixel records."""
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app-source/modules/trackmeasure'))
from cine_metadata import CineHandler
from cine_trim import trim_cine
from test_cine_metadata import write_cine
from video_export import ExportCancelled


def write_trim_fixture(path, frames=8, width=64, height=48):
    """Small v1 Cine with original frame IDs, per-frame tags and variable annotations."""
    pixels = write_cine(path, frames=frames, width=width, height=height)
    base = bytearray(path.read_bytes())
    setup_end = 84 + struct.unpack_from('<H', base, 84 + 142)[0]
    # Remove the metadata compatibility fixture's deliberately unknown tag 7777.
    # Supply valid, distinguishable timestamps, exposures and SMPTE timecodes.
    base = base[:setup_end]
    trigger = struct.unpack_from('<Q', base, 36)[0]
    def tag(kind, fmt, values, reserved):
        data = struct.pack('<' + fmt * frames, *values)
        return struct.pack('<IHH', 8 + len(data), kind, reserved) + data
    tags = (tag(1002, 'Q', [trigger - (frames-i)*23457 + i for i in range(frames)], 1) +
            tag(1003, 'I', range(3401, 3401 + frames), 1) +
            tag(1007, 'Q', [0x12345000 + i for i in range(frames)], 0))
    base.extend(tags)
    struct.pack_into('<iI', base, 8, -30, 40)
    struct.pack_into('<I', base, 32, len(base))
    table = len(base)
    base.extend(bytes(frames*8))
    for i, p in enumerate(pixels):
        struct.pack_into('<Q', base, table + i * 8, len(base))
        annotation = bytes([50+i%16]) * (i % 5)
        base.extend(struct.pack('<I', 8 + len(annotation)) + annotation +
                    struct.pack('<I', p.nbytes) + p[::-1].tobytes())
        base.extend(b'PAD')  # Not part of the image record.
    path.write_bytes(base)
    return pixels


def records_and_tags(path):
    data = path.read_bytes()
    table = struct.unpack_from('<I', data, 32)[0]
    count = struct.unpack_from('<I', data, 20)[0]
    offsets = struct.unpack_from('<' + 'Q' * count, data, table)
    records = []
    for offset in offsets:
        annotation, = struct.unpack_from('<I', data, offset)
        length, = struct.unpack_from('<I', data, offset + annotation - 4)
        records.append(data[offset:offset+annotation+length])
    pos = 84 + struct.unpack_from('<H', data, 84 + 142)[0]
    tags = {}
    while pos < table:
        length, kind, res = struct.unpack_from('<IHH', data, pos)
        tags[kind] = (res, data[pos+8:pos+length])
        pos += length
    return records, tags


class CineTrimTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.source = self.folder/'full.cine'
        self.dest = self.folder/'cut.cine'
        self.pixels = write_trim_fixture(self.source)

    def test_trim_reopens_with_exact_pixels_timing_metadata_and_annotations(self):
        events = []
        self.assertEqual(trim_cine(self.source, self.dest, 2, 5, progress=lambda *v: events.append(v)), self.dest)
        original, before = records_and_tags(self.source)
        actual, after = records_and_tags(self.dest)
        self.assertEqual(actual, original[2:6])
        for kind, stride in [(1002,8), (1003,4), (1007,8)]:
            self.assertEqual(after[kind], (before[kind][0], before[kind][1][2*stride:6*stride]))
        self.assertEqual(events[-1], (4, 4))
        source, cut = CineHandler(), CineHandler()
        self.addCleanup(source.close)
        self.addCleanup(cut.close)
        source.load_cine(self.source)
        cut.load_cine(self.dest)
        self.assertEqual(cut.metadata.FirstMovieImage, -30)
        self.assertEqual(cut.metadata.TotalImageCount, 40)
        self.assertEqual(cut.metadata.FirstImageNo, -8)
        self.assertEqual(cut.metadata.ImageCount, 4)
        self.assertEqual(cut.metadata.TriggerTime, source.metadata.TriggerTime)
        np.testing.assert_allclose(cut.capture_timestamps_from_trigger(), source.capture_timestamps_from_trigger()[2:6],
                                   rtol=0, atol=1e-15)
        for i in range(4):
            np.testing.assert_array_equal(cut.get_img(i), self.pixels[i+2])
        # A one-frame trim of an already trimmed Cine has the original frame identity.
        single = self.folder/'one.cine'
        trim_cine(self.dest, single, 3, 3)
        self.assertEqual(struct.unpack_from('<iI', single.read_bytes(), 16), (-5, 1))
        self.assertEqual(records_and_tags(single)[0], original[5:6])

    def test_cancel_invalid_range_and_unsupported_tags_never_replace_destination(self):
        self.dest.write_bytes(b'existing')
        cancelled = threading.Event()
        with self.assertRaises(ExportCancelled):
            trim_cine(self.source, self.dest, 0, 5, cancel=cancelled,
                      progress=lambda *_: cancelled.set())
        for bounds in [(-1, 2), (3, 2), (0, 8)]:
            with self.assertRaises(ValueError):
                trim_cine(self.source, self.dest, *bounds)
        write_cine(self.source)  # deliberately contains undocumented tag 7777
        with self.assertRaisesRegex(ValueError, '7777'):
            trim_cine(self.source, self.dest, 0, 2)
        self.assertEqual(self.dest.read_bytes(), b'existing')
        self.assertFalse(list(self.folder.glob('.pca-cine-*')))

    def test_original_cannot_be_overwritten_even_via_a_hard_link(self):
        os.link(self.source, self.dest)
        before = self.source.read_bytes()
        for alias in [self.source, self.dest]:
            with self.assertRaisesRegex(ValueError, 'original'):
                trim_cine(self.source, alias, 0, 1)
        self.assertEqual(self.source.read_bytes(), before)

    def test_short_source_record_does_not_publish_partial_output(self):
        data = self.source.read_bytes()
        self.source.write_bytes(data[:-50])
        with self.assertRaisesRegex(ValueError, 'frame size'):
            trim_cine(self.source, self.dest, 4, 7)
        self.assertFalse(self.dest.exists())

    @unittest.skipUnless(os.name == "posix", "Requires sparse files without allocating the unused 8 GiB")
    def test_sparse_source_uses_64_bit_frame_offsets(self):
        data = bytearray(self.source.read_bytes())
        table = struct.unpack_from('<I', data, 32)[0]
        original = struct.unpack_from('<Q', data, table+7*8)[0]
        far = 2**33+345
        struct.pack_into('<Q', data, table+7*8, far)
        with self.source.open('wb') as s:
            s.write(data[:original])
            s.seek(far)
            s.write(data[original:])
        trim_cine(self.source, self.dest, 7, 7)
        reader = CineHandler()
        self.addCleanup(reader.close)
        reader.load_cine(self.dest)
        np.testing.assert_array_equal(reader.get_img(0), self.pixels[-1])
        self.assertLess(self.dest.stat().st_size, 20000)
