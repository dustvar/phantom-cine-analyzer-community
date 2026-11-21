"""A separate two-video workspace with serial, off-thread decoding."""
from copy import deepcopy
from pathlib import Path
import time
from PySide6.QtCore import Qt, QThread, QTimer, QSignalBlocker
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QPushButton,QComboBox,QDoubleSpinBox,QSpinBox,QSlider,QFileDialog,QGroupBox,
    QCheckBox,QSplitter,QScrollArea)
from camera_comparator_ui import ImageView
from video_overlay import MAX_OVERLAY_PLAYBACK_FPS, OverlayPair, default_overlay


class OverlayJob(QThread):
    def __init__(self, parent, pair, settings):
        super().__init__(parent)
        self.pair,self.settings = pair,deepcopy(settings)
        self.result = self.error = None

    def run(self):
        try:
            paths = [str(Path(p).resolve()) for p in self.settings['paths']]
            if self.pair is None or self.pair.paths != paths:
                # Open both successfully before releasing a previous pair.
                replacement = OverlayPair(paths)
                if self.pair: self.pair.close()
                self.pair = replacement
            self.result = self.pair.render(self.settings)
        except Exception as error:
            self.error = str(error)


class VideoOverlayPanel(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('video_overlay')
        self.pair = self.job = None
        self.pending = False
        self.closing = self.updating = False
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick)
        self.debounce = QTimer(self); self.debounce.setSingleShot(True)
        self.debounce.setInterval(10);self.debounce.timeout.connect(self.start_render)
        self.sources=[];self.opacity=[];self.offsets=[];self.raw_t0=[];self.t0_frames=[]
        self.source_info=[];self.metadata=[]
        layout=QVBoxLayout(self)
        title=QLabel('Video Overlay');title.setStyleSheet('font-size: 21px; font-weight: 600;')
        layout.addWidget(title)
        subtitle=QLabel('Compare two test runs at the same time from trigger. Positive offsets move a video later.')
        subtitle.setWordWrap(True);layout.addWidget(subtitle)
        split=QSplitter(Qt.Orientation.Horizontal);layout.addWidget(split,1)
        controls=QWidget();col=QVBoxLayout(controls)
        for i in range(2):
            box=QGroupBox('Video '+ 'AB'[i]);form=QFormLayout(box)
            choice=QComboBox();choice.setMinimumContentsLength(14)
            choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            choice.addItem('Choose footage…', '')
            browse=QPushButton('Browse…');browse.clicked.connect(lambda checked=False,n=i:self.browse(n))
            row=QHBoxLayout();row.addWidget(choice,1);row.addWidget(browse);form.addRow(row)
            info=QLabel('No video selected');info.setWordWrap(True);form.addRow(info);self.source_info.append(info)
            alpha=QSpinBox();alpha.setRange(0,100);alpha.setValue(50);alpha.setSuffix(' %')
            alpha.setToolTip('50% each gives an equal blend. Combined opacity above 100% is normalized.')
            offset=QSpinBox();offset.setRange(-1000000,1000000);offset.setSuffix(' frames')
            offset.setKeyboardTracking(False)
            offset.setToolTip('Positive = this video occurs later; negative = earlier. Uses this video’s stored frame interval.')
            raw=QCheckBox('Use Cine trigger as T0');raw.setChecked(True)
            t0=QSpinBox();t0.setRange(-2147483647,2147483647);t0.setEnabled(False)
            t0.setKeyboardTracking(False)
            t0.setToolTip('Make this recorded frame the new zero time. The Cine file is unchanged.')
            form.addRow('Opacity',alpha);form.addRow('Time offset',offset);form.addRow(raw);form.addRow('T0 at frame',t0)
            here=QPushButton('Set displayed frame as T0');here.clicked.connect(lambda checked=False,n=i:self.zero_here(n));form.addRow(here)
            self.sources.append(choice);self.opacity.append(alpha);self.offsets.append(offset);self.raw_t0.append(raw);self.t0_frames.append(t0)
            choice.currentIndexChanged.connect(lambda value,n=i:self.source_changed(n))
            for control in (alpha,offset,t0):control.valueChanged.connect(self.request_render)
            raw.toggled.connect(lambda checked,n=i:self.zero_mode_changed(n,checked))
            col.addWidget(box)
        viewbox=QGroupBox('Comparison');form=QFormLayout(viewbox)
        self.mode=QComboBox();self.mode.addItems(['Blend','Difference']);self.mode.currentTextChanged.connect(self.request_render)
        self.x=QSpinBox();self.y=QSpinBox()
        for spin in (self.x,self.y):spin.setRange(-20000,20000);spin.setSuffix(' px');spin.valueChanged.connect(self.request_render)
        form.addRow('View',self.mode);form.addRow('Move B horizontally',self.x);form.addRow('Move B vertically',self.y)
        reset=QPushButton('Reset alignment');reset.clicked.connect(self.reset_alignment);form.addRow(reset);col.addWidget(viewbox)
        note=QLabel('Images retain their original pixel size and are centered. No spatial scaling or motion interpolation is applied. Difference shows absolute pixel differences.');note.setWordWrap(True);col.addWidget(note);col.addStretch()
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(controls);scroll.setMinimumWidth(300)
        split.addWidget(scroll);self.view=ImageView();self.view.message='Choose Video A and Video B to compare their test runs.';split.addWidget(self.view);split.setStretchFactor(1,1);split.setSizes([340,850])
        transport=QHBoxLayout();self.play=QPushButton('Play');self.play.clicked.connect(self.toggle_play)
        previous=QPushButton('‹');previous.clicked.connect(lambda:self.step(-1))
        following=QPushButton('›');following.clicked.connect(lambda:self.step(1))
        for button, label in ((previous, 'Previous frame'), (following, 'Next frame')):
            button.setProperty('analysisStep', True)
            button.setToolTip(label)
            button.setAccessibleName(label)
        self.play.setProperty('analysisPlay', True)
        self.slider=QSlider(Qt.Orientation.Horizontal);self.slider.setRange(0,0);self.slider.valueChanged.connect(self.request_render);self.slider.sliderPressed.connect(self.pause)
        self.fps=QDoubleSpinBox();self.fps.setRange(.1,MAX_OVERLAY_PLAYBACK_FPS);self.fps.setValue(30);self.fps.setSuffix(' fps replay');self.fps.valueChanged.connect(self.pause)
        for w in (previous,self.play,following,self.slider,self.fps):transport.addWidget(w,1 if w is self.slider else 0)
        layout.addLayout(transport)
        self.readout=QLabel('T = —');self.readout.setWordWrap(True);layout.addWidget(self.readout)
        self.status=QLabel('Two independent readers keep Viewer edits and tracking unchanged.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.last_result=None

    def settings(self):
        return dict(paths=[c.currentData() or '' for c in self.sources], opacity=[c.value() for c in self.opacity],
            offsets=[c.value() for c in self.offsets],raw_t0=[c.isChecked() for c in self.raw_t0],
            t0_frames=[c.value() for c in self.t0_frames],x=self.x.value(),y=self.y.value(),
            mode=self.mode.currentText(),playback_fps=self.fps.value(),position=self.slider.value())

    def add_sources(self,paths):
        self.updating=True
        for combo in self.sources:
            for path in paths:
                path=str(Path(path).resolve())
                if combo.findData(path)<0:combo.addItem(Path(path).name,path)
        for i,combo in enumerate(self.sources):
            if not combo.currentData() and combo.count()>i+1:combo.setCurrentIndex(i+1)
        self.updating=False
        self.request_render()

    def apply_settings(self,settings):
        s=default_overlay();s.update(deepcopy(settings));self.updating=True
        for i,combo in enumerate(self.sources):
            path=s['paths'][i]
            if path and combo.findData(path)<0:combo.addItem(Path(path).name,path)
            combo.setCurrentIndex(max(0,combo.findData(path)))
            self.opacity[i].setValue(s['opacity'][i]);self.offsets[i].setValue(s['offsets'][i])
            self.raw_t0[i].setChecked(s['raw_t0'][i]);self.t0_frames[i].setEnabled(not s['raw_t0'][i])
            self.t0_frames[i].setRange(-2147483647,2147483647);self.t0_frames[i].setValue(s['t0_frames'][i])
        self.x.setValue(s['x']);self.y.setValue(s['y']);self.mode.setCurrentText(s['mode']);self.fps.setValue(s['playback_fps'])
        self.slider.setRange(0,max(0,s['position']));self.slider.setValue(s['position'])
        self.updating=False;self.closing=False;self.request_render()

    def browse(self,index):
        path,_=QFileDialog.getOpenFileName(self,'Choose Video '+ 'AB'[index],'','Footage (*.cine *.mp4 *.mov *.avi *.m4v *.mkv)')
        if path:
            self.updating=True
            combo=self.sources[index]
            if combo.findData(path)<0:combo.addItem(Path(path).name,path)
            combo.setCurrentIndex(combo.findData(path));self.updating=False;self.source_changed(index)

    def source_changed(self,index):
        if self.updating:return
        self.pause();self.last_result=None;self.updating=True
        self.raw_t0[index].setChecked(True);self.t0_frames[index].setEnabled(False)
        self.offsets[index].setValue(0);self.slider.setValue(0)
        self.updating=False;self.request_render()

    def zero_mode_changed(self,index,raw):
        self.t0_frames[index].setEnabled(not raw);self.request_render()

    def zero_here(self,index):
        if self.last_result and self.last_result['frames'][index] is not None:
            self.pause();self.updating=True;self.raw_t0[index].setChecked(False)
            self.t0_frames[index].setEnabled(True);self.t0_frames[index].setValue(self.last_result['frames'][index])
            self.updating=False;self.request_render()

    def reset_alignment(self):
        self.pause();s=self.settings();s.update(offsets=[0,0],raw_t0=[True,True],x=0,y=0,position=0);self.apply_settings(s)

    def request_render(self,*args):
        if self.updating or self.closing:return
        self.pending=True;self.debounce.start()

    def start_render(self):
        if self.closing or self.job is not None or not self.pending:return
        settings=self.settings()
        self.pending=False
        if not all(settings['paths']):
            self.pause();self.last_result=None;self.view.image=None
            self.view.message='Choose Video A and Video B to compare their test runs.';self.view.update()
            self.readout.setText('T = —');self.status.setText('Choose both videos to align their triggers.');return
        self.status.setText('Loading / aligning footage…')
        self.job=OverlayJob(self,self.pair,settings);self.pair=None
        job=self.job;job.finished.connect(lambda:self.finished(job));job.start()

    def finished(self,job):
        self.job=None;self.pair=job.pair
        if self.closing:
            if self.pair:self.pair.close();self.pair=None
        elif self.pending:
            self.start_render()
        elif job.error:
            self.pause();self.status.setText(job.error);self.view.image=None;self.view.message=job.error;self.view.update();self.last_result=None
        else:
            r=job.result;self.last_result=r;self.metadata=r['metadata'];self.view.set_frame(r['image'])
            with QSignalBlocker(self.slider):self.slider.setRange(0,r['count']-1);self.slider.setValue(r['position'])
            self.updating=True
            for i,md in enumerate(r['metadata']):
                self.source_info[i].setText(f'{md["model"] or "Camera not stored"} · {md["width"]} × {md["height"]}\n{md["fps"]:,.3f} capture fps · {md["count"]:,} frames')
                with QSignalBlocker(self.t0_frames[i]):self.t0_frames[i].setRange(md['first'],md['first']+md['count']-1)
            self.updating=False
            labels=['AB'[i]+': '+ ('no capture' if f is None else f'frame {f:,} (Δt {r["errors_us"][i]:+.3f} µs)') for i,f in enumerate(r['frames'])]
            self.readout.setText(f'T = {r["time"]*1000:+,.6f} ms   |   '+ '   |   '.join(labels))
            self.status.setText('A: '+r['notes'][0]+'.  B: '+r['notes'][1]+'. Nearest stored captures; missing footage stays blank.')
        job.deleteLater()

    def pause(self):self.timer.stop();self.play.setText('Play')
    def toggle_play(self):
        if self.timer.isActive():self.pause()
        elif self.last_result:
            if self.slider.value()>=self.slider.maximum():self.slider.setValue(0)
            self.play_origin=self.slider.value();self.play_started=time.monotonic()
            self.timer.start(10);self.play.setText('Pause')
    def step(self,amount):self.pause();self.slider.setValue(self.slider.value()+amount)
    def tick(self):
        if self.job is not None or self.pending:return
        target=self.play_origin+int((time.monotonic()-self.play_started)*self.fps.value())
        if target>=self.slider.maximum():
            self.slider.setValue(self.slider.maximum());self.pause()
        elif target>self.slider.value():self.slider.setValue(target)
    def busy(self):return self.job is not None
    def shutdown(self):
        self.closing=True;self.pause();self.debounce.stop();self.pending=False
        if self.job is not None:return False
        if self.pair:self.pair.close();self.pair=None
        return True
