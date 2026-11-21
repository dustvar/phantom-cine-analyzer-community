"""Find PH16 and PH23 cameras using Phantom's wired discovery broadcast."""
from contextlib import ExitStack
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
import re
import select
import socket
import sys
from time import monotonic

from PySide6.QtNetwork import QNetworkInterface


_PORT = 7380
_QUERY = b'phantom?'
_XIP_NETWORK = IPv4Network('172.16.0.0/16')  # Conventional Phantom 10 Gb camera network
_GIGABIT_NETWORK = IPv4Network('100.100.0.0/16')
_REPLY = re.compile(
    rb'PH(?:16|23)[ \t]+7115[ \t]+[0-9]{1,10}(?:\.[0-9]{1,10}){0,3}[ \t]+'
    rb'([0-9]{1,10})(?:[ \t]+"([\x20-\x21\x23-\x7e]{0,96})")?'
)


@dataclass(frozen=True)
class DiscoveredCamera:
    host: str
    serial: str
    name: str = ''  # Optional user-assigned camera name, distinct from its model


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


def _parse(data, host, interface):
    if len(data) > 256:
        return None
    match = _REPLY.fullmatch(data.rstrip(b' \t\r\n\0'))
    if not match:
        return None
    ip = IPv4Address(host)
    if (ip not in interface.network or ip.is_multicast or ip.is_loopback
            or ip in (interface.ip, interface.network.network_address, interface.network.broadcast_address)):
        return None
    return DiscoveredCamera(host, match[1].decode('ascii'), (match[2] or b'').decode('ascii').strip())


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
                if sys.platform == 'darwin':
                    # Darwin's IP_BOUND_IF keeps even a limited broadcast off the default Wi-Fi route.
                    sock.setsockopt(socket.IPPROTO_IP, getattr(socket, 'IP_BOUND_IF', 25), index)
                sock.bind((str(interface.ip), 0))
                sock.setblocking(False)
                sock.sendto(_QUERY, (str(interface.network.broadcast_address), _PORT))
                listeners[sock] = interface
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
                for sock, interface in listeners.items():
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
                camera = _parse(data, host, listeners[sock])
                if camera is not None:
                    found[(host, camera.serial)] = camera
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
    hint = 'Check that the camera and wired adapter use the same subnet, then refresh.'
    if all(address.ip in _XIP_NETWORK for address in addresses):
        hint = 'For a camera labeled 100.100.x.x, give the adapter a different address on that subnet, then refresh.'
    elif all(address.ip in _GIGABIT_NETWORK for address in addresses):
        hint = 'For a camera labeled 172.16.x.x, give the adapter a different address on that subnet, then refresh.'
    return f'No camera replied on {hosts}. {hint}'
