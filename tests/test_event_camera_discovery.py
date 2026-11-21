"""Exercise Phantom broadcast discovery without sending queries onto a real network."""
from ipaddress import IPv4Interface
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtNetwork import QHostAddress, QNetworkAddressEntry, QNetworkInterface

import event_camera_discovery as discovery


class DiscoveryTests(unittest.TestCase):
    def test_wire_formats(self):
        net = IPv4Interface('10.3.4.3/24')
        peer = '10.3.4.25'
        self.assertEqual(discovery._parse(b'PH16 7115 8001 1234\r\n', peer, net),
                         discovery.DiscoveredCamera(peer, '1234'))
        self.assertEqual(discovery._parse(b'PH16 7115 1.0.0.5 1234 "Rig A"\x00', peer, net),
                         discovery.DiscoveredCamera(peer, '1234', 'Rig A'))
        for data in (b'phantom?', b'PH7 7115 123 456', b'PH16 123 1 4321',
                     b'PH16 7115 2 x1234', b'PH16 7115 2 1234\r\nother',
                     b'PH16 7115 2 1234 "' + b'A' * 260 + b'"'):
            with self.subTest(data=data):
                self.assertIsNone(discovery._parse(data, peer, net))
        for host in ('10.3.4.0', '10.3.4.255', '10.4.0.10', str(net.ip)):
            with self.subTest(host=host):
                self.assertIsNone(discovery._parse(b'PH16 7115 2 1234', host, net))

    def test_active_ethernet_aliases_only(self):
        types = QNetworkInterface.InterfaceType
        flags = QNetworkInterface.InterfaceFlag
        active = flags.IsUp | flags.IsRunning | flags.CanBroadcast
        entries = []
        for ip in ('10.3.4.3', '172.16.9.9', 'fe80::1'):
            entry = QNetworkAddressEntry()
            entry.setIp(QHostAddress(ip))
            entry.setPrefixLength(24)
            entries.append(entry)

        def adapter(kind, state):
            interface = MagicMock()
            interface.type.return_value = kind
            interface.flags.return_value = state
            interface.index.return_value = 12
            interface.addressEntries.return_value = entries
            return interface

        ethernet = adapter(types.Ethernet, active)
        wifi = adapter(types.Wifi, active)
        unplugged = adapter(types.Ethernet, flags.IsUp)
        with patch.object(QNetworkInterface, 'allInterfaces', return_value=[wifi, unplugged, ethernet]):
            self.assertEqual(discovery._wired_addresses(), [
                (12, IPv4Interface('10.3.4.3/24')), (12, IPv4Interface('172.16.9.9/24'))])
        wifi.addressEntries.assert_not_called()
        unplugged.addressEntries.assert_not_called()

    def test_sends_on_each_alias_keeps_alternates_and_prioritizes_xip(self):
        addresses = [(7, IPv4Interface('100.100.8.7/16')), (7, IPv4Interface('172.16.9.7/16'))]
        hosts = ['100.100.2.9', '172.16.6.10']
        listeners = []
        for host in hosts:
            sock = MagicMock()
            sock.__enter__.return_value = sock
            sock.recvfrom.return_value = (b'PH16 7115 8001 1234 "My camera"', (host, 7380))
            listeners.append(sock)
        with patch.object(discovery.socket, 'socket', side_effect=listeners), \
                patch.object(discovery.sys, 'platform', 'darwin'), \
                patch.object(discovery, 'monotonic', side_effect=[0, 0.01, 0.081, 0.16, 0.18]), \
                patch.object(discovery.select, 'select', return_value=(listeners, [], [])):
            cameras = discovery._search(addresses, lambda: False, timeout=.16)
        self.assertEqual([c.host for c in cameras], list(reversed(hosts)))
        for i, sock in enumerate(listeners):
            net = addresses[i][1]
            sock.bind.assert_called_once_with((str(net.ip), 0))
            sock.setsockopt.assert_any_call(discovery.socket.IPPROTO_IP, 25, 7)
            self.assertEqual([call.args for call in sock.sendto.call_args_list], [
                (b'phantom?', (str(net.network.broadcast_address), 7380)),
                (b'phantom?', ('255.255.255.255', 7380))])
            sock.__exit__.assert_called_once()

    def test_cancellation_and_no_ethernet(self):
        with patch.object(discovery.socket, 'socket') as factory:
            self.assertEqual(discovery._search([], lambda: True), [])
            with self.assertRaisesRegex(OSError, 'No active Ethernet'):
                discovery._search([], lambda: False)
            factory.assert_not_called()

    def test_one_unavailable_alias_does_not_hide_another_camera(self):
        addresses = [(5, IPv4Interface('10.3.4.2/24')), (6, IPv4Interface('10.3.5.2/24'))]
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.recvfrom.return_value = (b'PH16 7115 1 987', ('10.3.5.90', 7380))
        with patch.object(discovery.socket, 'socket', side_effect=[OSError('gone'), sock]), \
                patch.object(discovery, 'monotonic', side_effect=[0, 0, 2]), \
                patch.object(discovery.select, 'select', return_value=([sock], [], [])):
            self.assertEqual(discovery._search(addresses, lambda: False),
                             [discovery.DiscoveredCamera('10.3.5.90', '987')])
