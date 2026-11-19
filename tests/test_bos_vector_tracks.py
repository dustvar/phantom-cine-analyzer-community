"""Independent fades and styling must survive a disconnected source and MP4 export."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from bos_frames import BOSFrames, default_bos_settings
from bos_preview import prepare_bos_preview, export_cached_bos
from bos_preview_ui import BOSPreviewWindow
from bos_studio_model import StudioEdit
from bos_studio_cards import composite_card
from bos_studio_text import StudioText
from bos_vector_controls import VectorAppearance
from bos_vectors import VectorStyle
from test_bos_motion import texture, write_frames


class IndependentVectorTrackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root/'dots.cine'
        dot = texture()
        write_frames(self.source, np.array([dot] + [np.roll(dot, 1, axis=1)]*7))
        self.settings = dict(default_bos_settings(), path=str(self.source), reference=0,
                             roi=[15, 15, 340, 210], component='direction', limit=1., show_vectors=True,
                             vector_color='#83c8e7', vector_encoding='threshold', vector_shape='filled')

    def test_independent_fades_and_restyle_match_live_after_disconnecting_source(self):
        state = self.settings
        clip = prepare_bos_preview(dict(state, vector_background='footage'), 1, 7, root=self.root/'cache')
        self.addCleanup(clip.close)
        session = BOSFrames(self.source)
        try:
            def render(**extra):
                return session.render(dict(state, position=1, **extra))['image']
            raw = render(show_vectors=False, opacity=0)
            color = render(show_vectors=False)
            arrows = render(vector_background='footage')
            both = render()
            style = VectorStyle(shape='needle', color='#9c3015', thickness=.5, encoding='grayscale', outline=False)
            restyled = render(**style.settings())
        finally:
            session.close()
        self.source.unlink()
        with clip.reader() as reader:
            np.testing.assert_array_equal(reader.read(0), arrows)
            for c, v, image in [(255,255,both), (0,0,raw), (255,0,color), (0,255,arrows)]:
                np.testing.assert_array_equal(reader.blended(0,c,v), image)
            np.testing.assert_array_equal(reader.blended(0,255,255,style), restyled)
            np.testing.assert_array_equal(reader.blended(0,255,120,style),
                                         cv2.addWeighted(color, 1-120/255, restyled, 120/255, 0.))
        self.assertFalse(np.array_equal(both,color))
        self.assertFalse(np.array_equal(restyled,both))
        self.assertTrue(clip.compass_available(0,map_on=True))
        self.assertFalse(clip.compass_available(0))

        edit = StudioEdit(clip.count, 5)
        edit.trim_overlay(0, 0, 3)
        edit.trim_overlay(0, 2, 5, 'vectors')
        edit.set_fades(0, .2, 0, 'vectors')
        edit.vector_style = style
        clone = edit.copy()
        edit.remove_overlay(0, 'vectors')
        self.assertEqual(clone.opacity_bytes('vectors'), bytes([0,0,0,255,255,255,0]))
        with clip.reader() as reader:
            expected = [reader.blended(i, clone.opacity_bytes()[i], clone.opacity_bytes('vectors')[i], style)
                        for i in range(clip.count)]
        with patch('bos_preview.BOSFrames', side_effect=AssertionError('Unexpected Cine access')):
            export_cached_bos(clip, self.root/'result.mp4', 5, studio=clone)
        cap = cv2.VideoCapture(str(self.root/'result.mp4'))
        try:
            for image in expected:
                ok, frame = cap.read()
                self.assertTrue(ok)
                diff = np.abs(image.astype(float) - cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                self.assertLess(diff.mean(), 9)
            self.assertFalse(cap.read()[0])
        finally:
            cap.release()

    def test_studio_tab_controls_and_cache_invalidation(self):
        app = QApplication.instance() or QApplication([])
        clip = prepare_bos_preview(self.settings, 1, 7, root=self.root/'cache')
        w = BOSPreviewWindow(None, clip)
        w.show()
        try:
            def settled():
                until = time.monotonic()+4
                while time.monotonic()<until:
                    app.processEvents()
                    if w.position>=0 and w.pending_seek is None and w.reader.get(w.position) is not None:
                        return
                    time.sleep(.003)
                self.fail('Layer preview did not settle.')
            settled()
            w.studio_button.click()
            p = w.studio
            self.assertFalse(p.tabs[3].isHidden())
            p.tabs[3].click()
            self.assertEqual(p.layer,'vectors')
            w.seek(3)
            settled()
            p.split.click()
            p.fade_in.setValue(.25)
            self.assertEqual(w.edit_model.vectors[1].fade_in,.25)
            self.assertEqual(len(w.edit_model.overlays),1)
            p.delete.click()
            self.assertTrue(p.add.isEnabled())
            p.add.click()
            p.first.setValue(clip.source_frame(4))
            self.assertEqual(w.edit_model.vectors[1].first,4)
            def choose_style():
                dialog = app.activeModalWidget()
                self.assertIsInstance(dialog, QDialog)
                form = dialog.findChild(VectorAppearance)
                form.shape.setCurrentIndex(form.shape.findData('needle'))
                form.thickness.setValue(.5)
                form.outline.setChecked(False)
                dialog.accept()
            QTimer.singleShot(0, choose_style)
            p.appearance.click()
            self.assertEqual(w.edit_model.vector_style.shape, 'needle')
            accepted = w.edit_model.vector_style
            self.assertEqual(accepted.thickness,.5)
            self.assertFalse(accepted.outline)
            def cancel_style():
                dialog = app.activeModalWidget()
                form = dialog.findChild(VectorAppearance)
                form.thickness.setValue(8)
                self.assertEqual(w.edit_model.vector_style.thickness, 8)
                dialog.reject()
            QTimer.singleShot(0, cancel_style)
            p.appearance.click()
            self.assertEqual(w.edit_model.vector_style, accepted)
            w.overlay.setChecked(False)
            w.seek(5)
            settled()
            with clip.reader() as reader:
                np.testing.assert_array_equal(w.view._pixels, reader.blended(5,0,255,w.edit_model.vector_style, include_key=False))
                self.assertIsNotNone(w.view.vector_key_card)
            w.vectors.setChecked(False)
            settled()
            with clip.reader() as reader:
                np.testing.assert_array_equal(w.view._pixels, reader.read(5, False))
                self.assertIsNone(w.view.vector_key_card)
        finally:
            w.close()
            while not w.cleaned:
                app.processEvents()
                time.sleep(.005)

    def test_canvas_text_and_calibrated_key_remain_editable_and_export_from_cache(self):
        app = QApplication.instance() or QApplication([])
        clip = prepare_bos_preview(self.settings, 1, 7, root=self.root/'cache')
        self.source.unlink()
        window = BOSPreviewWindow(None, clip)
        window.resize(920, 700)
        window.show()
        def wait():
            until = time.monotonic()+5
            while time.monotonic() < until:
                app.processEvents()
                if window.position >= 0 and window.pending_seek is None:
                    return
                QTest.qWait(5)
            self.fail('Cached preview seek timed out')
        def drag(a, b):
            QTest.mousePress(window.view, Qt.LeftButton, pos=a)
            QTest.mouseMove(window.view, b)
            QTest.mouseRelease(window.view, Qt.LeftButton, pos=b)
            app.processEvents()
        try:
            wait()
            window.resize(560, 700)
            app.processEvents()
            self.assertTrue(window.direction_swatch.isHidden())
            self.assertGreaterEqual(window.header_title.width(),window.header_title.sizeHint().width())
            self.assertGreaterEqual(window.sample_summary.width(),window.sample_summary.sizeHint().width())
            window.resize(920, 700)
            app.processEvents()
            self.assertFalse(window.direction_swatch.isHidden())
            window.studio_button.click()
            studio = window.studio
            studio.tabs[2].click()
            def add_title():
                dlg = app.activeModalWidget()
                dlg.checks['frame'].setChecked(False)
                dlg.caption.setPlainText('Sunrise')
                dlg.accept()
            QTimer.singleShot(0, add_title)
            studio.add.click()
            app.processEvents()
            view = window.view
            self.assertLess((view.card_view_rect('text').center()-view.image_rect().center()).manhattanLength(),3)
            original_key = view.card_view_rect('vector_key')
            self.assertLess((original_key.bottomRight()-view.image_rect().bottomRight()).manhattanLength(),25)
            for name, delta in [('text', QPoint(-66,-43)),('vector_key', QPoint(-64,-38))]:
                initial = view.card_view_rect(name)
                drag(initial.center().toPoint(), initial.center().toPoint()+delta)
                moved = view.card_view_rect(name)
                self.assertLess((moved.center()-initial.center()-delta).manhattanLength(),4)
                self.assertEqual(view.selected_card, name)
                pos = (moved.topLeft()+QPointF(2,2)).toPoint()
                drag(pos, pos-QPoint(34,20))
                resized = view.card_view_rect(name)
                self.assertGreater(resized.width(),moved.width()+5)
                self.assertLess((resized.bottomRight()-moved.bottomRight()).manhattanLength(),4)
            first_title = studio.text_settings()
            self.assertIn('anchor',first_title)
            self.assertGreater(window.vector_key_placement.scale,1.)
            window.seek(3)
            wait()
            studio.tabs[2].click()
            studio.split.click()
            view = window.view
            pos = view.card_view_rect('text').center().toPoint()
            drag(pos,pos+QPoint(92,0))
            both = studio.text_settings()
            self.assertEqual(both['blocks'][0]['anchor'],first_title['anchor'])
            self.assertNotEqual(both['blocks'][1]['anchor'],first_title['anchor'])
            window.compass_button.setChecked(False)
            studio.tabs[3].click()
            studio.fade_in.setValue(.12)
            window.edit_model.set_all_rates(5)
            window.edit_model.set_fades(0,.4,0,'vectors')
            window._studio_changed()
            wait()
            renderer = StudioText(clip,both,window.capture_fps)
            movie = self.root/'placed.mp4'
            export_cached_bos(clip, movie, 5, studio=window.edit_model, vector_key=window.vector_key_placement,
                              text_renderer=renderer.composite)
            cap = cv2.VideoCapture(str(movie))
            try:
                for index in range(clip.count):
                    window.seek(index)
                    wait()
                    expected = window.view._pixels.copy()
                    composite_card(expected,view.vector_key_card)
                    composite_card(expected,view.text_card)
                    ok,bgr = cap.read()
                    self.assertTrue(ok)
                    diff = np.abs(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB).astype(float)-expected)
                    self.assertLess(diff.mean(),9)
                    if index==0:
                        self.assertIsNone(view.vector_key_card)
                    if index==1:
                        self.assertAlmostEqual(float(view.vector_key_card[0][0,0,3]),255*.45,delta=1)
                    for card in (view.text_card,view.vector_key_card):
                        if card is not None:
                            x,y,w,h = card[1]
                            self.assertLess(diff[y:y+h,x:x+w].mean(),9)
                self.assertFalse(cap.read()[0])
            finally:
                cap.release()
        finally:
            window.close()
            while not window.cleaned:
                app.processEvents()
                QTest.qWait(2)
