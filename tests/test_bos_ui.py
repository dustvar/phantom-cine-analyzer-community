import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from bos_ui import BOSPanel
from bos_frames import BOSFrames, validate_bos_settings
from test_cine_metadata import write_cine


def write_dot_movie(path):
    width, height, frames = 160, 96, 6
    write_cine(path, frames=frames, width=width, height=height)
    ref = np.random.default_rng(817).integers(10, 245, (height, width), dtype=np.uint8)
    ref = cv2.GaussianBlur(ref, (3, 3), .6)
    data = bytearray(path.read_bytes())
    table = struct.unpack_from('<I', data, 32)[0]
    for i in range(frames):
        start = struct.unpack_from('<Q', data, table+i*8)[0] + 8
        frame = np.roll(ref, min(2, i), axis=1)
        data[start:start+frame.size] = frame[::-1].tobytes()
    path.write_bytes(data)


class BOSWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name)/'test.cine'
        write_dot_movie(self.source)
        self.panel = BOSPanel()
        self.panel.resize(1050, 650)
        self.panel.show()
        self.panel.set_sources([self.source], self.source)

    def tearDown(self):
        self.panel.shutdown()
        self.wait(lambda: not self.panel.busy())
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self, predicate, timeout=6.):
        until = time.monotonic()+timeout
        while time.monotonic() < until and not predicate():
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), self.panel.status.text())

    def ready_at(self, index):
        return (not self.panel.pending and not self.panel.busy() and self.panel.last_result is not None
                and self.panel.last_result['index'] == index)

    def test_native_frame_reference_reverse_rectangle_and_source_switch(self):
        self.wait(lambda: self.ready_at(0))
        p = self.panel
        QTest.mouseClick(p.pin, Qt.MouseButton.LeftButton)
        self.wait(lambda: self.ready_at(0) and p.state['reference'] == 0)
        rect = p.view.image_rect()
        def point(x, y):
            return QPoint(round(rect.left()+x*rect.width()/160), round(rect.top()+y*rect.height()/96))
        QTest.mousePress(p.view, Qt.MouseButton.LeftButton, pos=point(142, 82))
        QTest.mouseMove(p.view, point(15, 12))
        QTest.mouseRelease(p.view, Qt.MouseButton.LeftButton, pos=point(15, 12))
        self.wait(lambda: self.ready_at(0) and p.last_result['field'] is not None)
        np.testing.assert_allclose(p.state['roi'], [15, 12, 127, 70], atol=2)
        p.frame_number.setValue(-8)
        self.wait(lambda: self.ready_at(2))
        field = p.last_result['field']
        self.assertGreater(np.mean(field.valid), .6)
        self.assertAlmostEqual(float(np.median(field.dx[field.valid])), 2, delta=.18)
        QTest.mouseMove(p.view, point(63, 50))
        self.assertIn('Δx +2.', p.probe_label.text())
        self.assertTrue(p.export.isEnabled())
        state = p.settings()
        other = Path(self.temp.name)/'other.cine'
        write_dot_movie(other)
        p.set_sources([self.source,other], other)
        self.wait(lambda: self.ready_at(0))
        self.assertIsNone(p.state['roi'])
        self.assertIsNone(p.state['reference'])
        p.apply_settings(state)
        self.wait(lambda: self.ready_at(2))
        self.assertEqual(p.settings(), state)

    def test_method_recomputes_preview_and_survives_source_switch(self):
        from bos_processing import compute_bos
        p = self.panel
        self.wait(lambda: self.ready_at(0))
        p.pin_reference()
        p.set_roi([10, 10, 130, 72])
        p.seek(2)
        self.wait(lambda: self.ready_at(2) and p.last_result['field'] is not None)
        legacy = p.settings()
        del legacy['method']
        del legacy['reduce_noise']
        self.assertTrue(p.reduce_noise.isChecked())
        self.assertTrue(validate_bos_settings({})['reduce_noise'])
        self.assertFalse(validate_bos_settings(legacy)['reduce_noise'])
        with self.assertRaisesRegex(ValueError, 'overlay noise'):
            validate_bos_settings(dict(legacy, reduce_noise='false'))
        self.assertEqual(validate_bos_settings(legacy)['method'], 'optical_flow')
        with self.assertRaisesRegex(ValueError, 'BOS method'):
            validate_bos_settings(dict(legacy, method='unknown'))
        flow = p.last_result['field']
        with patch('bos_frames.compute_bos', wraps=compute_bos) as measure:
            p.method.setCurrentIndex(p.method.findData('cross_correlation'))
            self.wait(lambda: self.ready_at(2))
            correlation = p.last_result['field']
            self.assertIsNot(flow, correlation)
            self.assertEqual(measure.call_args.kwargs['method'], 'cross_correlation')
            self.assertAlmostEqual(float(np.nanmedian(correlation.dx)), 2, delta=.15)
            p.reduce_noise.setChecked(False)
            self.wait(lambda: self.ready_at(2))
            self.assertIs(p.last_result['field'], correlation)
            p.palette.setCurrentIndex(p.palette.findData('color'))
            self.wait(lambda: self.ready_at(2))
            self.assertIs(p.last_result['field'], correlation)
            self.assertEqual(measure.call_count, 1)
        saved = p.settings()
        other = Path(self.temp.name)/'other.cine'
        write_dot_movie(other)
        p.set_sources([self.source, other], other)
        self.wait(lambda: self.ready_at(0))
        self.assertEqual(p.method.currentData(), 'optical_flow')
        p.set_sources([self.source, other], self.source)
        self.wait(lambda: self.ready_at(2))
        self.assertEqual(p.settings(), saved)
        self.assertEqual(p.method.currentData(), 'cross_correlation')
        p.apply_settings(legacy)
        self.wait(lambda: self.ready_at(2))
        self.assertEqual(p.method.currentData(), 'optical_flow')
        self.assertFalse(p.reduce_noise.isChecked())

    def test_vector_layers_share_matching_and_survive_a_job_restore(self):
        p = self.panel
        self.wait(lambda: self.ready_at(0))
        p.pin_reference()
        p.set_roi([0, 0, 160, 96])
        p.seek(2)
        self.wait(lambda: self.ready_at(2) and p.last_result['field'] is not None)
        field = p.last_result['field']
        self.assertFalse(p.vector_options.isVisible())
        legacy = p.settings()
        for key in ('show_vectors', 'vector_background', 'vector_density'):
            del legacy[key]
        self.assertFalse(validate_bos_settings(legacy)['show_vectors'])
        for key, bad in [('show_vectors', 'true'), ('vector_density', 'invalid'), ('vector_background', 1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_bos_settings(dict(legacy, **{key: bad}))
        with patch('bos_frames.compute_bos', side_effect=AssertionError('Repeated BOS matching for a vector display edit')):
            p.show_vectors.click()
            self.wait(lambda: self.ready_at(2))
            self.assertTrue(p.vector_options.isVisible())
            self.assertTrue(p.save_vectors.isEnabled())
            self.assertEqual(p.vector_background.currentData(), 'map')
            self.assertGreaterEqual(p.vector_density.findData('ultra_dense'), 0)
            self.assertRegex(p.vector_density.currentText(), r'^Up to [0-9,]+$')
            samples = p.last_result['vector_samples']
            self.assertIn(f'{len(samples.x):,} arrows', p.quality.text())
            p.vector_background.setCurrentIndex(p.vector_background.findData('footage'))
            p.vector_density.setCurrentIndex(p.vector_density.findData('dense'))
            self.wait(lambda: self.ready_at(2))
            self.assertIs(field, p.last_result['field'])
            self.assertFalse(p.component.isEnabled())
            self.assertFalse(p.opacity.isEnabled())
            self.assertTrue(p.limit.isEnabled())
            p._probe(QPointF(field.x[len(field.x)//2], field.y[len(field.y)//2]))
            self.assertIn('|Δ|', p.probe_label.text())
            saved = p.settings()
        p.apply_settings(legacy)
        self.wait(lambda: self.ready_at(2))
        self.assertFalse(p.show_vectors.isChecked())
        self.assertTrue(p.component.isEnabled())
        p.apply_settings(saved)
        self.wait(lambda: self.ready_at(2))
        self.assertTrue(p.show_vectors.isChecked())
        self.assertEqual(p.vector_background.currentData(), 'footage')
        self.assertEqual(p.vector_density.currentData(), 'dense')
        self.assertEqual(p.settings(), saved)

    def test_snapshot_and_csv_use_displayed_vector_settings(self):
        import csv
        from bos_vector_data import save_vector_csv
        from PySide6.QtWidgets import QFileDialog
        p = self.panel
        self.wait(lambda: self.ready_at(0))
        p.show_vectors.click()
        self.wait(lambda: self.ready_at(0))
        self.assertFalse(p.save_vectors.isEnabled())
        p.pin_reference()
        p.set_roi([0, 0, 160, 96])
        p.seek(2)
        self.assertFalse(p.save_vectors.isEnabled())
        self.wait(lambda: self.ready_at(2) and p.save_vectors.isEnabled())
        target = Path(self.temp.name)/'vectors.csv'
        with patch.object(QFileDialog, 'exec', return_value=QFileDialog.DialogCode.Accepted), \
             patch.object(QFileDialog, 'selectedFiles', return_value=[str(target)]), \
             patch('bos_vector_data.save_vector_csv', wraps=save_vector_csv) as save:
            p.save_vectors.click()
            save.assert_called_once_with(target, p.last_result, p.settings())
        with target.open(newline='') as saved:
            rows = list(csv.DictReader(saved))
        self.assertEqual(len(rows), int(p.last_result['field'].valid.sum()))
        self.assertTrue(all(int(row['cine_frame']) == -8 for row in rows))
        self.assertTrue(any(row['selected_for_arrow'] == '1' for row in rows))
        p.analyze_range.setChecked(True)
        with patch('bos_preview_ui.analyze_bos_range') as analyze:
            p._analyze_selected_range(1, 3)
            parent, state, first, last = analyze.call_args.args
            self.assertEqual((parent, first, last), (p, 1, 3))
            self.assertTrue(state['show_vectors'])
            self.assertEqual(state['vector_background'], 'map')
            p.show_vectors.click()
            self.assertTrue(state['show_vectors'])

    def test_fine_sampling_old_job_and_unmeasured_margin(self):
        p = self.panel
        self.wait(lambda: self.ready_at(0))
        p.pin_reference()
        p.set_roi([0, 0, 160, 96])
        self.wait(lambda: self.ready_at(0) and p.last_result['field'] is not None)
        fine = p.settings()
        self.assertTrue(fine['detail'])
        self.assertEqual(fine['window'], 17)
        self.assertEqual(p.last_result['field'].step, 4)

        # Old jobs may have a hand-selected window. Retain it, even when it
        # matches the fine preset: identical windows can use different grids.
        old = dict(fine)
        del old['detail']
        del old['reference_count']
        p.apply_settings(old)
        self.wait(lambda: self.ready_at(0))
        self.assertFalse(p.detail.currentData())
        self.assertEqual(p.window.currentData(), 17)
        self.assertEqual(p.last_result['field'].step, 8)
        p.detail.setCurrentIndex(p.detail.findData(True))
        self.wait(lambda: self.ready_at(0))
        self.assertEqual(p.settings(), fine)
        self.assertEqual(p.last_result['field'].step, 4)
        p._probe(QPointF(0, 0))
        self.assertIn('Region edge', p.probe_label.text())
        p.detail.setCurrentIndex(p.detail.findData(False))
        self.wait(lambda: self.ready_at(0))
        self.assertEqual(p.settings()['window'], 31)
        self.assertEqual(p.last_result['field'].step, 15)
        with self.assertRaises(ValueError):
            validate_bos_settings(dict(old, detail='false'))

    def test_reference_average_displays_actual_span_and_restores(self):
        p = self.panel
        self.wait(lambda: self.ready_at(0))
        p.seek(4)
        self.wait(lambda: self.ready_at(4))
        p.reference_count.setCurrentIndex(p.reference_count.findData(8))
        self.wait(lambda: self.ready_at(4))
        p.pin_reference()
        self.wait(lambda: self.ready_at(4))
        self.assertEqual(p.settings()['reference_count'], 8)
        self.assertIn('-6 to -5 (2 of 8 frames)', p.ref_label.text())
        saved = p.settings()
        p.reference_count.setCurrentIndex(p.reference_count.findData(1))
        self.wait(lambda: self.ready_at(4))
        self.assertEqual(p.ref_label.text(), 'Pinned frame -6')
        p.apply_settings(saved)
        self.wait(lambda: self.ready_at(4))
        self.assertEqual(p.reference_count.currentData(), 8)
        self.assertIn('2 of 8 frames', p.ref_label.text())
        with self.assertRaises(ValueError):
            validate_bos_settings(dict(saved, reference_count=True))

    def test_pause_during_disk_work_keeps_last_painted_frame(self):
        self.wait(lambda: self.ready_at(0))
        p = self.panel
        render = BOSFrames.render
        def slow_render(session, settings):
            time.sleep(.09)
            return render(session, settings)
        with patch.object(BOSFrames, 'render', slow_render):
            QTest.mouseClick(p.play, Qt.MouseButton.LeftButton)
            self.wait(lambda: p.busy())
            QTest.mouseClick(p.play, Qt.MouseButton.LeftButton)
            self.wait(lambda: not p.busy())
            self.assertEqual(p.last_result['index'], 0)
            self.assertEqual(p.settings()['position'], 0)
            self.assertFalse(p.playing)
