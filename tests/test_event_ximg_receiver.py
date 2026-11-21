"""Test the camera-only capture boundary and packet stream without network access."""
from concurrent.futures import CancelledError
import os
import struct
import sys
import threading
import unittest
from unittest.mock import patch

import event_ximg_receiver as rx


PEER = '192.0.2.20'
LINK = rx.CameraLink('en9', '021122334455', '020102030405')
ROUTE = 'interface: en9\nflags: <UP,HOST,DONE,LLINFO>\n'
ADAPTER = 'ether 2:1:2:3:4:5\nmedia: autoselect (10Gbase-T <full-duplex>)\nstatus: active\n'
NEIGHBOR = '? (192.0.2.20) at 2:11:22:33:44:55 on en9 ifscope [ethernet]'


class InterfaceTests(unittest.TestCase):
    def test_requires_direct_wired_camera_and_resolves_both_addresses(self):
        with patch.object(sys, 'platform', 'darwin'), patch.object(rx, '_system_info',
                side_effect=[ROUTE, ADAPTER, NEIGHBOR]):
            self.assertEqual(rx.resolve_link(PEER), LINK)
        for replies in ([ROUTE.replace('HOST', 'GATEWAY')], ['interface: en9'],
                        [ROUTE, ADAPTER.replace('active', 'inactive')],
                        [ROUTE, ADAPTER.replace('10Gbase-T', '1000baseT')], [ROUTE, 'media: autoselect'],
                        [ROUTE, ADAPTER, NEIGHBOR.replace('en9', 'en1')],
                        [ROUTE, ADAPTER, NEIGHBOR.replace('2:11', '1:11')]):
            with self.subTest(replies=replies), patch.object(sys, 'platform', 'darwin'), \
                    patch.object(rx, '_system_info', side_effect=replies):
                self.assertFalse(rx.can_receive(PEER))

    def test_verified_camera_interface_supplies_mac_when_neighbor_table_is_hidden(self):
        for camera_mac in (LINK.source, '02:11:22:33:44:55'):
            with patch.object(sys, 'platform', 'darwin'), patch.object(rx, '_system_info',
                    side_effect=[ROUTE, ADAPTER]) as details:
                self.assertEqual(rx.resolve_link(PEER, camera_mac), LINK)
                self.assertEqual(details.call_count, 2)

    def test_frozen_macos_privacy_placeholder_requires_the_exact_adapter(self):
        redacted = ADAPTER.replace('2:1:2:3:4:5', '02:00:00:00:00:00')
        hardware = 'Ethernet Address: 02:01:02:03:04:05 (Device: en9)\n'
        with patch.object(sys, 'platform', 'darwin'), patch.object(rx, '_system_info',
                side_effect=[ROUTE, redacted, hardware]) as details:
            self.assertEqual(rx.resolve_link(PEER, LINK.source), LINK)
            self.assertEqual(details.call_args.args, ('/usr/sbin/networksetup', '-getmacaddress', 'en9'))
        for other in (hardware.replace('en9', 'en8'), hardware.replace('02:01:02:03:04:05','02:00:00:00:00:00')):
            with patch.object(sys, 'platform', 'darwin'), patch.object(rx, '_system_info',
                    side_effect=[ROUTE, redacted, other]):
                self.assertFalse(rx.can_receive(PEER, LINK.source))

    def test_filter_applies_both_macs_and_direction_without_promiscuous_mode(self):
        args = rx._tcpdump_args(LINK)
        self.assertIn('-p', args)
        self.assertEqual(args[args.index('-Q')+1], 'in')
        self.assertEqual(args[-1], 'ether src 02:11:22:33:44:55 and '
            'ether dst 02:01:02:03:04:05 and ether proto 0x88b7')
        for link in (rx.CameraLink('any', LINK.source, LINK.dest),
                     rx.CameraLink('en0', ';open', LINK.dest)):
            with self.assertRaises(ValueError):
                rx._capture_command(link, '/tmp/example', 501)


@unittest.skipIf(sys.platform == 'win32', 'macOS capture uses POSIX stream descriptors')
class CaptureTests(unittest.TestCase):
    # The child models a capture utility which is idle during cancellation, or
    # splits a pcap record across reads. It never opens any network interfaces.
    def _receiver(self, body, cancelled=lambda:False):
        child = 'import os,time,sys\nos.write(2,b"listening on simulated capture\\n")\n'+body
        with patch.object(rx, 'resolve_link', return_value=LINK), \
                patch.object(rx.glob, 'glob', return_value=[__file__]), \
                patch.object(rx, '_tcpdump_args', return_value=[sys.executable, '-c', child]):
            return rx.PacketReceiver(PEER, cancelled)

    @staticmethod
    def _data(endian='<', truncated=False):
        header = struct.pack(endian+'IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
        packet = bytes(range(64))
        rec = struct.pack(endian+'IIII', 100, 10, 64, 65 if truncated else 64)
        return header, rec, packet

    def test_packets_preserved_across_timeout_and_split_headers(self):
        for endian in ('<','>'):
            h,r,p = self._data(endian)
            with self.subTest(endian=endian):
                rxr = self._receiver(f'os.write(1,{(h+r[:4])!r})\ntime.sleep(.2)\n'
                    f'os.write(1,{(r[4:]+p+r+p)!r})\ntime.sleep(60)')
                try:
                    self.assertIsNone(rxr.next_packet(.01))
                    self.assertEqual(rxr.next_packet(2), p)
                    self.assertEqual(rxr.next_packet(2), p)
                finally:
                    rxr.close()

    def test_rejects_truncation_before_emitting_data(self):
        pieces = self._data(truncated=True)
        rxr = self._receiver(f'os.write(1,{b"".join(pieces)!r})\ntime.sleep(60)')
        try:
            with self.assertRaisesRegex(rx.ReceiverError, 'truncated'):
                rxr.next_packet(2)
        finally:
            rxr.close()

    def test_cancels_and_closes_idle_stream_and_process(self):
        cancelled = threading.Event()
        rxr = self._receiver('time.sleep(60)', cancelled.is_set)
        child = rxr._process
        try:
            cancelled.set()
            with self.assertRaises(CancelledError):
                rxr.next_packet(30)
        finally:
            rxr.close()
        self.assertIsNotNone(child.poll())
        self.assertFalse(rxr._reader.is_alive())
        rxr.close()


if __name__ == '__main__':
    unittest.main()
