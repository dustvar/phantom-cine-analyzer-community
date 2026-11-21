"""Read completed Phantom PH16 RAM recordings over wired Ethernet.

Protocol reference: PH16 camera protocol, v2.3, sections 3, 4.7 and 5.9–5.11.
Acquisition stays under the camera operator's control. PCA requests corrected,
linear raw sensor intensities and crops the chosen rectangle on the host.
"""
from concurrent.futures import CancelledError
from dataclasses import dataclass
from ipaddress import IPv4Address
import math
import re
import socket
import sys
from time import monotonic

import numpy as np
from event_ximg import IncompleteXimgFrame, XimgError, XimgStream, decode_region, matches_standard
from event_ximg_receiver import PacketReceiver, can_receive


class CameraReadError(RuntimeError):
    """The requested completed recording cannot be read."""


class CameraRecordingChangedError(CameraReadError):
    """The selected source is no longer the same stored recording."""


class CameraCommandError(CameraReadError):
    """The camera explicitly declined a complete command."""


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
                    raise CameraCommandError('Camera replied: ' + text[:240])
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


def _reported_camera_mac(report, host):
    """Read the MAC of the Ethernet interface owning this exact control address."""
    # Cameras flatten the usual multi-line ifconfig output into a quoted string.
    # Keep each interface separate even when its address or MAC is missing.
    records = re.split(r'\b[\w.:-]+\s+Link\s+encap\s*:\s*', report, flags=re.I)[1:]
    matches = [record for record in records if host in
               re.findall(r'\binet\s+addr\s*:\s*([\d.]+)\b', record, re.I)]
    if len(matches) != 1 or not re.match(r'Ethernet\b', matches[0], re.I):
        return None
    addresses = re.findall(r'\bHWaddr\s+([^\s"]+)', matches[0], re.I)
    if len(addresses) != 1 or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', addresses[0], re.I):
        return None
    mac = bytes.fromhex(addresses[0].replace(':', ''))
    return mac.hex() if any(mac) and not mac[0] & 1 else None


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
    supports_ximg: bool = False
    camera_mac: str | None = None

    @property
    def transfer_modes(self):
        modes = {'full'} if self.pixel_format == 'P16' else {'fast'}
        if self.supports_eight_bit:
            modes.add('fast')
        if self.supports_ximg:
            modes.add('ximg')
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
        features = _tokens(channel.get('info.features'))
        if 'ATTACH' not in features:
            raise CameraReadError('This camera does not advertise the PH16 image connection used by Event Finder.')
        formats = _tokens(channel.get('info.imgformats'))
        wired_ximg = False
        camera_mac = None
        if (sys.platform == 'darwin' and 'XIMG' in features
                and {'P12L', '524'} & formats and {'P16', '272'} & formats):
            try:
                camera_mac = _reported_camera_mac(channel.request('ifconfig'), host)
            except CameraCommandError:
                # Older firmware may decline this optional information request.
                pass
            wired_ximg = can_receive(host, camera_mac)
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
                                  '8' in formats, wired_ximg and w % 2 == 0, camera_mac))
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
        self._mode = 'full'
        self._roi = None
        self._ximg = None
        self._ximg_disabled = ''
        self._ximg_calibrated = False
        self.ximg_frames = 0
        self.ximg_retries = 0
        self.ximg_standard_recoveries = 0
        self._consecutive_incomplete = 0
        self.transfer_notice = ''
        self._transfer_status = None

    def set_cancel_check(self, cancelled):
        self._cancelled = cancelled

    def set_transfer_status_callback(self, callback):
        self._transfer_status = callback

    def _notice(self, text):
        self.transfer_notice = text
        if self._transfer_status is not None:
            self._transfer_status(text)

    @property
    def batch_size(self):
        frame_bytes = self.width*self.height*(2 if self.pixel_format == 'P16' else 1)
        return max(1, min(256, (16 << 20)//frame_bytes))

    def set_transfer_mode(self, mode):
        if mode not in {'fast', 'full', 'ximg'} or (mode == 'ximg' and mode not in self.transfer_modes):
            raise ValueError('Choose a transfer mode supported by this camera and connection.')
        self.close()
        self._mode = mode
        self.ximg_frames = self.ximg_retries = 0
        self.ximg_standard_recoveries = self._consecutive_incomplete = 0
        self.pixel_format = ('8' if mode == 'fast' and 'fast' in self.transfer_modes
                             else self.recording.pixel_format)
        if mode == 'ximg' and self._ximg_disabled:
            self._notice(self._ximg_disabled)
        elif mode != 'ximg':
            self._notice(f'Standard Ethernet · {"16-bit" if self.pixel_format == "P16" else "8-bit"}')
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
        if self._ximg is not None:
            self._ximg.receiver.close()
            self._ximg = None
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        self.block = None
        self._previous = None
        self._sequence = False
        self._roi = None

    def _check_recording(self, channel):
        r = self.recording
        if not _stored(channel, r.cine) or _trigger(channel, r.cine) != r.trigger:
            raise CameraRecordingChangedError('This RAM recording changed. Reopen Camera recordings and select the current Cine.')

    def _verified_channel(self):
        if self.channel is None:
            self.channel = _Channel(self.recording.host, lambda: self._cancelled())
            if self.channel.get('info.serial') != self.recording.serial:
                raise CameraRecordingChangedError('A different camera is now at this address. Reopen Camera recordings.')
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

    def _ximg_region(self, channel, native, roi):
        if self._ximg is None:
            self._notice('Opening 10 Gb Ethernet · macOS may request permission.')
            receiver = PacketReceiver(self.recording.host, lambda: self._cancelled(), self.recording.camera_mac)
            try:
                self._ximg = XimgStream(receiver, self.recording, lambda: self._cancelled())
            except BaseException:
                receiver.close()
                raise
            self._notice('Using ximg · 12-bit')
        retries = self._ximg.retries
        try:
            packed = self._ximg.frame(channel, native)
        except IncompleteXimgFrame as err:
            self._consecutive_incomplete += 1
            if self._consecutive_incomplete >= 2:
                raise XimgError('Two successive images remained incomplete after retransmission.') from err
            _check(self._cancelled)
            self._check_recording(channel)
            x, y, w, h = roi
            standard = channel.images(self.recording, native, 1, 1, 'P16')[0]
            self._check_recording(channel)
            self.ximg_standard_recoveries += 1
            count = self.ximg_standard_recoveries
            self._notice(f'Using ximg · 12-bit · {count:,} {"frame" if count == 1 else "frames"} '
                         'recovered over standard Ethernet.')
            return standard[y:y+h, x:x+w] >> 4
        finally:
            self.ximg_retries += self._ximg.retries - retries
        self._consecutive_incomplete = 0
        if not self._ximg_calibrated:
            standard = channel.images(self.recording, native, 1, 1, 'P16')[0]
            if not matches_standard(packed, standard):
                raise XimgError('The 12-bit image differs from the standard readback.')
            self._ximg_calibrated = True
        self.ximg_frames += 1
        return decode_region(packed, self.width, roi)

    def _read_ximg_block(self, index, count, stride, roi):
        """Preserve the same linear 12-bit intensities if the fast path fails."""
        x, y, w, h = roi
        output = np.empty((count, h, w), np.uint16)
        try:
            channel = self._verified_channel()
            offset = 0
            if not self._ximg_disabled:
                try:
                    while offset < count:
                        native = self.first_frame + index + offset * stride
                        output[offset] = self._ximg_region(channel, native, roi)
                        offset += 1
                except (CancelledError, CameraRecordingChangedError):
                    raise
                except (RuntimeError, OSError, ValueError) as err:
                    _check(self._cancelled)
                    if self._ximg is not None:
                        self._ximg.receiver.close()
                        self._ximg = None
                    # A failed control request might leave a delayed reply in the socket.
                    channel.close()
                    self.channel = None
                    channel = self._verified_channel()
                    self._ximg_disabled = f'Standard Ethernet · 12-bit fallback. {err}'
                    self._notice(self._ximg_disabled)
            if offset < count:
                native = self.first_frame + index + offset * stride
                standard = channel.images(self.recording, native, count - offset, stride, 'P16')
                np.right_shift(standard[:, y:y+h, x:x+w], 4, out=output[offset:])
            self._check_recording(channel)
            return output
        except BaseException:
            self.close()
            raise

    def read_roi(self, index, roi):
        if not 0 <= index < self.count:
            raise ValueError('Frame is outside the recorded Cine.')
        x,y,w,h = roi
        if not (0 <= x < x+w <= self.width and 0 <= y < y+h <= self.height):
            raise ValueError('Choose a rectangle inside the recorded image.')
        _check(self._cancelled)
        frame_offset, remainder = divmod(index-self.start, self._block_stride)
        ximg = self._mode == 'ximg'
        if self.block is None or remainder or not 0 <= frame_offset < len(self.block) or (ximg and self._roi != roi):
            stride = count = 1
            if (self._sequence and self._scan_first <= index <= self._scan_last and
                    (index-self._scan_first) % self._scan_stride == 0):
                stride = self._scan_stride
                count = min(self.batch_size, 1+(self._scan_last-index)//stride)
            elif (not self._sequence and not self._reference and
                  (index == 0 or (self._previous is not None and index == self._previous+1))):
                count = min(self.batch_size, self.count-index)
            if ximg:
                self.block = self._read_ximg_block(index, count, stride, roi)
                self._roi = roi
            else:
                self.block = self._read(index, count, stride, self.pixel_format)
            self.start = index
            self._block_stride = stride
            frame_offset = 0
        self._previous = index
        result = self.block[frame_offset].copy() if ximg else self.block[frame_offset, y:y+h, x:x+w].copy()
        if (index == self.count-1 and not (ximg and self._reference)) or (self._sequence and index == self._scan_last):
            self.close()
        return result
