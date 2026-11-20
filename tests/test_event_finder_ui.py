import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from event_finder_ui import EventFinderPanel
from test_cine_metadata import write_cine


def bubble_clip(path):
    width,height,count = 96,72,52
    write_cine(path, width=width,height=height,frames=count)
    contents = bytearray(path.read_bytes())
    table = struct.unpack_from('<I', contents, 32)[0]
    generator = np.random.default_rng(87)
    ref = generator.integers(74,92,size=(height,width))
    for i in range(count):
        image = (ref + generator.integers(-1,2,size=ref.shape)).astype(np.uint8)
        if 13 <= i <= 16:
            image[33:39,45:51] += [54,85,61,52][i-13]
        if 35 <= i <= 36:
            image[3:16,4:19] = 225  # outside the chosen region
        offset = struct.unpack_from('<Q', contents, table + 8*i)[0]+8
        contents[offset:offset+image.size] = image[::-1].tobytes()
    path.write_bytes(contents)


class EventFinderUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'Bubble.cine'
        bubble_clip(self.path)
        self.panel = EventFinderPanel()
        self.panel.resize(950,560)
        self.panel.show()
        self.panel.set_sources([self.path], self.path, 3)
        self.wait(lambda: self.panel.info is not None and not self.panel.activity)

    def tearDown(self):
        self.wait(self.panel.shutdown)
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def wait(self, condition, timeout=8):
        until = time.monotonic()+timeout
        while time.monotonic()<until:
            self.app.processEvents()
            if condition():
                return
            time.sleep(.003)
        self.fail(self.panel.status.text())

    def draw_and_scan(self):
        p = self.panel
        r=p.view.image_rect()
        def screen(x,y):
            return QPoint(round(r.left()+r.width()*x/96),round(r.top()+r.height()*y/72))
        QTest.mousePress(p.view,Qt.LeftButton,pos=screen(73,60))
        QTest.mouseMove(p.view,screen(31,20))
        QTest.mouseRelease(p.view,Qt.LeftButton,pos=screen(31,20))
        self.assertTrue(p.analyze.isEnabled())
        p.analyze.click()
        self.wait(lambda: p.scanned and not p.activity)

    def test_roi_scan_native_peak_navigation_and_viewer_handoff(self):
        p = self.panel
        self.assertEqual(p.native.value(),-7)
        self.draw_and_scan()
        self.assertEqual(len(p.hits.events),1)
        hit = p.hits.events[0]
        self.assertEqual((hit.first,hit.peak,hit.last),(13,14,16))
        self.assertEqual(p.native.value(),4)
        self.assertEqual(p.hits.data(p.hits.index(0,0)), '4')
        self.assertEqual(p.slider.peaks,(14,))
        self.assertIn('4 frames',p.evidence.text())
        p.next.click()
        self.wait(lambda:not p.activity)
        self.assertEqual(p.native.value(),5)
        p.table.clicked.emit(p.hits.index(0,0))
        self.wait(lambda:not p.activity)
        self.assertEqual(p.native.value(),4)
        spy = QSignalSpy(p.openInViewer)
        p.viewer.click()
        self.assertEqual(spy.at(0),[str(self.path.resolve()),14])

    def test_restore_only_for_same_recording_and_eligibility(self):
        p = self.panel
        self.draw_and_scan()
        state = p.settings()
        p.apply_settings({})
        self.assertIsNone(p.info)
        self.assertFalse(p.analyze.isEnabled())
        p.apply_settings(state)
        self.wait(lambda:p.scanned and not p.activity)
        self.assertEqual(p.settings(),state)
        other = Path(self.tmp.name)/'Different.cine'
        bubble_clip(other)
        p.set_sources([other],other)
        self.assertEqual(p.current,str(self.path.resolve()))
        altered = dict(state,file_signature=[99,100])
        p.apply_settings(altered)
        self.wait(lambda:p.info is not None and not p.activity)
        self.assertFalse(p.scanned)
        self.assertEqual(p.roi,tuple(state['roi']))
        p.set_roi((31,20,42,40))
        p.sensitivity.setCurrentIndex(2)
        self.assertEqual(len(p.hits.events),0)

    def test_cancel_and_discard_results_from_interrupted_source(self):
        p=self.panel
        p.full.click()
        entered = threading.Event()
        def slow(read, first, last, *, cancelled, **kwargs):
            from concurrent.futures import CancelledError
            entered.set()
            for _ in range(1000):
                if cancelled():
                    raise CancelledError()
                time.sleep(.002)
            raise AssertionError('Cancellation did not reach detector.')
        with patch('event_finder_ui_worker.scan_events',slow):
            p.analyze.click()
            self.wait(entered.is_set)
            self.assertFalse(p.next.isEnabled())
            p.cancel.click()
            self.wait(lambda:not p.busy())
            self.assertIn('cancelled',p.status.text())
            self.assertTrue(p.analyze.isEnabled())
            entered.clear()
            p.analyze.click()
            self.wait(entered.is_set)
            saved_path = Path(self.tmp.name)/'Other.cine'
            bubble_clip(saved_path)
            p._add_source(str(saved_path))
            self.wait(lambda:not p.activity)
            self.assertEqual(p.current,str(saved_path.resolve()))
            self.assertEqual(p.position,0)
            self.assertIsNone(p.roi)
            self.assertFalse(p.scanned)

    def test_native_range_interval_mean_probe_and_job_round_trip(self):
        p=self.panel
        p.shape.setCurrentIndex(p.shape.findData('point'))
        self.assertIsNone(p.roi)
        self.assertFalse(p.full.isVisible())
        r = p.view.image_rect()
        QTest.mouseClick(p.view,Qt.LeftButton,pos=QPoint(round(r.left()+r.width()*48/96),
                                                        round(r.top()+r.height()*36/72)))
        self.assertEqual(p.roi[2:],(4,4))
        p.first.setValue(-2)
        p.last.setValue(12)
        p.step.setValue(3)
        self.assertIn('6 of 15',p.coverage.text())
        p.analyze.click()
        self.wait(lambda:p.scanned and not p.activity)
        self.assertEqual(len(p.hits.events),1)
        self.assertEqual(p.native.value(),4)
        self.assertIn('Sampled peak',p.evidence.text())
        state=p.settings()
        self.assertEqual(state['scan_range'],(8,22))
        self.assertEqual(state['shape'],'point')
        self.assertEqual(state['step'],3)
        p.apply_settings({})
        p.apply_settings(state)
        self.wait(lambda:p.scanned and not p.activity)
        self.assertEqual(p.settings(),state)
        self.assertTrue(p.view.point_mode)
        p.first.setValue(11)
        self.assertFalse(p.analyze.isEnabled())
        p.first.setValue(12)
        self.assertFalse(p.analyze.isEnabled())
        self.assertIn('at least three',p.coverage.text())
        p.reset_range.click()
        self.assertEqual((p.first.value(),p.last.value()),(-10,41))
        self.assertTrue(p.analyze.isEnabled())
        p.shape.setCurrentIndex(0)
        self.assertIsNone(p.roi)
        self.assertFalse(p.analyze.isEnabled())

    def test_camera_precision_stride_and_sensitivity_cache(self):
        from event_cine_source import EventCineSource
        p=self.panel
        path=self.path
        calls=[]
        class Reader(EventCineSource):
            transfer_modes=frozenset(('fast','full'))
            def set_transfer_mode(self,value):
                calls.append(('mode',value))
            def set_scan_stride(self,step,first,last):
                calls.append(('sequence',step,first,last))
            def validate_recording(self):
                calls.append(('validate',))
            def read_roi(self,index,roi):
                calls.append(('read',index))
                return super().read_roi(index,roi)
        class Camera:
            label='Test Cine 1'
            def open(self):
                return Reader(path)
        p._add_source(Camera())
        self.wait(lambda:p.info is not None and not p.activity)
        self.assertTrue(p.precision.isVisible())
        self.assertEqual(p.precision.currentData(),'fast')
        p.set_roi((31,20,42,40))
        p.first.setValue(-2)
        p.last.setValue(12)
        p.step.setValue(3)
        p.analyze.click()
        self.wait(lambda:p.scanned and not p.activity)
        self.assertIn(('mode','fast'),calls)
        self.assertIn(('sequence',3,8,22),calls)
        self.assertEqual({c[1] for c in calls if c[0]=='read'},{8,11,14,17,20,22})
        calls.clear()
        p.sensitivity.setCurrentIndex(2)
        p.analyze.click()
        self.wait(lambda:p.scanned and not p.activity)
        self.assertIn(('validate',),calls)
        self.assertFalse(any(c[0]=='read' for c in calls))
        calls.clear()
        p.precision.setCurrentIndex(p.precision.findData('full'))
        p.analyze.click()
        self.wait(lambda:p.scanned and not p.activity)
        self.assertIn(('mode','full'),calls)
        self.assertTrue(any(c[0]=='read' for c in calls))

    def test_failed_preview_clears_old_frame_and_retry_restores_actions(self):
        p = self.panel
        p.full.click()
        from event_cine_source import EventCineSource
        with patch.object(EventCineSource, 'preview', side_effect=OSError('Unable to read frame')):
            p.next.click()
            self.wait(lambda: not p.activity)
            self.assertIsNone(p.view.image)
            self.assertTrue(p.retry.isVisible())
            self.assertFalse(p.viewer.isEnabled())
            self.assertFalse(p.analyze.isEnabled())
        p.retry.click()
        self.wait(lambda: not p.activity)
        self.assertIsNotNone(p.view.image)
        self.assertTrue(p.viewer.isEnabled())
        self.assertEqual(p.native.value(), -6)

    def test_camera_source_uses_serial_worker_and_releases_reader(self):
        p=self.panel
        calls=[]
        class MemoryReader:
            count=30
            first_frame=-9
            height=40
            width=60
            fps=1000000
            def set_cancel_check(self, callback):
                self.cancel=callback
            def preview(self, index):
                calls.append(('preview',threading.get_ident()))
                return np.full((40,60),40,dtype=np.uint8)
            def read_roi(self,index,roi):
                calls.append(('read',threading.get_ident()))
                tile=np.full((roi[3],roi[2]),40,dtype=np.uint8)
                if index==19:
                    tile[10:14,15:20]=144
                return tile
            def close(self):
                calls.append(('close',threading.get_ident()))
        class Camera:
            label='RAM Cine 1'
            def open(self):
                calls.append(('open',threading.get_ident()))
                return MemoryReader()
        source=Camera()
        p._add_source(source)
        self.wait(lambda:p.info and p.info.first==-9)
        self.assertFalse(p.viewer.isVisible())
        p.full.click()
        p.analyze.click()
        self.wait(lambda:p.scanned and not p.activity)
        self.assertEqual(p.native.value(),10)
        self.assertEqual(p.settings(),{})
        self.assertTrue(any(action=='close' for action,_ in calls))
        self.assertEqual(len({thread for _,thread in calls}),1)
        self.assertNotEqual(calls[0][1],threading.get_ident())
