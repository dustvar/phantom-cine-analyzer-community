"""PH16 stored-Cine scan through two real TCP sockets on loopback only."""
from contextlib import closing
import re
import socket
from socketserver import StreamRequestHandler, TCPServer
import threading
import unittest
from unittest.mock import patch

import numpy as np

import event_camera
from event_detection import scan_events


class _Readback(StreamRequestHandler):
    def handle(self):
        # Independent server implementation of the documented PH16 RAM wire format.
        cine = self.server
        transfer = None
        info = {'info.pver': '16', 'info.serial': '45678', 'info.model': '"emulated PH16 camera"',
                'info.imgformats': '"8 P10 P16"', 'info.features': '"attach earlyimg"',
                'cam.cines': '2', 'c1.state': '{ DEF WTR ACT ABL }', 'c2.state': '{ STR DEF }',
                'c2.firstfr': '-275', 'c2.lastfr': '324', 'c2.rate': '1000000', 'c2.res': '32 x 24'}
        try:
            for wire in self.rfile:
                command = wire.decode('ascii').rstrip('\r\n')
                cine.commands.append(command)
                payload = None
                if command.startswith('get '):
                    key = command[4:]
                    if key == 'c2.trigtime':
                        value = '{ secs : 90017,  \\\r\n frac : %d }' % cine.frac
                    else:
                        value = info[key]
                    reply = key.rsplit('.',1)[-1] + ' : ' + value
                elif command.startswith('attach '):
                    source_port = int(re.fullmatch(r'attach \{port:(\d+)\}', command)[1])
                    transfer, peer = cine.image_port.accept()
                    if peer[0] != '127.0.0.1' or peer[1] != source_port:
                        raise AssertionError('Image connection and control connection do not belong together')
                    reply = 'Ok!'
                else:
                    f = re.fullmatch(r'img \{cine:2, start:(-?\d+), cnt:(\d+), fmt:P16\}', command)
                    if not f or transfer is None:
                        raise AssertionError('Unexpected request: '+command)
                    native, count = map(int, f.groups())
                    first = native+275
                    if not 0 <= first < first+count <= len(cine.images):
                        raise AssertionError('Bad native frame bounds')
                    payload = cine.images[first:first+count].tobytes()
                    cine.ranges.append((native,count))
                    reply = 'Ok! { cine: 2, \\\r\n res: 32x24, fmt: 272 }'
                self.wfile.write(reply.encode('ascii')+b'\r\n')
                if payload is not None:
                    transfer.sendall(payload[:29])
                    transfer.sendall(payload[29:])
        except Exception as exc:
            cine.errors.append(exc)
        finally:
            if transfer:
                transfer.close()


class LoopbackEventFinderTests(unittest.TestCase):
    def test_two_channels_negative_trigger_frames_and_four_frame_event(self):
        rng = np.random.default_rng(72)
        images = rng.integers(14980,15020,(600,24,32),dtype=np.uint16)
        for offset,increment in enumerate((900,2300,1600,1100)):
            images[251+offset,11:14,17:20] += increment
        # A much brighter event outside the ROI must never appear in the result.
        images[487:492,0:3,0:4] += 18000

        with closing(socket.socket()) as data, TCPServer(('127.0.0.1',0), _Readback) as control:
            data.bind(('127.0.0.1',0))
            data.listen()
            data.settimeout(2)
            control.image_port = data
            control.images = images
            control.frac = 593140
            control.commands, control.ranges, control.errors = [], [], []
            server = threading.Thread(target=control.serve_forever, kwargs={'poll_interval':.02}, daemon=True)
            server.start()
            ports = {7115:control.server_address[1], 7116:data.getsockname()[1]}
            def connector(address, timeout):
                host, port = address
                self.assertEqual(host,'127.0.0.1')
                return socket.create_connection((host,ports[port]),timeout)
            channel = event_camera._Channel
            def make_channel(host, cancel):
                return channel(host,cancel,connector)
            try:
                with patch.object(event_camera,'_Channel',side_effect=make_channel):
                    records = event_camera.list_recordings('127.0.0.1')
                    self.assertEqual([(r.cine,r.first_frame,r.last_frame) for r in records],[(2,-275,324)])
                    self.assertTrue(all(cmd.startswith('get ') for cmd in control.commands))
                    with closing(records[0].open()) as src:
                        result = scan_events(lambda i: src.read_roi(i,(11,6,14,13)),0,src.count-1)
                        hits = [(src.first_frame+e.first,src.first_frame+e.peak,src.first_frame+e.last)
                                for e in result.events]
                        self.assertEqual((result.frame_count,hits),(600,[(-24,-23,-21)]))
                        np.testing.assert_array_equal(src.preview(252), images[252] >> 8)
                        self.assertTrue(any(cnt>=200 for _,cnt in control.ranges))
                        self.assertTrue(set(range(-275,325)).issubset(
                            {i for first,n in control.ranges for i in range(first,first+n)}))
                        control.frac += 1
                        previous = len(control.ranges)
                        with self.assertRaisesRegex(event_camera.CameraReadError,'changed'):
                            src.preview(252)
                        self.assertEqual(len(control.ranges),previous)
                    self.assertEqual(control.errors,[])
            finally:
                control.shutdown()
                server.join()
