import os
from dataclasses import replace
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QStyle, QStyleOptionSlider

from camera_capture_modes import RateOption, published_modes
from camera_control import ControlStatus, StoredCine
from camera_control_ui import CameraControlPanel
from event_camera_discovery import DiscoveredCamera


def armed():
    return ControlStatus('Phantom test', '123', 1000., 49.256, 250, 1000, 80, 60,
                         ((0, frozenset(['PRE'])), (1, frozenset(['WTR', 'ABL', 'ACT']))))


def stored():
    return replace(armed(), slots=((0, frozenset(['PRE', 'ACT'])), (1, frozenset(['STR']))),
                   replaceable=(StoredCine(1, '123', (123456, 7)),))


class FakeSession:
    def __init__(self, camera, host, expected_serial, cancelled):
        self.camera = camera
        self.host = host
        self.cancelled = cancelled
        self.camera.calls.append(('connect', (host, expected_serial)))

    def status(self):
        if self.camera.status_error:
            raise RuntimeError('Camera offline')
        return self.camera.settings

    def preview(self, *, full_precision=False):
        self.camera.calls.append(('preview', None))
        if self.camera.preview_gate:
            self.camera.preview_gate.wait(2)
        if self.camera.preview_error:
            raise RuntimeError('Monitor unavailable')
        return np.full((60, 80), len(self.camera.calls) % 256, dtype=np.uint8)

    def stop_preview(self):
        self.camera.calls.append(('stop_preview', None))

    def apply(self, **kwargs):
        self.camera.calls.append(('apply', kwargs))
        if self.camera.apply_gate:
            self.camera.apply_gate.wait(3)
        if self.camera.apply_error:
            raise ValueError('Exposure outside sensor limits')
        fields = dict(kwargs)
        if 'exposure_us' in fields and self.camera.round_exposure:
            fields['exposure_us'] = round(fields['exposure_us'], 2)
        mode = fields.pop('mode', None)
        before = fields.pop('before_percent', None)
        if mode is not None:
            if before is None:
                before = 100 * (1 - self.camera.settings.post_frames / self.camera.settings.capacity)
            fields.update(width=mode[0], height=mode[1], binning=mode[2],
                          capacity=3600, post_frames=round(3600 * (1 - before / 100)))
        elif before is not None:
            fields['post_frames'] = round(self.camera.settings.capacity * (1 - before / 100))
        self.camera.settings = replace(self.status(), **fields)
        return self.status()

    def arm(self, replace=None):
        self.camera.calls.append(('arm', replace))
        self.camera.settings = armed()
        return self.status()

    def trigger(self, expected_slot, *, deadline):
        self.camera.calls.append(('trigger', expected_slot))
        self.camera.trigger_deadline = deadline
        if self.camera.trigger_gate:
            self.camera.trigger_gate.wait(2)
        self.camera.settings = armed() if self.camera.auto_rearm else stored()
        return self.status()

    def csr(self):
        self.camera.calls.append(('csr',None))
        return self.status()

    def close(self):
        self.camera.calls.append(('close', None))


class CameraControlPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.calls = []
        self.settings = armed()
        self.auto_rearm = False
        self.preview_gate = self.trigger_gate = self.apply_gate = None
        self.round_exposure = False
        self.trigger_deadline = None
        self.preview_error = self.apply_error = self.status_error = False
        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {'PCA_USER_DATA_DIR':profile}))
        self.discovery = self.enterContext(patch('event_camera_picker.discover_cameras',
                                            return_value=[DiscoveredCamera('192.0.2.33', '123')]))
        self.enterContext(patch('camera_control_ui_worker.CameraControl',
                          side_effect=lambda *a, **kw: FakeSession(self, *a, **kw)))
        self.panel = CameraControlPanel()
        self.panel.resize(960, 620)
        self.panel.show()

    def tearDown(self):
        self.wait(self.panel.shutdown)
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def wait(self, predicate):
        end = time.monotonic()+4
        while time.monotonic()<end and not predicate():
            QTest.qWait(8)
            time.sleep(.001)
        self.assertTrue(predicate())

    def start(self):
        self.panel.set_active(True)
        self.wait(lambda:self.panel.view.image is not None)
        self.assertEqual(self.panel.view.image.width(), 80)

    def writes(self):
        return [settings for command, settings in self.calls if command == 'apply']

    def type_number(self, box, text):
        box.setFocus()
        box.selectAll()
        QTest.keyClicks(box, text)

    def choose(self, button, menu_text):
        action = next(a for a in button.menu().actions() if a.text().startswith(menu_text))
        self.assertTrue(button.isEnabled() and action.isEnabled())
        action.trigger()

    def open_rates(self):
        button = self.panel.rate_presets
        QTest.mouseClick(button, Qt.LeftButton)
        self.wait(button.popup.isVisible)
        return button.popup.table

    def rate_cell(self, table, fps, mode):
        column = 1 if mode[2] == 1 else 2
        for row in range(table.rowCount()):
            item = table.item(row, column)
            option = item.data(Qt.UserRole) if item else None
            if option and (option.fps, option.mode) == (fps, mode):
                return item
        self.fail(f'Rate picker has no cell for {fps:g} fps, {mode}')

    def click_rate_cell(self, table, item):
        table.scrollToItem(item)
        self.app.processEvents()
        rect = table.visualItemRect(item)
        self.assertTrue(table.viewport().rect().contains(rect.center()))
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=rect.center())
        self.wait(lambda: not self.panel.rate_presets.popup.isVisible())

    def start_veo(self, binning=True):
        modes = published_modes('Phantom VEO 1310S', binning, 1_000_000)
        self.settings = replace(armed(), model='Phantom VEO 1310S', fps=100.,
                                width=1280, height=960, exposure_dead_us=.74,
                                min_fps=50, max_fps=1_000_000, published_rates=modes)
        self.start()

    def test_activation_is_read_only_and_unplug_pauses_monitor(self):
        self.assertFalse(self.calls)
        self.start()
        p = self.panel
        self.assertEqual(self.calls[0], ('connect', ('192.0.2.33', '123')))
        self.assertTrue(p.trigger_button.isEnabled())
        self.assertFalse(p.arm_button.isEnabled())
        self.assertEqual(p.exposure.value(), 49.256)
        self.assertTrue('Armed' in p.state.text())
        self.assertNotIn('Apply settings', [b.text() for b in p.findChildren(QPushButton)])
        self.assertFalse(any(c in ('arm','trigger','apply','csr') for c, _ in self.calls))
        p.preview_button.click()
        self.wait(lambda: ('stop_preview', None) in self.calls)
        self.assertTrue(p.trigger_button.isEnabled())
        self.assertFalse(p.preview_on)
        p.preview_button.click()
        self.wait(lambda:p.preview_on and p.frames>0)
        self.status_error = True
        p.next_status = 0
        self.wait(lambda:p.session is None and not p.workers)
        self.assertIn('offline', p.notice.text())
        self.assertFalse(p.trigger_button.isEnabled())
        self.assertIsNone(p.view.image)

    def test_typing_waits_for_enter_or_focus_change_even_across_a_status_poll(self):
        self.start()
        p = self.panel
        self.type_number(p.fps, '200')
        self.settings = replace(self.settings, fps=1010.)
        p.next_status = 0
        self.wait(lambda:p.snapshot.fps==1010.)
        self.assertEqual(self.writes(), [])
        QTest.keyClicks(p.fps, '0')
        QTest.keyClick(p.fps, Qt.Key_Return)
        self.wait(lambda: p.snapshot.fps == 2000)
        self.assertEqual(self.writes(), [dict(fps=2000.)])
        self.type_number(p.exposure, '15.431')
        self.round_exposure = True
        QTest.keyClick(p.exposure, Qt.Key_Tab)
        self.wait(lambda: p.snapshot.exposure_us == 15.43)
        self.assertEqual(p.exposure.value(), 15.43)
        self.assertEqual(self.writes()[-1], dict(exposure_us=15.431))
        self.type_number(p.post, '20')
        QTest.keyClick(p.post, Qt.Key_Return)
        self.wait(lambda: p.snapshot.post_frames == 20)
        self.assertEqual(p.position.value(), 98)
        self.assertEqual(p.breakdown.text(), '980 before · 20 after')
        self.assertEqual(self.writes()[-1], dict(post_frames=20))
        self.assertTrue(p.trigger_button.isEnabled())

    def test_trigger_position_sends_once_on_mouse_release_and_supports_keyboard_steps(self):
        self.settings = replace(armed(), post_frames=249)
        self.start()
        p = self.panel
        slider = p.position
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        handle = slider.style().subControlRect(QStyle.CC_Slider, option,
                                               QStyle.SC_SliderHandle, slider)
        self.assertEqual(slider.value(), 75)
        QTest.mouseClick(slider, Qt.LeftButton, pos=handle.center())
        QTest.qWait(100)
        self.assertEqual(p.post.value(), 249)
        self.assertFalse(self.writes())
        QTest.mousePress(slider, Qt.LeftButton, pos=handle.center())
        self.assertTrue(slider.isSliderDown())
        QTest.mouseMove(slider, QPoint(slider.width() // 2, handle.center().y()))
        end = QPoint(slider.width() // 3, handle.center().y())
        QTest.mouseMove(slider, end)
        percent = slider.value()
        self.assertLess(percent, 50)
        self.assertEqual(p.post.value(), (100 - percent) * 10)
        QTest.qWait(250)
        self.assertFalse(self.writes())
        QTest.mouseRelease(slider, Qt.LeftButton, pos=end)
        self.wait(lambda: p.snapshot.post_frames == (100 - percent) * 10)
        QTest.keyClick(slider, Qt.Key_Right)
        self.wait(lambda: p.snapshot.post_frames == (99 - percent) * 10)
        self.assertEqual(len(self.writes()), 2)
        self.assertFalse(any(c in ('arm','trigger','csr') for c, _ in self.calls))

    def test_rapid_edits_while_waiting_can_return_to_the_original_value(self):
        self.start()
        p = self.panel
        self.apply_gate = threading.Event()
        self.choose(p.exposure_presets, '20 µs')
        self.wait(lambda: len(self.writes()) == 1)
        self.assertFalse(p.trigger_button.isEnabled())
        self.choose(p.exposure_presets, '10 µs')
        self.type_number(p.exposure, '49.256')
        QTest.keyClick(p.exposure, Qt.Key_Return)
        self.assertEqual(self.writes(), [dict(exposure_us=20.)])
        self.apply_gate.set()
        self.wait(lambda: p.trigger_button.isEnabled() and len(self.writes()) == 2)
        self.assertEqual(self.writes()[-1], dict(exposure_us=49.256))
        self.assertEqual(p.exposure.value(), 49.256)

    def test_apply_failure_restores_readback_without_resubmitting(self):
        self.start()
        p = self.panel
        self.apply_gate = threading.Event()
        self.apply_error = True
        self.choose(p.exposure_presets, '20 µs')
        self.wait(lambda: len(self.writes()) == 1)
        self.choose(p.exposure_presets, '10 µs')
        self.type_number(p.fps, '500')
        self.apply_gate.set()
        self.wait(lambda:'outside sensor limits' in p.notice.text())
        self.assertEqual(p.exposure.value(),49.256)
        self.assertEqual(p.fps.valueFromText(p.fps.text()), p.snapshot.fps)
        self.assertTrue(p.trigger_button.isEnabled())
        QTest.keyClick(p.fps, Qt.Key_Tab)
        QTest.qWait(260)
        self.assertEqual(self.writes(), [dict(exposure_us=20.)])

    def test_leaving_camera_control_discards_followup_edit_before_reconnect(self):
        self.start()
        p = self.panel
        self.apply_gate = threading.Event()
        self.choose(p.exposure_presets, '20 µs')
        self.wait(lambda: len(self.writes()) == 1)
        self.choose(p.exposure_presets, '10 µs')
        p.pause()
        self.apply_gate.set()
        self.wait(lambda: not p.workers)
        self.settings = replace(armed(), exposure_us=80.)
        self.start()
        self.assertEqual(p.exposure.value(), 80.)
        self.assertEqual(self.writes(), [dict(exposure_us=20.)])

    def test_matching_presets_exposure_limit_and_duration_at_laptop_size(self):
        self.panel.resize(850, 540)
        current = RateOption(1000, 80, 60, 1)
        fast = RateOption(4000, 40, 30, 2)
        self.settings = replace(armed(), published_rates=(current, fast),
                                exposure_dead_us=.74, exposure_us=900, min_exposure_us=.689)
        self.start()
        p = self.panel
        self.apply_gate = threading.Event()
        self.assertTrue(p.rate_presets.isVisible())
        table = self.open_rates()
        fast_cell = self.rate_cell(table, fast.fps, fast.mode)
        self.assertIn('40 × 30', fast_cell.text())
        self.click_rate_cell(table, fast_cell)
        self.assertEqual(p.fps.value(), 4000)
        self.assertEqual(p.exposure.value(), 249.26)
        self.assertEqual(p.exposure.maximum(), 249.26)
        self.assertIn('Estimated record time', p.record_time.text())
        self.assertIn('1 s', p.record_time.text())
        self.assertEqual(p.before.text(), 'Before · 750 ms')
        self.assertEqual(p.after.text(), 'After · 250 ms')
        self.assertFalse(p.post.isVisible())
        exp_menu = p.exposure_presets.menu()
        self.assertEqual(exp_menu.actions()[0].text(), 'Longest · 249.26 µs')
        self.apply_gate.set()
        self.wait(lambda: p.snapshot.width == 40)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.writes()[0]['mode'], (40, 30, 2))
        self.assertEqual(self.writes()[0]['fps'], 4000.)
        self.assertEqual(self.writes()[0]['exposure_us'], 249.26)
        self.assertEqual(p.record_time.text(), 'Record time · 900 ms')
        self.assertEqual(p.breakdown.text(), '2,700 before · 900 after')
        self.assertTrue(p.post.isVisible())
        self.choose(p.exposure_presets, '10 µs')
        self.wait(lambda: p.snapshot.exposure_us == 10)
        self.choose(p.exposure_presets, 'Longest')
        self.wait(lambda: p.snapshot.exposure_us == 249.26)
        self.assertEqual(len(self.writes()), 3)

    def test_menus_are_populated_before_first_press_and_stable_while_open(self):
        self.settings = replace(armed(), exposure_dead_us=.74)
        self.start()
        p = self.panel
        btn = p.exposure_presets
        menu = btn.menu()
        first = menu.actions()[0]  # actions must exist before Cocoa handles mouse-down
        observations = []

        def inspect_popup():
            popup = QApplication.activePopupWidget()
            observations.append(popup is menu and menu.isVisible() and menu.actions()[0] is first)
            if popup:
                QTest.keyClick(popup, Qt.Key_Escape)

        QTimer.singleShot(15, inspect_popup)
        QTest.mouseClick(btn, Qt.LeftButton)
        self.wait(lambda: observations)
        self.assertEqual(observations, [True])
        self.wait(lambda: not menu.isVisible())

        menu = p.exposure_presets.menu()
        existing = menu.actions()[0]
        self.assertEqual(existing.text(), 'Longest · 999.26 µs')
        p._readback(self.settings, reset=False)
        self.assertIs(menu.actions()[0], existing)
        menu.popup(p.exposure_presets.mapToGlobal(p.exposure_presets.rect().bottomLeft()))
        self.wait(menu.isVisible)
        self.settings = replace(self.settings, fps=2000.)
        p._readback(self.settings, reset=False)
        self.assertIs(menu.actions()[0], existing)
        menu.close()
        self.wait(lambda: menu.actions()[0].text() == 'Longest · 499.26 µs')
        self.settings = replace(self.settings, fps=500.)
        p._readback(self.settings, reset=True)
        self.assertEqual(menu.actions()[0].text(), 'Longest · 1,999.26 µs')

    def test_rate_picker_pairs_full_sensor_outputs_and_switches_modes_at_the_same_fps(self):
        self.start_veo()
        p = self.panel
        table = self.open_rates()
        headers = [table.horizontalHeaderItem(col).text().casefold() for col in range(3)]
        self.assertIn('fps', headers[0])
        self.assertIn('standard', headers[1])
        self.assertIn('binned', headers[2])
        normal = self.rate_cell(table, 100, (1280, 960, 1))
        binned = self.rate_cell(table, 100, (640, 480, 2))
        self.assertEqual(normal.row(), binned.row())
        self.assertIn('640 × 480', binned.text())
        self.assertIs(table.currentItem(), normal)
        self.assertIn('100 fps', normal.data(Qt.AccessibleTextRole))
        self.assertIn('standard', normal.data(Qt.AccessibleTextRole).casefold())
        self.assertIn('binned', binned.data(Qt.AccessibleTextRole).casefold())
        QTest.keyClick(table, Qt.Key_Right)
        self.assertIs(table.currentItem(), binned)
        self.assertEqual(self.writes(), [])
        QTest.keyClick(table, Qt.Key_Return)
        self.wait(lambda: p.snapshot.binning == 2)
        self.assertEqual(p.snapshot.fps, 100)
        self.assertEqual(self.writes(), [dict(fps=100., mode=(640, 480, 2))])

        table = self.open_rates()
        self.assertIs(table.currentItem(), self.rate_cell(table, 100, (640, 480, 2)))
        self.click_rate_cell(table, self.rate_cell(table, 100, (1280, 960, 1)))
        self.wait(lambda: p.snapshot.binning == 1)
        self.assertEqual(self.writes(), [dict(fps=100., mode=(640, 480, 2)), dict(fps=100., mode=(1280, 960, 1))])
        self.assertFalse(any(cmd in ('arm', 'csr', 'trigger') for cmd, _ in self.calls))

    def test_rate_picker_navigation_cancel_and_exact_entry(self):
        self.start_veo()
        p = self.panel
        table = self.open_rates()
        option = self.rate_cell(table, 100, (640, 480, 2))
        position = option.row()
        QTest.keyClick(table, Qt.Key_Right)
        p.next_status = 0
        self.settings = replace(self.settings, exposure_us=200.)
        self.wait(lambda: p.snapshot.exposure_us == 200)
        self.assertIs(table.item(position, 2), option)
        QTest.keyClick(table, Qt.Key_Escape)
        self.wait(lambda: not p.rate_presets.popup.isVisible())
        self.assertFalse(self.writes())
        self.open_rates()
        QTest.mouseClick(p.rate_presets.popup.custom_button, Qt.LeftButton)
        self.wait(lambda: not p.rate_presets.popup.isVisible())
        self.assertEqual(self.writes(), [])
        QTest.keyClicks(p.fps, '137')
        QTest.keyClick(p.fps, Qt.Key_Return)
        self.wait(lambda: p.snapshot.fps == 137.)
        self.assertEqual(self.writes(), [dict(fps=137.)])

    def test_rate_picker_disables_unavailable_binning(self):
        self.start_veo(binning=False)
        table = self.open_rates()
        left = self.rate_cell(table, 100, (1280, 960, 1))
        right = table.item(left.row(), 2)
        self.assertFalse(right and right.flags() & Qt.ItemIsEnabled)
        side = table.model().index(left.row(), 2)
        self.assertFalse(table.model().flags(side) & Qt.ItemIsEnabled)
        table.scrollTo(side)
        self.app.processEvents()
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=table.visualRect(side).center())
        self.assertTrue(self.panel.rate_presets.popup.isVisible())
        QTest.keyClick(table, Qt.Key_Escape)
        self.assertFalse(self.writes())

    def test_rate_picker_rechecks_support_on_click_after_live_limits_change(self):
        self.start_veo()
        table = self.open_rates()
        cell = self.rate_cell(table, 100, (640, 480, 2))
        self.settings = replace(self.settings, published_rates=published_modes('Phantom VEO 1310S', False, 1_000_000))
        self.panel.next_status = 0
        self.wait(lambda: self.panel.snapshot.published_rates == self.settings.published_rates)
        self.assertTrue(self.panel.rate_presets.popup.isVisible())
        self.click_rate_cell(table, cell)
        QTest.qWait(40)
        self.assertEqual(self.writes(), [])

    def test_custom_rate_beyond_window_is_blocked_and_explained(self):
        self.settings = replace(armed(), published_rates=(RateOption(1200,80,60,1),))
        self.start()
        p = self.panel
        self.type_number(p.fps, '2400')
        QTest.keyClick(p.fps, Qt.Key_Return)
        self.wait(lambda: '1,200 fps' in p.notice.text())
        self.assertEqual(self.writes(), [])
        self.assertEqual(p.fps.value(), 1000)
        self.assertEqual(p.position.value(),75)
        self.assertEqual(p.record_time.text(),'Record time · 1 s')

    def test_camera_without_exposure_margin_gets_rate_and_frame_period_together(self):
        self.settings = replace(armed(), exposure_us=800., exposure_dead_us=None)
        self.start()
        p = self.panel
        self.type_number(p.fps, '2400')
        QTest.keyClick(p.fps, Qt.Key_Return)
        self.wait(lambda: p.snapshot.fps == 2400)
        self.assertEqual(self.writes(), [dict(fps=2400., exposure_us=416.666)])
        self.assertEqual(p.exposure.value(), 416.666)

    def test_csr_requires_confirmation_and_committed_settings(self):
        self.settings = replace(armed(), supports_csr=True, capping_shutter=False)
        self.start()
        p = self.panel
        self.assertTrue(p.csr_button.isVisible())
        self.assertTrue(p.csr_button.isEnabled())
        self.apply_gate = threading.Event()
        self.choose(p.exposure_presets, '20 µs')
        self.assertFalse(p.csr_button.isEnabled())
        self.apply_gate.set()
        self.wait(p.csr_button.isEnabled)
        def answer(run):
            box = QApplication.activeModalWidget()
            self.assertIn('Cover the lens', box.informativeText())
            if run:
                next(b for b in box.buttons() if b.text() == 'Run CSR').click()
            else:
                box.reject()
        QTimer.singleShot(15, lambda: answer(False))
        p.csr_button.click()
        self.assertNotIn(('csr', None), self.calls)
        QTimer.singleShot(15, lambda: answer(True))
        p.csr_button.click()
        self.wait(lambda: ('csr',None) in self.calls and not p.pending)

    def test_trigger_and_confirmed_replacement(self):
        self.start()
        p = self.panel
        p.trigger_button.click()
        p.trigger_button.click()
        self.wait(lambda:'stored' in p.state.text())
        self.assertEqual([v for c,v in self.calls if c=='trigger'],[1])
        self.assertFalse(p.trigger_button.isEnabled())
        self.assertTrue(p.arm_button.isEnabled())
        def answer(yes):
            modal = QApplication.activeModalWidget()
            self.assertIsInstance(modal, QMessageBox)
            self.assertIn('Cine 1',modal.text())
            if yes:
                b=next(b for b in modal.buttons() if modal.buttonRole(b)==QMessageBox.DestructiveRole)
                b.click()
            else:
                modal.reject()
        QTimer.singleShot(20, lambda:answer(False))
        p.arm_button.click()
        self.assertFalse(any(c=='arm' for c,v in self.calls))
        QTimer.singleShot(20, lambda:answer(True))
        p.arm_button.click()
        self.wait(lambda:p.trigger_button.isEnabled())
        self.assertEqual([v for c,v in self.calls if c=='arm'],list(stored().replaceable))

    def test_trigger_double_click_guard_survives_fast_rearm_and_pending_action(self):
        self.auto_rearm = True
        self.start()
        p = self.panel
        interval = 200
        self.addCleanup(self.app.setDoubleClickInterval, self.app.doubleClickInterval())
        self.app.setDoubleClickInterval(interval)
        started = time.monotonic()
        p.trigger_button.click()
        self.wait(lambda: self.trigger_deadline is not None and not p.pending)
        self.assertGreaterEqual(self.trigger_deadline, started+1.)
        self.assertLessEqual(self.trigger_deadline, time.monotonic()+1.)
        self.assertTrue(p.snapshot.can_trigger)
        self.assertFalse(p.trigger_button.isEnabled())
        p.trigger_button.click()
        self.assertEqual(len([1 for c,_ in self.calls if c=='trigger']), 1)
        self.wait(p.trigger_button.isEnabled)
        self.trigger_gate = threading.Event()
        p.trigger_button.click()
        self.wait(lambda:not p.trigger_guard.isActive())
        self.assertFalse(p.trigger_button.isEnabled())
        self.trigger_gate.set()
        self.wait(p.trigger_button.isEnabled)

    def test_trigger_expires_in_queue_behind_delayed_preview(self):
        self.start()
        p = self.panel
        self.preview_gate = threading.Event()
        self.wait(lambda: p.read_pending and p.preview_on)
        # Advance only the worker's clock past the absolute deadline set on click.
        with patch('camera_control_ui_worker.monotonic', side_effect=lambda: time.monotonic()+2.):
            p.trigger_button.click()
            self.preview_gate.set()
            self.wait(lambda:'Trigger expired' in p.notice.text())
        self.assertFalse(any(c=='trigger' for c,_ in self.calls))
        self.assertTrue(p.snapshot.can_trigger)
        self.wait(p.trigger_button.isEnabled)

    def test_multiple_cameras_manual_connection_and_page_switch(self):
        a = DiscoveredCamera('192.0.2.33','123')
        self.discovery.return_value=[a,DiscoveredCamera('192.0.2.34','42')]
        self.panel.set_active(True)
        p = self.panel
        self.wait(lambda:not p.workers)
        self.assertFalse(self.calls)
        p.cameras.setCurrentIndex(p.cameras.count()-1)
        p.address.setText('bad address')
        p.connect_button.click()
        self.assertIsNone(p.session)
        p.address.setText('192.0.2.55')
        p.connect_button.click()
        self.wait(lambda:p.view.image is not None)
        self.assertEqual(self.calls[0],('connect',('192.0.2.55','')))
        self.discovery.return_value=[a]
        p.pause()
        p.set_active(True)
        self.wait(lambda:p.view.image is not None and len([1 for c,v in self.calls if c=='connect'])==2)
        self.assertIn(('close', None),self.calls)

    def test_scan_excludes_only_the_camera_in_use(self):
        p = self.panel
        p.camera_in_use = lambda hosts, serial: serial == '123'
        self.discovery.return_value += [DiscoveredCamera('192.0.2.34','42')]
        p.set_active(True)
        self.wait(lambda: not p.workers)
        p.connect_button.click()
        self.assertIn('Event Finder is scanning', p.notice.text())
        self.assertFalse(self.calls)
        p.cameras.setCurrentIndex(1)
        p.connect_button.click()
        self.wait(lambda: p.view.image is not None)
        self.assertEqual(self.calls[0], ('connect', ('192.0.2.34', '42')))

    def test_long_frame_schedules_next_read_immediately_with_one_read_in_flight(self):
        self.start()
        p=self.panel
        # Model arrival after the 30-fps cap has already elapsed.
        p.timer.stop()
        self.wait(lambda:not p.read_pending)
        p.timer.stop()
        p.next_frame=time.monotonic()-.08
        p.next_status=time.monotonic()+1
        p._schedule()
        self.assertEqual(p.timer.remainingTime(),0)
        p.timer.stop()
        p._tick()
        self.assertTrue(p.read_pending)
        p._schedule()
        self.assertFalse(p.timer.isActive())

    def test_preview_failure_keeps_recording_controls_and_requires_explicit_resume(self):
        self.preview_error=True
        self.panel.set_active(True)
        p=self.panel
        self.wait(lambda:'Monitor unavailable' in p.preview_info.text())
        self.assertTrue(p.trigger_button.isEnabled())
        self.assertFalse(p.preview_on)
        n=sum(c=='preview' for c,_ in self.calls)
        QTest.qWait(210)
        self.assertEqual(sum(c=='preview' for c,_ in self.calls),n)
        self.preview_error=False
        p.preview_button.click()
        self.wait(lambda:p.view.image is not None)
