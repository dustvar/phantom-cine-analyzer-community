"""Bounds-checked Cine metadata and the existing memory-mapped pixel reader.

Offsets are relative to SETUP, not the file. The stable fields follow Vision
Research's Cine File Format, Appendix 4. Later optional fields use the layout
supported by pyphantom-cine 1.1.1 and are read only if SETUP.Length includes them.
"""
import datetime
import math
import mmap
import os
import struct
import sys

if sys.platform == 'darwin':
    import fcntl

import numpy as np
from cine_render import RENDER_DESCRIPTION
from cinereader.cinereader import MetaData, unpacking_the_image
from pyphantom_cine.cine_handler import CineHandler as BaseCineHandler


def capture_time_seconds(timestamp, *, from_trigger=False):
    # The Cine reader returns trigger offsets in microseconds and start offsets in seconds.
    if from_trigger and 'time_from_trig' in timestamp:
        return timestamp['time_from_trig'] * 1e-6
    return timestamp['time_from_start']


def read_metadata(path):
    md = MetaData()
    with open(path, 'rb') as stream:
        header = stream.read(44)
        if len(header) != 44 or header[:2] != b'CI':
            raise ValueError('Not a supported Phantom Cine file')
        fields = ('Type', 'HeaderSize', 'Compression', 'Version', 'FirstMovieImage',
                  'TotalImageCount', 'FirstImageNo', 'ImageCount', 'OffImageHeader',
                  'OffSetup', 'OffImageOffsets', 'TriggerTime')
        for name, value in zip(fields, struct.unpack('<2sHHHiIiIIIIQ', header)):
            setattr(md, name, value)
        md.Type = 'CI'
        size = os.fstat(stream.fileno()).st_size
        if not (44 <= md.OffImageHeader < md.OffSetup < md.OffImageOffsets <= size):
            raise ValueError('Invalid Cine metadata offsets')
        if md.OffImageOffsets + md.ImageCount * 8 > size:
            raise ValueError('Truncated Cine image-offset table')
        stream.seek(md.OffImageHeader)
        bitmap = stream.read(40)
        for name, value in zip(
            ('biSize', 'biWidth', 'biHeight', 'biPlanes', 'biBitCount', 'biCompression',
             'biSizeImage', 'biXPelsPerMeter', 'biYPelsPerMeter', 'biClrUsed', 'biClrImportant'),
            struct.unpack('<IiiHHIIiiII', bitmap),
        ):
            setattr(md, name, value)
        stream.seek(md.OffSetup)
        prefix = stream.read(144)
        if len(prefix) < 144 or prefix[140:142] != b'ST':
            raise ValueError('Invalid Cine SETUP marker')
        md.Length = struct.unpack_from('<H', prefix, 142)[0]
        if md.Length < 144 or md.OffSetup + md.Length > md.OffImageOffsets:
            raise ValueError('Invalid Cine SETUP length')
        stream.seek(md.OffSetup)
        setup = stream.read(md.Length)
    def field(offset, fmt, default=None):
        if offset + struct.calcsize('<' + fmt) > len(setup):
            return default
        values = struct.unpack_from('<' + fmt, setup, offset)
        return values[0] if len(values) == 1 else values
    def number(offset, fmt, default, positive=False):
        value = field(offset, fmt, default)
        return value if math.isfinite(value) and (not positive or value > 0) else default

    md.PcaRendered = setup[19:140].startswith(RENDER_DESCRIPTION)
    md.Mark = 'ST'
    md.ImWidth, md.ImHeight = md.biWidth, abs(md.biHeight)
    md.Serial = field(743, 'I', None)
    md.CameraVersion = field(792, 'I', None)
    md.SoftwareVersion = field(800, 'I', None)
    md.FrameRate = float(field(768, 'I', field(0, 'H', 0)))
    md.FrameRateSource = 'Cine SETUP frame rate'
    precise = field(10400, 'd')
    if precise is not None and math.isfinite(precise) and 0 < precise <= 4_000_000_000:
        if md.FrameRate == 0 or abs(precise - md.FrameRate) <= max(1, md.FrameRate * 0.01):
            md.FrameRate = precise
            md.FrameRateSource = 'Cine SETUP high-precision frame rate'
    md.Decimation = max(1, field(1556, 'I', 1))
    md.fDecimation = number(10388, 'f', float(md.Decimation), positive=True)
    if md.fDecimation > 1_000_000:
        md.fDecimation = float(md.Decimation)
    md.DecimatedFrameRate = md.FrameRate / md.fDecimation
    # Optional raw sensor-mode codes are exposed, not interpreted as a binning flag.
    md.SensorMode = field(10408, 'I', None)
    md.SupportsBinning = field(10416, 'I', None)
    md.CameraModel = ''
    raw_name = field(10128, '256s', b'').split(b'\0', 1)[0]
    try:
        model = raw_name.decode('utf-8').strip()
        if model and all(character.isprintable() for character in model):
            md.CameraModel = model
    except UnicodeDecodeError:
        pass
    md.ExposureTime = field(1568, 'I', field(772, 'I', field(2, 'H', 0)) * 1000) / 1000
    md.EDR = field(1572, 'I', field(776, 'I', 0) * 1000) / 1000
    md.RealBPP = field(896, 'I', md.biBitCount)
    md.CFA = {0: 'Mono', 3: 'gb/rg', 4: 'rg/gb', 5: 'gr/gb', 6: 'bg/gr'}.get(field(808, 'I', 0), 'unknown CFA')
    if md.Compression == 0 and md.biCompression == 0 and md.biBitCount == 24:
        md.CFA, md.RealBPP = 'RGB', 8
    elif not (0 < md.RealBPP <= 16):
        raise ValueError(f'Unsupported Cine bit depth: {md.RealBPP}')
    md.BlackLevel = field(5732, 'i', 0)
    md.WhiteLevel = field(5736, 'i', (1 << md.RealBPP) - 1)
    md.Bright = number(6008, 'f', 0.0)
    md.Contrast = number(6012, 'f', 1.0, positive=True)
    md.Gamma = number(6024, 'f', 1.0, positive=True)
    md.WBGain = [round(number(offset, 'f', 1.0, positive=True), 2) for offset in (852, 856)]
    md.TonePoints = field(6312, 'i', 0)
    md.fTone = np.asarray(field(6316, '64f', np.zeros(64)), dtype=np.float32)
    md.cmCalib = list(field(7252, '9f', np.eye(3).ravel()))
    md.fPbRate = number(6976, 'f', 30.0, positive=True)
    seconds, fraction = md.TriggerTime >> 32, md.TriggerTime & 0xffffffff
    trigger = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    trigger += datetime.timedelta(seconds=fraction / 2**32)
    md.TriggerTime_displ = trigger.strftime('%Y-%m-%d %H:%M:%S.%f UTC')
    if md.PcaRendered:
        md.TriggerTime_displ = 'Start of edited timeline'
    return md


