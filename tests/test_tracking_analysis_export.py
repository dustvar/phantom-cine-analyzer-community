"""Actual MP4/Cine outputs, atomic cancellation and immutable measurement snapshots."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import cv2
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from cine_metadata import CineHandler
from test_cine_metadata import write_cine
from tracking_analysis_export import AnalysisOverlay, AnalysisPlot, export_tracking_analysis
from video_export import ExportCancelled
from viewer_processing import default_grade

SETTINGS=dict(image_brightness=0,image_contrast=1,image_gamma=1,toggle_ftone=False,raw_enabled=False)


def tracks():
    return {0:dict(name='Marker',color='#ff0000',frames=np.arange(8),
                   points=np.column_stack((10+np.arange(8)*8,np.full(8,20.))),heat_values=np.linspace(0,1,8)),
            1:dict(name='Disabled',color='#0000ff',enabled=False,frames=np.arange(8),
                   points=np.column_stack((np.full(8,80.),np.full(8,45.))))}


def graph():
    return dict(metric='Y-Displacement',mode='Displacement',x_unit='s',y_unit='mm',
                x_label='Time (s)',y_label='Y-Displacement (mm)',title='Y-Displacement (mm) vs Time (s)',
                xlim=(0,.0035),ylim=(-1,8),time_reference='start',time_scale=1,
                series=[dict(cine_index=0,object_id=0,name='Marker',color='#ff0000',
                             x=np.arange(8)*.0005,y=np.arange(8,dtype=float),frames=np.arange(8))])


class AnalysisExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source.cine'
        write_cine(self.source,frames=8,width=96,height=64)
        self.before=self.source.read_bytes()

    def export(self,path,**changes):
        options=dict(source=self.source,destination=path,first=2,last=5,fps=24,settings=SETTINGS,
                     grade=default_grade(),tracks=tracks(),style={'names':False})
        options.update(changes);return export_tracking_analysis(**options)

    def test_rendered_cine_has_exact_range_rotation_and_graphics_without_mutating_source(self):
        out=self.root/'analysis.cine';progress=[]
        self.export(out,rotation=90,style={'names':False,'radius_scale':4},progress=lambda n,total:progress.append((n,total)))
        reader=CineHandler();reader.load_cine(str(out));self.addCleanup(reader.close)
        self.assertTrue(reader.metadata.PcaRendered)
        self.assertEqual((reader.metadata.ImageCount,reader.metadata.ImWidth,reader.metadata.ImHeight),(4,64,96))
        self.assertEqual(reader.metadata.FrameRate,24)
        image=reader.get_img(0)
        np.testing.assert_array_equal(image[0,0],(60,60,60))
        # Original point (10,20) becomes (43,10) after a clockwise turn.
        # It remains the low end of the green-red heat map, even in frame 2.
        self.assertGreater(int(image[10,43,1]),int(image[10,43,0])+100)
        # The disabled blue object (80,45) would become (18,80).
        np.testing.assert_array_equal(image[80,18],(60,60,60))
        self.assertEqual(progress,[(1,4),(2,4),(3,4),(4,4)])
        self.assertEqual(self.source.read_bytes(),self.before)

    def test_silent_mp4_decodes_with_graph_and_requested_playback_rate(self):
        out=self.root/'analysis.mp4';g=graph();before=g['series'][0]['y'].copy()
        self.export(out,graph=g)
        capture=cv2.VideoCapture(str(out));self.assertTrue(capture.isOpened());self.addCleanup(capture.release)
        self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_COUNT),4)
        self.assertAlmostEqual(capture.get(cv2.CAP_PROP_FPS),24)
        self.assertEqual((capture.get(cv2.CAP_PROP_FRAME_WIDTH),capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),(640,264))
        images=[]
        while True:
            ok,image=capture.read()
            if not ok:break
            images.append(image)
        self.assertEqual(len(images),4)
        # Grid/labels/curves make the graph nonuniform; changing capture time
        # moves its cursor even though the underlying measured curve is fixed.
        self.assertGreater(float(images[0][64:].std()),10)
        self.assertGreater(np.count_nonzero(images[0][64:]!=images[-1][64:]),100)
        np.testing.assert_array_equal(g['series'][0]['y'],before)
        self.assertEqual(self.source.read_bytes(),self.before)

    def test_cancellation_and_reader_failure_preserve_destination_and_remove_partials(self):
        for ext in ('.mp4','.cine'):
            out=self.root/('cancel'+ext);out.write_bytes(b'original destination')
            event=threading.Event()
            with self.assertRaises(ExportCancelled):
                self.export(out,first=0,last=7,cancel=event,progress=lambda n,total:event.set())
            self.assertEqual(out.read_bytes(),b'original destination')
            self.assertEqual(list(self.root.glob('.pca-*')),[])
        class BrokenReader(CineHandler):
            def get_img(self,index=None):
                if index==3:raise OSError('Simulated source disconnect')
                return super().get_img(index)
        out=self.root/'broken.cine';out.write_bytes(b'original destination')
        with self.assertRaisesRegex(OSError,'disconnect'):self.export(out,handler_factory=BrokenReader)
        self.assertEqual(out.read_bytes(),b'original destination')
        self.assertEqual(list(self.root.glob('.pca-*')),[])

    def test_invalid_ranges_and_original_destination_are_rejected(self):
        out=self.root/'invalid.mp4';out.write_bytes(b'existing')
        for bounds in [(-1,3),(3,2),(0,8)]:
            with self.assertRaises(ValueError):self.export(out,first=bounds[0],last=bounds[1])
        with self.assertRaisesRegex(ValueError,'source Cine'):self.export(self.source)
        with self.assertRaisesRegex(ValueError,'Enable at least'):self.export(out,tracks={})
        self.assertEqual(out.read_bytes(),b'existing');self.assertEqual(self.source.read_bytes(),self.before)

    def test_heatmap_and_fade_combine_and_missing_points_do_not_get_connected(self):
        data={0:dict(name='Dot',color='#ff0000',frames=np.array([0,2]),
                     points=np.array([[15.,20.],[65.,20.]]),heat_values=np.array([0.,1.]))}
        style=dict(names=False,fade_enabled=True,fade_radius=0,transparency=75,radius_scale=5)
        overlay=AnalysisOverlay(data,style,(96,64),(96,64))
        base=np.zeros((64,96,3),np.uint8)
        image=overlay.render(base,0)
        self.assertGreater(int(image[20,15,1]),0)
        self.assertLessEqual(int(image[20,15,1]),65)
        self.assertEqual(int(image[20,15,0]),0)
        np.testing.assert_array_equal(image[20,40],(0,0,0))
        np.testing.assert_array_equal(data[0]['points'],[[15,20],[65,20]])

    def test_plot_cursor_uses_snapshot_units_and_clears_missing_value_guide(self):
        g=graph();g['series'][0]['frames']=np.array([0,1,2,4,5,6,7,8])
        plot=AnalysisPlot(g,640,220)
        first=plot.render(2,.001,0,0);missing=plot.render(3,.0015,0,0)
        self.assertEqual(plot.axes.get_ylabel(),'Y-Displacement (mm)')
        self.assertEqual(plot.axes.get_ylim(),(-1,8))
        self.assertGreater(np.count_nonzero(first!=missing),100)
        again=plot.render(2,.001,0,0)
        np.testing.assert_array_equal(first,again)


if __name__=='__main__':unittest.main()

# UI integration uses the same real Cine/VM fixture as guided tracking.
import test_guided_workspace as workspace
from PySide6.QtCore import QEventLoop, QTimer, QRect, QPoint
from PySide6.QtWidgets import QDialog, QMessageBox
from tracking_analysis_export_ui import TrackingAnalysisDialog
from workspace_job import read_job, save_job


class AnalysisExportUITests(unittest.TestCase):
    setUpClass=classmethod(workspace.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(workspace.GuidedWorkspaceTests.tearDownClass.__func__)
    setUp=workspace.GuidedWorkspaceTests.setUp
    tearDown=workspace.GuidedWorkspaceTests.tearDown
    wait_worker=workspace.GuidedWorkspaceTests.wait_worker

    def test_export_dialog_large_text_keeps_footer_on_short_screen(self):
        self.w.resize(1280,720)
        dialog=TrackingAnalysisDialog(self.w,self.w.vm.cine_handler.metadata,0,7,30)
        dialog.setStyleSheet('QWidget { font-size: 24px; }')
        dialog.contents.setCurrentIndex(1)
        dialog.format.setCurrentIndex(1)
        available=QRect(0,0,1280,720)
        with patch.object(dialog,'_available_rect',return_value=available):
            dialog.show();self.app.processEvents()
            self.assertLessEqual(dialog.height(),656)
            self.assertLessEqual(dialog.width(),1248)
            footer=dialog.choose_file
            self.assertTrue(footer.isVisible())
            self.assertTrue(dialog.rect().contains(footer.mapTo(dialog,footer.rect().bottomRight())))
            self.assertTrue(available.contains(footer.mapToGlobal(footer.rect().bottomRight())))
            self.assertEqual(dialog.scroll.horizontalScrollBar().maximum(),0)
            vertical=dialog.scroll.verticalScrollBar()
            self.assertGreater(vertical.maximum(),0)
            vertical.setValue(vertical.maximum());self.app.processEvents()
            # The final explanatory paragraph can be reached while Save stays
            # fixed below the scrolling viewport.
            bottom=dialog.graph_note.mapTo(dialog.scroll.viewport(),dialog.graph_note.rect().bottomRight())
            self.assertLessEqual(bottom.y(),dialog.scroll.viewport().height())
            self.assertTrue(footer.isVisible())
            self.assertTrue(dialog.rect().contains(footer.mapTo(dialog,footer.rect().bottomRight())))
        dialog.hide()

    def test_export_dialog_invalid_range_disables_save_without_estimating_size(self):
        dialog=TrackingAnalysisDialog(self.w,self.w.vm.cine_handler.metadata,0,7,30)
        origin=self.w.vm.cine_handler.metadata.FirstImageNo
        with patch('tracking_analysis_export_ui.rendered_size',side_effect=AssertionError('Invalid range reached size estimate')):
            dialog.first.setValue(origin+20)
            dialog.last.setValue(origin+10)
            for extension in ('.mp4','.cine'):
                dialog.format.setCurrentIndex(dialog.format.findData(extension))
                dialog.refresh()
                self.assertFalse(dialog.choose_file.isEnabled())
                self.assertIn('at or after',dialog.duration.text())
                with self.assertRaisesRegex(ValueError,'at or after'):dialog.options()
                dialog.accept()
                self.assertNotEqual(dialog.result(),QDialog.Accepted)
        for extension in ('.mp4','.cine'):
            dialog.format.setCurrentIndex(dialog.format.findData(extension))
            dialog.last.setValue(origin+22)
            dialog.refresh()
            self.assertTrue(dialog.choose_file.isEnabled())
            self.assertIn('3 frames',dialog.duration.text())
            self.assertEqual(dialog.options()['first'],20)
            self.assertEqual(dialog.options()['last'],22)

    def add_track(self):
        w=self.w;vm=w.vm
        w.track_tool.click();vm.track_type='Manual';vm._update_track_type()
        vm.active_tool.track_select=True
        for frame in range(8):vm.active_tool.collect_points((150+frame,120),frame)
        vm.image_scale=.25;vm.length_units='in';vm.active_frame=2;vm.track_fig_calculations()
        w._graph_length_unit='cm';w.fig_dropdown.setCurrentText('X-Displacement')
        w.track_canvas.redraw(vm.track_data)
        return vm.track_data[0]

    def test_save_action_exports_graph_in_chosen_units_from_worker(self):
        w=self.w;w.save_job_menu.aboutToShow.emit();self.assertFalse(w.export_tracking_action.isEnabled())
        self.add_track();w.save_job_menu.aboutToShow.emit();self.assertTrue(w.export_tracking_action.isEnabled())
        out=self.root/'analysis.cine';captured={}
        def accept(dialog):
            dialog.contents.setCurrentIndex(1);dialog.format.setCurrentIndex(1)
            dialog.last.setValue(dialog.first.value()+3)
            captured.update(dialog.options());return QDialog.Accepted
        with patch.object(TrackingAnalysisDialog,'exec',accept),patch('tracking_analysis_export_ui.QFileDialog.getSaveFileName',return_value=(str(out),'')),patch.object(QMessageBox,'exec',return_value=QMessageBox.Ok):
            w.export_tracking_action.trigger()
            self.assertIsNotNone(w.export_job);job=w.export_job
            snapshot=job.options['graph']
            self.assertEqual(snapshot['y_unit'],'cm')
            self.assertAlmostEqual(snapshot['series'][0]['y'][1],.635)
            loop=QEventLoop();timer=QTimer();timer.setInterval(10)
            timer.timeout.connect(lambda:loop.quit() if w.export_job is None else None)
            timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
            timer.start();timeout.start(10000);loop.exec();timer.stop();timeout.stop()
            self.assertIsNone(w.export_job)
        reader=CineHandler();reader.load_cine(str(out));self.addCleanup(reader.close)
        self.assertEqual(reader.metadata.ImageCount,4)
        self.assertGreater(reader.metadata.ImHeight,240)
        self.assertEqual(captured['first'],0);self.assertEqual(captured['last'],3)
        self.assertIn('Video exported:',w.status_bar.text())

    def test_graph_units_are_saved_restored_and_validated_without_recalibration(self):
        self.add_track();w=self.w
        path=self.root/'units.pcajob';w.save_job_path(path)
        self.assertEqual(read_job(path)['view']['graph_length_unit'],'cm')
        w._graph_length_unit='mm';w.open_job_path(path)
        self.assertEqual(w._graph_length_unit,'cm');self.assertEqual(w.vm.length_units,'in')
        self.assertEqual(w.vm.image_scale,.25)
        payload=read_job(path);payload['view']['graph_length_unit']=['invalid']
        bad=self.root/'bad.pcajob';save_job(bad,payload)
        with self.assertRaisesRegex(ValueError,'graph length unit'):w.open_job_path(bad)
        self.assertEqual(w.vm.length_units,'in')
        payload=read_job(path);payload['view'].pop('graph_length_unit')
        old=self.root/'older.pcajob';save_job(old,payload);w.open_job_path(old)
        self.assertIsNone(w._graph_length_unit)
