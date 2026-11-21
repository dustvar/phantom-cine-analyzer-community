"""Compact header access, layout and contextual inspectors through real Qt."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QPushButton
import test_guided_workspace as workspace


class WorkspaceHeaderTests(unittest.TestCase):
    setUpClass=classmethod(workspace.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(workspace.GuidedWorkspaceTests.tearDownClass.__func__)
    setUp=workspace.GuidedWorkspaceTests.setUp
    tearDown=workspace.GuidedWorkspaceTests.tearDown
    wait_worker=workspace.GuidedWorkspaceTests.wait_worker

    def test_one_row_preserves_video_space_and_control_order(self):
        w=self.w;w.resize(1280,720);QTest.qWait(50)
        choose=w.findChild(QPushButton,'cine_upload_button')
        controls=(w.brand,choose,w.cine_path_disp,w.open_job_button,w.save_job_button)
        positions=[c.mapTo(w,QPoint(0,0)) for c in controls]
        self.assertLessEqual(positions[0].x(),8)
        self.assertEqual(sorted(p.x() for p in positions),[p.x() for p in positions])
        self.assertLess(max(p.y() for p in positions)-min(p.y() for p in positions),8)
        self.assertLess(w.analysis_tools.geometry().right(), w.workflow_stack.geometry().left())
        self.assertTrue(all(b.isVisible() for b in w.nav_buttons.values()))
        self.assertLess(w.workspace_header.height(),48)
        self.assertGreater(w.graph.height(),540)
        self.assertTrue(w.job_label.isHidden())
        self.assertLess(w.workspace_header.geometry().bottom(),w.workflow_stack.geometry().top())
        for control in controls:
            self.assertTrue(control.isVisible());self.assertTrue(w.workspace_header.isAncestorOf(control))
            self.assertGreater(control.width(),20)

    def test_job_actions_preserve_open_save_and_save_as(self):
        w=self.w
        with patch.object(w,'open_job_dialog') as opened,patch.object(w,'save_job_dialog') as saved:
            w.open_job_action.trigger();opened.assert_called_once_with()
            w.save_job_action.trigger();saved.assert_called_once_with()
            saved.reset_mock();w.save_job_as_action.trigger();saved.assert_called_once_with(save_as=True)
        destination=self.root/'header.pcajob'
        w.save_job_path(str(destination));self.assertTrue(destination.exists())
        self.assertIn('header.pcajob',w.save_job_button.toolTip())
        w.open_job_path(str(destination));self.assertEqual(w.job_path,str(destination.resolve()))
        self.assertIn('Restored',w.save_job_button.toolTip())

    def test_tools_keep_contextual_tracks_and_other_inspectors(self):
        w=self.w
        self.assertFalse(w.details_buttons[2].isVisible())
        w.mode_actions[w.track_tool].trigger();QTest.qWait(20)
        self.assertIs(w.current_tool,w.track_tool);self.assertTrue(w.details_buttons[2].isVisible())
        w.details_buttons[2].click();self.assertTrue(w.details_panel.isVisible())
        self.assertEqual(w.main_tab.currentIndex(),2)
        w.mode_actions[w.viewer_tool].trigger();QTest.qWait(20)
        self.assertFalse(w.details_buttons[2].isVisible());self.assertTrue(w.details_panel.isHidden())
        for index in (0,1):
            w.details_buttons[index].click();self.assertTrue(w.details_panel.isVisible())
            self.assertEqual(w.main_tab.currentIndex(),index);w.details_close.click()
        w.mode_actions[w.overlay_tool].trigger();QTest.qWait(30)
        self.assertIs(w.workflow_stack.currentWidget(),w.overlay_panel)
        self.assertTrue(w.inspector_controls.isHidden());self.assertTrue(w.workspace_header.isVisible())
        w.mode_actions[w.viewer_tool].trigger();QTest.qWait(20)
        self.assertIs(w.workflow_stack.currentWidget(),w.tab_splitter)
        self.assertTrue(w.details_buttons[1].isVisible());self.assertTrue(w.details_buttons[2].isHidden())

    def test_inspectors_align_in_header_and_body_reaches_window_edge(self):
        w = self.w
        margin = w.layout().contentsMargins().right()
        for width in (900, 1280):
            w.resize(width, 720)
            for tool in (w.viewer_tool, w.track_tool, w.overlay_tool):
                w.mode_actions[tool].trigger()
                QTest.qWait(20)
                self.assertEqual(w.width(), width)
                self.assertEqual(w.workflow_stack.geometry().right(), width - margin - 1)
                buttons = [b for b in w.details_buttons if b.isVisible()]
                self.assertEqual(len(buttons), {w.track_tool: 3, w.viewer_tool: 2}.get(tool, 0))
                choose = w.findChild(QPushButton, 'cine_upload_button')
                source = [choose] if choose.isVisible() else []
                right = -1
                for button in (*source, *buttons, w.open_job_button, w.save_job_button):
                    self.assertTrue(w.workspace_header.isAncestorOf(button))
                    position = button.mapTo(w, QPoint(0, 0))
                    self.assertGreater(position.x(), right)
                    right = position.x() + button.width() - 1
                    self.assertEqual(position.y(), w.open_job_button.mapTo(w, QPoint(0, 0)).y())
                    self.assertEqual(button.height(), w.open_job_button.height())

    def test_inspector_resize_close_and_keyboard_switching(self):
        w = self.w
        w.mode_actions[w.track_tool].trigger()
        info, adjust, tracks = w.details_buttons
        info.setFocus()
        QTest.keyClick(info, Qt.Key_Space)
        self.assertTrue(w.details_panel.isVisible())
        available = sum(w.tab_splitter.sizes())
        w.tab_splitter.setSizes([available - 475, 475])
        resized = w.tab_splitter.sizes()[1]
        QTest.keyClick(info, Qt.Key_Tab)
        self.assertTrue(adjust.hasFocus())
        QTest.keyClick(adjust, Qt.Key_Space)
        self.assertEqual(w.main_tab.currentIndex(), 1)
        self.assertEqual(w.tab_splitter.sizes()[1], resized)
        self.assertTrue(adjust.isChecked())
        QTest.keyClick(adjust, Qt.Key_Tab)
        self.assertTrue(tracks.hasFocus())
        QTest.keyClick(tracks, Qt.Key_Space)
        self.assertEqual(w.main_tab.currentIndex(), 2)
        w.details_close.setFocus()
        QTest.keyClick(w.details_close, Qt.Key_Space)
        self.assertTrue(w.details_panel.isHidden())
        self.assertTrue(tracks.hasFocus())
        self.assertEqual(w.tab_splitter.sizes()[1], 0)
        QTest.keyClick(tracks, Qt.Key_Space)
        self.assertEqual(w.tab_splitter.sizes()[1], resized)
        QTest.keyClick(tracks, Qt.Key_Space)
        self.assertTrue(w.details_panel.isHidden())
        self.assertTrue(all(not b.isChecked() for b in w.details_buttons))

    def test_camera_control_uses_its_own_header_and_stops_on_return(self):
        w = self.w
        choose = w.findChild(QPushButton, 'cine_upload_button')
        with patch.object(w.camera_control, 'set_active') as set_active:
            w.mode_actions[w.camera_control_tool].trigger()
            self.assertIs(w.workflow_stack.currentWidget(), w.camera_control)
            set_active.assert_called_with(True)
            self.assertTrue(w.mode_actions[w.camera_control_tool].isChecked())
            self.assertTrue(choose.isHidden())
            self.assertTrue(w.nav_buttons[w.scale_tool].isVisible())
            w.mode_actions[w.viewer_tool].trigger()
            self.assertIs(w.workflow_stack.currentWidget(), w.tab_splitter)
            set_active.assert_called_with(False)
            self.assertFalse(choose.isHidden())

    def test_calibration_remains_accessible_and_has_live_tool_state(self):
        w=self.w
        rail = w.analysis_tools.layout()
        row = rail.getItemPosition(rail.indexOf(w.nav_buttons[w.scale_tool]))[0]
        self.assertEqual(rail.itemAtPosition(row-1, 0).widget().text(), 'SETUP')
        self.assertEqual(rail.itemAtPosition(row+1, 0).widget().text(), 'ANALYSIS')
        w.nav_buttons[w.scale_tool].click();QTest.qWait(20)
        self.assertIs(w.current_tool,w.scale_tool)
        self.assertTrue(w.scale_tool.isChecked())
        self.assertIsNotNone(w.graph._image_item)
        self.assertGreater(w.graph._image_item.pixmap().width(),0)
        w.analysis_tools.setEnabled(False)
        self.assertFalse(w.nav_buttons[w.scale_tool].isEnabled());self.assertFalse(w.scale_tool.isEnabled())
        w.analysis_tools.setEnabled(True)

    def test_larger_fonts_keep_inspector_labels_and_fps_readable(self):
        w=self.w
        for button in w.details_buttons:button.setStyleSheet('font-size: 20px;')
        w.playback_fps.setStyleSheet('font-size: 20px;')
        QTest.qWait(40)
        for button in w.details_buttons:
            self.assertGreaterEqual(button.width(),button.fontMetrics().horizontalAdvance(button.text())+12)
        self.assertGreaterEqual(w.playback_fps.width(),w.playback_fps.sizeHint().width())
        self.assertGreater(w.playback_card.width(),268)
        self.assertGreaterEqual(w.playback_card.height(),w.playback_card.layout().minimumSize().height())


class CineSelectionRailTests(unittest.TestCase):
    setUpClass = classmethod(workspace.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass = classmethod(workspace.GuidedWorkspaceTests.tearDownClass.__func__)
    tearDown = workspace.GuidedWorkspaceTests.tearDown

    def setUp(self):
        workspace.GuidedWorkspaceTests.setUp(self, load=False)

    def choose(self, button, files):
        with patch.object(QFileDialog, 'getOpenFileNames', return_value=(files, '')) as dialog:
            QTest.mouseClick(button, Qt.LeftButton)
            dialog.assert_called_once()
            for _ in range(200):
                if self.w.load_cine_thread is None:
                    break
                QTest.qWait(5)
        self.assertIsNone(self.w.load_cine_thread)
        self.assertTrue(self.w.isEnabled())

    def test_welcome_and_replacement_preserve_tool_availability(self):
        w = self.w
        bad = str(self.root / 'missing.cine')
        for files in ([], [bad]):
            self.choose(w.welcome.choose, files)
            self.assertTrue(w.welcome.isVisible())
            self.assertFalse(w.nav_buttons[w.scale_tool].isEnabled())
            self.assertFalse(w.nav_buttons[w.track_tool].isEnabled())
            self.assertTrue(w.nav_buttons[w.camera_control_tool].isEnabled())
        self.choose(w.welcome.choose, [str(self.source)])
        self.assertFalse(w.welcome.isVisible())
        self.assertEqual(w.cine_path, str(self.source))
        self.assertFalse(w.report_action.isEnabled())
        self.assertIn('measurement', w.report_button.accessibleDescription())
        choose = w.findChild(QPushButton, 'cine_upload_button')
        for files in (None, [bad], []):
            if files is not None:
                self.choose(choose, files)
            self.assertEqual(w.cine_path, str(self.source))
            for tool in (w.track_tool, w.scale_tool):
                button = w.nav_buttons[tool]
                self.assertTrue(w.mode_actions[tool].isEnabled())
                self.assertTrue(button.isEnabled())
                QTest.mouseClick(button, Qt.LeftButton)
                self.assertIs(w.current_tool, tool)
            w.nav_buttons[w.viewer_tool].click()


if __name__=='__main__':unittest.main()
