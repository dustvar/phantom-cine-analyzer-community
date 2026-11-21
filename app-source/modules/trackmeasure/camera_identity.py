"""Phantom camera configuration and attached storage from read-only protocol fields.

PH16 §§4.2, 4.14–15 define info.memsz/info.cinemem in MB and media sizes
in kB. The display preserves the units for each capacity.
"""
from dataclasses import dataclass, replace
import re

from event_camera import CameraCommandError


def _read(channel, key):
    try:
        return channel.get(key)
    except CameraCommandError:
        # A camera may explicitly reject a field it doesn't implement.
        return ''


def _uint(value):
    value = value.strip()
    if re.fullmatch(r'0[xX][\da-fA-F]{1,8}', value):
        return int(value, 16)
    if re.fullmatch(r'[0-9]{1,10}', value):
        number = int(value)
        if number <= 0xffffffff:
            return number
    return None


def _media_status(channel, key):
    # Read state and size together so a card swap cannot mix two devices.
    response = _read(channel, key)
    fields = dict(re.findall(r'(\w+)\s*:\s*("[^"]*"|[^,{}]+)', response))
    state = _uint(fields.get('state', ''))
    size = _uint(fields.get('size', ''))
    if state == 0 or (key == 'cf' and state == 1):
        return 'No media inserted'
    if state is None:
        return 'Not reported'
    if state in (10, 11):
        return 'Media error'
    if key == 'cf':
        if state == 2:
            return 'Initializing'
        ready = state == 4
    else:
        kind = _uint(fields.get('type', ''))
        if kind in (1000, 1010, 2000):
            return 'CineStream adapter'
        if state in (1, 2, 3, 12, 13):
            return {1: 'Powered off', 2: 'Initializing', 3: 'Scanning',
                    12: 'Unrecognized device', 13: 'Unrecognized device'}[state]
        ready = state in (4, 5, 6, 7, 8, 9)
    if ready:
        return f'{size:,} kB' if size else 'Capacity not reported'
    return 'Status not reported'


@dataclass(frozen=True)
class CameraIdentity:
    cfa: int | None = None
    installed_mb: int | None = None
    recording_mb: int | None = None
    media_label: str = 'Not reported'

    @property
    def sensor_label(self):
        if self.cfa == 0:
            return 'Monochrome'
        if self.cfa in (1, 2, 3, 4, 5, 6):
            return 'Color'
        return 'Not reported' if self.cfa is None else f'Unknown CFA ({self.cfa})'

    @property
    def installed_memory_label(self):
        return f'{self.installed_mb:,} MB' if self.installed_mb else 'Not reported'

    @property
    def recording_memory_label(self):
        return f'{self.recording_mb:,} MB' if self.recording_mb is not None else 'Not reported'

    @property
    def summary(self):
        parts = []
        if self.sensor_label in ('Monochrome', 'Color'):
            parts.append(self.sensor_label)
        if self.installed_mb:
            parts.append(f'{self.installed_memory_label} RAM')
        elif self.recording_mb is not None:
            parts.append(f'{self.recording_memory_label} Cine RAM')
        return ' · '.join(parts)

    @property
    def details(self):
        return (f'Sensor: {self.sensor_label}\nInstalled RAM: {self.installed_memory_label}\n'
                f'Cine recording RAM: {self.recording_memory_label}\nStorage media: {self.media_label}')

    def with_current_media(self, channel, features):
        ports = [(label, _media_status(channel, key))
                 for key, label in (('cf', 'Storage port'), ('mag', 'CineMag port'))
                 if key.upper() in features]
        if not ports:
            return self
        if len(ports) == 1 or all(status == 'No media inserted' for _, status in ports):
            return replace(self, media_label=ports[0][1])
        return replace(self, media_label=' · '.join(f'{label}: {status}' for label, status in ports))


def read_camera_identity(channel):
    return CameraIdentity(cfa=_uint(_read(channel, 'info.cfa')),
                          installed_mb=_uint(_read(channel, 'info.memsz')) or None,
                          recording_mb=_uint(_read(channel, 'info.cinemem')))
