"""Compact header access, layout and contextual inspectors through real Qt."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
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
        controls=(w.mode_button,w.scale_tool,choose,w.cine_path_disp,w.open_job_button,w.save_job_button)
        positions=[c.mapTo(w,QPoint(0,0)) for c in controls]
        self.assertEqual(positions[0].x(),6)
        self.assertEqual(sorted(p.x() for p in positions),[p.x() for p in positions])
        self.assertLess(max(p.y() for p in positions)-min(p.y() for p in positions),8)
        self.assertEqual(w.workflow_stack.mapTo(w,QPoint()).x(),6)
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
        self.assertTrue(w.details_rail.isHidden());self.assertTrue(w.workspace_header.isVisible())
        w.mode_actions[w.viewer_tool].trigger();QTest.qWait(20)
        self.assertIs(w.workflow_stack.currentWidget(),w.tab_splitter)
        self.assertTrue(w.details_buttons[1].isVisible());self.assertTrue(w.details_buttons[2].isHidden())

    def test_calibration_remains_accessible_and_has_live_tool_state(self):
        w=self.w;w.scale_tool.click();QTest.qWait(20)
        self.assertIs(w.current_tool,w.scale_tool)
        self.assertTrue(w.scale_tool.isChecked())
        self.assertIsNotNone(w.graph._image_item)
        self.assertGreater(w.graph._image_item.pixmap().width(),0)
        w.analysis_tools.setEnabled(False)
        self.assertFalse(w.mode_button.isEnabled());self.assertFalse(w.scale_tool.isEnabled())
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


if __name__=='__main__':unittest.main()
