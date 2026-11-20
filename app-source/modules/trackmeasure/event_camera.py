"""Read completed Phantom PH16 RAM recordings over the standard Ethernet interface.

Protocol reference: PH16 camera protocol, v2.3, sections 3, 4.7, 5.9 and 5.10.
Acquisition stays under the camera operator's control. PCA requests corrected,
linear raw sensor intensities and crops the chosen rectangle on the host.
"""
from concurrent.futures import CancelledError
from dataclasses import dataclass
from ipaddress import IPv4Address
import math
import re
import socket
from time import monotonic

import numpy as np


class CameraReadError(RuntimeError):
    """The requested completed recording cannot be read."""


def validate_address(value):
    try:
        address = IPv4Address(value.strip())
        if address.is_multicast or address.is_unspecified or int(address) == 0xffffffff:
            raise ValueError()
    except (AttributeError, ValueError):
        raise ValueError('Enter the IPv4 address of the individual Phantom camera.') from None
    return str(address)


def _check(cancelled):
    if cancelled():
        raise CancelledError()


def _tokens(value):
    return set(re.findall(r'[\w-]+', value.upper()))


class _Channel:
    """One command connection, plus an image connection attached only for a read."""
    def __init__(self, host, cancelled, connect=socket.create_connection):
        self.host = validate_address(host)
        self.cancelled = cancelled
        self.connect = connect
        self.data = None
        _check(cancelled)
        try:
            self.control = connect((self.host, 7115), timeout=2.5)
        except OSError as exc:
            raise CameraReadError('Could not reach the camera. Check its IP address and the wired adapter settings.') from exc

    def close(self):
        if self.data is not None:
            self.data.close()
            self.data = None
        self.control.close()

    def _receive(self, sock, target, deadline):
        while True:
            _check(self.cancelled)
            remaining = deadline-monotonic()
            if remaining <= 0:
                raise CameraReadError('Timed out reading from the camera. Check the Ethernet connection.')
            sock.settimeout(min(.2, remaining))
            try:
                count = sock.recv_into(target)
            except socket.timeout:
                continue
            if not count:
                raise CameraReadError('The camera closed the connection before the complete response arrived.')
            return count

    def request(self, command):
        self._send(command)
        return self._reply()

    def _send(self, command):
        _check(self.cancelled)
        self.control.settimeout(2.5)
        self.control.sendall(command.encode('ascii') + b'\r\n')

    def _reply(self):
        reply = bytearray()
        chunk = bytearray(4096)
        deadline = monotonic()+4
        while len(reply) < 65536:
            n = self._receive(self.control, chunk, deadline)
            reply.extend(chunk[:n])
            if reply.endswith(b'\n') and not reply.rstrip(b'\r\n').endswith(b'\\'):
                text = bytes(reply).replace(b'\\\r\n', b' ').replace(b'\\\n', b' ').decode('ascii', 'replace').strip()
                if text.upper().startswith('ERR'):
                    raise CameraReadError('Camera replied: ' + text[:240])
                return text
        raise CameraReadError('The camera returned an oversized control response.')

    def get(self, key):
        # Firmware may prefix the value with the leaf name or the full path.
        raw = self.request('get ' + key)
        match = re.match(r'^[\w.]+\s*:\s*', raw)
        if match:
            raw = raw[match.end():]
        return raw.strip('"')

    def attach(self):
        if self.data is None:
            self.data = self.connect((self.host, 7116), timeout=2.5)
            port = self.data.getsockname()[1]
            if self.request(f'attach {{port:{port}}}').upper() != 'OK!':
                raise CameraReadError('The camera did not confirm the image connection.')

    def _confirm_image(self, recording, fmt):
        reply = self._reply()
        values = {key.lower(): value.strip() for key, value in
                  re.findall(r'(\w+)\s*:\s*([^,}]*)', reply)}
        resolution = re.fullmatch(r'\s*(\d+)\s*[xX]\s*(\d+)\s*', values.get('res', ''))
        # Some VEO firmware acknowledges only the Cine and resolution. The
        # command already selected a linear format advertised by this camera.
        formats = {'P16', '272'} if fmt == 'P16' else {'8'}
        echoed_format = values.get('fmt')
        if (not reply.upper().startswith('OK!') or
                values.get('cine', '').strip() != str(recording.cine) or not resolution or
                tuple(map(int,resolution.groups())) != (recording.width, recording.height) or
                (echoed_format is not None and echoed_format.upper() not in formats)):
            raise CameraReadError('The image response does not match the selected recording and pixel format.')

    def _read_pixels(self, pixels):
        dest = memoryview(pixels).cast('B')
        offset = 0
        deadline = monotonic()+5+len(dest)/(2*1024**2)
        while offset < len(dest):
            offset += self._receive(self.data, dest[offset:], deadline)

    def images(self, recording, first, count, stride, fmt):
        self.attach()
        pixels = np.empty((count, recording.height, recording.width), dtype='<u2' if fmt == 'P16' else 'u1')
        def queue(start, length):
            self._send(f'img {{cine:{recording.cine}, start:{start}, cnt:{length}, fmt:{fmt}}}')

        if stride == 1:
            queue(first, count)
            self._confirm_image(recording, fmt)
            self._read_pixels(pixels)
            return pixels

        # PH16 has no on-camera frame stride. Queue the next sampled image
        # while receiving this one, so each command overlaps the previous
        # transfer. At most one control reply awaits reading at any time.
        queue(first, 1)
        for i in range(count):
            self._confirm_image(recording, fmt)
            if i+1 < count:
                queue(first+(i+1)*stride, 1)
            self._read_pixels(pixels[i])
        return pixels


