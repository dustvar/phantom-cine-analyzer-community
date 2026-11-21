"""Known packed wire samples, packet loss, cancellation and RAM readback fallback."""
from collections import deque
from concurrent.futures import CancelledError
from dataclasses import replace
import re
import unittest
from unittest.mock import patch

import numpy as np

import event_camera as ec
from event_ximg import XimgStream, XimgError, decode_region


def _packed(image):
    left, right = image[:, ::2], image[:, 1::2]
    b = np.empty((*left.shape, 3), np.uint8)
    b[:, :, 0] = left >> 4
    b[:, :, 1] = ((left & 15) << 4) | (right >> 8)
    b[:, :, 2] = right & 255
    return b.tobytes()


class _Wire:
    dest, source = '020000000001', '020000000002'

    def __init__(self):
        self.queue = deque()
        self.closed = False

    def close(self):
        self.closed = True

    def next_packet(self, _timeout):
        return self.queue.popleft() if self.queue else None

    def emit(self, stream, data, corrupt):
        frames = []
        for index, pos in enumerate(range(0, len(data), 64)):
            hdr = bytearray(32)
            hdr[:14] = bytes.fromhex(self.dest + self.source + '88b7')
            hdr[14], hdr[15] = 64, 128 if index == 0 else 0
            hdr[16:18] = ((65534 + index) % 65536).to_bytes(2, 'big')
            hdr[22:26] = stream.to_bytes(4, 'big')
            hdr[28:32] = len(data).to_bytes(4, 'big')
            payload = data[pos:pos+64]
            frames.append(bytes(hdr) + payload.ljust(28, b'\0'))
        if corrupt == 'gap':
            del frames[1]
        if corrupt == 'repeat':
            frames.insert(1, frames[0])
        if corrupt == 'length':
            frames[1] = frames[1][:-1]
        if corrupt == 'declared':
            frames[0] = frames[0][:28] + bytes(4) + frames[0][32:]
        if corrupt == 'foreign':
            # Wrong src/dst, stream and EtherType are ignored, even while reading a frame.
            for offset in (0, 6, 12, 22):
                wrong = bytearray(frames[0])
                wrong[offset] ^= 1
                self.queue.append(bytes(wrong))
        self.queue.extend(frames)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.rec = ec.CameraRecording('192.0.2.7', 'Demo', '123', 1, -7, 1, 20, 9, 1000,
                                      (2, 8), 'P16', True, True)
        self.images = np.random.default_rng(51).integers(0, 65536, (9, 9, 20), dtype=np.uint16)
        self.wire = _Wire()
        self.corrupt = deque()
        self.standard = []
        self.requests = []
        self.ssrc = 712
        self.control_closed = False
        self.reply_override = None
        self.channel = self.make_channel()

    def make_channel(self):
        test = self
        class Channel:
            def close(self):
                test.control_closed = True

            def get(self, key):
                return {'info.serial': '123', 'c1.state': '{ STR }',
                        'c1.trigtime': '{ secs: 2, frac: 8 }'}[key]

            def images(self, recording, native, count, stride, fmt):
                test.standard.append((native, count, stride, fmt))
                lo = native - recording.first_frame
                return test.images[lo:lo+count*stride:stride]

            def request(self, cmd):
                match = re.fullmatch(r'ximg \{cine:1, start:(-?\d+), cnt:1, fmt:P12L, dest:020000000001\}', cmd)
                if not match:
                    raise AssertionError(cmd)
                native = int(match[1])
                test.requests.append(native)
                test.ssrc += 1
                data = test.images[native - test.rec.first_frame] >> 4
                fault = test.corrupt.popleft() if test.corrupt else None
                if fault == 'wrong_pixels':
                    data = data.copy()
                    data[-1, -1] ^= 1
                test.wire.emit(test.ssrc, _packed(data), fault)
                return (test.reply_override if test.reply_override is not None else
                        f'Ok! {{cine:1, res:20 x 9, fmt:524, ssrc:{test.ssrc}}}')
        return Channel()

    def setup_source(self):
        channel = patch.object(ec, '_Channel', return_value=self.channel)
        channel.start()
        self.addCleanup(channel.stop)
        receiver = patch.object(ec, 'PacketReceiver', return_value=self.wire)
        construct = receiver.start()
        self.addCleanup(receiver.stop)
        src = self.rec.open()
        self.addCleanup(src.close)
        src.set_transfer_mode('ximg')
        return src, construct

    def test_known_packed_samples_and_odd_crops(self):
        samples = bytes.fromhex('12 3a bc ff f0 00')
        self.assertEqual(decode_region(samples, 4, (0, 0, 4, 1)).tolist(), [[0x123, 0xabc, 0xfff, 0]])
        self.assertEqual(decode_region(samples, 4, (1, 0, 3, 1)).tolist(), [[0xabc, 0xfff, 0]])

    def test_sparse_scan_with_off_grid_endpoint_retries_and_crop_change(self):
        src, construct = self.setup_source()
        self.corrupt.extend(['gap', 'foreign', 'repeat', None])
        notifications = []
        src.set_transfer_status_callback(notifications.append)
        src.set_scan_stride(3, 0, 8)
        for i in (0, 3, 6, 8):
            np.testing.assert_array_equal(src.read_roi(i, (3, 2, 5, 4)), self.images[i, 2:6, 3:8] >> 4)
        self.assertEqual(self.requests, [-7, -7, -4, -4, -1, 1])
        self.assertEqual(self.standard, [(-7, 1, 1, 'P16')])
        self.assertEqual((src.ximg_frames, src.ximg_retries), (4, 2))
        self.assertTrue(self.wire.closed)
        self.assertIn('Using ximg', ' '.join(notifications))
        self.assertEqual(construct.call_count, 1)
        self.wire.closed = False
        src.set_transfer_mode('ximg')
        a = src.read_roi(1, (0, 0, 1, 1))
        b = src.read_roi(1, (1, 0, 1, 1))
        self.assertEqual(a.item(), int(self.images[1, 0, 0]) >> 4)
        self.assertEqual(b.item(), int(self.images[1, 0, 1]) >> 4)
        self.assertEqual(len(self.standard), 1)

    def test_single_exhausted_frame_recovers_at_same_precision_and_resumes_ximg(self):
        src, _ = self.setup_source()
        self.corrupt.extend([None, 'length', 'repeat', 'gap'])
        src.set_scan_stride(2, 0, 5)
        for i in (0, 2, 4, 5):
            np.testing.assert_array_equal(src.read_roi(i, (19, 3, 1, 2)), self.images[i, 3:5, 19:20] >> 4)
        self.assertEqual(self.requests, [-7, -5, -5, -5, -3, -2])
        self.assertEqual((src.ximg_frames, src.ximg_standard_recoveries), (3, 1))
        self.assertEqual(self.standard, [(-7, 1, 1, 'P16'), (-5, 1, 1, 'P16')])
        self.assertIn('1 frame recovered', src.transfer_notice)

    def test_fallback_only_after_consecutive_exhausted_frames(self):
        src, _ = self.setup_source()
        self.corrupt.extend(['gap'] * 3 + [None] + ['gap'] * 6)
        src.set_scan_stride(2, 0, 8)
        for i in range(0,9,2):
            np.testing.assert_array_equal(src.read_roi(i, (0, 0, 2, 2)), self.images[i, :2, :2] >> 4)
        self.assertEqual(self.requests, [-7]*3 + [-5] + [-3]*3 + [-1]*3)
        self.assertEqual(self.standard, [(-7, 1, 1, 'P16'), (-5, 1, 1, 'P16'),
                                        (-3, 1, 1, 'P16'), (-1, 2, 2, 'P16')])
        self.assertEqual((src.ximg_frames, src.ximg_retries, src.ximg_standard_recoveries), (1, 6, 2))
        self.assertIn('fallback', src.transfer_notice)

    def test_recording_replaced_during_recovery_aborts_whole_block(self):
        src, _ = self.setup_source()
        self.corrupt.extend([None, 'gap', 'gap', 'gap'])
        normal = self.channel.images
        def read_and_replace(rec, native, count, stride, fmt):
            pixels = normal(rec, native, count, stride, fmt)
            if native == -6:
                self.channel.get = lambda _: '{ secs: 4, frac: 2 }'
            return pixels
        self.channel.images = read_and_replace
        src.set_scan_stride(1, 0, 2)
        with self.assertRaises(ec.CameraRecordingChangedError):
            src.read_roi(0, (0, 0, 2, 2))
        self.assertIsNone(src.block)
        self.assertTrue(self.wire.closed)

    def test_denied_permission_only_prompts_once_per_reader(self):
        src, constructor = self.setup_source()
        constructor.side_effect = PermissionError('Access denied.')
        for _ in range(2):
            src.set_transfer_mode('ximg')
            src.set_scan_stride(1, 2, 2)
            roi = src.read_roi(2, (1, 0, 4, 4))
            np.testing.assert_array_equal(roi, self.images[2, :4, 1:5] >> 4)
        self.assertEqual(constructor.call_count, 1)
        self.assertIn('Access denied', src.transfer_notice)

    def test_first_frame_check_catches_wrong_pixels_even_outside_roi(self):
        src, _ = self.setup_source()
        self.corrupt.append('wrong_pixels')
        src.read_roi(2, (0, 0, 1, 1))
        self.assertEqual(src.ximg_frames, 0)
        self.assertIn('differs', src.transfer_notice)

    def test_cancellation_aborts_without_standard_transfer(self):
        src, constructor = self.setup_source()
        constructor.side_effect = CancelledError()
        with self.assertRaises(CancelledError):
            src.read_roi(0, (0, 0, 1, 1))
        self.assertFalse(self.standard)
        self.assertTrue(self.control_closed)

    def test_mismatched_readback_identity_or_format_is_rejected(self):
        ximg = XimgStream(self.wire, self.rec, lambda: False)
        for reply in ('Ok! {cine:1,res:20 x 9,fmt:266,ssrc:1}',
                      'Ok! {cine:1,res:20 x 9,fmt:P12L}',
                      'Ok! {cine:1,res:20 x 9,fmt:524,ssrc:4294967296}',
                      'Ok! {cine:2,res:20 x 9,fmt:524,ssrc:1}',
                      'Ok! {cine:1,res:20 x 8,fmt:524,ssrc:1}'):
            self.reply_override = reply
            with self.subTest(reply=reply), self.assertRaises(XimgError):
                ximg.frame(self.channel, -7)

    def test_unadvertised_mode_cannot_be_selected(self):
        with self.assertRaises(ValueError):
            replace(self.rec, supports_ximg=False).open().set_transfer_mode('ximg')

    def test_fast_mode_requires_compatible_recording_camera_and_wired_mac(self):
        metadata = {'info.pver': '16', 'info.features': 'attach ximg', 'info.imgformats': '8 P16 P12L',
                    'info.model': 'Demo', 'info.serial': '123', 'cam.cines': '1',
                    'c1.state': '{ STR }', 'c1.trigtime': '{ secs: 2, frac: 8 }',
                    'c1.firstfr': '-7', 'c1.lastfr': '1', 'c1.res': '20 x 9', 'c1.rate': '1000'}
        self.channel.get = metadata.__getitem__
        reported = 'portA Link encap:Ethernet HWaddr 02:00:00:00:00:07 inet addr:192.0.2.7'
        self.channel.request = lambda cmd: reported if cmd == 'ifconfig' else self.fail(cmd)
        for platform, wired, feature, fmt, res, expected in (
                ('darwin', True, 'attach ximg', 'P16 P12L', '20 x 9', True),
                ('win32', True, 'attach ximg', 'P16 P12L', '20 x 9', False),
                ('darwin', False, 'attach ximg', 'P16 P12L', '20 x 9', False),
                ('darwin', True, 'attach', 'P16 P12L', '20 x 9', False),
                ('darwin', True, 'attach ximg', 'P16 P10', '20 x 9', False),
                ('darwin', True, 'attach ximg', 'P16 P12L', '19 x 9', False)):
            metadata.update({'info.features': feature, 'info.imgformats': fmt, 'c1.res': res})
            with (self.subTest(platform=platform, wired=wired, fmt=fmt, res=res),
                  patch.object(ec.sys, 'platform', platform),
                  patch.object(ec, '_Channel', return_value=self.channel),
                  patch.object(ec, 'can_receive', return_value=wired) as can_receive):
                recording = ec.list_recordings(self.rec.host)[0]
                self.assertEqual('ximg' in recording.transfer_modes, expected)
                if can_receive.called:
                    can_receive.assert_called_once_with(self.rec.host, '020000000007')
                    self.assertEqual(recording.camera_mac, '020000000007')
        metadata.update({'info.features': 'attach ximg', 'info.imgformats': 'P16 P12L', 'c1.res': '20 x 9'})
        def unsupported(cmd):
            self.assertEqual(cmd, 'ifconfig')
            raise ec.CameraCommandError('Camera replied: ERR unsupported')
        self.channel.request = unsupported
        with (patch.object(ec.sys, 'platform', 'darwin'),
              patch.object(ec, '_Channel', return_value=self.channel),
              patch.object(ec, 'can_receive', return_value=False) as can_receive):
            self.assertEqual(ec.list_recordings(self.rec.host)[0].transfer_modes, {'full'})
            can_receive.assert_called_once_with(self.rec.host, None)

    def test_mac_is_only_from_one_interface_containing_the_exact_selected_ip(self):
        foreign = 'eth0 Link encap:Ethernet HWaddr 02:00:00:00:00:70 inet addr:192.0.2.70 '
        local = 'lo Link encap:Local Loopback inet addr:127.0.0.1 '
        selected = 'ph10g0 Link encap:Ethernet HWaddr 02:00:00:00:00:07 inet addr:192.0.2.7 '
        self.assertEqual(ec._reported_camera_mac('"' + foreign + local + selected + '"', self.rec.host),
                         '020000000007')
        for invalid in (foreign, selected+selected, selected.replace('Ethernet', 'Local Loopback'),
                        selected.replace('02:', '01:'), selected.replace('02:00:00:00:00:07', '00:00:00:00:00:00'),
                        foreign + selected.replace('HWaddr 02:00:00:00:00:07 ', '')):
            with self.subTest(invalid=invalid):
                self.assertIsNone(ec._reported_camera_mac(invalid, self.rec.host))
