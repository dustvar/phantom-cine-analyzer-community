"""Visible, shared setup controls for all three PCA tracking adapters."""
from copy import deepcopy
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QPushButton,QToolButton,QComboBox,QCheckBox,QSpinBox,QDoubleSpinBox,QScrollArea,QLineEdit,QProgressBar,
    QDialog,QSizePolicy)
from tumbling_tracker import TrackerSettings, TUMBLING_TRACKING_METHOD

CLASSIC='Classic (Intensity Only)'
EDGE='Hybrid (Edge + Intensity)'
METHODS=[('Classic',CLASSIC),('Edge Assist',EDGE),('Tumbling',TUMBLING_TRACKING_METHOD)]

# These select numerical search/resolution settings, not a claimed physical
# uncertainty. Keep the full rotation recovery range for fast-tumbling footage.
EDGE_ACCURACY_PRESETS={
    'normal':dict(label='Normal · faster',settings=dict(rotation_step=6.,position_precision=.25,angle_precision=.5)),
    'tight':dict(label='Tight · finer',settings=dict(rotation_step=2.,position_precision=.1,angle_precision=.1)),
    'extreme':dict(label='Extreme · finest',settings=dict(rotation_step=1.,position_precision=.05,angle_precision=.05)),
}


def edge_accuracy_name(state):
    for name,preset in EDGE_ACCURACY_PRESETS.items():
        if all(abs(float(state.get(key,-1))-value)<1e-9 for key,value in preset['settings'].items()):return name
    return 'custom'


class EdgeAccuracyDialog(QDialog):
    """Explicit cost/resolution choice before entering guided Edge setup."""
    def __init__(self,parent=None,selected='normal'):
        super().__init__(parent);self.setWindowTitle('Edge Assist accuracy');self.setObjectName('edge_accuracy_dialog')
        self.setMinimumWidth(480);root=QVBoxLayout(self);root.setContentsMargins(20,18,20,18);root.setSpacing(12)
        title=QLabel('Choose the detail level for this object');title.setWordWrap(True);root.addWidget(title)
        text=QLabel('Normal is a faster starting point. Tighter settings use finer subpixel position and angle grids, '
            'and examine more rotations, so they can take longer. These are search resolutions, not a guarantee '
            'of measurement accuracy. You can edit every setting before tracking.')
        text.setWordWrap(True);root.addWidget(text)
        self.choice=QComboBox();self.choice.setObjectName('edge_accuracy_choice')
        for name,preset in EDGE_ACCURACY_PRESETS.items():
            s=preset['settings'];self.choice.addItem(f"{preset['label']} — {s['position_precision']:g} px / {s['angle_precision']:g}°",name)
        self.choice.setCurrentIndex(max(0,self.choice.findData(selected)));root.addWidget(self.choice)
        row=QHBoxLayout();back=QPushButton('Back');back.clicked.connect(self.reject);row.addWidget(back);row.addStretch()
        self.continue_button=QPushButton('Continue');self.continue_button.setObjectName('edge_accuracy_continue');self.continue_button.setProperty('primaryAction',True)
        self.continue_button.clicked.connect(self.accept);row.addWidget(self.continue_button);root.addLayout(row)

    @classmethod
    def choose(cls,parent=None,selected='normal'):
        dialog=cls(parent,selected)
        result=dialog.choice.currentData() if dialog.exec()==QDialog.Accepted else None
        dialog.hide();dialog.setParent(None);dialog.deleteLater();return result


def setup_defaults(method, first=0,last=0):
    state=dict(tracking_method=method,name='',start=first,end=last,frames_enable=True,
                tpl_rng=(61,61) if method==TUMBLING_TRACKING_METHOD else (31,31),
                search_area=(101,101),search_area_enable=True,tpl_rng_enable=True,
                search_area_manual=False,search_offset=(0.,0.),
                acceptable_score=.4 if method==EDGE else .6,subpixel_size='1/10 pix',subpixel_type='cubic',
                update_template_enable=method==CLASSIC,tpl_score=.8,rotation_allowed=method==EDGE,
                rotation_range=180.,rotation_step=2.,edge_weight=.6,edge_threshold=.30,
                adjacent_confidence_weight=.65,search_area_multiplier=1.5,smart_frames=method==EDGE,
                smart_miss_limit=3,anchor_refinement_enabled=True,position_precision=.1,angle_precision=.1,
                tumbling=TrackerSettings().to_dict())
    if method==EDGE:state.update(EDGE_ACCURACY_PRESETS['normal']['settings'])
    return state


