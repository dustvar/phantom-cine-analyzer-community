"""Read stored Cine frames for Event Finder in the original image coordinates."""
import copy
import math
import struct
from pathlib import Path

import cv2
import numpy as np
from cinereader.cinereader import unpacking_the_image

from camera_comparator import linear_frame
from cine_metadata import CineHandler


class EventCineSource:
    """One worker owns one reader. Indices start at zero, even before trigger."""

    def __init__(self, path):
        if Path(path).suffix.lower() != '.cine':
            raise ValueError('Choose a Phantom Cine recording.')
        self.reader = CineHandler()
        self.reader.load_cine(str(path))
        md = self.reader.metadata
        self.count, self.first_frame = int(md.ImageCount), int(md.FirstImageNo)
        self.width, self.height = int(md.ImWidth), int(md.ImHeight)
        rate = float(md.FrameRate or 0)
        self.fps = rate if rate > 0 and math.isfinite(rate) else None
        self._mono = md.CFA.lower() in ('mono', 'monochrome')

    def close(self):
        self.reader.close()

    def _check_index(self, index):
        if not 0 <= index < self.count:
            raise ValueError('Frame is outside the recorded Cine.')

    def _gray(self, index):
        raw = self.reader.get_img(index)
        if self._mono:
            return raw
        rgb = linear_frame(raw, self.reader.metadata)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    def preview(self, index):
        self._check_index(index)
        image = self._gray(index)
        md = self.reader.metadata
        if self._mono:
            black = float(md.BlackLevel or 0)
            white = float(md.WhiteLevel or (2**md.RealBPP - 1))
        else:
            black, white = 0., 1.
        # Use one scale for the whole recording so a flash stays visible.
        return np.ascontiguousarray(np.clip((image.astype(np.float32)-black)
                                  * (255/max(white-black, 1.)), 0, 255).astype(np.uint8))

    def read_roi(self, index, roi):
        self._check_index(index)
        x, y, w, h = roi
        if min(x, y) < 0 or min(w, h) < 1 or x+w > self.width or y+h > self.height:
            raise ValueError('Select a rectangle inside the Cine image.')
        if self._mono:
            region = self._read_mono_region(index, x, y, w, h)
            if region is not None:
                return region
        return self._gray(index)[y:y+h, x:x+w].copy()

    def _read_mono_region(self, index, x, y, width, height):
        # Map just the relevant rows and packed-pixel groups. This avoids
        # unpacking or copying an entire sensor image for a tiny search area.
        reader = self.reader
        md = reader.metadata
        if md.biHeight != self.height:
            return None
        packing = md.biCompression
        if packing == 256:
            pixels, size = 4, 5
        elif packing == 1024:
            pixels, size = 2, 3
        elif packing == 0:
            pixels, size = 1, (1 if md.RealBPP <= 8 else 2)
        else:
            return None
        stride = self.width * size // pixels
        if self.width % pixels or stride*self.height != md.biSizeImage:
            return None

        start = int(reader._image_offsets[index])
        mapping = reader._mmap
        limit = int(reader._image_offsets[index+1]) if index+1 < self.count else len(mapping)
        if not 0 < start <= limit-4 or limit > len(mapping):
            raise ValueError('Invalid Cine frame offset.')
        annotation, = struct.unpack_from('<I', mapping, start)
        if annotation < 4 or start+annotation+md.biSizeImage > limit:
            raise ValueError('Incomplete Cine frame.')
        first_group, last_group = x//pixels, (x+width+pixels-1)//pixels
        first_row = y if packing else self.height-y-height
        offset = start+annotation + first_row*stride + first_group*size
        rows = np.ndarray((height,(last_group-first_group)*size), np.uint8, buffer=mapping,
                          offset=offset, strides=(stride,1))
        if not packing:
            values = rows if size == 1 else rows.view('<u2')
            return values[::-1].copy()
        # The vendor decoder applies the same P10 lookup table as the Viewer.
        patch_metadata = copy.copy(md)
        patch_metadata.biWidth = (last_group-first_group)*pixels
        patch_metadata.biHeight = height
        block = unpacking_the_image(patch_metadata, np.ascontiguousarray(rows).ravel())
        left = x-first_group*pixels
        return np.ascontiguousarray(block[:, left:left+width])
