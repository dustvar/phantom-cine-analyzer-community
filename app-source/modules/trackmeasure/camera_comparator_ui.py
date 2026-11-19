"""Native Camera Comparator workspace, isolated from editor and tracking state."""
from copy import deepcopy
from pathlib import Path
import math
import threading
import time

import numpy as np
from PySide6.QtCore import Qt, QThread, QTimer, Signal, QPointF, QRectF
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QPushButton,QComboBox,QDoubleSpinBox,QSpinBox,QSlider,QScrollArea,QSplitter,
    QFileDialog,QMessageBox,QGroupBox,QProgressBar,QCheckBox,QSizePolicy)
from camera_comparator import (PROFILES,available_modes,default_settings,
    save_comparison,load_comparison,display_frame,capture_interval)
from camera_comparator_media import ComparisonSession,export_comparison
from video_export import ExportCancelled


class ImageView(QWidget):
    point_clicked=Signal(float,float)
    def __init__(self,parent=None):
        super().__init__(parent)
        self.image=None;self.message='Choose source footage';self.anchor=None
        self.setMinimumSize(140,130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)

    def set_frame(self,frame):
        array=np.ascontiguousarray(frame)
        self.image=QImage(array.data,array.shape[1],array.shape[0],array.strides[0],QImage.Format.Format_RGB888).copy()
        self.message='';self.update()

    def image_rect(self):
        if self.image is None: return QRectF()
        size=self.image.size().scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width()-size.width())/2,(self.height()-size.height())/2,size.width(),size.height())

    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#151515'))
        if self.image is None:
            p.setPen(QColor('#aaaaaa'));p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,self.message);return
        p.drawImage(self.image_rect(),self.image)
        if self.anchor is not None:
            r=self.image_rect();x=r.x()+self.anchor[0]*r.width()/self.image.width();y=r.y()+self.anchor[1]*r.height()/self.image.height()
            p.setPen(QPen(QColor('#ffdd55'),2));p.drawEllipse(QPointF(x,y),5,5)

    def mousePressEvent(self,event):
        r=self.image_rect()
        if self.image is not None and r.contains(event.position()):
            self.point_clicked.emit((event.position().x()-r.x())*self.image.width()/r.width(),(event.position().y()-r.y())*self.image.height()/r.height())


class LoadJob(QThread):
    def __init__(self,parent,path):
        super().__init__(parent);self.path=path;self.result=None;self.error=None
    def run(self):
        try:
            self.result=ComparisonSession(self.path)
            self.result.frame(0)  # Decode/validate initial pixels off the UI thread.
        except Exception as error:
            if self.result is not None:self.result.close();self.result=None
            self.error=str(error)


class RenderJob(QThread):
    def __init__(self,parent,comparison,index,generation):
        super().__init__(parent);self.comparison=comparison;self.index=index;self.generation=generation;self.result=None;self.error=None
    def run(self):
        try: self.result=self.comparison.render(self.index)
        except Exception as error: self.error=str(error)


class ComparatorExportJob(QThread):
    progress=Signal(int,int)
    def __init__(self,parent,source,destination,settings,layout):
        super().__init__(parent);self.args=(source,destination,deepcopy(settings),layout)
        self.cancel_event=threading.Event();self.result=None;self.error=None;self.cancelled=False
    def run(self):
        try: self.result=export_comparison(*self.args,cancel=self.cancel_event,progress=self.progress.emit)
        except ExportCancelled: self.cancelled=True
        except Exception as error: self.error=str(error)