def _trigger(channel, cine):
    value = channel.get(f'c{cine}.trigtime')
    fields = dict(re.findall(r'\b(secs|frac)\s*:\s*(\d+)\b', value))
    if fields.keys() != {'secs', 'frac'}:
        raise CameraReadError('The camera returned an incomplete recording timestamp.')
    return int(fields['secs']), int(fields['frac'])


def _stored(channel, cine):
    return 'STR' in _tokens(channel.get(f'c{cine}.state'))


@dataclass(frozen=True)
class CameraRecording:
    host: str
    model: str
    serial: str
    cine: int
    first_frame: int
    last_frame: int
    width: int
    height: int
    fps: float | None
    trigger: tuple
    pixel_format: str
    supports_eight_bit: bool = False

    @property
    def transfer_modes(self):
        modes = {'full'} if self.pixel_format == 'P16' else {'fast'}
        if self.supports_eight_bit:
            modes.add('fast')
        return frozenset(modes)

    @property
    def label(self):
        return f'{self.model} · RAM Cine {self.cine}'

    @property
    def count(self):
        return self.last_frame-self.first_frame+1

    def open(self):
        return CameraSource(self)


def list_recordings(host, cancelled=lambda: False):
    """List completed cine slots. Listing never opens the shared image channel."""
    host = validate_address(host)
    channel = _Channel(host, cancelled)
    try:
        if channel.get('info.pver').strip() != '16':
            raise CameraReadError('This camera must support the Phantom PH16 protocol.')
        if 'ATTACH' not in _tokens(channel.get('info.features')):
            raise CameraReadError('This camera does not advertise the PH16 image connection used by Event Finder.')
        formats = _tokens(channel.get('info.imgformats'))
        if {'P16', '272'} & formats:
            fmt = 'P16'
        elif '8' in formats:
            fmt = '8'
        else:
            raise CameraReadError('This camera does not advertise a supported linear image format.')
        model = channel.get('info.model')[:100]
        serial = channel.get('info.serial')
        cines = int(channel.get('cam.cines'))
        if not 0 <= cines <= 256:
            raise CameraReadError('The camera reported an unexpected number of RAM partitions.')
        recordings = []
        for cine in range(1,cines+1):
            if not _stored(channel, cine):
                continue
            stamp = _trigger(channel, cine)
            first = int(channel.get(f'c{cine}.firstfr'))
            last = int(channel.get(f'c{cine}.lastfr'))
            resolution = re.fullmatch(r'\s*(\d+)\s*[xX]\s*(\d+)\s*', channel.get(f'c{cine}.res'))
            if not resolution:
                raise CameraReadError('Could not read the stored Cine resolution.')
            w,h = map(int, resolution.groups())
            if not (1 <= w*h <= 64*1024**2 and 0 < last-first+1 <= 2**31-1 and
                    -(2**31) <= first <= last < 2**31):
                raise CameraReadError('The stored Cine dimensions or frame range are unsupported.')
            rate = float(channel.get(f'c{cine}.rate'))
            if _stored(channel, cine) and _trigger(channel, cine) == stamp:
                recordings.append(CameraRecording(host, model, serial, cine, first, last, w, h,
                                  rate if math.isfinite(rate) and rate > 0 else None, stamp, fmt,
                                  '8' in formats))
        return recordings
    finally:
        channel.close()