class CineHandler(BaseCineHandler):
    def close(self):
        super().close()
        self.timestamps = None
        self._timestamp_block_info = None
        self.img = None
        self.cine_loaded = False
        if hasattr(self, '_first_timestamp'):
            del self._first_timestamp

    def load_cine(self, cine_path, *, initial_index=0, index_snapshot=None):
        self.close()
        self.cine_path = cine_path
        self.frame_i = int(initial_index)
        try:
            self._file_handle = open(cine_path, 'rb')
            stat = os.fstat(self._file_handle.fileno())
            self._file_identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            self._mmap = mmap.mmap(self._file_handle.fileno(), 0, access=mmap.ACCESS_READ)
            if index_snapshot is not None and index_snapshot[0] == self._file_identity:
                _, self.metadata, self._image_offsets, self._timestamp_block_info = index_snapshot
            else:
                self.metadata = read_metadata(cine_path)
                self._load_image_offsets()
                self._find_timestamp_block()
            self.img = self._read_image_from_mmap(self.frame_num)
            self.cine_loaded = True
        except Exception:
            self.close()
            raise

    def playback_index(self):
        """Immutable file index; the worker still owns its own file descriptor."""
        if not self.cine_loaded:
            return None
        return self._file_identity, self.metadata, self._image_offsets, self._timestamp_block_info

    def _load_image_offsets(self):
        # Copy just the offset table; the reader can close its mapping immediately
        # even while a decoded frame is still displayed.
        start = self.metadata.OffImageOffsets
        self._file_handle.seek(start)
        size = self.metadata.ImageCount * 8
        data = self._file_handle.read(size)
        if len(data) != size:
            raise ValueError('Truncated Cine image-offset table')
        self._image_offsets = np.frombuffer(data, dtype='<u8')

    def advise_playback(self, indices):
        """Hint only the next requested disk ranges. Unsupported devices can ignore this."""
        if sys.platform != 'darwin' or getattr(self, '_advice_unavailable', False):
            return
        # Darwin sys/fcntl.h: F_RDADVISE (44), struct radvisory {off_t, int}.
        # This asks the kernel to read asynchronously without copying pixels into Python.
        budget = 12 * 1024 * 1024
        try:
            for index in indices:
                if not 0 <= index < self.metadata.ImageCount:
                    continue
                start = int(self._image_offsets[index])
                size = min(self.metadata.biSizeImage + 8, len(self._mmap) - start, budget)
                if size <= 0:
                    break
                fcntl.fcntl(self._file_handle.fileno(), 44, struct.pack('@qi4x', start, size))
                budget -= size
        except OSError:
            self._advice_unavailable = True

    def _read_image_from_mmap(self, frame_num):
        # File reads release the GIL while waiting for e.g. a USB drive. mmap.read
        # faulting in a whole image can stall Python input handlers in other threads.
        md = self.metadata
        index = frame_num - md.FirstImageNo
        if not 0 <= index < md.ImageCount:
            raise ValueError(f'Frame {frame_num} is out of range')
        start = int(self._image_offsets[index])
        stream = self._file_handle
        if not 0 < start <= len(self._mmap) - 4:
            raise ValueError('Invalid Cine frame offset')
        next_start = (int(self._image_offsets[index + 1]) if index + 1 < md.ImageCount
                      else len(self._mmap))
        record_size = next_start - start
        stream.seek(start)
        if md.biSizeImage + 4 <= record_size <= md.biSizeImage + 4096:
            # Almost all records have an eight-byte header. Read both header and
            # pixels in one OS call, especially helpful on external disks.
            data = stream.read(record_size)
            annotation, = struct.unpack_from('<I', data)
            if annotation < 4 or annotation + md.biSizeImage > len(data):
                raise ValueError('Invalid or truncated Cine frame')
        else:
            annotation, = struct.unpack('<I', stream.read(4))
            if annotation < 4 or start + annotation + md.biSizeImage > len(self._mmap):
                raise ValueError('Invalid or truncated Cine frame')
            stream.seek(start + annotation)
            data = stream.read(md.biSizeImage)
            if len(data) != md.biSizeImage:
                raise ValueError('Truncated Cine frame')
            annotation = 0
        if md.CFA == 'RGB' and md.biBitCount == 24:
            row_bytes = md.ImWidth * 3
            stride, remainder = divmod(md.biSizeImage, md.ImHeight)
            if remainder or stride not in (row_bytes, (row_bytes + 3) & ~3):
                raise ValueError('Unexpected RGB Cine row size.')
            rows = np.frombuffer(data, np.uint8, count=md.biSizeImage, offset=annotation).reshape(md.ImHeight, stride)
            if md.biHeight > 0:
                rows = rows[::-1]
            return np.ascontiguousarray(rows[:, :row_bytes].reshape(md.ImHeight, md.ImWidth, 3)[:, :, ::-1])
        dtype = np.dtype('u1' if md.RealBPP <= 8 or md.biCompression in (256, 1024) else '<u2')
        packed = np.frombuffer(data, dtype, count=md.biSizeImage // dtype.itemsize, offset=annotation)
        return unpacking_the_image(md, packed)

    def exposure_ticks(self, first, last, step=1):
        """Read selected 0.32-second exposure samples, always including the last frame.

        Cine File Format §3.4.2/4: tag 1003 indexes saved images, while the
        older 1001 tag can append exposures for the entire camera recording.
        The returned array owns its data so it survives closing the mapping.
        """
        md = self.metadata
        if not 0 <= first <= last < md.ImageCount or step < 1:
            raise ValueError('Invalid Cine exposure range.')
        pos = md.OffSetup + md.Length
        end = md.OffImageOffsets
        start = None
        while pos + 8 <= end:
            size, tag, _ = struct.unpack_from('<IHH', self._mmap, pos)
            if size < 8 or size > end - pos:
                break
            if tag == 1003 and size == 8 + 4 * md.ImageCount:
                start = pos + 8
                break
            if tag == 1001 and size == 8 + 12 * md.TotalImageCount:
                offset = md.FirstImageNo - md.FirstMovieImage
                if 0 <= offset <= md.TotalImageCount - md.ImageCount:
                    start = pos + 8 + 8 * md.TotalImageCount + 4 * offset
            pos += size
        if start is None:
            return None
        values = np.frombuffer(self._mmap, dtype='<u4', count=md.ImageCount, offset=start)
        selected = values[first:last+1:step]
        if (last-first) % step:
            return np.append(selected, values[last])
        return selected.copy()

    def relative_capture_timestamps(self):
        """Read TIME64 relative to frame zero without epoch-float precision loss.

        A separate float64 time per frame is the only allocation; image pixels
        stay memory mapped. A missing/truncated tag is never replaced by data
        read beyond the tag into another block.
        """
        info = self._timestamp_block_info
        count = self.metadata.ImageCount
        if info is None:
            return None
        offset = info['offset']
        if info['blocksize'] < count*8 or offset+count*8 > len(self._mmap):
            raise ValueError('Truncated Cine capture timestamp block.')
        pairs = np.frombuffer(self._mmap, dtype='<u4', count=count*2, offset=offset).reshape(-1,2)
        first_fraction, first_seconds = map(int,pairs[0])
        result = np.empty(count,dtype=np.float64)
        for start in range(0,count,65536):
            block = pairs[start:start+65536]
            result[start:start+len(block)] = ((block[:,1].astype(np.int64)-first_seconds)
                +(block[:,0].astype(np.int64)-first_fraction)/2**32)
        return result

    def capture_timestamps_from_trigger(self):
        """Read capture minus trigger using integer TIME64 parts before converting."""
        relative = self.relative_capture_timestamps()
        if relative is None:
            return None
        fraction, seconds = struct.unpack_from('<II', self._mmap, self._timestamp_block_info['offset'])
        trigger = int(self.metadata.TriggerTime)
        offset = (seconds-(trigger >> 32)) + (fraction-(trigger & 0xffffffff))/2**32
        return relative + offset

    def print_metadata(self):
        rows = super().print_metadata()
        if self.metadata.PcaRendered:
            rows.insert(0, ['Source', 'Rendered Viewer timeline'])
        for row in rows:
            if row[0] == 'Frame Rate':
                rate = self.metadata.FrameRate
                row[1] = f'{rate:,.6f}'.rstrip('0').rstrip('.') + ' fps' if rate > 0 else 'Not stored'
            elif row[0] == 'Model' and not self.metadata.CameraModel:
                row[1] = 'Not stored in this Cine'
        rows.append(['Camera Hardware ID', str(self.metadata.CameraVersion or 'Not stored')])
        if self.metadata.fDecimation != 1:
            rows.append(['Saved Frame Rate', f'{self.metadata.DecimatedFrameRate:,.6g} fps'])
        return rows
