"""Write Apple's typed, movie-level full-rate intent into a newly encoded HFR MP4."""
import os
import struct


INTENT_KEY = 'com.apple.quicktime.full-frame-rate-playback-intent'


def _find_box(stream, name, first, end):
    """Read atom headers only; a movie's video payload can exceed 4 GB."""
    pos = first
    while pos + 8 <= end:
        stream.seek(pos)
        size, kind = struct.unpack('>I4s', stream.read(8))
        header = 8
        if size == 1:
            size = struct.unpack('>Q', stream.read(8))[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos+size > end:
            break
        if kind == name:
            return pos, size, header
        pos += size
    raise ValueError('The encoder did not produce the expected MP4 metadata.')


def finalize_full_rate_intent(path):
    """Convert FFmpeg's generated UTF-8 item to the native AVFoundation representation.

    Native QuickTime metadata is a direct child of moov and uses a basic meta box.
    FFmpeg's MP4 muxer writes a full meta box inside moov/udta and writes all its
    item values as strings. Preserve every sample offset and the fast-start layout
    by placing the extra twelve wrapper bytes in an equally sized free box.
    """
    with open(path, 'r+b') as stream:
        end = stream.seek(0, os.SEEK_END)
        movie, movie_size, header = _find_box(stream, b'moov', 0, end)
        user, user_size, _ = _find_box(stream, b'udta', movie+header, movie+movie_size)
        if user_size > 65536:
            raise ValueError('Unexpected movie-level metadata in the generated BOS MP4.')
        stream.seek(user)
        metadata = bytearray(stream.read(user_size))
        # Only transform the private, freshly encoded file when its single mdta
        # box and the flag's exact one-byte text item have the expected shape.
        utf8_one = struct.pack('>I4sIIB', 17, b'data', 1, 0, ord('1'))
        expected = (struct.pack('>I4sI', user_size-8, b'meta', 0) == metadata[8:20]
                    and metadata.count(b'mdta'+INTENT_KEY.encode('ascii')) == 1
                    and metadata.count(utf8_one) == 1)
        if not expected:
            raise ValueError('The encoder did not write the full-frame-rate metadata item.')
        data = metadata.index(utf8_one)
        # QuickTime type 22 is an unsigned integer; one payload byte means UInt8.
        metadata[data:data+17] = struct.pack('>I4sIIB', 17, b'data', 22, 0, 1)
        metadata[:20] = struct.pack('>I4sII4s', 12, b'free', 0, user_size-12, b'meta')
        stream.seek(user)
        stream.write(metadata)