class TrackingSetupPanel(QWidget):
    changed=Signal(object)
    methodChosen=Signal(str)
    objectChosen=Signal(int)
    processRequested=Signal()
    cancelRequested=Signal()
    previewRequested=Signal()
    anchorRequested=Signal()
    removeRequested=Signal()
    helpRequested=Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('tracking_setup_panel');self.loading=False;self.controls={};self.rows={}
        self.state=setup_defaults(CLASSIC);self.active=None;self.busy=False
        root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0);root.setSpacing(7)
        row=QHBoxLayout();self.methods={}
        for label,key in METHODS:
            b=QPushButton(label);b.setCheckable(True);b.setObjectName('tracking_method_choice')
            b.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
            b.setToolTip('Set up a new '+label+' object');b.clicked.connect(lambda checked=False,m=key:self.methodChosen.emit(m))
            row.addWidget(b,1);self.methods[key]=b
        self.help=QToolButton();self.help.setText('?');self.help.setAccessibleName('Tracking guide and technical explanation')
        self.help.clicked.connect(self.helpRequested);row.addWidget(self.help);root.addLayout(row)
        self.objects=QComboBox();self.objects.setToolTip('Select an existing object to view and adjust its settings.')
        self.objects.activated.connect(lambda i:self.objectChosen.emit(self.objects.itemData(i)) if self.objects.itemData(i) is not None else None)
        root.addWidget(self.objects)
        self.hint=QLabel();self.hint.setWordWrap(True);self.hint.setObjectName('tracking_setup_hint');root.addWidget(self.hint)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content=QWidget();self.form=QFormLayout(content);self.form.setContentsMargins(8,8,8,8);self.form.setSpacing(7)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.scroll.setWidget(content);root.addWidget(self.scroll,1)
        self.accuracy=QComboBox();self.accuracy.setObjectName('edge_accuracy_settings');self.accuracy.setAccessibleName('Edge Assist accuracy preset')
        for name,preset in EDGE_ACCURACY_PRESETS.items():self.accuracy.addItem(preset['label'],name)
        self.accuracy.addItem('Custom settings','custom')
        self.accuracy_label=QLabel('Accuracy preset');self.form.addRow(self.accuracy_label,self.accuracy)
        self.accuracy.setToolTip('Finer position/angle grids and rotation search cost more time. Changing an advanced resolution marks the preset Custom.')
        self.accuracy.currentIndexChanged.connect(self._accuracy_changed)
        self._text('name','Object name')
        self._number('start','First frame',-2_000_000_000,2_000_000_000,integer=True)
        self._number('end','Last frame',-2_000_000_000,2_000_000_000,integer=True)
        self._check('frames_enable','Use frame range')
        self._number('tpl_width','Region width',9,4095,integer=True,suffix=' px',tip='Include the whole object for Tumbling; distinctive detail for Classic/Edge Assist.')
        self._number('tpl_height','Region height',9,4095,integer=True,suffix=' px')
        self._check('tpl_rng_enable','Show region / mask')
        self._number('search_width','Search width',11,8191,integer=True,suffix=' px')
        self._number('search_height','Search height',11,8191,integer=True,suffix=' px')
        self._check('search_area_enable','Limit search region')
        self._number('acceptable_score','Accept confidence',0,1,tip='Minimum heuristic match quality. It is not a probability of being correct.')
        self._combo('subpixel_size','Position interpolation',[(f'1/{i} pix' if i>1 else '1.0 pix',f'1/{i} pix' if i>1 else '1.0 pix') for i in range(1,11)])
        self._combo('subpixel_type','Interpolation curve',[(s.title(),s) for s in ('linear','quadratic','cubic','quartic','quintic')])
        self._check('update_template_enable','Update Classic template')
        self._number('tpl_score','Template update cutoff',0,1,tip='Classic behavior: update an accepted template when its score falls below this cutoff.')
        self._check('smart_frames','Find start / stop automatically')
        self._check('rotation_allowed','Allow in-plane rotation')
        self._number('rotation_range','Rotation search',0,180,suffix='°')
        self._number('rotation_step','Rotation step',.1,45,suffix='°')
        self._number('position_precision','Position resolution',.01,1,suffix=' px')
        self._number('angle_precision','Angle resolution',.05,1,suffix='°')
        self._number('edge_weight','Edge weight',0,1)
        self._number('edge_threshold','Edge threshold',.02,1)
        self._number('adjacent_confidence_weight','Neighbor reference weight',0,1,tip='The remaining weight belongs to the original setup frame.')
        self._number('search_area_multiplier','Search size multiplier',1,10,suffix='×')
        self._number('smart_miss_limit','Stop after misses',1,100,integer=True)
        self._check('anchor_refinement_enabled','Point Lock refinement')
        self._combo('tumbling.mode','Segmentation', [('Adaptive color / texture','adaptive'),('Dark silhouette','dark'),('Bright silhouette','bright')])
        self._number('tumbling.threshold','Silhouette threshold',.05,.95,tip='Fraction from local background toward foreground brightness. Used only in dark/bright modes.')
        self._number('tumbling.update_confidence','Update confidence',0,1,tip='Appearance updates require at least this confidence AND the acceptance threshold.')
        self._number('tumbling.adaptation','Appearance learning rate',0,1,tip='0 freezes the appearance reference; 1 replaces it at every trusted frame.')
        self._number('tumbling.max_motion','Max motion / frame',1,2000,integer=True,suffix=' px')
        self._number('tumbling.growth','Contour growth margin',1,200,integer=True,suffix=' px')
        self._number('tumbling.min_area','Minimum foreground',3,10000000,integer=True,suffix=' px²')
        self._number('tumbling.area_ratio','Max area change',1.1,20,suffix='×',tip='Allowed ratio versus the last accepted silhouette, in either direction.')
        self._number('tumbling.miss_limit','Stop after misses',1,100,integer=True)
        self._number('tumbling.iterations','Segmentation iterations',1,10,integer=True)
        self._number('tumbling.cleanup','Mask cleanup radius',0,5,integer=True,suffix=' px')
        row=QHBoxLayout();self.anchor=QPushButton('Setup frame');self.preview=QPushButton('Preview mask')
        self.anchor.clicked.connect(self.anchorRequested);self.preview.clicked.connect(self.previewRequested)
        row.addWidget(self.anchor);row.addWidget(self.preview);root.addLayout(row)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);self.progress.setFormat('Ready');root.addWidget(self.progress)
        self.status=QLabel('');self.status.setWordWrap(True);root.addWidget(self.status)
        row=QHBoxLayout();self.run=QPushButton('Track object');self.run.setProperty('primaryAction',True)
        self.cancel=QPushButton('Cancel');self.remove=QPushButton('Remove')
        self.run.clicked.connect(self.processRequested);self.cancel.clicked.connect(self.cancelRequested);self.remove.clicked.connect(self.removeRequested)
        row.addWidget(self.run,1);row.addWidget(self.cancel);row.addWidget(self.remove);root.addLayout(row)
        self.load(self.state,None,{},True)

    def _add(self,key,label,widget,tip=''):
        caption=QLabel(label);self.form.addRow(caption,widget);self.controls[key]=widget;self.rows[key]=(caption,widget)
        widget.setAccessibleName(label);widget.setToolTip(tip);caption.setToolTip(tip)
        signal=(widget.editingFinished if isinstance(widget,QLineEdit) else widget.currentIndexChanged if isinstance(widget,QComboBox) else widget.toggled if isinstance(widget,QCheckBox) else widget.valueChanged)
        signal.connect(self._changed)

    def _number(self,key,label,lo,hi,integer=False,suffix='',tip=''):
        w=QSpinBox() if integer else QDoubleSpinBox();w.setRange(lo,hi);w.setKeyboardTracking(False);w.setSuffix(suffix)
        if not integer:w.setDecimals(2);w.setSingleStep(.05)
        self._add(key,label,w,tip)
    def _text(self,key,label):self._add(key,label,QLineEdit())
    def _check(self,key,label):self._add(key,label,QCheckBox())
    def _combo(self,key,label,choices):
        w=QComboBox()
        for text,value in choices:w.addItem(text,value)
        self._add(key,label,w)

    def _changed(self,*args):
        if self.loading:return
        state=deepcopy(self.state)
        if self.sender() in (self.controls['search_width'],self.controls['search_height']):
            state['search_area_manual']=True
        elif self.sender() is self.controls['search_area_multiplier']:
            state['search_area_manual']=False
        for key,w in self.controls.items():
            value=w.text() if isinstance(w,QLineEdit) else w.currentData() if isinstance(w,QComboBox) else w.isChecked() if isinstance(w,QCheckBox) else w.value()
            if key.startswith('tumbling.'):state.setdefault('tumbling',{})[key.split('.')[1]]=value
            else:state[key]=value
        state['tpl_rng']=(state.pop('tpl_width'),state.pop('tpl_height'))
        state['search_area']=(state.pop('search_width'),state.pop('search_height'))
        if state['tracking_method']==TUMBLING_TRACKING_METHOD:state['tumbling']['confidence']=state['acceptable_score']
        self.state=state;self._visibility();self.changed.emit(deepcopy(state))

    def _accuracy_changed(self,*args):
        if self.loading:return
        key=self.accuracy.currentData()
        if key not in EDGE_ACCURACY_PRESETS:return
        self.loading=True
        for field,value in EDGE_ACCURACY_PRESETS[key]['settings'].items():self.controls[field].setValue(value)
        self.loading=False;self._changed()

    def load(self,state,active,tracks,pending=False):
        self.loading=True;self.active=active;self.state=deepcopy(state)
        self.objects.clear()
        if pending or active is None:self.objects.addItem('New object · choose a point',None)
        for key,track in tracks.items():self.objects.addItem(track['name'],key)
        if active is not None and not pending:self.objects.setCurrentIndex(self.objects.findData(active))
        defaults=setup_defaults(state['tracking_method'])
        for key,w in self.controls.items():
            if key.startswith('tumbling.'):value=state.get('tumbling',defaults['tumbling']).get(key.split('.')[1],defaults['tumbling'][key.split('.')[1]])
            elif key in ('tpl_width','tpl_height'):value=state['tpl_rng'][key=='tpl_height']
            elif key in ('search_width','search_height'):value=state['search_area'][key=='search_height']
            else:value=state.get(key,defaults.get(key,''))
            if isinstance(w,QLineEdit):w.setText(str(value))
            elif isinstance(w,QComboBox):w.setCurrentIndex(max(0,w.findData(str(value).lower() if key=='subpixel_type' else value)))
            elif isinstance(w,QCheckBox):w.setChecked(bool(value))
            else:w.setValue(value)
        for key,b in self.methods.items():b.setChecked(key==state['tracking_method'])
        hints={CLASSIC:'Click a distinctive point. Classic follows its intensity pattern.',
               EDGE:'Click the exact point, then fit the reinforcement box to rigid detail.',
               TUMBLING_TRACKING_METHOD:'Click inside the object. Fit the region around its whole outline with a little background. Preview the mask before tracking.'}
        self.hint.setText(hints[state['tracking_method']]);self.loading=False;self._visibility();self.set_busy(self.busy)
        self.run.setEnabled(not pending and active is not None and not self.busy)

    def _visibility(self):
        method=self.state['tracking_method'];edge=method==EDGE;tumble=method==TUMBLING_TRACKING_METHOD
        self.accuracy.setVisible(edge);self.accuracy_label.setVisible(edge)
        with QSignalBlocker(self.accuracy):self.accuracy.setCurrentIndex(self.accuracy.findData(edge_accuracy_name(self.state)))
        classic_keys={'subpixel_size','subpixel_type','update_template_enable','tpl_score','frames_enable'}
        edge_keys={'smart_frames','rotation_allowed','rotation_range','rotation_step','edge_weight','edge_threshold','adjacent_confidence_weight','search_area_multiplier','smart_miss_limit','anchor_refinement_enabled','position_precision','angle_precision'}
        for key,widgets in self.rows.items():
            visible=(not tumble if key in ('search_width','search_height','search_area_enable') else method==CLASSIC if key in classic_keys else edge if key in edge_keys else tumble if key.startswith('tumbling.') else True)
            for w in widgets:w.setVisible(visible)
        self.controls['tumbling.threshold'].setEnabled(self.state.get('tumbling',{}).get('mode')!='adaptive')
        self.controls['tumbling.iterations'].setEnabled(self.state.get('tumbling',{}).get('mode')=='adaptive')
        self.preview.setVisible(tumble)

    def set_busy(self,busy):
        self.busy=busy;self.scroll.setEnabled(not busy);self.objects.setEnabled(not busy)
        for b in self.methods.values():b.setEnabled(not busy)
        self.run.setEnabled(not busy and self.active is not None);self.cancel.setEnabled(True)
        self.preview.setEnabled(not busy and self.active is not None);self.anchor.setEnabled(not busy and self.active is not None)
        self.remove.setEnabled(not busy and self.active is not None)
