"""Independent old/new scene pixels and plot marker correctness."""
import io,os,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from PySide6.QtCore import Qt,QRectF,QPointF
from PySide6.QtGui import QColor,QPen,QBrush,QImage,QPainter,QTransform
from PySide6.QtWidgets import QApplication,QGraphicsScene,QWidget,QComboBox,QVBoxLayout
from tracking_render import add_faded_path,fade_alphas
from simplemeas_ui import TrackingGraph,MainWindow

class TrackingRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    @staticmethod
    def pixels(scene,scale=1,angle=0):
        image=QImage(320,240,QImage.Format_ARGB32);image.fill(QColor('#202020'))
        p=QPainter(image);p.translate(160,120);p.rotate(angle);p.scale(scale,scale);p.translate(-160,-120)
        scene.render(p,QRectF(0,0,320,240),QRectF(0,0,320,240));p.end()
        return np.array(image.bits()).reshape(240,320,4).copy()
    def test_faded_path_matches_individual_scene_items_at_zoom_and_rotation(self):
        points=np.column_stack((np.linspace(20,290,45),100+35*np.sin(np.arange(45)/3)))
        alphas=np.asarray([70 if i%3 else 255 for i in range(45)],np.uint8)
        for heat in (None,np.linspace(0,1,len(points))):
            legacy=QGraphicsScene();new=QGraphicsScene();graph=SimpleNamespace(scene=lambda:new)
            for i,point in enumerate(points):
                color=QColor('#ee44aa') if heat is None else MainWindow._heat_map_color(heat[i])
                color.setAlpha(int(alphas[i]));pen=QPen(color);pen.setCosmetic(True)
                legacy.addEllipse(point[0]-4,point[1]-4,8,8,pen,QBrush(color))
                if i:
                    c=QColor('#ee44aa');c.setAlpha(int(min(alphas[i-1],alphas[i])));pen=QPen(c,1);pen.setCosmetic(True)
                    legacy.addLine(*points[i-1],*point,pen)
            add_faded_path(graph,points,4,'#ee44aa',alphas,heat,MainWindow._heat_map_color)
            self.assertEqual(len(new.items()),1)
            for scale,angle in ((1,0),(1.75,0),(.6,90)):
                with self.subTest(heat=heat is not None,scale=scale,angle=angle):
                    np.testing.assert_array_equal(self.pixels(new,scale,angle),self.pixels(legacy,scale,angle))
            item=new.items()[0]
            self.assertEqual(item.acceptedMouseButtons(),Qt.NoButton)
            editable=new.addRect(80,80,80,60);editable.setZValue(-1)
            self.assertIs(new.itemAt(QPointF(100,100),QTransform()),editable)
            new.clear()
            add_faded_path(graph,points,4,'#ee44aa',alphas,heat,MainWindow._heat_map_color)
            self.assertEqual(len(graph._recorded_track_paths),1)
            new.removeItem(graph._recorded_track_paths[0])
            add_faded_path(graph,points,4,'#ee44aa',alphas,heat,MainWindow._heat_map_color)
            self.assertEqual(len(graph._recorded_track_paths),1)
    def test_vector_fade_preserves_distance_and_opacity(self):
        points=np.array([[0.,0.],[3.,4.],[5.001,0.],[80,90]])
        centers=np.array([[0.,0.],[100.,100.]])
        result=fade_alphas(points,centers,5,70)
        expected=[77 if np.min(np.linalg.norm(centers-p,axis=1))<=5 else 255 for p in points]
        np.testing.assert_array_equal(result,expected)
    def test_marker_blit_matches_full_redraw_after_seek_and_resize(self):
        host=QWidget();layout=QVBoxLayout(host);host.fig_dropdown=QComboBox();host.fig_dropdown.addItems(['Displacement','Speed'])
        t=dict(name='Tracked',enabled=True,color='#ff0000',frames=np.arange(20),points=np.zeros((20,2)),frame_ts=np.arange(20)*.001,Displacement=np.sin(np.arange(20)),Speed=np.ones(19),notes={})
        host.vm=SimpleNamespace(track_data={0:t},active_object=0,active_frame=2,n_diff=1,length_units='pix',time_units='s',workspace_contexts=[],cal=SimpleNamespace(point_transform=lambda a:a))
        graph=TrackingGraph(host);layout.addWidget(graph);host.resize(700,350);host.show();self.app.processEvents()
        try:
            graph.redraw({0:t});self.app.processEvents()
            for size,metric,frame in (((700,350),'Displacement',10),((850,420),'Speed',7)):
                host.resize(*size);host.fig_dropdown.setCurrentText(metric);graph.redraw({0:t});self.app.processEvents()
                host.vm.active_frame=frame
                with patch.object(graph,'draw_idle',wraps=graph.draw_idle) as draw:
                    graph.draw_frame_pos();self.assertEqual(draw.call_count,0)
                blitted=np.asarray(graph.buffer_rgba()).copy();graph.draw()
                np.testing.assert_array_equal(np.asarray(graph.buffer_rgba()),blitted)
                self.assertEqual(tuple(graph.frame_pos_line.get_xdata()),(frame*.001,frame*.001))
            graph.ax.set_xlim(.002,.015)
            self.assertIsNone(graph._plot_background)
            graph.draw_frame_pos();self.app.processEvents()
            after_limits=np.asarray(graph.buffer_rgba()).copy();graph.draw()
            np.testing.assert_array_equal(np.asarray(graph.buffer_rgba()),after_limits)
            graph.fig.savefig(io.BytesIO(),format='png',dpi=180)
            graph.draw_frame_pos();self.app.processEvents()
            after_save=np.asarray(graph.buffer_rgba()).copy();graph.draw()
            np.testing.assert_array_equal(np.asarray(graph.buffer_rgba()),after_save)
            graph.hide();self.app.processEvents()
            with patch.object(graph,'redraw',wraps=graph.redraw) as draw:
                graph.request_redraw({})
                graph.request_redraw({0:t})
                self.assertEqual(draw.call_count,0)
                graph.show();self.app.processEvents()
                self.assertEqual(draw.call_count,1)
                self.assertIsNone(graph._pending_plot_data)
            graph.hide();host.vm.active_frame=12;graph.draw_frame_pos();graph.show();self.app.processEvents()
            self.assertEqual(tuple(graph.frame_pos_line.get_xdata()),(.012,.012))
            host.resize(600,170)
            graph.redraw({i:dict(t,name=f'Object {i}') for i in range(3)});self.app.processEvents()
            renderer=graph.get_renderer();legend=graph.ax.get_legend()
            rows=[text.get_window_extent(renderer).y0 for text in legend.get_texts()]
            self.assertLess(max(rows)-min(rows),1)
            self.assertGreaterEqual(graph.height(),140)
            self.assertGreater(graph.ax.bbox.height/graph.device_pixel_ratio,75)
            host.vm.active_frame=999;graph.draw_frame_pos();self.assertIsNone(graph.frame_pos_line)
            cleared=np.asarray(graph.buffer_rgba()).copy();graph.draw();np.testing.assert_array_equal(np.asarray(graph.buffer_rgba()),cleared)
        finally:
            host.close();host.deleteLater();self.app.processEvents()

class PlotWorkspaceBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_guided_workspace import GuidedWorkspaceTests
        cls.fixture=GuidedWorkspaceTests
        cls.fixture.setUpClass()
    @classmethod
    def tearDownClass(cls):cls.fixture.tearDownClass()
    def test_full_plot_panel_reserves_separate_control_rows(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        case=self.fixture('test_video_first_layout_and_side_only_inspector');case.setUp()
        try:
            w=case.w;w.resize(1280,720);w.track_tool.click();w._set_graphs_open(True)
            w.details_buttons[2].click();QTest.qWait(40)
            def bounds(widget):
                top=widget.mapTo(w.status_toolbar,QPoint()).y()
                return top,top+widget.height()
            for enlarged in (False,True):
                if enlarged:
                    w.motion_filter.setStyleSheet('font-size: 20px;')
                    w.motion_window.setStyleSheet('font-size: 20px;');QTest.qWait(40)
                canvas=bounds(w.track_canvas);selector=bounds(w.fig_dropdown)
                derivative=bounds(w.motion_filter);hint=bounds(w.motion_hint)
                self.assertLessEqual(canvas[1],selector[0])
                self.assertLessEqual(selector[1],derivative[0])
                self.assertLessEqual(derivative[1],hint[0])
                self.assertLessEqual(hint[1],w.status_toolbar.height())
        finally:case.tearDown()

if __name__=='__main__':unittest.main()
