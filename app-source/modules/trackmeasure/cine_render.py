"""Write an edited output timeline as uncompressed Phantom Cine v1 RGB frames.

Header and bitmap layout follow Vision Research's Phantom Cine File Format.
Frame numbers and TIME64 values describe the edited sequence, starting at zero.
"""
import os
from pathlib import Path
import shutil
import struct
import tempfile

import numpy as np


RENDER_DESCRIPTION = b'PCA rendered timeline'
_SETUP_SIZE = 10408  # Through the precise floating-point frame rate in SETUP.
_HEADER = struct.Struct('<2sHHHiIiIIIIQ')
_BITMAP = struct.Struct('<IiiHHIIiiII')


def rendered_size(width, height, count):
    row_bytes = (3 * width + 3) & ~3
    return _HEADER.size + _BITMAP.size + _SETUP_SIZE + 8 + count * (24 + row_bytes * height)


def _headers(width, height, total, fps, pixels):
    setup = bytearray(_SETUP_SIZE)
    description = RENDER_DESCRIPTION + b'; RGB display pixels. Frame 0 = start of edit. Times are relative to the edit.'
    setup[19:19+len(description)] = description
    setup[1596:1596+len(description)] = description
    struct.pack_into('<H', setup, 0, round(fps))
    struct.pack_into('<2sH', setup, 140, b'ST', len(setup))
    struct.pack_into('<iI', setup, 429, 0, total)
    struct.pack_into('<HH', setup, 737, width, height)
    struct.pack_into('<I', setup, 768, round(fps))
    struct.pack_into('<I', setup, 788, 1)                # bEnableColor
    struct.pack_into('<ff', setup, 852, 1., 1.)         # WBGain
    struct.pack_into('<I', setup, 896, 8)               # 8 bits per channel
    struct.pack_into('<I', setup, 1556, 1)              # Decimation
    struct.pack_into('<II', setup, 1588, width, height)
    struct.pack_into('<i', setup, 5736, 255)            # WhiteLevel
    for offset in (6012, 6024, 10388):                  # Contrast, Gamma, fDecimation
        struct.pack_into('<f', setup, offset, 1.)
    struct.pack_into('<9f', setup, 7252, 1,0,0,0,1,0,0,0,1)
    struct.pack_into('<f', setup, 6976, fps)
    struct.pack_into('<d', setup, 10400, fps)
    tag_size = 8 + total * 8
    table_start = _HEADER.size + _BITMAP.size + len(setup) + tag_size
    if table_start > 0xffffffff:
        raise ValueError('This edit exceeds the Cine timing-table limit.')
    bitmap = _BITMAP.pack(40, width, height, 1, 24, 0, pixels, 0, 0, 0, 0)
    header = _HEADER.pack(b'CI', 44, 0, 1, 0, total, 0, total, 44, 84, table_start, 0)
    return header + bitmap + setup + struct.pack('<IHH', tag_size, 1002, 0), table_start


def write_rendered_cine(destination, frames, width, height, fps, total, cancel, progress=None):
    from video_export import ExportCancelled
    destination = Path(destination)
    if not (0 < width <= 65535 and 0 < height <= 65535 and 0 < total < 2**29-2):
        raise ValueError('The rendered sequence exceeds the Cine dimensions or timing-table limits.')
    if not 1 <= fps <= 120:
        raise ValueError('Choose an output rate between 1 and 120 fps.')
    stride = (width * 3 + 3) & ~3
    storage = stride * height
    if storage >= 2**32:
        raise ValueError('One rendered frame exceeds the 4 GiB Cine record limit.')
    needed = rendered_size(width, height, total)
    if needed > shutil.disk_usage(destination.parent).free:
        raise OSError(f'This uncompressed RGB Cine needs {needed / 1e9:,.2f} GB of free space.')
    def check_cancel():
        if cancel.is_set():
            raise ExportCancelled()
    check_cancel()
    header, table_start = _headers(width, height, total, fps, storage)
    offset = table_start + total * 8
    annotation = struct.pack('<II', 8, storage)
    # Positive-height DIBs hold bottom-up BGR scanlines, padded to four bytes.
    rows = np.zeros((height, stride), dtype=np.uint8)
    colors = rows[:, :width*3].reshape(height, width, 3)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.pca-render-', suffix='.cine', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(header)
            for start in range(0, total, 32768):
                check_cancel()
                seconds = np.arange(start, min(start+32768, total), dtype=np.float64) / fps
                # Two least significant TIME64 bits are status flags.
                ticks = np.rint(seconds * 2**30).astype('<u8') << 2
                stream.write(ticks.tobytes())
            for start in range(0, total, 32768):
                check_cancel()
                indexes = np.arange(start, min(start+32768,total), dtype='<u8')
                stream.write((offset + indexes * (8 + storage)).astype('<u8').tobytes())
            completed = 0
            for rgb in frames:
                check_cancel()
                if completed >= total or rgb.shape != (height, width, 3) or rgb.dtype != np.uint8:
                    raise ValueError('The rendered frame differs from the declared Cine layout.')
                colors[:] = rgb[::-1, :, ::-1]
                stream.write(annotation)
                stream.write(memoryview(rows))
                completed += 1
                if progress:
                    progress(completed, total)
            if completed != total:
                raise ValueError('The render ended before all Cine frames were produced.')
        check_cancel()
        os.replace(temporary, destination)
        return str(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
