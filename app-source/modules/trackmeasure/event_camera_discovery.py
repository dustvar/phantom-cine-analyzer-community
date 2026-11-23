"""Find Phantom cameras using their wired discovery broadcast."""
from contextlib import ExitStack
from dataclasses import dataclass
import errno
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
import re
import select
import socket
import sys
from time import monotonic

from PySide6.QtNetwork import QNetworkInterface
from event_camera import READABLE_PROTOCOLS


_PORT = 7380
_QUERY = b'phantom?'
_XIP_NETWORK = IPv4Network('172.16.0.0/16')  # Conventional Phantom 10 Gb camera network
_GIGABIT_NETWORK = IPv4Network('100.100.0.0/16')
_REPLY = re.compile(
    rb'PH([1-9][0-9]{0,2})[ \t]+7115[ \t]+[0-9]{1,10}(?:\.[0-9]{1,10}){0,3}[ \t]+'
    rb'([0-9]{1,10})(?:[ \t]+"([\x20-\x21\x23-\x7e]{0,96})")?'
)


@dataclass(frozen=True)
class DiscoveredCamera:
    host: str
    serial: str
    name: str = ''  # Optional user-assigned camera name, distinct from its model
    protocol: str = '16'
    setup_device: str = ''  # A scoped reply from outside the adapter's IPv4 network.

    @property
    def needs_network_setup(self):
        return bool(self.setup_device) and self.protocol in READABLE_PROTOCOLS

    @property
    def connection_issue(self):
        if self.protocol not in READABLE_PROTOCOLS:
            return f'Camera found. PCA does not support its PH{self.protocol} protocol yet.'
        if self.needs_network_setup:
            return f'Camera found at {self.host}. Open Ethernet setup to put the adapter on its network.'
        return ''


def camera_groups(cameras):
    """Prefer reachable endpoints; an alternate address can require network setup."""
    groups = {}
    for camera in cameras:
        groups.setdefault(camera.serial, []).append(camera)
    return {serial: [c for c in group if not c.connection_issue] or group
            for serial, group in groups.items()}


def wired_interfaces():
    flag = QNetworkInterface.InterfaceFlag
    required = flag.IsRunning | flag.IsUp | flag.CanBroadcast
    for adapter in QNetworkInterface.allInterfaces():
        if adapter.type() != QNetworkInterface.InterfaceType.Ethernet:
            continue
        if adapter.flags() & required != required:
            continue
        addresses = []
        for entry in adapter.addressEntries():
            if entry.ip().toIPv4Address() and 1 <= entry.prefixLength() <= 30:
                addresses.append(IPv4Interface((entry.ip().toString(), entry.prefixLength())))
        yield adapter, tuple(dict.fromkeys(addresses))


def _wired_addresses():
    return [(adapter.index(), address) for adapter, addresses in wired_interfaces()
            for address in addresses]


def _parse(data, host, interface, scoped_device=''):
    if len(data) > 256:
        return None
    match = _REPLY.fullmatch(data.rstrip(b' \t\r\n\0'))
    if not match:
        return None
    ip = IPv4Address(host)
    if (ip.is_multicast or ip.is_loopback or ip.is_unspecified or ip == IPv4Address('255.255.255.255')
            or ip in (interface.ip, interface.network.network_address, interface.network.broadcast_address)):
        return None
    off_link = ip not in interface.network
    if off_link and not scoped_device:
        return None
    return DiscoveredCamera(host, match[2].decode('ascii'), (match[3] or b'').decode('ascii').strip(),
                            match[1].decode('ascii'), scoped_device if off_link else '')


def _search(addresses, cancelled, timeout=1.6):
    if cancelled():
        return []
    if not addresses:
        raise OSError('No active Ethernet adapter with an IPv4 address was found.')
    found = {}
    with ExitStack() as stack:
        listeners = {}
        for index, interface in addresses:
            if cancelled():
                return []
            try:
                sock = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                device = ''
                if sys.platform == 'darwin':
                    # Darwin's IP_BOUND_IF keeps even a limited broadcast off the default Wi-Fi route.
                    sock.setsockopt(socket.IPPROTO_IP, getattr(socket, 'IP_BOUND_IF', 25), index)
                    try:
                        device = socket.if_indextoname(index)
                    except OSError:
                        pass
                try:
                    sock.bind((str(interface.ip), _PORT))
                except OSError as exc:
                    if exc.errno != errno.EADDRINUSE:
                        raise
                    sock.bind((str(interface.ip), 0))
                sock.setblocking(False)
                sock.sendto(_QUERY, (str(interface.network.broadcast_address), _PORT))
                listeners[sock] = (interface, device)
            except OSError:
                continue
        if not listeners:
            raise OSError('PCA could not send camera discovery on the wired network. Check the Ethernet connection and local network access.')

        deadline = monotonic() + timeout
        retry_at = deadline - timeout / 2
        while listeners and not cancelled():
            now = monotonic()
            if now >= deadline:
                break
            if retry_at is not None and now >= retry_at:
                for sock, (interface, _) in listeners.items():
                    # Some firmware listens for a limited broadcast. Only use one on a scoped socket.
                    target = '255.255.255.255' if sys.platform == 'darwin' else str(interface.network.broadcast_address)
                    try:
                        sock.sendto(_QUERY, (target, _PORT))
                    except OSError:
                        pass
                retry_at = None
            readable, _, _ = select.select(list(listeners), [], [], min(.1, deadline - now))
            for sock in readable:
                try:
                    data, (host, _) = sock.recvfrom(257)
                except BlockingIOError:
                    continue
                except OSError:
                    # The operator may have unplugged one of several adapters.
                    del listeners[sock]
                    continue
                camera = _parse(data, host, *listeners[sock])
                if camera is not None:
                    key = (host, camera.serial)
                    if key not in found or not camera.setup_device:
                        found[key] = camera
    if cancelled():
        return []
    # Keep alternate addresses for the same camera, with its usual XIP first.
    return sorted(found.values(), key=lambda cam: (
        int(cam.serial), IPv4Address(cam.host) not in _XIP_NETWORK, IPv4Address(cam.host)))


def discover_cameras(cancelled=lambda: False):
    """Discover directly reachable cameras on active wired IPv4 interfaces."""
    return _search(_wired_addresses(), cancelled)


def empty_discovery_hint():
    addresses = [interface for _, interface in _wired_addresses()]
    if not addresses:
        return 'No active Ethernet adapter with an IPv4 address was found.'
    hosts = ', '.join(str(address.ip) for address in addresses)
    hint = 'Check that the camera and wired adapter use the same subnet in Ethernet setup.'
    if all(address.ip in _XIP_NETWORK for address in addresses):
        hint = 'Open Ethernet setup to add the other standard Phantom network (100.100.x.x).'
    elif all(address.ip in _GIGABIT_NETWORK for address in addresses):
        hint = 'Open Ethernet setup to add the other standard Phantom network (172.16.x.x).'
    return f'No camera replied on {hosts}. {hint}'
