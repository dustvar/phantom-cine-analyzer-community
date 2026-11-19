"""Ruler precision and explicit Edge cost/resolution choices through real Qt UI."""
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
import test_guided_workspace as workspace
import simplemeas_ui
from tracking_setup_ui import CLASSIC, EDGE, EDGE_ACCURACY_PRESETS, EdgeAccuracyDialog, setup_defaults


class CalibrationAccuracyTests(unittest.TestCase):
    setUpClass=classmethod(workspace.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(workspace.GuidedWorkspaceTests.tearDownClass.__func__)
    setUp=workspace.GuidedWorkspaceTests.setUp
    tearDown=workspace.GuidedWorkspaceTests.tearDown
    wait_worker=workspace.GuidedWorkspaceTests.wait_worker

    def ruler(self):
        w=self.w;w.scale_tool.click();w.raise_();w.activateWindow();QTest.qWait(50);w.graph.setFocus()
        self.assertEqual(w._precision_capture_kind(),'calibration')
        self.assertTrue(w._point_picker.isVisible())
        return w

    def test_ruler_loupe_fractional_keys_and_live_red_line(self):
        w=self.ruler();frame=w.vm.active_frame
        w._set_point_candidate(QPointF(100,100));QTest.keyClick(w.graph,Qt.Key_Right,Qt.ShiftModifier)
        QTest.keyClick(w.graph,Qt.Key_Return);QTest.qWait(20)
        self.assertEqual(w.vm.active_tool.points,[(100.1,100.0)])
        self.assertTrue(w._point_picker.isVisible());self.assertIsNotNone(w.graph._calibration_preview)
        w._set_point_candidate(QPointF(200,100));QTest.keyClick(w.graph,Qt.Key_Right,Qt.ShiftModifier)
        line=w.graph._calibration_preview.line()
        self.assertEqual(line.p1(),QPointF(100.1,100));self.assertEqual(line.p2(),QPointF(200.1,100))
        with patch.object(simplemeas_ui.ScaleDialog,'launch',return_value=(10.,'mm')):
            QTest.keyClick(w.graph,Qt.Key_Return);QTest.qWait(30)
        self.assertAlmostEqual(w.vm.image_scale,.1);self.assertEqual(w.vm.length_units,'mm')
        self.assertEqual(w.vm.meas_data['Scale Tool']['X0'],[100.1])
        self.assertEqual(w.vm.meas_data['Scale Tool']['X1'],[200.1])
        self.assertFalse(w._point_picker.isVisible());self.assertIsNone(w.graph._calibration_preview)
        self.assertEqual(w.vm.active_frame,frame)

    def test_ruler_mouse_preview_zoom_rotated_keypad_and_input_focus(self):
        w=self.ruler();g=w.graph;g.zoom_at(g.viewport().rect().center(),4);g.rotate(90)
        point=QPointF(140,110);pos=g.mapFromScene(point);viewport=g.viewport()
        QApplication.sendEvent(viewport,QMouseEvent(QEvent.MouseMove,QPointF(pos),QPointF(viewport.mapToGlobal(pos)),Qt.NoButton,Qt.NoButton,Qt.NoModifier))
        self.assertLess((w._point_candidate-point).manhattanLength(),1)
        w._set_point_candidate(point);QTest.keyClick(g,Qt.Key_6,Qt.KeypadModifier)
        self.assertEqual(w._point_candidate,QPointF(140,109))
        QTest.keyClick(g,Qt.Key_2,Qt.KeypadModifier|Qt.ShiftModifier)
        self.assertEqual(w._point_candidate,QPointF(140.1,109))
        # Graph and other editor widgets own their normal keyboard behavior.
        w.playback_fps.setFocus() if hasattr(w,'playback_fps') else w.tracking_setup.controls['start'].setFocus()
        candidate=QPointF(w._point_candidate)
        self.assertFalse(w._guided_wants_key(__import__('PySide6.QtGui',fromlist=['QKeyEvent']).QKeyEvent(QEvent.KeyPress,Qt.Key_Right,Qt.NoModifier)))
        self.assertEqual(w._point_candidate,candidate)
        w.viewer_tool.click();QTest.qWait(20);self.assertFalse(w._point_picker.isVisible())

    def test_ruler_cancelled_length_can_start_new_pair(self):
        w=self.ruler();w._set_point_candidate(QPointF(50,50));QTest.keyClick(w.graph,Qt.Key_Return);QTest.qWait(10)
        w._set_point_candidate(QPointF(150,50))
        with patch.object(simplemeas_ui.ScaleDialog,'launch',return_value=None):
            QTest.keyClick(w.graph,Qt.Key_Return);QTest.qWait(20)
        self.assertTrue(w._point_picker.isVisible());self.assertIsNone(w.graph._calibration_preview)
        w.graph.setFocus();w._set_point_candidate(QPointF(75.5,70));QTest.keyClick(w.graph,Qt.Key_Return);QTest.qWait(20)
        self.assertEqual(w.vm.active_tool.points,[(75.5,70.0)])
        self.assertEqual(w.vm.active_tool.points_frame_ids,[w.vm.active_frame])
        self.assertIsNotNone(w.graph._calibration_preview)

    def test_ruler_follows_active_cine_without_transferring_first_point(self):
        from test_cine_metadata import write_cine
        second=self.root/'smaller.cine';write_cine(second,frames=20,width=120,height=100)
        w=self.w;w.vm.load_cine_workspace_cb([str(self.source),str(second)]);QTest.qWait(30)
        self.ruler();w._set_point_candidate(QPointF(200,180));QTest.keyClick(w.graph,Qt.Key_Return);QTest.qWait(15)
        first_tool=w.vm.active_tool;self.assertEqual(first_tool.points,[(200.,180.)])
        w.activate_cine_pane(1);QTest.qWait(30);w.graph.setFocus()
        self.assertIs(w._point_picker.graph,w.graph)
        self.assertIsNot(w.vm.active_tool,first_tool);self.assertFalse(w.vm.active_tool.points)
        self.assertLessEqual(w._point_candidate.x(),119);self.assertLessEqual(w._point_candidate.y(),99)
        w._set_point_candidate(QPointF(40.5,30));QTest.keyClick(w.graph,Qt.Key_Return);QTest.qWait(15)
        self.assertEqual(w.vm.active_tool.points,[(40.5,30.)]);self.assertEqual(first_tool.points,[(200.,180.)])

    def test_method_chooser_equal_buttons_without_cancel_and_escape(self):
        errors=[]
        def inspect():
            try:
                dialog=QApplication.activeModalWidget();buttons=[dialog.findChild(QPushButton,n) for n in ('classic_method_button','hybrid_method_button','tumbling_method_button')]
                self.assertIsNone(dialog.findChild(QPushButton,'tracking_method_cancel_button'))
                self.assertLessEqual(max(b.width() for b in buttons)-min(b.width() for b in buttons),1)
                self.assertEqual(len({b.height() for b in buttons}),1)
                QTest.keyClick(dialog,Qt.Key_Escape)
            except BaseException as error:errors.append(error);QApplication.activeModalWidget().reject()
        QTimer.singleShot(40,inspect)
        self.assertIsNone(simplemeas_ui.TrackingMethodDialog.choose(self.w))
        if errors:raise errors[0]

    def test_edge_chooser_and_presets_apply_real_tracking_settings(self):
        w=self.w;w.track_tool.click()
        QTimer.singleShot(40,lambda:QApplication.activeModalWidget().findChild(QPushButton,'hybrid_method_button').click())
        with patch.object(EdgeAccuracyDialog,'choose',return_value='extreme') as chooser:w.add_object_button.click()
        chooser.assert_called_once();self.assertEqual(w._pending_tracking_settings['position_precision'],.05)
        self.assertEqual(w.tracking_guide.edge_accuracy.currentData(),'extreme')
        w.tracking_guide.edge_accuracy.setCurrentIndex(w.tracking_guide.edge_accuracy.findData('normal'))
        self.assertEqual(w._pending_tracking_settings['rotation_step'],6.)
        w._guide_target_chosen(QPointF(150,120));track=w._guide_track()
        for key,value in EDGE_ACCURACY_PRESETS['normal']['settings'].items():self.assertEqual(track[key],value)
        w.tracking_setup.accuracy.setCurrentIndex(w.tracking_setup.accuracy.findData('tight'))
        for key,value in EDGE_ACCURACY_PRESETS['tight']['settings'].items():self.assertEqual(track[key],value)
        w.tracking_setup.controls['position_precision'].setValue(.2)
        self.assertEqual(track['position_precision'],.2);self.assertEqual(w.tracking_setup.accuracy.currentData(),'custom')
        # Existing jobs retain their exact stored numbers; presets never rewrite
        # a loaded old object's precise settings just to fit the new default.
        old=dict(setup_defaults(EDGE),**EDGE_ACCURACY_PRESETS['extreme']['settings'])
        w.tracking_setup.load(old,0,{0:track});self.assertEqual(w.tracking_setup.accuracy.currentData(),'extreme')
        self.assertEqual(w.tracking_setup.controls['position_precision'].value(),.05)
        w.tracking_guide.cancel.click()

    def test_accuracy_dialog_explains_resolution_not_guaranteed_accuracy(self):
        dialog=EdgeAccuracyDialog(self.w);dialog.show();QTest.qWait(10)
        self.assertEqual(dialog.choice.currentData(),'normal')
        for key in ('normal','tight','extreme'):self.assertGreaterEqual(dialog.choice.findData(key),0)
        self.assertLess(EDGE_ACCURACY_PRESETS['normal']['settings']['angle_precision']**-1,
                        EDGE_ACCURACY_PRESETS['extreme']['settings']['angle_precision']**-1)
        dialog.close();dialog.deleteLater()


if __name__=='__main__':unittest.main()