class CameraSource:
    """Exclusive image reads in short, sequential batches; no full Cine download."""
    def __init__(self, recording):
        self.recording = recording
        self.width, self.height = recording.width, recording.height
        self.first_frame, self.count, self.fps = recording.first_frame, recording.count, recording.fps
        self.transfer_modes = recording.transfer_modes
        self.pixel_format = recording.pixel_format
        self._cancelled = lambda: False
        self.channel = None
        self.block = None
        self.start = 0
        self._block_stride = 1
        self._scan_stride = 1
        self._scan_first = 0
        self._scan_last = self.count-1
        self._sequence = False
        self._reference = False
        self._previous = None

    def set_cancel_check(self, cancelled):
        self._cancelled = cancelled

    @property
    def batch_size(self):
        frame_bytes = self.width*self.height*(2 if self.pixel_format == 'P16' else 1)
        return max(1, min(256, (16 << 20)//frame_bytes))

    def set_transfer_mode(self, mode):
        if mode not in {'fast', 'full'}:
            raise ValueError('Choose fast or full-precision camera transfer.')
        self.close()
        self.pixel_format = ('8' if mode == 'fast' and 'fast' in self.transfer_modes
                             else self.recording.pixel_format)
        # The detector first reads a few widely separated reference frames.
        # Avoid starting a full transfer until the sequential pass begins.
        self._reference = True

    def set_scan_stride(self, step, first=0, last=None):
        if last is None:
            last = self.count-1
        if not (isinstance(step, int) and step >= 1 and 0 <= first <= last < self.count):
            raise ValueError('Choose a positive stride and an interval inside the recording.')
        self._sequence = True
        self._reference = False
        self._scan_stride, self._scan_first, self._scan_last = step, first, last
        self.block = None
        self._previous = None

    def close(self):
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        self.block = None
        self._previous = None
        self._sequence = False

    def _check_recording(self, channel):
        r = self.recording
        if not _stored(channel, r.cine) or _trigger(channel, r.cine) != r.trigger:
            raise CameraReadError('This RAM recording changed. Reopen Camera recordings and select the current Cine.')

    def _verified_channel(self):
        if self.channel is None:
            self.channel = _Channel(self.recording.host, lambda: self._cancelled())
            if self.channel.get('info.serial') != self.recording.serial:
                raise CameraReadError('A different camera is now at this address. Reopen Camera recordings.')
        self._check_recording(self.channel)
        return self.channel

    def validate_recording(self):
        """Recheck cached measurements against camera RAM without an image request."""
        try:
            self._verified_channel()
        finally:
            self.close()

    def _read(self, index, count, stride, fmt):
        if not 0 <= index <= index+(count-1)*stride < self.count:
            raise ValueError('Frame is outside the recorded Cine.')
        try:
            channel = self._verified_channel()
            pixels = channel.images(self.recording, self.first_frame+index, count, stride, fmt)
            self._check_recording(channel)
            return pixels
        except BaseException:
            self.close()
            raise

    def preview(self, index):
        # Return the shared transfer channel to the operator after an exact seek.
        try:
            image = self._read(index, 1, 1, self.recording.pixel_format)[0]
            if self.recording.pixel_format == 'P16':
                return (image >> 8).astype(np.uint8)
            return image
        finally:
            self.close()

    def read_roi(self, index, roi):
        x,y,w,h = roi
        if not (0 <= x < x+w <= self.width and 0 <= y < y+h <= self.height):
            raise ValueError('Choose a rectangle inside the recorded image.')
        _check(self._cancelled)
        frame_offset, remainder = divmod(index-self.start, self._block_stride)
        if self.block is None or remainder or not 0 <= frame_offset < len(self.block):
            stride = count = 1
            if (self._sequence and self._scan_first <= index <= self._scan_last and
                    (index-self._scan_first) % self._scan_stride == 0):
                stride = self._scan_stride
                count = min(self.batch_size, 1+(self._scan_last-index)//stride)
            elif (not self._sequence and not self._reference and
                  (index == 0 or (self._previous is not None and index == self._previous+1))):
                count = min(self.batch_size, self.count-index)
            self.block = self._read(index, count, stride, self.pixel_format)
            self.start = index
            self._block_stride = stride
            frame_offset = 0
        self._previous = index
        result = self.block[frame_offset, y:y+h, x:x+w].copy()
        if index == self.count-1 or (self._sequence and index == self._scan_last):
            self.close()
        return result
