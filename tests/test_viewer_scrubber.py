"""A real Viewer job with two Cines exercises both meanings of the shared rail."""
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
import test_guided_workspace as fixtures


class ViewerScrubberTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.GuidedWorkspaceTests.tearDownClass.__func__)
    setUp = fixtures.GuidedWorkspaceTests.setUp
    tearDown = fixtures.GuidedWorkspaceTests.tearDown

    def point(self, frame, part='head'):
        bar = self.w.frame_slider
        x = round(14 + frame * (bar.width()-28) / bar.maximum())
        return QPoint(x + {'in':-3, 'out':3, 'head':0}[part],
                      bar.height()//2 + (6 if part != 'head' else -8))

    def drag(self, start, end, part):
        bar = self.w.frame_slider
        QTest.mousePress(bar, Qt.LeftButton, pos=self.point(start, part))
        QTest.mouseMove(bar, self.point(end, part))
        QTest.mouseRelease(bar, Qt.LeftButton, pos=self.point(end, part))

    def test_mark_seek_and_trim_roundtrip_without_opening_timeline(self):
        w = self.w
        second = self.root/'another.cine'
        fixtures.motion_cine(second, frames=12)
        self.assertTrue(w.vm.load_cine_workspace_cb([str(self.source), str(second)]))
        QTest.qWait(30)
        self.assertTrue(w.frame_slider.isVisible())
        self.assertTrue(w.viewer_controls.isHidden())
        self.assertTrue(w.mark_in_button.isVisible())
        self.assertTrue(w.mark_out_button.isVisible())
        self.drag(0, 5, 'in')
        self.drag(29, 23, 'out')
        self.assertEqual(w.vm.viewer_edit['selection'], [5, 23])
        self.assertEqual(w.clip_start_label.text(), 'In: -5')
        self.assertEqual(w.clip_end_label.text(), 'Out: 13')
        self.drag(5, 18, 'head')
        self.assertEqual(w.frame_slider.value(), 18)
        self.assertEqual(w._playback_bounds(), (5, 23))
        # Seek outside the selection and mark another frame with the side button.
        QTest.mouseClick(w.frame_slider, Qt.LeftButton, pos=self.point(26))
        self.assertEqual(w.frame_slider.value(), 26)
        self.assertEqual(w.vm.viewer_edit['selection'], [5, 23])
        QTest.mouseClick(w.mark_out_button, Qt.LeftButton)
        self.assertEqual(w._playback_bounds(), (5, 26))
        w.activate_cine_pane(1)
        self.assertEqual(w._playback_bounds(), (0, 11))
        self.drag(0, 3, 'in')
        w.activate_cine_pane(0)
        self.assertEqual(w._playback_bounds(), (5, 26))
        saved = self.root/'brackets.pcajob'
        w.save_job_path(str(saved))
        w.reset_clip_button.click()
        self.assertEqual(w._playback_bounds(), (0, 29))
        w.open_job_path(str(saved))
        self.assertEqual(w._playback_bounds(), (5, 26))
        w.activate_cine_pane(1)
        self.assertEqual(w._playback_bounds(), (3, 11))

    def test_coincident_brackets_and_head_can_each_be_dragged(self):
        w = self.w
        self.drag(0, 15, 'in')
        self.drag(29, 15, 'out')
        self.assertEqual(w._playback_bounds(), (15, 15))
        self.drag(15, 20, 'head')
        self.assertEqual(w.frame_slider.value(), 20)
        self.assertEqual(w._playback_bounds(), (15, 15))
        self.drag(15, 24, 'out')
        self.drag(15, 9, 'in')
        self.assertEqual(w._playback_bounds(), (9, 24))
        self.assertTrue(w.viewer_controls.isHidden())

    def test_pressing_exact_handles_on_long_cine_does_not_round_them_to_pixels(self):
        from viewer_scrubber import ViewerScrubber
        bar = ViewerScrubber()
        bar.resize(500, 34)
        bar.set_viewer_mode(True)
        bar.setRange(0, 96182)
        bar.clip_range.setEnabled(True)
        bar.clip_range.setRange(0, 96182)
        bar.clip_range.setValues(31487, 80471)
        bar.setValue(61403)
        bar.show()
        try:
            def point(frame, offset, y):
                return QPoint(round(14 + frame * (bar.width()-28) / bar.maximum()) + offset, y)
            for frame, offset, y in ((61403, 0, 8), (31487, -3, 23), (80471, 3, 23)):
                QTest.mouseClick(bar, Qt.LeftButton, pos=point(frame, offset, y))
            self.assertEqual(bar.value(), 61403)
            self.assertEqual((bar.clip_range.lowerValue(), bar.clip_range.upperValue()), (31487, 80471))
        finally:
            bar.close()
