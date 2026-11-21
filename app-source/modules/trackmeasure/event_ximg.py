"""Checked Phantom ximg single-frame readback and linear P12L region decoding."""
from concurrent.futures import CancelledError
import re
import struct
from time import monotonic

import numpy as np


class XimgError(RuntimeError):
    """The requested fast transfer could not be verified."""


class IncompleteXimgFrame(XimgError):
    """Transmission of an individual frame may be retried."""


def decode_region(packed, width, roi):
    """PH16 P12L stores two adjacent linear samples in three network-order bytes."""
    x, y, w, h = roi
    first_pair = x // 2
    end_pair = (x + w + 1) // 2
    rows = np.frombuffer(packed, dtype=np.uint8).reshape(-1, width // 2, 3)
    pairs = rows[y:y+h, first_pair:end_pair].astype(np.uint16)
    out = np.empty((h, 2 * (end_pair - first_pair)), np.uint16)
    out[:, ::2] = (pairs[:, :, 0] << 4) | (pairs[:, :, 1] >> 4)
    out[:, 1::2] = ((pairs[:, :, 1] & 15) << 8) | pairs[:, :, 2]
    return out[:, x % 2:x % 2 + w]


def matches_standard(packed, standard):
    """Validate the first frame in strips to bound temporary memory on large sensors."""
    height, width = standard.shape
    for y in range(0, height, 32):
        rows = standard[y:y+32]
        decoded = decode_region(packed, width, (0, y, width, len(rows)))
        if not np.array_equal(decoded, rows >> 4):
            return False
    return True


class XimgStream:
    def __init__(self, receiver, recording, cancelled):
        self.receiver = receiver
        self.recording = recording
        self.cancelled = cancelled
        self.retries = 0
        self.frames = 0
        self.frame_bytes = recording.width * recording.height * 3 // 2
        self.mac_header = bytes.fromhex(receiver.dest + receiver.source + '88b7')

    def frame(self, channel, native):
        # One request per native frame permits sparse sampling and precise retransmission.
        # Each response identifies its own Ethernet stream, including on a retry.
        rec = self.recording
        command = f'ximg {{cine:{rec.cine}, start:{native}, cnt:1, fmt:P12L, dest:{self.receiver.dest}}}'
        for attempt in range(3):
            if self.cancelled():
                raise CancelledError()
            reply = channel.request(command)
            fields = {k.lower(): v.strip() for k, v in re.findall(r'(\w+)\s*:\s*([^,}]*)', reply)}
            size = re.fullmatch(r'(\d+)\s*[xX]\s*(\d+)', fields.get('res', ''))
            stream = fields.get('ssrc', '')
            if (not reply.upper().startswith('OK!') or fields.get('cine') != str(rec.cine)
                    or size is None or tuple(map(int, size.groups())) != (rec.width, rec.height)
                    or fields.get('fmt', '').upper() not in {'524', 'P12L'}
                    or not re.fullmatch(r'\d{1,10}', stream) or int(stream) > 0xffffffff):
                raise XimgError('The camera did not confirm the requested 12-bit image format and stream.')
            try:
                packed = self._receive(int(stream))
                self.frames += 1
                return packed
            except IncompleteXimgFrame:
                if attempt == 2:
                    raise
                self.retries += 1

    def _receive(self, stream):
        target = stream.to_bytes(4, 'big')
        expected_size = self.frame_bytes.to_bytes(4, 'big')
        deadline = monotonic() + .30 + self.frame_bytes / 25_000_000
        buffer = bytearray()
        sequence = packet_size = None
        while len(buffer) < self.frame_bytes:
            if self.cancelled():
                raise CancelledError()
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise IncompleteXimgFrame('The camera image did not arrive in full.')
            packet = self.receiver.next_packet(min(.1, remaining))
            if (packet is None or len(packet) < 32 or packet[:14] != self.mac_header
                    or packet[22:26] != target):
                continue
            if packet[28:32] != expected_size or packet[14] != 0x40:
                raise XimgError('The Ethernet image header does not match the requested frame.')
            marker = bool(packet[15] & 128)
            seq = struct.unpack_from('>H', packet, 16)[0]
            if marker != (sequence is None) or (sequence is not None and seq != (sequence + 1) & 65535):
                raise IncompleteXimgFrame('Packets were lost or arrived out of sequence.')
            sequence = seq
            length = len(packet) - 32
            if packet_size is None:
                packet_size = length
            required = min(packet_size, self.frame_bytes - len(buffer))
            if required <= 0 or (length != required and not (required < 28 and length == 28)):
                raise IncompleteXimgFrame('The camera sent an incomplete image packet.')
            buffer.extend(memoryview(packet)[32:32+required])
        return buffer
