"""Save-menu workflow for exporting a measured Cine with permanent graphics."""
from copy import deepcopy
import os
from pathlib import Path
import threading

import numpy as np
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QLabel,
                               QLayout, QMessageBox, QProgressDialog, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)
from cine_render import rendered_size
from tracking_analysis_export import export_tracking_analysis
from video_export import ExportCancelled


def analysis_export_available(owner):
    vm = getattr(owner, 'vm', None)
    return bool(vm is not None and vm.active_cine_index >= 0
                and getattr(owner, 'export_job', None) is None
                and not getattr(owner, '_guide', None)
                and not owner._job_work_busy()
                and any(t.get('enabled', True) and len(t.get('frames', [])) for t in vm.track_data.values()))


class TrackingAnalysisDialog(QDialog):
    def __init__(self, parent, metadata, first, last, fps, rotation=0):
        super().__init__(parent)
        self.setWindowTitle('Export Tracking Analysis')
        self.setMinimumWidth(460)
        self.metadata = metadata;self.rotation = rotation
        outer = QVBoxLayout(self)
        self.content=QWidget(self)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0,0,8,0)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.scroll=QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll,1)
        intro = QLabel('Export the selected Cine with its enabled object paths, heat map and transparency.');intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout();form.setRowWrapPolicy(QFormLayout.WrapLongRows);layout.addLayout(form)
        self.contents = QComboBox()
        self.contents.addItem('Video + tracking graphics', False)
        self.contents.addItem('Video + tracking graphics + graph', True)
        form.addRow('Include', self.contents)
        self.format = QComboBox()
        self.format.addItem('MP4 · H.264 (recommended)', '.mp4')
        self.format.addItem('Rendered Cine · graphics baked in', '.cine')
        form.addRow('File format', self.format)
        low = int(metadata.FirstImageNo);high = low+int(metadata.ImageCount)-1
        self.first = QSpinBox();self.last = QSpinBox()
        for spin in (self.first,self.last):spin.setRange(low,high);spin.setKeyboardTracking(False)
        self.first.setValue(low+first);self.last.setValue(low+last)
        form.addRow('First source frame',self.first);form.addRow('Last source frame',self.last)
        self.fps=QDoubleSpinBox();self.fps.setRange(1,120);self.fps.setDecimals(2);self.fps.setSuffix(' fps');self.fps.setValue(float(np.clip(fps,1,120)))
        form.addRow('Playback rate',self.fps)
        self.size=QComboBox()
        for label,height in [('Original video size',None),('Video up to 1080 px high',1080),('Video up to 720 px high',720)]:self.size.addItem(label,height)
        form.addRow('Video size',self.size)
        self.names=QCheckBox('Show object names');self.names.setToolTip('Place each name beside its current tracked point.');self.names.setChecked(True);form.addRow(self.names)
        self.quality=QComboBox();self.quality.addItems(['High','Balanced','Small']);form.addRow('Movie quality',self.quality)
        self.duration=QLabel();self.duration.setWordWrap(True);layout.addWidget(self.duration)
        self.note=QLabel();self.note.setWordWrap(True);layout.addWidget(self.note)
        source_note=QLabel('Each selected source frame appears once. Rotation and image adjustments are included. Timeline speed edits and text are exported separately through Viewer → Export Clip.');source_note.setWordWrap(True)
        layout.addWidget(source_note)
        self.graph_note=QLabel('The graph is placed below the footage and uses the current metric, units, objects and zoom. Output is at least 640 px wide to keep labels readable.');self.graph_note.setWordWrap(True);layout.addWidget(self.graph_note)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        self.footer=buttons
        self.choose_file=buttons.button(QDialogButtonBox.Save)
        self.choose_file.setText('Choose File…')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);outer.addWidget(buttons)
        for widget in (self.format,self.size,self.contents):widget.currentIndexChanged.connect(self.refresh)
        for widget in (self.first,self.last,self.fps):widget.valueChanged.connect(self.refresh)
        self.refresh()

    def _available_rect(self):
        screen=self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry()

    def showEvent(self,event):
        # Keep the action row outside the scrolling content, including on
        # short laptop displays or when system text is enlarged.
        rect=self._available_rect().adjusted(16,32,-16,-32)
        parent=self.parentWidget()
        width_limit=min(rect.width(),parent.width()) if parent is not None else rect.width()
        height_limit=min(rect.height(),parent.height()) if parent is not None else rect.height()
        self.setMinimumSize(min(460,width_limit),min(260,height_limit))
        self.setMaximumSize(width_limit,height_limit)
        width=min(width_limit,max(460,min(720,self.content.sizeHint().width()+32),self.footer.sizeHint().width()+24))
        content_height=self.content.layout().totalHeightForWidth(max(1,width-32))
        height=min(height_limit,max(260,content_height+self.footer.sizeHint().height()+36))
        self.resize(width,height)
        center=parent.frameGeometry().center() if parent is not None else rect.center()
        self.move(max(rect.left(),min(center.x()-width//2,rect.right()-width+1)),
                  max(rect.top(),min(center.y()-height//2,rect.bottom()-height+1)))
        self._dialog_bounds=rect
        super().showEvent(event)
        QTimer.singleShot(0,self,self._expand_for_content)

    def _expand_for_content(self):
        # Word-wrapped labels settle after the scroll viewport gets its real
        # width. Use spare screen height before introducing a scroll bar.
        if not self.isVisible():return
        growth=min(self.scroll.verticalScrollBar().maximum(),self.maximumHeight()-self.height())
        if growth>0:
            self.resize(self.width(),self.height()+growth)
            bounds=self._dialog_bounds
            if self.y()+self.height()>bounds.bottom()+1:
                self.move(self.x(),max(bounds.top(),bounds.bottom()-self.height()+1))

    def refresh(self):
        count=self.last.value()-self.first.value()+1
        rendered=self.format.currentData()=='.cine';self.quality.setEnabled(not rendered)
        self.graph_note.setVisible(bool(self.contents.currentData()))
        if self.isVisible():QTimer.singleShot(0,self,self._expand_for_content)
        self.choose_file.setEnabled(count>0)
        if count<=0:
            self.duration.setText('Choose a last frame that is at or after the first frame.')
            self.note.setText('Adjust either frame field to continue. No file will be written until the range is valid.')
            return
        self.duration.setText(f'{count:,} frames · {count/self.fps.value():,.2f} seconds at {self.fps.value():g} fps')
        if rendered:
            width,height=self.metadata.ImWidth,self.metadata.ImHeight
            if self.rotation in (90,270):width,height=height,width
            maximum=self.size.currentData()
            if maximum and height>maximum:width,height=max(2,round(width*maximum/height)),maximum
            if self.contents.currentData():width,height=max(640,width),height+max(200,min(360,round(height*.3)))
            self.duration.setText(self.duration.text()+f' · approximately {rendered_size(width,height,count)/1e6:,.1f} MB')
            self.note.setText('Rendered Cine stores uncompressed RGB display pixels with the graphics permanently included. It is not raw camera data; output frame numbers and timing start at zero.')
        else:
            self.note.setText('MP4 is a standard, shareable video format and does not require audio. This export is silent. The original Cine and tracking job remain unchanged.')

    def options(self):
        if self.first.value()>self.last.value():
            raise ValueError('The last frame must be at or after the first frame.')
        return dict(first=self.first.value()-int(self.metadata.FirstImageNo),
                    last=self.last.value()-int(self.metadata.FirstImageNo),
                    fps=self.fps.value(),max_height=self.size.currentData(),quality=self.quality.currentText())

    def accept(self):
        self.refresh()
        if self.choose_file.isEnabled():
            super().accept()


class TrackingAnalysisJob(QThread):
    progress=Signal(int,int)
    def __init__(self,parent,**options):
        super().__init__(parent);self.options=options;self.cancel_event=threading.Event()
        self.result=None;self.error=None;self.cancelled=False
    def run(self):
        try:self.result=export_tracking_analysis(**self.options,cancel=self.cancel_event,progress=self.progress.emit)
        except ExportCancelled:self.cancelled=True
        except Exception as error:self.error=str(error)


def _snapshot_tracks(owner):
    tracks={}
    for object_id,track in owner.vm.track_data.items():
        if not track.get('enabled',True) or not len(track.get('frames',[])):continue
        tracks[object_id]={key:deepcopy(track[key]) for key in ('frames','points','name','color') if key in track}
        heat=owner._heat_map_values_for_track(object_id,len(track['points']))
        tracks[object_id]['heat_values']=None if heat is None else np.array(heat,copy=True)
    return tracks


def open_tracking_analysis_export(owner):
    if not analysis_export_available(owner):return
    owner._stop_playback()
    vm=owner.vm;metadata=vm.cine_handler.metadata
    frames=np.concatenate([np.asarray(t['frames']) for t in vm.track_data.values() if t.get('enabled',True) and len(t.get('frames',[]))])
    first,last=max(0,int(frames.min())),min(metadata.ImageCount-1,int(frames.max()))
    rotation=int(vm.viewer_edit.get('rotation',0))
    dialog=TrackingAnalysisDialog(owner,metadata,first,last,getattr(owner,'_analysis_fps',30),rotation)
    if dialog.exec()!=QDialog.Accepted:dialog.deleteLater();return
    options=dialog.options();extension=dialog.format.currentData()
    with_graph=bool(dialog.contents.currentData());names=dialog.names.isChecked();dialog.deleteLater()
    destination,_=QFileDialog.getSaveFileName(owner,'Export Tracking Analysis',
        str(Path.home()/'Desktop'/(Path(vm.cine_path).stem+'-analysis'+extension)),f'{extension[1:].upper()} video (*{extension})')
    if not destination:return
    if not destination.lower().endswith(extension):
        destination+=extension
        if os.path.exists(destination) and QMessageBox.question(owner,'Replace video?',f'Replace {Path(destination).name}?',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
    sources=[c['cine_path'] for c in vm.workspace_contexts]
    for context in vm.workspace_contexts:
        sources.extend(c['source_path'] for c in context.get('viewer_edit',{}).get('copies',[]) if c.get('source_path'))
    dest=Path(destination)
    if any(Path(source).resolve()==dest.resolve() or (dest.exists() and Path(source).exists() and Path(source).samefile(dest)) for source in sources):
        QMessageBox.warning(owner,'Choose another file','The destination is a source video in this workspace.');return
    try:
        graph=None
        if with_graph:
            owner.track_canvas.redraw(vm.track_data)
            graph=owner.track_canvas.graph_export_snapshot()
        options.update(source=vm.cine_path,destination=destination,settings=deepcopy(vm.image_settings_snapshot()),
                       grade=deepcopy(vm.viewer_grade),tracks=_snapshot_tracks(owner),rotation=rotation,graph=graph,
                       cine_index=vm.active_cine_index,object_id=vm.active_object,
                       style=dict(fade_enabled=owner._path_fade_enabled,fade_radius=owner._path_fade_radius,
                                  transparency=owner._path_fade_transparency,radius_scale=owner._path_point_radius_scale,names=names))
        owner.export_job=TrackingAnalysisJob(owner,**options)
    except Exception as error:
        QMessageBox.warning(owner,'Export Tracking Analysis',str(error));return
    owner.export_button.setEnabled(False)
    owner.export_progress=QProgressDialog('Rendering tracking analysis…','Cancel',0,options['last']-options['first']+1,owner)
    owner.export_progress.setWindowTitle('Export Tracking Analysis')
    owner.export_progress.setWindowModality(Qt.WindowModal);owner.export_progress.setAutoClose(False);owner.export_progress.setAutoReset(False)
    owner.export_progress.canceled.connect(owner.export_job.cancel_event.set)
    owner.export_job.progress.connect(owner._on_export_progress)
    owner.export_job.finished.connect(owner._on_export_finished)
    owner.export_progress.show();owner.export_job.start()