class CameraComparatorPanel(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('camera_comparator')
        self.session=None;self.comparison=None;self.load_job=None;self.render_job=None;self.export_job=None
        self.pending_path=None;self.pending_render=None;self.generation=0;self.updating=False;self.closing=False
        self.saved_states={};self.calibrations={};self.pending_preset=None
        self.timer=QTimer(self);self.timer.setInterval(15);self.timer.timeout.connect(self._tick)
        self.debounce=QTimer(self);self.debounce.setSingleShot(True);self.debounce.setInterval(130);self.debounce.timeout.connect(self.rebuild)
        self._build_ui()

    @staticmethod
    def number(minimum,maximum,value=0,decimals=3):
        widget=QDoubleSpinBox();widget.setRange(minimum,maximum);widget.setDecimals(decimals);widget.setValue(value)
        widget.setKeyboardTracking(False);return widget

    def _build_ui(self):
        root=QVBoxLayout(self)
        title=QLabel('Camera Comparator');title.setStyleSheet('font-size: 20px; font-weight: bold;')
        root.addWidget(title)
        bar=QHBoxLayout();self.sources=QComboBox();self.sources.setMinimumWidth(150)
        self.sources.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.sources.currentIndexChanged.connect(self._source_selected)
        bar.addWidget(self.sources,1)
        for text,method in [('Open footage…',self.choose_source),('Save comparison…',self.save_preset),('Load comparison…',self.load_preset)]:
            button=QPushButton(text);button.clicked.connect(method);bar.addWidget(button)
        root.addLayout(bar)
        split=QSplitter(Qt.Orientation.Horizontal);root.addWidget(split,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setMinimumWidth(270);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls=QWidget();form=QVBoxLayout(controls);scroll.setWidget(controls);split.addWidget(scroll)
        self.source_summary=QLabel('Original footage only; editor effects are excluded.');self.source_summary.setWordWrap(True);self.source_summary.setMinimumWidth(0);self.source_summary.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);form.addWidget(self.source_summary)
        self.source_group=QGroupBox('Source metadata / assumptions');sf=QFormLayout(self.source_group)
        self.source_model=QComboBox();self.source_model.addItem('Custom / unknown','')
        for key,p in PROFILES.items(): self.source_model.addItem(p['name'],key)
        self.source_pitch=self.number(0,200,0);self.source_pitch.setSuffix(' µm')
        self.source_bin=QComboBox()
        for label,value in [('Choose source binning…',0),('Unbinned · 1×1',1),('Binned · 2×2',2)]: self.source_bin.addItem(label,value)
        self.source_fps=self.number(0,10_000_000,0);self.source_fps.setSuffix(' fps')
        self.exposure=self.number(0,100_000_000,0,4);self.exposure.setSpecialValueText('Unknown');self.exposure.setSuffix(' µs')
        self.source_x=self.number(-1000000,1000000,0);self.source_y=self.number(-1000000,1000000,0)
        self.source_x.setToolTip('Recorded image center relative to sensor center, in native sensor pixels. Zero assumes centered recording.')
        self.source_y.setToolTip(self.source_x.toolTip())
        for name,widget in [('Camera',self.source_model),('Native pixel pitch',self.source_pitch),('Acquisition FPS',self.source_fps),('Exposure',self.exposure),('Crop center X (native px)',self.source_x),('Crop center Y (native px)',self.source_y)]: sf.addRow(name,widget)
        sampling=QFormLayout();sampling.addRow('Source sampling',self.source_bin);form.addLayout(sampling)
        self.source_details=QPushButton('Source details / overrides ▾');self.source_details.setCheckable(True);self.source_details.setChecked(True)
        self.source_details.toggled.connect(self.source_group.setVisible);form.addWidget(self.source_details)
        form.addWidget(self.source_group)
        target_group=QGroupBox('Target camera');tf=QFormLayout(target_group)
        self.target_model=QComboBox()
        for key,p in PROFILES.items():self.target_model.addItem(p['name'],key)
        self.target_model.setCurrentIndex(self.target_model.findData('T3610'))
        self.variant=QComboBox();self.mode=QComboBox();self.mode.setMinimumContentsLength(12)
        self.target_fps=self.number(100,10_000_000,30000);self.target_fps.setSuffix(' fps')
        self.filter=QComboBox()
        for label,value in [('All sampling modes',0),('Unbinned',1),('2×2 binning',2)]: self.filter.addItem(label,value)
        self.profile_info=QPushButton('Specifications and sources…');self.profile_info.clicked.connect(self.show_profile)
        for name,w in [('Camera',self.target_model),('Installed option',self.variant),('Acquisition FPS',self.target_fps),('Sampling filter',self.filter),('Verified preset',self.mode)]:tf.addRow(name,w)
        hint=QLabel('Published presets only. Other camera-supported modes may exist.');hint.setWordWrap(True);tf.addRow(hint);tf.addRow(self.profile_info);form.addWidget(target_group)
        playback=QGroupBox('Timing and view');pf=QFormLayout(playback)
        self.playback_fps=self.number(1,120,30);self.playback_fps.setSuffix(' fps')
        self.playback_mode=QComboBox();self.playback_mode.addItem('Same playback FPS','same_fps');self.playback_mode.addItem('Synchronized event time','event_sync')
        self.phase=QSlider(Qt.Orientation.Horizontal);self.phase.setRange(0,999)
        self.phase_label=QLabel('0% of target interval')
        phase_box=QWidget();phase_layout=QVBoxLayout(phase_box);phase_layout.setContentsMargins(0,0,0,0);phase_layout.addWidget(self.phase);phase_layout.addWidget(self.phase_label)
        self.first=QSpinBox();self.last=QSpinBox()
        for w in (self.first,self.last):w.setRange(-2147483647,2147483647);w.setKeyboardTracking(False)
        self.aim_x=self.number(-32768,32768,0);self.aim_y=self.number(-32768,32768,0)
        self.gamma=self.number(.1,5,1,2)
        self.feature=self.number(0,32768,0,2);self.feature.setSpecialValueText('Not measured')
        self.measure=QPushButton('Measure feature on source');self.measure.setCheckable(True)
        self.measure.toggled.connect(lambda checked:self._clear_measure())
        for name,w in [('Playback FPS',self.playback_fps),('Comparison timing',self.playback_mode),('Sampling offset',phase_box),('First source frame',self.first),('Last source frame',self.last),('Re-aim X (source px)',self.aim_x),('Re-aim Y (source px)',self.aim_y),('Shared display gamma',self.gamma),('Feature width (source px)',self.feature)]:pf.addRow(name,w)
        pf.addRow(self.measure);form.addWidget(playback);form.addStretch(1)
        right=QWidget();rl=QVBoxLayout(right);split.addWidget(right);split.setStretchFactor(1,1);split.setSizes([330,950])
        previews=QSplitter(Qt.Orientation.Horizontal)
        self.left_view=ImageView();self.right_view=ImageView();self.left_info=QLabel('Original source');self.right_info=QLabel('Simulated target')
        for label,view in [(self.left_info,self.left_view),(self.right_info,self.right_view)]:
            box=QWidget();v=QVBoxLayout(box);v.setContentsMargins(0,0,0,0);label.setWordWrap(True);v.addWidget(label);v.addWidget(view,1);previews.addWidget(box)
        self.left_view.point_clicked.connect(self._measure_point)
        rl.addWidget(previews,1)
        self.readout=QLabel();self.readout.setWordWrap(True);self.readout.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);rl.addWidget(self.readout)
        transport=QHBoxLayout();self.play=QPushButton('Play');self.play.clicked.connect(self.toggle_play)
        self.back=QPushButton('◀');self.forward=QPushButton('▶');self.back.clicked.connect(lambda:self._step(-1));self.forward.clicked.connect(lambda:self._step(1))
        self.slider=QSlider(Qt.Orientation.Horizontal);self.slider.setRange(0,0);self.slider.valueChanged.connect(self.request_render);self.slider.sliderPressed.connect(self.pause)
        for w in (self.play,self.back,self.slider,self.forward):transport.addWidget(w,1 if w==self.slider else 0)
        rl.addLayout(transport)
        self.summary=QLabel();self.summary.setWordWrap(True);rl.addWidget(self.summary)
        self.notices=QLabel('Choose a Cine, confirm source sampling, then select a target.');self.notices.setWordWrap(True);self.notices.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        notes_scroll=QScrollArea();notes_scroll.setWidgetResizable(True);notes_scroll.setWidget(self.notices);notes_scroll.setMinimumHeight(85);notes_scroll.setMaximumHeight(145);rl.addWidget(notes_scroll)
        export_bar=QHBoxLayout();self.export_target=QPushButton('Export target…');self.export_pair=QPushButton('Export comparison…')
        self.export_target.clicked.connect(lambda:self.export('target'));self.export_pair.clicked.connect(lambda:self.export('comparison'))
        export_bar.addWidget(self.export_target);export_bar.addWidget(self.export_pair);rl.addLayout(export_bar)
        self.progress=QProgressBar();self.progress.hide();self.cancel=QPushButton('Cancel export');self.cancel.hide();self.cancel.clicked.connect(self.cancel_export)
        rl.addWidget(self.progress);rl.addWidget(self.cancel)
        self.status=QLabel('');self.status.setWordWrap(True);root.addWidget(self.status)
        self.target_model.currentIndexChanged.connect(self._target_changed)
        self.variant.currentIndexChanged.connect(self._refresh_modes)
        self.filter.currentIndexChanged.connect(self._refresh_modes)
        self.target_fps.valueChanged.connect(self._refresh_modes)
        self.source_model.currentIndexChanged.connect(self._source_model_changed)
        for w in (self.source_pitch,self.source_fps,self.exposure,self.source_x,self.source_y,self.playback_fps,self.first,self.last,self.aim_x,self.aim_y,self.gamma,self.feature):w.valueChanged.connect(self.schedule_rebuild)
        for w in (self.source_bin,self.mode,self.playback_mode):w.currentIndexChanged.connect(self.schedule_rebuild)
        self.phase.valueChanged.connect(self.schedule_rebuild)
        for f in (sf,tf,pf):
            f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for combo in (self.mode,self.playback_mode,self.source_model,self.target_model,self.variant):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            combo.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self._target_changed();self._enable_comparison(False)

    def _enable_comparison(self,enabled):
        for w in (self.play,self.back,self.forward,self.slider):w.setEnabled(enabled)
        exporting=self.export_job is not None
        self.export_target.setEnabled(enabled and not exporting);self.export_pair.setEnabled(enabled and not exporting)

    def _source_model_changed(self):
        key=self.source_model.currentData()
        if key:self.source_pitch.setValue(PROFILES[key]['native_pitch_um'])
        self.source_pitch.setEnabled(not key);self.schedule_rebuild()

    def _target_changed(self):
        was=self.updating;self.updating=True
        self.variant.clear()
        for key,value in PROFILES[self.target_model.currentData()]['variants'].items():self.variant.addItem(value['label'],key)
        self.updating=was;self._refresh_modes()

    def _refresh_modes(self):
        if self.updating:return
        selected=self.mode.currentData();self.updating=True
        self.mode.clear()
        options=available_modes(self.target_model.currentData(),self.variant.currentData())
        options.sort(key=lambda m:m['width']*m['height']*m['binning']**2,reverse=True)
        for mode in options:
            if self.filter.currentData() and mode['binning']!=self.filter.currentData():continue
            if mode['max_fps']+1e-6<self.target_fps.value():continue
            label=f'{mode["width"]}×{mode["height"]} · '+('2×2 bin' if mode['binning']==2 else 'Unbinned')+f' · ≤{mode["max_fps"]:,} fps'
            self.mode.addItem(label,mode['id'])
        index=self.mode.findData(selected)
        if index>=0:self.mode.setCurrentIndex(index)
        self.updating=False;self.schedule_rebuild()

    def schedule_rebuild(self,*args):
        if not self.updating:
            self.pause();self.debounce.start()

    def settings(self):
        if self.session is None:raise ValueError('Choose source footage first.')
        md=self.session.metadata;pitch=self.source_pitch.value()
        return dict(target=self.target_model.currentData(),variant=self.variant.currentData(),mode=self.mode.currentData(),target_fps=self.target_fps.value(),playback_fps=self.playback_fps.value(),playback_mode=self.playback_mode.currentData(),phase=self.phase.value()/1000,first=self.first.value()-md.FirstImageNo,last=self.last.value()-md.FirstImageNo,aim_x=self.aim_x.value(),aim_y=self.aim_y.value(),gamma=self.gamma.value(),feature_pixels=self.feature.value(),source=dict(width=md.ImWidth,height=md.ImHeight,profile_id=self.source_model.currentData(),pitch_um=pitch,binning=self.source_bin.currentData(),capture_fps=self.source_fps.value(),exposure_us=self.exposure.value(),center_x_um=self.source_x.value()*pitch,center_y_um=self.source_y.value()*pitch))

    def apply_settings(self,s):
        self.updating=True
        src=s['source']
        self.source_model.setCurrentIndex(self.source_model.findData(src['profile_id']))
        self.source_pitch.setValue(src['pitch_um']);self.source_pitch.setEnabled(not src['profile_id'])
        self.source_bin.setCurrentIndex(self.source_bin.findData(src['binning']))
        self.source_fps.setValue(src['capture_fps']);self.exposure.setValue(src['exposure_us'])
        self.source_x.setValue(src['center_x_um']/src['pitch_um'] if src['pitch_um'] else 0);self.source_y.setValue(src['center_y_um']/src['pitch_um'] if src['pitch_um'] else 0)
        self.target_model.setCurrentIndex(self.target_model.findData(s['target']));self._target_changed()
        self.variant.setCurrentIndex(self.variant.findData(s['variant']))
        self.target_fps.setValue(s['target_fps']);self.filter.setCurrentIndex(0)
        for key,widget in [('playback_fps',self.playback_fps),('aim_x',self.aim_x),('aim_y',self.aim_y),('gamma',self.gamma),('feature_pixels',self.feature)]:widget.setValue(s[key])
        self.phase.setValue(round(s['phase']*1000));self.playback_mode.setCurrentIndex(self.playback_mode.findData(s['playback_mode']))
        self.first.setValue(s['first']+self.session.metadata.FirstImageNo);self.last.setValue(s['last']+self.session.metadata.FirstImageNo)
        self.updating=False;self._refresh_modes();self.mode.setCurrentIndex(self.mode.findData(s['mode']));self.debounce.stop();self.rebuild()

    def add_sources(self,paths,active=None,calibrations=None):
        self.calibrations.update(calibrations or {})
        self.sources.blockSignals(True)
        for path in paths:
            path=str(Path(path).resolve())
            if self.sources.findData(path)<0:self.sources.addItem(Path(path).name,path)
        if self.session is None and active:
            index=self.sources.findData(str(Path(active).resolve()))
            if index>=0:self.sources.setCurrentIndex(index)
        self.sources.blockSignals(False)
        if self.session is None and self.sources.count():self._source_selected()

    def choose_source(self):
        path,_=QFileDialog.getOpenFileName(self,'Source footage','','Footage (*.cine *.mp4 *.mov *.avi *.m4v *.mkv);;All files (*)')
        if path:
            self.add_sources([path]);self.sources.setCurrentIndex(self.sources.findData(str(Path(path).resolve())))
            self.open_source(path)

    def _source_selected(self,*args):
        path=self.sources.currentData()
        if path:self.open_source(path)

    def open_source(self,path):
        path=str(Path(path).resolve())
        if self.session is not None and self.session.path==path:return
        if self.load_job is not None and self.load_job.path==path and self.load_job.isRunning():return
        self.pause();self.debounce.stop();self.generation+=1;self.pending_render=None;self.pending_path=path
        self.comparison=None;self._enable_comparison(False)
        self._start_pending_load()

    def _start_pending_load(self):
        if not self.pending_path or self.closing:return
        if any(job is not None for job in (self.render_job,self.load_job)):return
        path=self.pending_path;self.pending_path=None
        if self.session is not None:
            try:self.saved_states[self.session.path]=self.settings()
            except Exception:pass
            self.session.close();self.session=None
        self.left_view.image=None;self.right_view.image=None
        self.left_view.message=self.right_view.message='Loading source…';self.left_view.update();self.right_view.update()
        self.status.setText('Reading source metadata and capture timestamps…')
        job=LoadJob(self,path);self.load_job=job;job.finished.connect(lambda:self._load_finished(job));job.start()

    def _load_finished(self,job):
        self.load_job=None
        if self.closing or self.pending_path:
            if job.result:job.result.close()
        elif job.error:
            self.status.setText(job.error);self.notices.setText(job.error)
            for view in (self.left_view,self.right_view):view.message='Could not load source';view.update()
        else:self.set_session(job.result)
        job.deleteLater();self._start_pending_load()

    def set_session(self,session):
        self.session=session;md=session.metadata
        self.sources.blockSignals(True)
        if self.sources.findData(session.path)<0:self.sources.addItem(Path(session.path).name,session.path)
        self.sources.setCurrentIndex(self.sources.findData(session.path));self.sources.blockSignals(False)
        self.left_view.set_frame(display_frame(session.frame(0)))
        self.updating=True
        self.first.setRange(md.FirstImageNo,md.FirstImageNo+md.ImageCount-1);self.last.setRange(md.FirstImageNo,md.FirstImageNo+md.ImageCount-1)
        self.updating=False
        s=self.saved_states.get(session.path)
        if s is None:
            s=default_settings(md.ImageCount);s['source']=session.source_defaults()
            if s['source']['capture_fps']>0:
                available=s['source']['capture_fps']/max(1,getattr(md,'fDecimation',1))
                if session.timestamps is not None and len(session.timestamps)>1:available=min(available,1/capture_interval(session.timestamps))
                s['target_fps']=max(100,available)
            options=[m for m in available_modes(s['target']) if m['max_fps']>=s['target_fps']]
            if options:
                options.sort(key=lambda m:(m['binning']==1,m['width']*m['height']),reverse=True)
                s['mode']=options[0]['id']
        self.source_details.setChecked(not bool(s['source']['profile_id']))
        self.source_summary.setText(f'{Path(session.path).name}\n{getattr(md,"CameraModel","") or "Camera not stored"} · {md.ImWidth}×{md.ImHeight} · {md.ImageCount:,} frames\nCentered source crop assumed; use overrides if needed.')
        self.status.setText('Source loaded. Confirm source sampling in the left panel.');self.apply_settings(s)
        if hasattr(self, '_job_restore_position'):
            self.slider.setValue(self._job_restore_position);del self._job_restore_position

    def rebuild(self):
        if self.updating or self.session is None or self.closing:return
        self.generation+=1;self.comparison=None;self.pending_render=None
        self.phase_label.setText(f'{self.phase.value()/10:g}% · {self.phase.value()/1000/self.target_fps.value()*1e6:.3f} µs')
        try:
            self.comparison=self.session.build(self.settings())
            c=self.comparison;s=c.settings;p=c.plan;fps=s['playback_fps']
            self.slider.setRange(0,len(p.times)-1)
            self.summary.setText(f'{c.mode["width"]}×{c.mode["height"]} output · {c.geo.retained_fraction:.1%} of recorded source area retained · {c.geo.available_fraction:.1%} of target area available\n'
                f'{len(p.indices):,} selected captures over {p.duration*1e6:,.2f} µs · target-only {c.frame_count("target")/fps:.2f}s · comparison {c.frame_count("comparison")/fps:.2f}s at {fps:g} fps')
            notes=list(c.notes)
            if s['feature_pixels']:
                measured=f'Feature: {s["feature_pixels"]/c.geo.step:.2f} target pixels across ({s["feature_pixels"]:.2f} source pixels).'
                cal=self.calibrations.get(self.session.path)
                if cal and cal[1] not in ('px','pixels'):measured+=f' {s["feature_pixels"]*cal[0]:.4g} {cal[1]}.'
                notes.insert(0,measured)
            self.notices.setText('\n\n'.join(notes));self._enable_comparison(True)
            if not self.export_job:self.status.setText('Comparison ready. Original source footage is unchanged.')
            self.request_render()
        except (ValueError,KeyError,TypeError) as error:
            self.notices.setText(str(error));self.summary.clear();self.readout.clear();self._enable_comparison(False)
            self.right_view.image=None;self.right_view.message='Complete the comparison settings';self.right_view.update()

    def request_render(self,*args):
        if self.comparison is None or self.closing:return
        self.pending_render=(self.comparison,self.slider.value(),self.generation)
        self._start_render()

    def _start_render(self):
        if self.render_job is not None:return
        if not self.pending_render or self.closing or self.pending_path:return
        c,index,generation=self.pending_render;self.pending_render=None
        job=RenderJob(self,c,index,generation);self.render_job=job;job.finished.connect(lambda:self._render_finished(job));job.start()

    def _render_finished(self,job):
        self.render_job=None
        if not self.closing and job.generation==self.generation:
            if job.error:
                self.status.setText('Preview failed: '+job.error);self.pause()
                self.right_view.image=None;self.right_view.message='Could not decode this frame';self.right_view.update()
            else:
                if self.status.text().startswith('Preview failed:'):self.status.setText('Comparison ready.')
                left,right,source,target,ended=job.result;c=job.comparison;s=c.settings
                self.left_view.set_frame(left);self.right_view.set_frame(right)
                source_num=source+s['first']+self.session.metadata.FirstImageNo
                target_num='—' if target is None else str(target+s['first']+self.session.metadata.FirstImageNo)
                self.left_info.setText(f'Original · {c.source.capture_fps:,.3f} capture fps\nFrame {source_num} · {c.source.width}×{c.source.height}')
                self.right_info.setText(f'{c.profile["name"]} · {s["target_fps"]:,.3f} capture fps\nFrame {target_num} · {c.mode["width"]}×{c.mode["height"]}'+(' · Finished (held)' if ended else ''))
                if s['playback_mode']=='same_fps':
                    replay=f'Source {c.plan.available_fps/s["playback_fps"]:,.1f}× slower · Target {s["target_fps"]/s["playback_fps"]:,.1f}× slower · {s["playback_fps"]:g} fps playback'
                else:replay=f'Synchronized event replay {c.plan.available_fps/s["playback_fps"]:,.1f}× slower · target holds are presentation repeats'
                self.readout.setText(replay+'\n'+f'Source event time {c.plan.times[source]*1e6:,.2f} µs; target '+('not yet captured' if target is None else f'{c.plan.times[target]*1e6:,.2f} µs'))
        job.deleteLater()
        if self.pending_path:self._start_pending_load()
        else:self._start_render()

    def pause(self):self.timer.stop();self.play.setText('Play')
    def toggle_play(self):
        if self.timer.isActive():self.pause();return
        if self.comparison is None:return
        if self.slider.value()>=self.slider.maximum():self.slider.setValue(0)
        self.play_start=time.monotonic();self.play_origin=self.slider.value();self.play.setText('Pause');self.timer.start()
    def _tick(self):
        if self.comparison is None:self.pause();return
        frame=self.play_origin+int((time.monotonic()-self.play_start)*self.playback_fps.value())
        self.slider.setValue(min(frame,self.slider.maximum()))
        if frame>=self.slider.maximum():self.pause()
    def _step(self,direction):self.pause();self.slider.setValue(self.slider.value()+direction)

    def _clear_measure(self):self.left_view.anchor=None;self.left_view.update()
    def _measure_point(self,x,y):
        if not self.measure.isChecked():return
        if self.left_view.anchor is None:self.left_view.anchor=(x,y);self.left_view.update();return
        a=self.left_view.anchor;self.feature.setValue(math.hypot(x-a[0],y-a[1]));self.measure.setChecked(False)

    def show_profile(self):
        p=PROFILES[self.target_model.currentData()]
        lines=[f'{p["name"]} · checked {p["verified_on"]}',f'Active array {p["active_pixels"][0]}×{p["active_pixels"][1]}, native pitch {p["native_pitch_um"]} µm.',f'ROI increments: {p["roi_increments"]}. Only listed presets are offered.',*p['notes'],*p['sources']]
        box=QMessageBox(self);box.setWindowTitle('Camera specifications');box.setText('\n\n'.join(lines));box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);box.exec()

    def save_preset(self):
        self.debounce.stop();self.rebuild()
        if self.comparison is None:return
        path,_=QFileDialog.getSaveFileName(self,'Save camera comparison','','PCA comparison (*.pcacompare)')
        if not path:return
        if not Path(path).suffix:path+='.pcacompare'
        try:save_comparison(path,self.session.path,self.comparison.settings,self.comparison.profile);self.status.setText('Comparison saved: '+path)
        except Exception as error:QMessageBox.warning(self,'Save comparison',str(error))

    def load_preset(self):
        if self.session is None:QMessageBox.information(self,'Load comparison','Choose the original source footage first.');return
        path,_=QFileDialog.getOpenFileName(self,'Load camera comparison','','PCA comparison (*.pcacompare)')
        if not path:return
        try:
            md=self.session.metadata;s=load_comparison(path,self.session.path,md.ImageCount,md.ImWidth,md.ImHeight)
            self.pause();self.apply_settings(s);self.status.setText('Comparison restored: '+path)
        except Exception as error:QMessageBox.warning(self,'Load comparison',str(error))

    def export(self,layout):
        if self.export_job is not None:return
        self.pause();self.debounce.stop();self.rebuild()
        if self.comparison is None:return
        path,selected=QFileDialog.getSaveFileName(self,'Export simulated camera comparison','','MP4 video (*.mp4);;QuickTime video (*.mov);;AVI video (*.avi)')
        if not path:return
        if not Path(path).suffix:path+=('.mov' if 'mov' in selected else '.avi' if 'avi' in selected else '.mp4')
        job=ComparatorExportJob(self,self.session.path,path,self.comparison.settings,layout);self.export_job=job
        self.progress.setRange(0,100);self.progress.setValue(0);self.progress.show();self.cancel.show()
        job.progress.connect(lambda value,total:self.progress.setValue(round(value/max(1,total)*100)))
        job.finished.connect(lambda:self._export_finished(job));self._enable_comparison(True);self.export_target.setEnabled(False);self.export_pair.setEnabled(False)
        self.status.setText('Exporting simulated comparison…');job.start()

    def cancel_export(self):
        if self.export_job is not None:self.export_job.cancel_event.set();self.status.setText('Cancelling comparison export…')
    def _export_finished(self,job):
        self.progress.hide();self.cancel.hide();self.export_job=None
        self.status.setText('Comparison export cancelled.' if job.cancelled else 'Export failed: '+job.error if job.error else 'Saved: '+job.result)
        self._enable_comparison(self.comparison is not None);job.deleteLater()

    def busy(self):return any(job is not None for job in (self.load_job,self.render_job,self.export_job))
    def shutdown(self):
        self.closing=True;self.pause();self.debounce.stop();self.pending_path=None;self.pending_render=None;self.cancel_export()
        if self.busy():return False
        if self.session is not None:self.session.close();self.session=None
        return True
