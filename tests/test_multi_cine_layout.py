"""Real multi-Cine view arrangement without mutating source identity or measurements."""
import unittest,shutil
import numpy as np
from PySide6.QtCore import QPoint,QPointF,Qt
from PySide6.QtTest import QTest
import test_guided_workspace as workspace
from multi_cine_layout import pane_positions


class MultiCineLayoutTests(unittest.TestCase):
    setUpClass=classmethod(workspace.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(workspace.GuidedWorkspaceTests.tearDownClass.__func__)
    setUp=workspace.GuidedWorkspaceTests.setUp
    tearDown=workspace.GuidedWorkspaceTests.tearDown
    wait_worker=workspace.GuidedWorkspaceTests.wait_worker

    def load(self,count):
        paths=[str(self.source)]
        for index in range(1,count):
            path=self.root/f'motion-{index+1}.cine';shutil.copyfile(self.source,path);paths.append(str(path))
        self.assertTrue(self.w.vm.load_cine_workspace_cb(paths));self.w.resize(1280,720)
        self.w.raise_();self.w.activateWindow();QTest.qWait(60)

    def positions(self):
        grid=self.w.workspace_grid_layout
        return {i:grid.getItemPosition(grid.indexOf(pane)) for i,pane in enumerate(self.w.workspace_panes) if not pane.isHidden()}

    def choose(self,mode):
        combo=self.w.pane_layout_manager.choice;combo.setCurrentIndex(combo.findData(mode));QTest.qWait(30)

    def test_two_three_and_four_panes_support_every_layout(self):
        for count in (2,3,4):
            self.load(count)
            for mode in ('auto','grid','side_by_side','stacked'):
                self.choose(mode)
                self.assertEqual(list(self.positions().values()),pane_positions(count,mode))
                self.assertIs(self.w.playback_card.parentWidget(),self.w.seek_bar)
                for pane in self.w.workspace_panes[:count]:
                    self.assertTrue(pane.isVisible());self.assertGreater(pane.graph.width(),80);self.assertGreater(pane.graph.height(),40)
                    self.assertTrue(self.w.rect().contains(pane.mapTo(self.w,QPoint())+pane.rect().bottomRight()))

    def test_reorder_hide_restore_preserve_source_identity_and_view(self):
        self.load(4);w=self.w;manager=w.pane_layout_manager
        identity=[id(context) for context in w.vm.workspace_contexts]
        sources=[context['cine_path'] for context in w.vm.workspace_contexts]
        graph=w.workspace_panes[1].graph;graph.zoom_at(graph.viewport().rect().center(),4)
        graph.centerOn(QPointF(145,115));QTest.qWait(20)
        transform=graph.transform();center=graph.mapToScene(graph.viewport().rect().center())
        manager.move_pane(3,0);QTest.qWait(30)
        self.assertEqual(w.pane_order,[3,0,1,2]);self.assertEqual(w.vm.active_cine_index,0)
        self.assertEqual(graph.transform(),transform)
        moved=graph.mapToScene(graph.viewport().rect().center())
        self.assertLess((moved-center).manhattanLength(),2)
        w.hide_cine_pane(1);QTest.qWait(30)
        manager.move_pane(2,0);QTest.qWait(30)
        self.assertEqual(manager.visible_order(),[2,3,0])
        w.restore_cine_pane(1);QTest.qWait(30)
        self.assertEqual(w.pane_order,[2,3,1,0]);self.assertEqual(graph.transform(),transform)
        self.assertEqual([id(c) for c in w.vm.workspace_contexts],identity)
        self.assertEqual([c['cine_path'] for c in w.vm.workspace_contexts],sources)

    def test_inactive_pane_zoom_and_pan_do_not_activate_or_move_other_pane(self):
        self.load(2);w=self.w;pane=w.workspace_panes[1];other=w.workspace_panes[0].graph
        initial=other.transform();pane.view_controls.plus.click();pane.view_controls.plus.click();QTest.qWait(30)
        self.assertEqual(w.vm.active_cine_index,0);self.assertEqual(other.transform(),initial)
        graph=pane.graph;before=graph.mapToScene(graph.viewport().rect().center())
        pos=graph.viewport().rect().center()
        QTest.mousePress(graph.viewport(),Qt.LeftButton,pos=pos)
        QTest.mouseMove(graph.viewport(),pos+QPoint(25,15),20)
        QTest.mouseRelease(graph.viewport(),Qt.LeftButton,pos=pos+QPoint(25,15));QTest.qWait(20)
        self.assertEqual(w.vm.active_cine_index,0)
        self.assertGreater((graph.mapToScene(pos)-before).manhattanLength(),2)
        self.assertEqual(other.transform(),initial)
        pane.view_controls.fit.click();self.assertEqual(graph._view_zoom,1)
        QTest.mouseClick(pane.title,Qt.LeftButton);self.assertEqual(w.vm.active_cine_index,1)

    def test_rotated_zoom_layout_and_job_roundtrip(self):
        self.load(3);w=self.w;w.track_tool.click()
        w._set_image_rotation(90);QTest.qWait(30)
        graph=w.graph;graph.zoom_at(graph.viewport().rect().center(),5);graph.centerOn(QPointF(145,120))
        zoom=graph._view_zoom;transform=graph.transform()
        self.choose('stacked');w.pane_layout_manager.move_pane(2,0);QTest.qWait(40)
        self.assertEqual(graph.transform(),transform);self.assertEqual(graph._view_zoom,zoom)
        before=[(p.graph.transform(),p.graph._view_zoom,p.graph.mapToScene(p.graph.viewport().rect().center())) for p in w.workspace_panes[:3]]
        path=self.root/'pane-layout.pcajob';w.save_job_path(path);w.open_job_path(path);QTest.qWait(120)
        self.assertEqual(w.pane_order[:3],[2,0,1]);self.assertEqual(w.pane_layout,'stacked')
        for pane,(transform,zoom,center) in zip(w.workspace_panes,before):
            self.assertEqual(pane.graph.transform(),transform);self.assertEqual(pane.graph._view_zoom,zoom)
            self.assertLess((pane.graph.mapToScene(pane.graph.viewport().rect().center())-center).manhattanLength(),3)
        self.assertEqual(w.graph._pca_rotation,90)

    def test_narrow_tracking_header_controls_remain_accessible(self):
        self.load(4);w=self.w;w.track_tool.click();self.choose('side_by_side');QTest.qWait(60)
        self.assertLessEqual(w.width(),1280)
        self.assertTrue(w.add_object_button.isVisible());self.assertTrue(w.header_more.isVisible())
        actions=[action.text() for action in w.header_more.menu().actions()]
        self.assertIn('Edit selected object…',actions);self.assertIn('Path settings…',actions)
        for pane in w.workspace_panes:
            self.assertGreater(pane.graph.width(),100)
            self.assertTrue(pane.view_controls.plus.isVisible());self.assertTrue(pane.view_controls.move.isVisible())

    def test_transport_returns_to_floating_with_only_one_visible_pane(self):
        self.load(2);w=self.w
        self.assertIs(w.playback_card.parentWidget(),w.seek_bar)
        w.hide_cine_pane(1);QTest.qWait(40)
        self.assertIs(w.playback_card.parentWidget(),w.workspace_grid_widget)
        self.assertTrue(w.playback_card.isVisible())
        w.restore_cine_pane(1);QTest.qWait(40)
        self.assertIs(w.playback_card.parentWidget(),w.seek_bar)
        w.transport_toggle.click();self.assertTrue(w.playback_card.isHidden())
        w.transport_toggle.click();self.assertTrue(w.playback_card.isVisible())


if __name__=='__main__':unittest.main()
