"""Losslessly extract an inclusive frame range from a Phantom v1 Cine.

Layout and tag semantics: Vision Research, Phantom Cine File Format, sections
3.1, 3.4 and 3.5. Acquisition counters cover the camera recording, while file
counters, the frame directory and modern per-image tags cover the saved range.
"""
import operator
import os
from pathlib import Path
import struct
import tempfile

_HEADER = struct.Struct('<2sHHHiIiIIIIQ')
_TAG = struct.Struct('<IHH')
_CHUNK = 1024 * 1024


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        from video_export import ExportCancelled
        raise ExportCancelled()


def _read(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('The source Cine ended before its declared data.')
    return data


def _copy(stream, output, count, cancel):
    while count:
        _check_cancel(cancel)
        chunk = _read(stream, min(_CHUNK, count))
        output.write(chunk)
        count -= len(chunk)


def _tag_range(tag, size, setup, images, first, count):
    if tag in (1000, 1001):
        # Legacy signal/time tags describe the ENTIRE camera recording.
        # They stay aligned with FirstMovieImage and TotalImageCount.
        return 0, size
    stride = {1002: 8, 1003: 4, 1007: 8}.get(tag)
    if tag == 1004 and len(setup) >= 1556:
        stride, = struct.unpack_from('<I', setup, 1552)
    if tag in (1005, 1006) and len(setup) >= 243:
        samples = setup[150]
        if tag == 1006:
            channels, = struct.unpack_from('<h', setup, 241)
            stride = 2 * samples * channels
        else:
            channels, = struct.unpack_from('<H', setup, 148)
            bits = channels * samples
            if bits % 8 == 0:
                stride = bits // 8
    if stride is None:
        raise ValueError(f'Native trimming cannot safely copy camera metadata block {tag} in this Cine.')
    if stride < 1 or stride * images != size:
        raise ValueError(f'Camera metadata block {tag} has an unsupported per-frame layout.')
    return first * stride, count * stride


def _write_metadata(stream, output, first, last, filesize, cancel):
    header = _read(stream, _HEADER.size)
    (magic, hsize, compression, version, _, _, native_first, images, bitmap_at,
     setup_at, table_at, _) = _HEADER.unpack(header)
    if magic != b'CI' or version != 1 or compression not in (0, 2):
        raise ValueError('Native trim requires a version 1 uncompressed or packed Phantom Cine.')
    if hsize != _HEADER.size or not (hsize <= bitmap_at and bitmap_at + 40 <= setup_at and
                                     setup_at + 144 <= table_at and table_at + images * 8 <= filesize):
        raise ValueError('The source Cine has an invalid or unsupported header layout.')
    if not 0 <= first <= last < images:
        raise ValueError('Select a valid inclusive In/Out range in the source Cine.')
    if native_first + last > 2**31-1:
        raise ValueError('The source frame numbers exceed the Cine format.')
    stream.seek(bitmap_at)
    bitmap = _read(stream, 40)
    bitmap_size, = struct.unpack_from('<I', bitmap)
    pixel_size, = struct.unpack_from('<I', bitmap, 20)
    if not 40 <= bitmap_size <= setup_at - bitmap_at:
        raise ValueError('Invalid Cine bitmap header.')
    stream.seek(setup_at + 140)
    magic, setup_len = struct.unpack('<2sH', _read(stream, 4))
    if magic != b'ST' or not 144 <= setup_len <= table_at - setup_at:
        raise ValueError('Invalid Cine camera setup.')
    stream.seek(setup_at)
    setup = _read(stream, setup_len)
    stream.seek(0)
    _copy(stream, output, setup_at + setup_len, cancel)
    count = last - first + 1
    while stream.tell() < table_at:
        tagstart = stream.tell()
        if table_at - tagstart < _TAG.size:
            raise ValueError('Incomplete Cine metadata block header.')
        size, tag, reserved = _TAG.unpack(_read(stream, _TAG.size))
        if not _TAG.size <= size <= table_at - tagstart:
            raise ValueError('Invalid Cine metadata block size.')
        offset, keep = _tag_range(tag, size - _TAG.size, setup, images, first, count)
        output.write(_TAG.pack(_TAG.size + keep, tag, reserved))
        stream.seek(tagstart + _TAG.size + offset)
        _copy(stream, output, keep, cancel)
        stream.seek(tagstart + size)
    new_table = output.tell()
    output.seek(16)
    output.write(struct.pack('<iI', native_first + first, count))
    output.seek(32)
    output.write(struct.pack('<I', new_table))
    output.seek(new_table + count * 8)
    return table_at, images, pixel_size, new_table


def _copy_frames(stream, output, first, last, metadata, filesize, cancel, progress):
    table, images, pixel_size, new_table = metadata
    first_record = table + images * 8
    count = last - first + 1
    # Small directory batches bound memory even for very long acquisitions.
    for start in range(first, last + 1, 2048):
        batch = min(2048, last - start + 1)
        stream.seek(table + 8 * start)
        n = min(batch + 1, images - start)
        offsets = list(struct.unpack(f'<{n}Q', _read(stream, 8 * n)))
        if n == batch:
            offsets.append(filesize)
        rewritten = []
        for j, (begin, end) in enumerate(zip(offsets, offsets[1:])):
            _check_cancel(cancel)
            if not first_record <= begin <= end - 8 or end > filesize:
                raise ValueError('Invalid or unordered frame offsets in source Cine.')
            stream.seek(begin)
            annotation_data = _read(stream, 4)
            annotation, = struct.unpack('<I', annotation_data)
            if not 8 <= annotation <= end - begin:
                raise ValueError('Invalid Cine frame annotation size.')
            rewritten.append(output.tell())
            output.write(annotation_data)
            _copy(stream, output, annotation - 8, cancel)
            size_data = _read(stream, 4)
            size, = struct.unpack('<I', size_data)
            if size == 0 or annotation + size > end - begin or (pixel_size and size != pixel_size):
                raise ValueError('The Cine frame size does not match the source image data.')
            output.write(size_data)
            _copy(stream, output, size, cancel)
            if progress is not None:
                progress(start - first + j + 1, count)
        tail = output.tell()
        output.seek(new_table + 8 * (start - first))
        output.write(struct.pack(f'<{batch}Q', *rewritten))
        output.seek(tail)


def trim_cine(source, destination, first, last, cancel=None, progress=None):
    """Copy raw native frames [first, last] (zero-based), plus their camera metadata."""
    first, last = operator.index(first), operator.index(last)
    destination = Path(destination)
    if destination.suffix.lower() != '.cine':
        raise ValueError('Choose a .cine filename for native export.')
    temporary = None
    try:
        _check_cancel(cancel)
        with open(source, 'rb') as stream:
            original = os.fstat(stream.fileno())
            if destination.exists() and os.path.samestat(original, destination.stat()):
                raise ValueError('Choose a different filename from the original Cine.')
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.pca-cine-',
                                             suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                metadata = _write_metadata(stream, output, first, last, original.st_size, cancel)
                _copy_frames(stream, output, first, last, metadata, original.st_size, cancel, progress)
                now = os.fstat(stream.fileno())
                if (now.st_size, now.st_mtime_ns) != (original.st_size, original.st_mtime_ns):
                    raise ValueError('The source Cine changed during export. Retry with a finished recording.')
                output.flush()
                os.fsync(output.fileno())
        _check_cancel(cancel)
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
