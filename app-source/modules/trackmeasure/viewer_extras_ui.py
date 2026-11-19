"""Compact native controls for segment zoom and timed custom text."""
from copy import deepcopy
import uuid
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,
    QDoubleSpinBox,QComboBox,QCheckBox,QPlainTextEdit,QListWidget,QScrollArea)
from viewer_timeline import copy_index,copy_options,zoom_options


class CommitTextEdit(QPlainTextEdit):
    editingFinished=Signal()
    def focusOutEvent(self,event):
        self.editingFinished.emit()
        super().focusOutEvent(event)


class TimelineViewport(QScrollArea):
    def __init__(self,timeline,parent=None):
        super().__init__(parent)
        self.timeline=timeline;self.factor=1.0
        self.setWidget(timeline);self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName('viewer_timeline_viewport')
        self.setAccessibleName('Timeline viewport: zoom to scroll through segments')
        self.setFixedHeight(69);self.setMinimumWidth(100)
        self.setStyleSheet('QScrollArea { border: none; }')
        self.setToolTip('Zoom the timeline with − / +. Drag the scrollbar to pan through the sequence.')

    def set_zoom(self,factor):
        fraction=(self.horizontalScrollBar().value()+self.viewport().width()/2)/max(1,self.timeline.width())
        self.factor=min(1000,max(1,factor));self._resize_timeline()
        self.horizontalScrollBar().setValue(round(fraction*self.timeline.width()-self.viewport().width()/2))

    def _resize_timeline(self):
        self.timeline.resize(min(8_000_000,max(1,round(self.viewport().width()*self.factor))),56)

    def resizeEvent(self,event):
        super().resizeEvent(event);self._resize_timeline()


