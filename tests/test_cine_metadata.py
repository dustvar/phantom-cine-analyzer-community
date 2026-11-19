"""Real binary Cine fixtures exercise SETUP bounds, pixels and timestamp reuse."""
import sys
import struct
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app-source/modules/trackmeasure'))
from cine_metadata import read_metadata, CineHandler


def write_cine(path, length=9344, frames=8, width=32, height=24, start_second=1700000000):
    setup = bytearray(length)
    def field(offset, fmt, *values):
        if offset + struct.calcsize('<' + fmt) <= length:
            struct.pack_into('<' + fmt, setup, offset, *values)
    field(0, 'H', 2000)
    field(140, '2sH', b'ST', length)
    for offset, value in [(743, 31851), (768, 2000), (792, 12001), (896, 8),
                          (1568, 416666), (5736, 255)]:
        field(offset, 'I', value)
    for offset in (852, 856, 6012, 6024):
        field(offset, 'f', 1.0)
    field(7252, '9f', *np.eye(3).ravel())
    field(10128, '256s', b'Phantom Test Camera')
    field(10388, 'f', 1.0)
    field(10400, 'd', 2000.25)
    # Older files have unrelated tagged data where a newer SETUP would continue.
    padding = struct.pack('<IHH', 1608, 7777, 0) + b'Z' * 1600
    times = [(start_second << 32) + round(i / 2000 * 2**32) for i in range(frames)]
    timestamps = struct.pack('<IHH', 8 + frames * 8, 1002, 0) + struct.pack('<' + 'Q' * frames, *times)
    table_start = 84 + length + len(padding) + len(timestamps)
    image_start = table_start + frames * 8
    pixels = [np.full((height, width), (20 + i * 20) % 256, dtype=np.uint8) for i in range(frames)]
    image_size = width * height
    header = struct.pack('<2sHHHiIiIIIIQ', b'CI', 44, 0, 1, -10, frames, -10, frames,
                         44, 84, table_start, times[0])
    bitmap = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 8, 0, image_size, 0, 0, 0, 0)
    offsets = [image_start + i * (image_size + 8) for i in range(frames)]
    path.write_bytes(header + bitmap + setup + padding + timestamps +
                     struct.pack('<' + 'Q' * frames, *offsets) +
                     b''.join(struct.pack('<II', 8, image_size) + image[::-1].tobytes() for image in pixels))
    return pixels


class CineMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'source.cine'

    def test_old_setup_does_not_read_model_and_rate_from_tagged_data(self):
        pixels = write_cine(self.path)
        h = CineHandler()
        self.addCleanup(h.close)
        h.load_cine(self.path)
        self.assertEqual(h.metadata.FrameRate, 2000)
        self.assertEqual(h.metadata.fDecimation, 1)
        self.assertEqual(h.metadata.CameraModel, '')
        self.assertEqual(h.metadata.CameraVersion, 12001)
        self.assertEqual(h.metadata.Serial, 31851)
        self.assertAlmostEqual(h.metadata.ExposureTime, 416.666)
        self.assertEqual(dict(h.print_metadata())['Frame Rate'], '2,000 fps')
        self.assertEqual(dict(h.print_metadata())['Model'], 'Not stored in this Cine')
        np.testing.assert_array_equal(h.get_img(3), pixels[3])
        self.assertAlmostEqual(h.get_timestamp(2)['time_from_start'], .001, places=6)

    def test_new_setup_retains_valid_precise_rate_and_model(self):
        write_cine(self.path, length=11000)
        md = read_metadata(self.path)
        self.assertEqual(md.CameraModel, 'Phantom Test Camera')
        self.assertEqual(md.FrameRate, 2000.25)

    def test_invalid_precise_rate_and_nontext_model_use_safe_fallback(self):
        write_cine(self.path, length=11000)
        with self.path.open('r+b') as stream:
            stream.seek(84 + 10400)
            stream.write(struct.pack('<d', 1.8e205))
            stream.seek(84 + 10128)
            stream.write(b'\xff\x00')
        md = read_metadata(self.path)
        self.assertEqual(md.FrameRate, 2000)
        self.assertEqual(md.CameraModel, '')

    def test_playback_snapshot_reused_only_for_same_file(self):
        write_cine(self.path, frames=8)
        base, worker = CineHandler(), CineHandler()
        self.addCleanup(base.close)
        self.addCleanup(worker.close)
        base.load_cine(self.path)
        snapshot = base.playback_index()
        worker.load_cine(self.path, initial_index=6, index_snapshot=snapshot)
        self.assertIs(worker._image_offsets, base._image_offsets)
        self.assertIsNot(worker._file_handle, base._file_handle)
        self.assertEqual(worker.get_img()[0, 0], 140)
        other = self.path.with_name('replacement.cine')
        write_cine(other, frames=4)
        worker.load_cine(other, initial_index=2, index_snapshot=snapshot)
        self.assertIsNot(worker._image_offsets, base._image_offsets)
        self.assertEqual(worker.metadata.ImageCount, 4)
        self.assertEqual(worker.get_img()[0, 0], 60)

    def test_invalid_setup_bounds_rejected(self):
        write_cine(self.path)
        with self.path.open('r+b') as stream:
            stream.seek(84 + 142)
            stream.write(struct.pack('<H', 65535))
        with self.assertRaisesRegex(ValueError, 'SETUP length'):
            read_metadata(self.path)

    def test_reusing_reader_clears_previous_timestamp_origin(self):
        write_cine(self.path)
        other = self.path.with_name('later.cine')
        write_cine(other, start_second=1700001000)
        h = CineHandler()
        self.addCleanup(h.close)
        h.load_cine(self.path)
        h.get_timestamp(1)
        h.load_cine(other)
        self.assertEqual(h.get_timestamp(0)['time_from_start'], 0)
        h.close()
        self.assertFalse(h.cine_loaded)
        self.assertIsNone(h.img)
