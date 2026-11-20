"""Exercise PH16 framing and RAM reads without a network interface or a camera."""
from concurrent.futures import CancelledError
import re
import socket
import unittest
from unittest.mock import patch

import numpy as np

import event_camera as ec


class _Socket:
    def __init__(self, camera, port):
        self.closed = False
        self.camera = camera
        self.port = port
        self.buffer = bytearray()

    def recv_into(self, destination):
        length = min(len(self.buffer), len(destination), 19)
        if not length and self.camera.stall:
            self.camera.polls += 1
            raise socket.timeout()
        destination[:length] = self.buffer[:length]
        del self.buffer[:length]
        return length

    def close(self):
        self.closed = True

    def settimeout(self, value):
        pass

    def getsockname(self):
        return ('127.0.0.1', 50711)

    def sendall(self, data):
        assert self.port == 7115 and data.endswith(b'\r\n') and not self.closed
        command = data.decode('ascii')[:-2]
        self.buffer.extend((self.camera.reply(command)+'\r\n').encode('ascii'))


class _Camera:
    def __init__(self, fmt='P16'):
        self.commands = []
        self.opened = []
        self.data = None
        self.fmt = fmt
        self.stamp = 456
        self.changed_during_read = False
        self.truncate = self.stall = False
        self.polls = 0
        self.image_reply = None
        self.frames = np.arange(6*5*7, dtype=np.uint16).reshape(6,5,7)*257
        self.metadata = {
            'info.pver': '16', 'info.features': '"attach notify"', 'info.imgformats': f'"{fmt}"',
            'info.model': '"Demo"', 'info.serial': '123', 'cam.cines': '3',
            'c1.state': '{ STR DEF }', 'c2.state': '{ WTR ACT }', 'c3.state': '{ TRG ACT }',
            'c1.firstfr': '-4', 'c1.lastfr': '1', 'c1.rate': '1000000', 'c1.res': '7 x 5'
        }

    def connect(self, address, timeout):
        sock = _Socket(self, address[1])
        self.opened.append(sock)
        if address[1] == 7116:
            self.data = sock
        return sock

    def reply(self, command):
        self.commands.append(command)
        if command.startswith('get '):
            key = command[4:]
            if key=='c1.trigtime':
                return f'trigtime : {{ secs : 100, \\\r\n frac : {self.stamp} }}'
            return f'{key.split(".")[-1]} : {self.metadata[key]}'
        if command=='attach {port:50711}':
            return 'Ok!'
        m = re.fullmatch(r'img \{cine:1, start:(-?\d+), cnt:(\d+), fmt:(P16|8)\}', command)
        if not m:
            raise AssertionError('Unexpected command '+command)
        first, count = int(m[1])+4, int(m[2])
        block = self.frames[first:first+count]
        if m[3]=='8':
            block = (block >> 8).astype(np.uint8)
        pixels = block.tobytes()
        self.data.buffer.extend(pixels[:len(pixels)//2] if self.truncate else pixels)
        if self.changed_during_read:
            self.stamp += 1
        if self.image_reply is not None:
            return self.image_reply
        fmt = '8' if m[3]=='8' else '272'
        return f'Ok! {{ cine: 1, res: 7x5, fmt: {fmt} }}'


class CameraTests(unittest.TestCase):
    def wire(self, camera):
        # Both simulated sockets behave like partial TCP streams.
        original = ec._Channel
        fake = lambda host, cancel: original(host, cancel, camera.connect)
        self.addCleanup(patch.stopall)
        patch.object(ec, '_Channel', side_effect=fake).start()
        result = ec.list_recordings('127.0.0.1')
        self.assertEqual(len(result), 1)
        self.assertTrue(all(x.port == 7115 and x.closed for x in camera.opened))
        return result[0].open()

    def test_stored_cines_only_signed_roi_subframe_precision_and_batch(self):
        camera = _Camera()
        src = self.wire(camera)
        self.assertEqual((src.first_frame,src.count,src.fps), (-4,6,1e6))
        self.assertEqual(src.recording.label, 'Demo · RAM Cine 1')
        np.testing.assert_array_equal(src.preview(4), camera.frames[4] >> 8)
        self.assertTrue(all(s.closed for s in camera.opened))
        for i in range(src.count):
            patch_data = src.read_roi(i, (2,1,3,2))
            np.testing.assert_array_equal(patch_data,camera.frames[i,1:3,2:5])
        self.assertTrue(all(s.closed for s in camera.opened))
        self.assertEqual([cmd for cmd in camera.commands if cmd.startswith('img ')],
                         ['img {cine:1, start:0, cnt:1, fmt:P16}', 'img {cine:1, start:-4, cnt:6, fmt:P16}'])
        src.close()

    def test_fallback_to_advertised_linear_eight_bit(self):
        camera = _Camera('8')
        src = self.wire(camera)
        np.testing.assert_array_equal(src.preview(0), camera.frames[0] >> 8)

    def test_veo_1310s_acknowledgement_without_format(self):
        camera = _Camera()
        camera.metadata['c1.res'] = '800 x 600'
        camera.metadata['info.imgformats'] = '"8 8R P16 P16R P10 P12L"'
        frame = np.arange(800*600, dtype='<u2').reshape(600,800)
        camera.frames = np.broadcast_to(frame,(6,600,800))
        camera.image_reply = 'Ok! { cine : 1, res : 800 x 600}'
        src = self.wire(camera)
        try:
            np.testing.assert_array_equal(src.preview(4), frame >> 8)
            self.assertIn('img {cine:1, start:0, cnt:1, fmt:P16}', camera.commands)
        finally:
            src.close()

    def test_optional_format_does_not_hide_conflicts_or_short_payloads(self):
        for reply, truncated in [
                ('Ok! { cine:1, res:7x5, FMT:8 }', False),
                ('Ok! { cine:1, res:7x5, fmt:P16R }', False),
                ('Ok! { cine:1, res:7x5, fmt: }', False),
                ('Ok! { cine:2, res:7x5 }', False),
                ('Ok! { cine:1, res:7x4 }', False),
                ('Ok! { cine:1, res:7x5 }', True)]:
            with self.subTest(reply=reply, truncated=truncated):
                camera = _Camera()
                camera.image_reply, camera.truncate = reply, truncated
                src = self.wire(camera)
                with self.assertRaises(ec.CameraReadError):
                    src.preview(0)
                self.assertTrue(all(sock.closed for sock in camera.opened))
                patch.stopall()

    def test_changed_cine_or_short_or_mismatched_transfer_discards_data(self):
        for behavior in ['changed_during_read','truncate']:
            with self.subTest(behavior=behavior):
                camera = _Camera()
                src = self.wire(camera)
                setattr(camera,behavior,True)
                with self.assertRaises(ec.CameraReadError):
                    src.read_roi(0,(0,0,7,5))
                self.assertTrue(all(s.closed for s in camera.opened))
                patch.stopall()

    def test_cancellation_during_network_wait_releases_both_connections(self):
        camera = _Camera()
        src = self.wire(camera)
        camera.truncate = camera.stall = True
        src.set_cancel_check(lambda: camera.polls >= 1)
        with self.assertRaises(CancelledError):
            src.read_roi(0,(0,0,7,5))
        self.assertTrue(all(s.closed for s in camera.opened))

    def test_refuse_live_and_changed_slot_and_invalid_address(self):
        camera = _Camera()
        src = self.wire(camera)
        camera.metadata['c1.state'] = '{ WTR ACT }'
        with self.assertRaisesRegex(ec.CameraReadError,'changed'):
            src.preview(2)
        self.assertFalse(any(x.port == 7116 for x in camera.opened))
        camera.metadata['c1.state'] = '{ STR }'
        camera.stamp += 1
        with self.assertRaisesRegex(ec.CameraReadError,'changed'):
            src.preview(2)
        self.assertFalse(any(x.port == 7116 for x in camera.opened))
        for value in ('', '127.0.0.1\r\nset', '255.255.255.255','0.0.0.0','224.0.0.1'):
            with self.assertRaises(ValueError):
                ec.validate_address(value)