class ExtraEditorControls:
    def _build_zoom(self):
        tab=QWidget();layout=QVBoxLayout(tab);self._add_editor_tab(tab,'Zoom')
        self.zoom_section_label=QLabel();layout.addWidget(self.zoom_section_label)
        row=QHBoxLayout();self.zoom_presets={}
        for factor in (1,2,3):
            button=QPushButton(f'{factor}×');self.zoom_presets[factor]=button
            button.clicked.connect(lambda checked=False,value=factor:self._zoom_change('factor',value))
            row.addWidget(button)
        layout.addLayout(row)
        form=QFormLayout()
        self.zoom_factor=QDoubleSpinBox();self.zoom_factor.setRange(1,20);self.zoom_factor.setDecimals(2)
        self.zoom_factor.setSuffix('×');self.zoom_factor.setKeyboardTracking(False)
        self.zoom_duration=QDoubleSpinBox();self.zoom_duration.setRange(0,3600);self.zoom_duration.setDecimals(2)
        self.zoom_duration.setSingleStep(.1);self.zoom_duration.setSuffix(' s');self.zoom_duration.setSpecialValueText('Instant')
        self.zoom_duration.setKeyboardTracking(False)
        self.zoom_curve=QComboBox()
        for label,key in [('Smooth','smooth'),('Linear','linear'),('Ease in','ease_in'),('Ease out','ease_out')]: self.zoom_curve.addItem(label,key)
        self.zoom_x=QDoubleSpinBox();self.zoom_y=QDoubleSpinBox()
        for spin in (self.zoom_x,self.zoom_y): spin.setRange(0,100);spin.setSuffix(' %');spin.setKeyboardTracking(False)
        form.addRow('Custom zoom',self.zoom_factor);form.addRow('Zoom duration',self.zoom_duration)
        form.addRow('Motion',self.zoom_curve);form.addRow('Center X',self.zoom_x);form.addRow('Center Y',self.zoom_y)
        layout.addLayout(form)
        self.crop_edit_button=QPushButton('Adjust yellow frame');self.crop_edit_button.setCheckable(True)
        layout.addWidget(self.crop_edit_button)
        note=QLabel('Select a segment, then set its target zoom. Use 1× to zoom back out. Duration is in finished-video seconds; 0 is instant.\n\nAdjust yellow frame shows the full image. Drag inside the rectangle to move it, or drag a corner to resize it. Turn it off or press Play to preview the zoom.')
        note.setWordWrap(True);layout.addWidget(note);layout.addStretch()
        self.zoom_factor.valueChanged.connect(lambda v:self._zoom_change('factor',v))
        self.zoom_duration.valueChanged.connect(lambda v:self._zoom_change('duration',v))
        self.zoom_curve.currentIndexChanged.connect(lambda:self._zoom_change('curve',self.zoom_curve.currentData()))
        self.zoom_x.valueChanged.connect(lambda v:self._zoom_change('cx',v/100))
        self.zoom_y.valueChanged.connect(lambda v:self._zoom_change('cy',v/100))

    def _load_zoom(self):
        if not self.edit['sections']: return
        options=zoom_options(self.edit,self.selected)
        self.zoom_section_label.setText(f'Video {copy_index(self.edit,self.frame)+1} · Segment {self.selected+1}')
        self.zoom_factor.setValue(options['factor']);self.zoom_duration.setValue(options['duration'])
        self.zoom_curve.setCurrentIndex(self.zoom_curve.findData(options['curve']))
        self.zoom_x.setValue(options['cx']*100);self.zoom_y.setValue(options['cy']*100)

    def _zoom_change(self,key,value):
        if self._updating or not self.edit['sections']: return
        result=deepcopy(self.edit);options=zoom_options(result,self.selected)
        options.update(inherit=False);options[key]=value
        half=.5/options['factor']
        options['cx']=min(1-half,max(half,options['cx']));options['cy']=min(1-half,max(half,options['cy']))
        result['sections'][self.selected]['zoom']=options
        self.changed.emit(result)
        if key=='factor':self.crop_edit_button.setChecked(True)

    def _build_text(self):
        self.selected_text_id=None;self.text_seconds=0
        tab=QWidget();layout=QVBoxLayout(tab);self._add_editor_tab(tab,'Text')
        self.text_list=QListWidget();self.text_list.setFixedHeight(75);layout.addWidget(self.text_list)
        row=QHBoxLayout();self.add_text_button=QPushButton('Add Text');self.remove_text_button=QPushButton('Remove')
        row.addWidget(self.add_text_button);row.addWidget(self.remove_text_button);layout.addLayout(row)
        self.text_enabled=QCheckBox('Enable');layout.addWidget(self.text_enabled)
        self.text_content=CommitTextEdit();self.text_content.setPlaceholderText('Explain what is happening…')


        self.text_content.setFixedHeight(76);layout.addWidget(self.text_content)
        self.text_timer=QTimer(self);self.text_timer.setSingleShot(True);self.text_timer.setInterval(400)
        self.text_content.textChanged.connect(self._queue_text)
        self.text_content.editingFinished.connect(self._commit_text);self.text_timer.timeout.connect(self._commit_text)
        form=QFormLayout()
        self.text_start=QDoubleSpinBox();self.text_duration=QDoubleSpinBox()
        for spin in (self.text_start,self.text_duration): spin.setRange(0,1e9);spin.setDecimals(2);spin.setSuffix(' s');spin.setKeyboardTracking(False)
        self.text_duration.setMinimum(.01)
        self.text_size=QDoubleSpinBox();self.text_size.setRange(1,15);self.text_size.setSuffix(' %');self.text_size.setKeyboardTracking(False)
        self.text_x=QDoubleSpinBox();self.text_y=QDoubleSpinBox()
        for spin in (self.text_x,self.text_y):spin.setRange(0,100);spin.setSuffix(' %');spin.setKeyboardTracking(False)
        self.text_color=QComboBox()
        for name,color in [('White','#ffffff'),('Gold','#ffdf8c'),('Cyan','#7ef9ff')]:self.text_color.addItem(name,color)
        for label,widget in [('Start in this video',self.text_start),('Duration',self.text_duration),('Text size',self.text_size),('Horizontal',self.text_x),('Vertical',self.text_y),('Color',self.text_color)]:form.addRow(label,widget)
        layout.addLayout(form)
        self.text_background=QCheckBox('Translucent background');self.text_pinned=QCheckBox('Keep on screen during zoom')
        layout.addWidget(self.text_background);layout.addWidget(self.text_pinned)
        self.show_text_button=QPushButton('Go to text start');layout.addWidget(self.show_text_button)
        note=QLabel('Timing starts at the beginning of this video in the sequence, in finished-video seconds. Drag visible text on the image to position it. Text is saved with this video only.')
        note.setWordWrap(True);layout.addWidget(note);layout.addStretch()
        self.add_text_button.clicked.connect(self._add_text);self.remove_text_button.clicked.connect(self._remove_text)
        self.text_list.currentRowChanged.connect(self._select_text)
        self.text_enabled.toggled.connect(lambda v:self._text_change('enabled',v))
        for spin,key,factor in [(self.text_start,'start',1),(self.text_duration,'duration',1),(self.text_size,'size',1),(self.text_x,'x',.01),(self.text_y,'y',.01)]:
            spin.valueChanged.connect(lambda v,k=key,m=factor:self._text_change(k,v*m))
        self.text_color.currentIndexChanged.connect(lambda:self._text_change('color',self.text_color.currentData()))
        self.text_background.toggled.connect(lambda v:self._text_change('background',v))
        self.text_pinned.toggled.connect(lambda v:self._text_change('pinned',v))
        self.show_text_button.clicked.connect(self._seek_text)

    def _texts(self,edit=None):
        return copy_options(edit or self.edit,copy_index(self.edit,self.frame)).setdefault('texts',[])

    def _load_text(self):
        texts=self._texts();self.text_list.clear()
        for item in texts:self.text_list.addItem(item['text'].splitlines()[0][:34] if item['text'] else '(Empty text)')
        index=next((i for i,t in enumerate(texts) if t['id']==self.selected_text_id),0 if texts else -1)
        self.text_list.setCurrentRow(index);item=texts[index] if index>=0 else None
        self.selected_text_id=item['id'] if item else None
        for widget in (self.text_enabled,self.text_content,self.text_start,self.text_duration,self.text_size,self.text_x,self.text_y,self.text_color,self.text_background,self.text_pinned,self.remove_text_button,self.show_text_button):widget.setEnabled(item is not None)
        if not item:
            self.text_content.clear();return
        if self.text_content.toPlainText()!=item['text']:self.text_content.setPlainText(item['text'])
        self.text_enabled.setChecked(item['enabled']);self.text_start.setValue(item['start']);self.text_duration.setValue(item['duration'])
        self.text_size.setValue(item['size']);self.text_x.setValue(item['x']*100);self.text_y.setValue(item['y']*100)
        self.text_color.setCurrentIndex(self.text_color.findData(item['color']))
        self.text_background.setChecked(item['background']);self.text_pinned.setChecked(item['pinned'])

    def _add_text(self):
        result=deepcopy(self.edit);texts=self._texts(result)
        if len(texts)>=200:return
        item=dict(id=uuid.uuid4().hex,text='Your text',start=max(0,self.text_seconds),duration=2.,enabled=True,
                  x=.5,y=.12,size=4.,color='#ffffff',background=True,pinned=True)
        texts.append(item);self.selected_text_id=item['id'];self.changed.emit(result)
        self.text_content.setFocus();self.text_content.selectAll()

    def _remove_text(self):
        result=deepcopy(self.edit);texts=self._texts(result)
        texts[:]=[t for t in texts if t['id']!=self.selected_text_id];self.changed.emit(result)

    def _select_text(self,index):
        if self._updating:return
        texts=self._texts()
        if 0<=index<len(texts):
            self.selected_text_id=texts[index]['id'];self._updating=True;self._load_text();self._updating=False

    def _queue_text(self):
        if not self._updating:self.text_timer.start()

    def _commit_text(self):
        if not self.text_timer.isActive() and not self.sender() is self.text_timer:return
        self.text_timer.stop();self._text_change('text',self.text_content.toPlainText()[:2000])

    def _text_change(self,key,value):
        if self._updating:return
        result=deepcopy(self.edit)
        item=next((t for t in self._texts(result) if t['id']==self.selected_text_id),None)
        if item is not None:item[key]=value;self.changed.emit(result)

    def _seek_text(self):
        item=next((t for t in self._texts() if t['id']==self.selected_text_id),None)
        if item:self.textSeekRequested.emit(item['start'])
