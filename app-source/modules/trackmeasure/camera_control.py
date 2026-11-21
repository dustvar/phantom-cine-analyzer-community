"""Operator-driven Phantom RAM acquisition, using PH16 sections 4.6–4.8 and 5.2–5.10."""
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass
import math
from numbers import Integral
import re
from time import monotonic

import numpy as np

from event_camera import CameraReadError, CameraCommandError, _Channel, _stored, _tokens, _trigger, validate_address


@dataclass(frozen=True)
class StoredCine:
    slot: int
    serial: str
    trigger: tuple


@dataclass(frozen=True)
class ControlStatus:
    model: str
    serial: str
    fps: float
    exposure_us: float
    post_frames: int
    capacity: int
    width: int
    height: int
    slots: tuple
    replaceable: tuple = ()
    min_fps: float | None = None
    max_fps: float | None = None
    min_exposure_us: float | None = None
    exposure_dead_us: float = 0.0
    manual_exposure: bool = True
    internal_clock: bool = True
    direct_recording: bool = False

    @property
    def active_slot(self):
        return next((number for number, flags in self.slots if number and 'ACT' in flags), None)

    @property
    def _active_flags(self):
        return next((flags for _, flags in self.slots if 'ACT' in flags), frozenset())

    @property
    def can_apply(self):
        flags = self._active_flags
        return not self.direct_recording and 'TRG' not in flags and bool({'PRE', 'WTR'} & flags)

    @property
    def can_arm(self):
        return (not self.direct_recording and 'PRE' in self._active_flags
                and any(n and 'RDY' in flags for n, flags in self.slots))

    @property
    def can_trigger(self):
        flags = self._active_flags
        return (not self.direct_recording and self.active_slot is not None and
                {'WTR', 'ABL'} <= flags and not {'TRG', 'STR', 'PRE'} & flags)


def _cine_states(reply):
    # Accept the documented cstats table. An unexpected/missing row must never
    # be interpreted as an empty partition that is safe to record into.
    matches = re.findall(r'\bc(\d+)\s*:\s*\{([^{}]*)\}', reply, flags=re.I)
    states = []
    for expected, (number, body) in enumerate(matches):
        if int(number) != expected or expected > 256:
            raise CameraReadError('Camera returned an inconsistent RAM partition table.')
        flags = frozenset(_tokens(body))
        if flags == {'INV'}:
            if expected != len(matches) - 1:
                raise CameraReadError('Camera returned an invalid RAM partition table.')
            break
        if not flags or 'INV' in flags:
            raise CameraReadError('Camera returned an invalid partition state.')
        states.append((expected, flags))
    if not states or 'PRE' not in states[0][1] or sum('ACT' in f for _, f in states) > 1:
        raise CameraReadError('Could not identify the camera’s active Cine.')
    return tuple(states)


def _resolution(value):
    m = re.fullmatch(r'\s*(\d+)\s*[xX]\s*(\d+)\s*', value)
    if m:
        w, h = map(int, m.groups())
        if 0 < w*h <= 64*1024**2:
            return w, h
    raise CameraReadError('The camera returned unsupported image dimensions.')


