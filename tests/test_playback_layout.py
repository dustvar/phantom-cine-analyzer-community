"""Shared Qt playback control geometry at laptop widths."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect, Qt, QEventLoop, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QPushButton

import test_guided_workspace as workspace


class PlaybackLayoutTests(unittest.TestCase):
    setUpClass = classmethod(workspace.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass = classmethod(workspace.GuidedWorkspaceTests.tearDownClass.__func__)
    setUp = workspace.GuidedWorkspaceTests.setUp
    tearDown = workspace.GuidedWorkspaceTests.tearDown

    def assert_aligned_transport(self):
        w = self.w
        card = w.playback_card
        self.assertTrue(card.isVisible())

        def bounds(widget):
            return QRect(widget.mapTo(card, QPoint()), widget.size())

        play = [bounds(b) for b in (w.reverse_play_button, w.pause_button, w.forward_play_button)]
        step = [bounds(b) for b in (w.reverse_faster_button, w.previous_frame_button,
                                     w.next_frame_button, w.forward_faster_button)]
        frame, fps = bounds(w.active_frame_label), bounds(w.playback_fps)
        self.assertEqual(frame.left(), fps.left())
        self.assertEqual(frame.right(), fps.right())
        self.assertEqual(play[0].left(), step[0].left())
        self.assertEqual(play[-1].right(), step[-1].right())
        self.assertEqual({r.height() for r in [*play, *step, frame, fps]}, {fps.height()})
        for buttons, field in ((play, frame), (step, fps)):
            self.assertEqual({r.width() for r in buttons}, {buttons[0].width()})
            self.assertEqual({r.top() for r in buttons}, {field.top()})
            self.assertEqual({b.left()-a.right()-1 for a,b in zip(buttons, buttons[1:])}, {4})
            self.assertEqual(field.left()-buttons[-1].right()-1, 6)
        self.assertEqual(fps.top()-frame.bottom()-1, 4)
        self.assertEqual(play[0].left(), card.rect().right()-fps.right())
        self.assertEqual(frame.top(), card.rect().bottom()-fps.bottom())
        for rectangle in [*play, *step, frame, fps]:
            self.assertTrue(card.rect().contains(rectangle))
        self.assertGreaterEqual(w.playback_fps.width(), w.playback_fps.sizeHint().width())
        self.assertGreaterEqual(w.active_frame_label.width(), w.active_frame_label.sizeHint().width())

    def test_shared_transport_in_viewer_tracking_and_multicine_dock(self):
        w = self.w
        for width in (1024, 1280):
            w.resize(width, 700)
            for tool in (w.viewer_tool, w.track_tool):
                with self.subTest(width=width, tool=tool.text()):
                    tool.click()
                    QTest.qWait(30)
                    self.assertIs(w.playback_card.parentWidget(), w.workspace_grid_widget)
                    self.assert_aligned_transport()
        second = self.root / 'second.cine'
        workspace.motion_cine(second)
        self.assertTrue(w.vm.load_cine_workspace_cb([str(self.source), str(second)]))
        for width in (1280, 1024):
            with self.subTest(width=width, docked=True):
                w.resize(width, 700)
                QTest.qWait(30)
                self.assertIs(w.playback_card.parentWidget(), w.seek_bar)
                self.assert_aligned_transport()

    def test_long_frame_numbers_and_large_fps_fonts_preserve_both_columns(self):
        self.w.playback_fps.setStyleSheet('font-size: 20px;')
        self.w.playback_fps.setValue(self.w.playback_fps.maximum())
        self.w.active_frame_label.setText('Frame V4 · −2,147,483,648')
        QTest.qWait(50)
        self.assert_aligned_transport()
        entry = self.w.playback_fps.lineEdit()
        self.assertGreaterEqual(entry.contentsRect().width(), entry.fontMetrics().horizontalAdvance(entry.text()))
        viewport = self.w.workspace_panes[0].graph.viewport()
        card = self.w.playback_card
        rect = QRect(viewport.mapFromGlobal(card.mapToGlobal(QPoint())), card.size())
        self.assertTrue(viewport.rect().contains(rect))

    def test_seek_travel_when_multi_cine_inspectors_open_and_close(self):
        w = self.w
        paths = [str(self.source)]
        for n in range(2, 5):
            path = self.root / f'Camera {n}.cine'
            workspace.motion_cine(path)
            paths.append(str(path))
        for count in (2, 4):
            with patch.object(QFileDialog, 'getOpenFileNames', return_value=(paths[:count], '')):
                QTest.mouseClick(w.findChild(QPushButton, 'cine_upload_button'), Qt.LeftButton)
            loop = QEventLoop()
            timer = QTimer()
            timer.timeout.connect(lambda: loop.quit() if w.isEnabled() else None)
            timer.start(20)
            QTimer.singleShot(10000, loop, loop.quit)
            loop.exec()
            timer.stop()
            self.assertEqual(len(w.vm.workspace_contexts), count)
            for width in (1024, 1280):
                w.resize(width, 720)
                for tool, index in ((w.viewer_tool, 0), (w.track_tool, 2)):
                    with self.subTest(count=count, width=width, tool=tool.text()):
                        QTest.mouseClick(w.nav_buttons[tool], Qt.LeftButton)
                        QTest.qWait(40)
                        if not w.details_panel.isVisible() or w.main_tab.currentIndex() != index:
                            QTest.mouseClick(w.details_buttons[index], Qt.LeftButton)
                        QTest.qWait(50)
                        self.assertTrue(w.details_panel.isVisible())
                        self.assertEqual(w.main_tab.currentIndex(), index)
                        self.assertEqual(w.width(), width)
                        self.assertGreaterEqual(w.frame_slider.width(), 200)
                        self.assert_aligned_transport()
                        QTest.mouseClick(w.details_close, Qt.LeftButton)
                        QTest.qWait(50)
                        self.assertTrue(w.details_panel.isHidden())
                        self.assertGreaterEqual(w.frame_slider.width(), 200)
                        card = w.playback_card
                        slider_start = card.mapFromGlobal(w.frame_slider.mapToGlobal(QPoint()))
                        self.assertGreater(slider_start.x(), card.rect().right())
            QTest.mouseClick(w.nav_buttons[w.viewer_tool], Qt.LeftButton)


if __name__ == '__main__':
    unittest.main()
