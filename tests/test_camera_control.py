"""Exercise the operator controls on a stateful, fragmented PH16 wire simulation."""
from concurrent.futures import CancelledError
import re
import unittest
from unittest.mock import patch

import numpy as np

import camera_control as cc
from event_camera import _Channel
from test_event_camera import _Socket


class _Veo:
    def __init__(self):
        self.commands = []
        self.opened = []
        self.stall = False
        self.stamp = 4
        self.fields = {
            'info.pver':'16','info.serial':'45678','info.model':'"Simulated VEO"',
            'info.features':'"attach aexp mag"','info.imgformats':'"8 P16"',
            'info.minfrate':'50','info.maxrate':'1000000','info.minexp':'689','info.expdead':'740',
            'defc.res':'800 x 600','defc.rate':'20000','defc.exp':'49256','defc.ptframes':'75',
            'defc.frcount':'100','defc.shoff':'0','defc.aexpmode':'0','cam.syncimg':'0',
            'mag.state':'0','mag.runstop':'0'
        }
        self.state = ['RDY DEF PRE ACT','RDY DEF']
        self.frame = np.arange(21, dtype=np.uint8).reshape(3,7)
        self.image_reply = 'Ok! {cine : 0, res : 7 x 3}'
        self.truncated = False
        self.lose_trigger_ack = False

    def connect(self, address, timeout):
        sock = _Socket(self, address[1])
        self.opened.append(sock)
        if address[1] == 7116:
            self.data = sock
        return sock

    def reply(self, cmd):
        self.commands.append(cmd)
        if cmd.startswith('get '):
            key = cmd[4:]
            if key == 'c1.trigtime':
                return f'trigtime : {{ secs : 203, frac : {self.stamp} }}'
            if key == 'c1.state':
                return 'state : {' + self.state[1] + '}'
            return f'{key} : {self.fields[key]}'
        if cmd == 'cstats':
            return ' '.join(f'c{i} : {{ {flags} }}' for i,flags in enumerate(self.state+['INV']))
        if cmd.startswith('attach '):
            return 'Ok!'
        if cmd.startswith('img '):
            assert re.fullmatch(r'img \{cine:-1, start:0, cnt:1, fmt:(8|P16)\}', cmd)
            pixels = self.frame.tobytes()
            self.data.buffer.extend(pixels[:8] if self.truncated else pixels)
            return self.image_reply
        if cmd.startswith('set defc '):
            for field, value in re.findall(r'(rate|exp|ptframes):([0-9.e+-]+)', cmd):
                # Model exposure quantization in readback.
                self.fields['defc.'+field] = str(int(value)//4*4) if field=='exp' else value
            return 'Ok!'
        if cmd in {'rec','rec 1'}:
            self.state = ['RDY DEF PRE','WTR DEF ACT ABL']
            return 'Ok!'
        if cmd == 'trig':
            self.state = ['RDY DEF PRE ACT','TRG DEF STR']
            if self.lose_trigger_ack:
                raise ConnectionResetError('test dropped acknowledgement')
            return 'Ok!'
        raise AssertionError(cmd)

    @property
    def writes(self):
        return [c for c in self.commands if c.startswith(('trig','rec','set ','del ','rel '))]


class CameraControlTests(unittest.TestCase):
    def setUp(self):
        self.veo = _Veo()
        self.cancel = False
        self.session = cc.CameraControl('127.0.0.1', '45678', cancelled=lambda:self.cancel)
        patcher = patch.object(cc,'_Channel',side_effect=lambda h,c:_Channel(h,c,self.veo.connect))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.session.close)

    def test_live_observation_uses_reply_dimensions_and_owns_no_acquisition(self):
        status = self.session.status()
        self.assertTrue(status.can_arm)
        self.assertFalse(status.can_trigger)
        self.assertEqual((status.exposure_us,status.capacity,status.post_frames), (49.256,100,75))
        np.testing.assert_array_equal(self.session.preview(), self.veo.frame)
        self.session.stop_preview()
        self.assertTrue(all(s.closed for s in self.veo.opened))
        self.assertTrue(self.session.status().can_apply)
        self.assertEqual(self.veo.writes,[])

    def test_p16_fallback_and_truncated_or_oversized_image_discards_channel(self):
        self.veo.fields['info.imgformats']='"272"'
        expected=self.veo.frame
        self.veo.frame=(expected.astype('<u2')<<8)
        self.veo.image_reply='Ok! {cine:1, res:7x3, fmt:272}'
        np.testing.assert_array_equal(self.session.preview(),expected)
        self.veo.truncated=True
        with self.assertRaisesRegex(cc.CameraReadError,'closed'):
            self.session.preview()
        self.assertTrue(all(s.closed for s in self.veo.opened))
        self.veo.truncated=False
        self.veo.image_reply='Ok! {cine:1, res:65536x65536, fmt:272}'
        with self.assertRaisesRegex(cc.CameraReadError,'dimensions'):
            self.session.preview()
        self.assertTrue(all(s.closed for s in self.veo.opened))

    def test_apply_preserves_armed_recording_and_reads_back_camera_rounding(self):
        self.veo.state=['RDY DEF PRE','ACT DEF WTR ABL']
        state=self.session.apply(10000, 20.123, 40)
        self.assertEqual((state.fps,state.exposure_us,state.post_frames),(10000,20.120,40))
        self.assertTrue(state.can_trigger)
        self.assertEqual(self.veo.writes,['set defc {rate:10000, exp:20123, ptframes:40}'])
        self.session.apply(10000,20.120,40)
        self.assertEqual(len(self.veo.writes),1)

    def test_reject_invalid_or_externally_controlled_changes_before_writing(self):
        for kw in (dict(fps=float('nan')),dict(fps=3),dict(fps=2000001),dict(exposure_us=.002),
                   dict(exposure_us=50),dict(post_frames=105),dict(post_frames=2.2),dict(exposure_us=1e300)):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.session.apply(**kw)
        self.veo.fields['defc.shoff']='1'
        with self.assertRaisesRegex(ValueError,'Manual exposure'):
            self.session.apply(exposure_us=8)
        self.veo.fields['cam.syncimg']='1'
        with self.assertRaisesRegex(ValueError,'external frame sync'):
            self.session.apply(fps=5000)
        self.assertFalse(self.veo.writes)

    def test_arm_trigger_and_confirmed_replacement_are_separate(self):
        armed=self.session.arm()
        self.assertEqual(armed.active_slot,1)
        with self.assertRaises(ValueError):
            self.session.trigger(3)
        stored=self.session.trigger(1)
        self.assertFalse(stored.can_trigger)
        self.assertFalse(stored.can_arm)
        with self.assertRaises(ValueError):
            self.session.arm()
        token,=stored.replaceable
        self.veo.stamp+=1
        with self.assertRaisesRegex(ValueError,'changed'):
            self.session.arm(replace=token)
        current,=self.session.status().replaceable
        self.assertTrue(self.session.arm(replace=current).can_trigger)
        self.assertEqual(self.veo.writes,['rec','trig','rec 1'])

    def test_direct_media_posttrigger_and_pending_input_prevent_trigger(self):
        self.veo.state=['RDY DEF PRE','ACT WTR DEF']  # not triggerable yet
        with self.assertRaises(ValueError):
            self.session.trigger(1)
        self.veo.state[1]='ACT DEF TRG'
        with self.assertRaises(ValueError):
            self.session.apply(fps=30000)
        with self.assertRaises(ValueError):
            self.session.trigger(1)
        self.veo.state[1]='WTR DEF ACT ABL'
        self.veo.fields['mag.state']='5'
        with self.assertRaises(ValueError):
            self.session.trigger(1)
        self.assertFalse(self.veo.writes)

    def test_trigger_expires_in_queue_and_after_slow_state_read(self):
        self.veo.state = ['RDY DEF PRE', 'WTR DEF ABL ACT']
        with patch.object(cc, 'monotonic', return_value=12.0):
            with self.assertRaisesRegex(ValueError, 'No trigger was sent'):
                self.session.trigger(1, deadline=12.0)
        self.assertEqual(self.veo.commands, [])
        with patch.object(cc, 'monotonic', side_effect=[11.8, 12.01]):
            with self.assertRaisesRegex(ValueError, 'No trigger was sent'):
                self.session.trigger(1, deadline=12.0)
        self.assertIn('cstats', self.veo.commands)
        self.assertEqual(self.veo.writes, [])
        with patch.object(cc, 'monotonic', side_effect=[12.1, 12.12]):
            self.session.trigger(1, deadline=13.0)
        self.assertEqual(self.veo.writes, ['trig'])

    def test_lost_acknowledgement_never_reissues_trigger_and_rechecks_identity(self):
        self.veo.state=['RDY DEF PRE','WTR DEF ABL ACT']
        self.veo.lose_trigger_ack=True
        with self.assertRaisesRegex(cc.CameraReadError,'did not confirm'):
            self.session.trigger(1)
        self.assertFalse(self.session.status().can_trigger)
        self.assertEqual(self.veo.writes,['trig'])
        self.session.close()
        self.veo.fields['info.serial']='23456'
        with self.assertRaisesRegex(cc.CameraReadError,'identity'):
            self.session.arm()
        self.assertTrue(all(s.closed for s in self.veo.opened))

    def test_cancelled_and_malformed_state_never_arm_or_trigger(self):
        self.veo.state=['PRE ACT','ACT WTR ABL']
        with self.assertRaises(cc.CameraReadError):
            self.session.trigger(1)
        self.cancel=True
        with self.assertRaises(CancelledError):
            self.session.arm()
        self.assertFalse(self.veo.writes)