def _positive(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = math.nan
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise ValueError(f'{label} must be a positive finite number.')
    return number


class CameraControl:
    """One serialized session. Dropped connections are reidentified on the next action."""

    def __init__(self, host, expected_serial='', cancelled=lambda: False):
        self.host = validate_address(host)
        self.serial = str(expected_serial or '')
        self.cancelled = cancelled
        self.channel = None

    def close(self):
        if self.channel is not None:
            self.channel.close()
            self.channel = None

    def stop_preview(self):
        # Closing both ends releases the camera's single image stream without
        # racing an outstanding transfer. A later status read reopens control.
        if self.channel is not None and self.channel.data is not None:
            self.close()

    @contextmanager
    def _connection(self):
        try:
            if self.channel is None:
                channel = self.channel = _Channel(self.host, self.cancelled)
                if channel.get('info.pver') != '16':
                    raise CameraReadError('Camera Control requires the Phantom PH16 protocol.')
                serial = channel.get('info.serial')
                if not serial.isdigit() or (self.serial and self.serial != serial):
                    raise CameraReadError('The camera identity changed. Select the camera again.')
                self.serial = serial
                self.model = channel.get('info.model')
                self.features = _tokens(channel.get('info.features'))
                formats = _tokens(channel.get('info.imgformats'))
                self.format = '8' if '8' in formats else ('P16' if {'P16','272'} & formats else None)
                self.min_fps = self._limit(channel, 'info.minfrate')
                self.max_fps = self._limit(channel, 'info.maxrate')
                self.min_exp_ns = self._limit(channel, 'info.minexp')
                self.dead_ns = self._limit(channel, 'info.expdead') or 0
            yield self.channel
        except (CameraReadError, CancelledError, OSError):
            self.close()
            raise

    @staticmethod
    def _limit(channel, key):
        try:
            value = float(channel.get(key))
        except CameraCommandError:
            return None
        if not math.isfinite(value) or value < 0:
            raise CameraReadError(f'Invalid camera limit for {key}.')
        return value

    def _status(self, channel):
        width, height = _resolution(channel.get('defc.res'))
        fps = _positive(channel.get('defc.rate'), 'Camera frame rate')
        exposure = _positive(channel.get('defc.exp'), 'Camera exposure') / 1000
        capacity = int(channel.get('defc.frcount'))
        post = int(channel.get('defc.ptframes'))
        if min(capacity, post) < 0:
            raise CameraReadError('Camera returned an invalid trigger position or frame capacity.')
        manual = not int(channel.get('defc.shoff'))
        if 'AEXP' in self.features:
            manual = manual and not int(channel.get('defc.aexpmode'))
        internal = int(channel.get('cam.syncimg')) == 0
        direct = 'MAG' in self.features and (
            int(channel.get('mag.runstop')) != 0 or int(channel.get('mag.state')) == 5)
        slots = _cine_states(channel.request('cstats'))
        replaceable = ()
        if (not direct and {'PRE','ACT'} <= slots[0][1] and
                not any(n and 'RDY' in flags for n, flags in slots)):
            replaceable = tuple(StoredCine(n, self.serial, _trigger(channel, n))
                                for n, flags in slots if n and 'STR' in flags)
        return ControlStatus(self.model, self.serial, fps, exposure, post, capacity,
                             width, height, slots, replaceable, self.min_fps, self.max_fps,
                             None if self.min_exp_ns is None else self.min_exp_ns / 1000,
                             self.dead_ns / 1000, manual, internal, direct)

    def status(self):
        with self._connection() as channel:
            return self._status(channel)

    def preview(self):
        with self._connection() as channel:
            if 'ATTACH' not in self.features or self.format is None:
                raise CameraReadError('This camera does not offer a supported live image format over Ethernet.')
            channel.attach()
            reply = channel.request(f'img {{cine:-1, start:0, cnt:1, fmt:{self.format}}}')
            values = dict(re.findall(r'(\w+)\s*:\s*([^,}]+)', reply))
            # A hardware trigger can change cine/resolution at any instant.
            # PH16 returns the actual dimensions in this image's atomic header.
            cine = values.get('cine', '').strip()
            fmt = values.get('fmt', self.format).strip().upper()
            if (not reply.upper().startswith('OK!') or not re.fullmatch(r'-1|\d+', cine)
                    or not (fmt == self.format or self.format == 'P16' and fmt == '272')):
                raise CameraReadError('Unexpected metadata for the live sensor image.')
            width, height = _resolution(values.get('res', ''))
            pixels = np.empty((height, width), dtype='u1' if fmt == '8' else '<u2')
            channel._read_pixels(pixels)
            return pixels if fmt == '8' else (pixels >> 8).astype(np.uint8)

    @staticmethod
    def _command(channel, wire):
        try:
            response = channel.request(wire)
        except CameraCommandError:
            raise
        except (CameraReadError, OSError) as exc:
            raise CameraReadError('The camera did not confirm the command. Check its state before retrying.') from exc
        if response.strip().upper() != 'OK!':
            raise CameraReadError('Unexpected camera acknowledgement. Check the camera state before retrying.')

    def apply(self, fps=None, exposure_us=None, post_frames=None):
        with self._connection() as channel:
            s = self._status(channel)
            if not s.can_apply:
                raise ValueError('Wait for RAM acquisition to finish before applying capture settings.')
            fields = []
            rate = s.fps if fps is None else _positive(fps, 'Frame rate')
            if rate != s.fps:
                if not s.internal_clock:
                    raise ValueError('The camera is using an external frame sync source.')
                if (s.min_fps is not None and rate < s.min_fps or
                        s.max_fps is not None and rate > s.max_fps):
                    raise ValueError('Frame rate is outside the range reported by this camera.')
                fields.append(f'rate:{rate:.12g}')
            if exposure_us is not None and _positive(exposure_us, 'Exposure') != s.exposure_us:
                if not s.manual_exposure:
                    raise ValueError('Manual exposure is unavailable in the current camera exposure mode.')
                exposure = float(exposure_us)
                if exposure > 1e6/rate:
                    raise ValueError('Exposure is longer than the requested frame period.')
                exp_ns = round(exposure*1000)
                if not (max(1, self.min_exp_ns or 1) <= exp_ns <= 1e9/rate-self.dead_ns):
                    raise ValueError('Exposure is outside the available window at the requested frame rate.')
                fields.append(f'exp:{exp_ns}')
            if post_frames is not None:
                if not isinstance(post_frames, Integral) or isinstance(post_frames, bool):
                    raise ValueError('Post-trigger frames must be a whole number.')
                if post_frames != s.post_frames:
                    if not 0 <= post_frames <= s.capacity:
                        raise ValueError('Post-trigger frames must fit in the current RAM partition.')
                    fields.append(f'ptframes:{int(post_frames)}')
            if not fields:
                return s
            self._command(channel, 'set defc {' + ', '.join(fields) + '}')
            return self._status(channel)

    def arm(self, replace=None):
        with self._connection() as channel:
            s = self._status(channel)
            if replace is None:
                if not s.can_arm:
                    raise ValueError('No empty RAM Cine is ready to arm.')
                # With no argument PH16 can only use a ready, empty partition.
                command = 'rec'
            else:
                if not isinstance(replace, StoredCine) or replace not in s.replaceable:
                    raise ValueError('The stored recording changed. Review the current Cine before replacing it.')
                if not _stored(channel, replace.slot) or _trigger(channel, replace.slot) != replace.trigger:
                    raise ValueError('The recording changed before arming. Review the current Cine.')
                command = f'rec {replace.slot}'
            self._command(channel, command)
            return self._status(channel)

    def trigger(self, expected_slot, *, deadline=None):
        def check_deadline():
            if deadline is not None and (not math.isfinite(deadline) or monotonic() >= deadline):
                raise ValueError('Trigger expired while waiting for the camera. No trigger was sent.')

        check_deadline()
        with self._connection() as channel:
            s = self._status(channel)
            if (not s.can_trigger or not isinstance(expected_slot, Integral) or
                    isinstance(expected_slot, bool) or s.active_slot != expected_slot):
                raise ValueError('The selected RAM Cine is no longer waiting for a trigger.')
            # Connecting, identifying the camera, and reading its current state
            # can take longer than the operator's original click remains valid.
            check_deadline()
            self._command(channel, 'trig')
            return self._status(channel)
