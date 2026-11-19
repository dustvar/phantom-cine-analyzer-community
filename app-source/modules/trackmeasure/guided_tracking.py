"""Guided, reversible setup for PCA tracking adapters."""
from copy import deepcopy
import math
import numpy as np
from PySide6.QtCore import Qt,QPointF,QRectF,QTimer,QSignalBlocker
from PySide6.QtGui import QColor,QPen,QKeySequence,QShortcut,QCursor
from PySide6.QtWidgets import (QFrame,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,
    QToolButton,QComboBox,QStackedWidget,QWidget,QSpinBox,QDoubleSpinBox,QLineEdit,QSizePolicy,QProgressBar,
    QApplication,QAbstractSpinBox,QAbstractSlider,QTextEdit,QPlainTextEdit)
from tracking_setup_ui import CLASSIC,EDGE,setup_defaults,EDGE_ACCURACY_PRESETS,edge_accuracy_name
from tumbling_tracker import TUMBLING_TRACKING_METHOD
from tracking_geometry import rotate,pose_at,search_pose,contain_region
from tracking_precision import PointPickerOverlay,image_step_for_arrow


def number(low,high,value=0,decimals=None):
    widget=QSpinBox() if decimals is None else QDoubleSpinBox()
    if decimals is not None:widget.setDecimals(decimals)
    widget.setRange(low,high);widget.setValue(value);widget.setKeyboardTracking(False)
    return widget


class TrackingGuide(QFrame):
    def __init__(self,owner):
        super().__init__();self.loading=False;self.setObjectName('tracking_guide')
        root=QVBoxLayout(self);root.setContentsMargins(8,6,8,6);root.setSpacing(4)
        row=QHBoxLayout();self.title=QLabel();self.title.setWordWrap(True);self.title.setObjectName('guide_title');row.addWidget(self.title,1)
        self.anchor=QPushButton();self.anchor.clicked.connect(owner._go_to_tracking_anchor);self.anchor.setToolTip('Return to the setup frame to edit the tracking geometry');row.addWidget(self.anchor)
        self.settings=QToolButton();self.settings.setText('Settings…');self.settings.clicked.connect(owner._guide_settings)
        row.addWidget(self.settings)
        self.back=QPushButton('Back');self.back.clicked.connect(lambda:owner._guide_step(-1));row.addWidget(self.back)
        self.next=QPushButton('Next');self.next.clicked.connect(lambda:owner._guide_step(1));row.addWidget(self.next)
        self.cancel=QPushButton('Cancel');self.cancel.clicked.connect(owner._cancel_guided_setup);row.addWidget(self.cancel)
        root.addLayout(row)
        self.hint=QLabel();self.hint.setWordWrap(True);root.addWidget(self.hint)
        self.pages=QStackedWidget();root.addWidget(self.pages)
        page=QWidget();row=QHBoxLayout(page);row.setContentsMargins(0,0,0,0)
        self.target=QComboBox();self.target.addItem('Select point + region (recommended)','point');self.target.addItem('Draw region · use its center','region_center')
        self.target.currentIndexChanged.connect(owner._guide_target_mode);row.addWidget(self.target)
        row.addStretch();self.pages.addWidget(page)
        self.edge_accuracy=QComboBox();self.edge_accuracy.setAccessibleName('Edge Assist accuracy')
        for key,settings in EDGE_ACCURACY_PRESETS.items():self.edge_accuracy.addItem(settings['label'],key)
        self.edge_accuracy.currentIndexChanged.connect(owner._guide_accuracy_changed)
        row.addWidget(self.edge_accuracy)
        page=QWidget();column=QVBoxLayout(page);column.setContentsMargins(0,0,0,0);column.setSpacing(4)
        row=QHBoxLayout();column.addLayout(row)
        self.roi_w=number(9,8191,31);self.roi_h=number(9,8191,31);self.roi_angle=number(-180,180,0,1)
        for widget in (self.roi_w,self.roi_h,self.roi_angle):widget.setFixedWidth(100)
        for label,widget in [('Width',self.roi_w),('Height',self.roi_h),('Angle',self.roi_angle)]:
            caption=QLabel(label);row.addWidget(caption);row.addWidget(widget);widget.valueChanged.connect(owner._guide_roi_values)
            if widget is self.roi_angle:self.roi_angle_label=caption
        row=QHBoxLayout();column.addLayout(row)
        self.pick_point=QPushButton('Choose tracked point');self.pick_point.clicked.connect(lambda:owner._guide_pick('point'));row.addWidget(self.pick_point)
        self.use_center=QPushButton('Use region center');self.use_center.clicked.connect(owner._guide_use_center);row.addWidget(self.use_center)
        self.preview_mask=QPushButton('Preview silhouette mask');self.preview_mask.clicked.connect(owner._preview_tumbling_mask);row.addWidget(self.preview_mask)
        self.pages.addWidget(page)
        page=QWidget();column=QVBoxLayout(page);column.setContentsMargins(0,0,0,0)
        self.rigid_search=QWidget();rows=QVBoxLayout(self.rigid_search);rows.setContentsMargins(0,0,0,0);rows.setSpacing(4);column.addWidget(self.rigid_search)
        row=QHBoxLayout();rows.addLayout(row)
        self.search_w=number(11,32767,101);self.search_h=number(11,32767,101);self.search_angle=number(-180,180,0,1)
        for widget in (self.search_w,self.search_h,self.search_angle):widget.setFixedWidth(100)
        for label,widget in [('Width',self.search_w),('Height',self.search_h),('Angle',self.search_angle)]:
            row.addWidget(QLabel(label));row.addWidget(widget);widget.valueChanged.connect(owner._guide_search_values)
        row=QHBoxLayout();rows.addLayout(row)
        self.fit=QPushButton('Fit around region');self.fit.clicked.connect(owner._guide_fit_search);row.addWidget(self.fit)
        self.motion=QComboBox();self.motion.addItem('Free X/Y motion','xy');self.motion.addItem('Vertical only','y');self.motion.addItem('Horizontal only','x')
        self.motion.currentIndexChanged.connect(owner._guide_motion);row.addWidget(self.motion)
        self.adaptive_search=QWidget();row=QHBoxLayout(self.adaptive_search);row.setContentsMargins(0,0,0,0);column.addWidget(self.adaptive_search)
        self.max_motion=number(1,2000,50);self.growth=number(1,200,10)
        for label,widget in [('Max motion / frame',self.max_motion),('Contour growth',self.growth)]:
            widget.setSuffix(' px');row.addWidget(QLabel(label));row.addWidget(widget);widget.valueChanged.connect(owner._guide_adaptive_search)
        self.pages.addWidget(page)
        page=QWidget();grid=QGridLayout(page);grid.setContentsMargins(0,0,0,0);grid.setSpacing(5)
        self.name=QLineEdit();self.first=number(-2_000_000_000,2_000_000_000);self.last=number(-2_000_000_000,2_000_000_000)
        self.confidence=number(0,1,.4,2)
        for widget in (self.first,self.last,self.confidence):widget.setMinimumWidth(70);widget.setMaximumWidth(140)
        self.name.setMinimumWidth(0);self.name.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
        for col,(label,widget) in enumerate([('Name',self.name),('First frame',self.first),('Last frame',self.last),('Confidence',self.confidence)]):
            grid.addWidget(QLabel(label),col//2,(col%2)*2);grid.addWidget(widget,col//2,(col%2)*2+1)
        self.reference=QComboBox()
        for label,value in [('First tracked point','first'),('Object at frame','frame'),('Fixed image pixel','pixel')]:self.reference.addItem(label,value)
        self.ref_frame=number(-2_000_000_000,2_000_000_000)
        self.ref_x=number(0,32767,0,2);self.ref_y=number(0,32767,0,2)
        self.pick_reference=QPushButton('Pick reference pixel');self.pick_reference.clicked.connect(lambda:owner._guide_pick('reference'))
        grid.addWidget(QLabel('Displacement zero'),2,0);grid.addWidget(self.reference,2,1,1,3)
        reference_row=QHBoxLayout();reference_row.addWidget(self.ref_frame);reference_row.addWidget(self.ref_x);reference_row.addWidget(self.ref_y);reference_row.addWidget(self.pick_reference)
        grid.addLayout(reference_row,3,0,1,4)
        self.reference.currentIndexChanged.connect(self.reference_visibility)
        self.pages.addWidget(page);self.reference_visibility()

    def fit_page(self):
        page=self.pages.currentWidget();page.ensurePolished();page.layout().invalidate();page.layout().activate()
        self.pages.setFixedHeight(page.sizeHint().height())
        self.layout().invalidate();self.layout().activate()
        height=self.layout().totalHeightForWidth(self.width())
        self.setMinimumHeight(height if height>=0 else self.layout().sizeHint().height())
        self.updateGeometry()
        if self.parentWidget():self.parentWidget().updateGeometry()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        QTimer.singleShot(0,self,self.fit_page)

    def reference_visibility(self):
        mode=self.reference.currentData();self.ref_frame.setVisible(mode=='frame')
        for widget in (self.ref_x,self.ref_y,self.pick_reference):widget.setVisible(mode=='pixel')
        self.ref_x.setPrefix('X ');self.ref_y.setPrefix('Y ')
        self.ref_x.setToolTip('Fixed raw image X pixel; never follows the object')
        self.ref_y.setToolTip('Fixed raw image Y pixel; never follows the object')


class GuidedTrackingMixin:
    def _install_tracking_guide(self):
        self._guide=None;self._guide_press=None;self._guide_preview=None
        self._point_picker=None;self._point_candidate=None;self._point_keyboard=False
        self.tracking_guide=TrackingGuide(self);self.tracking_guide.hide()
        grid=self.cine_disp_col.layout();item=grid.itemAtPosition(1,0)
        workspace=item.layout();grid.removeItem(workspace)
        grid.setRowStretch(1,0);grid.addLayout(workspace,2,0,1,-1);grid.setRowStretch(2,1)
        self.setup_area=QWidget();area=QVBoxLayout(self.setup_area);area.setContentsMargins(0,0,0,0);area.setSpacing(0)
        area.addWidget(self.tracking_guide)
        self.run_panel=QFrame();row=QHBoxLayout(self.run_panel);row.setContentsMargins(8,4,8,4)
        row.addWidget(QLabel('Tracking'));self.run_progress=QProgressBar();self.run_progress.setRange(0,100);row.addWidget(self.run_progress,1)
        self.run_cancel=QPushButton('Cancel tracking');self.run_cancel.clicked.connect(lambda:self.process_autotrack('Cancel'));row.addWidget(self.run_cancel)
        self.run_panel.hide();area.addWidget(self.run_panel);self.setup_area.hide()
        grid.addWidget(self.setup_area,1,0,1,-1)
        self.guide_escape=QShortcut(QKeySequence('Escape'),self);self.guide_escape.setEnabled(False)
        self.guide_escape.activated.connect(self._guide_cancel_capture)
        self.frame_slider.valueChanged.connect(lambda *_:QTimer.singleShot(0,self,self._refresh_point_picker))
        self.analysis_tools_bg.buttonClicked.connect(lambda *_:QTimer.singleShot(0,self,self._sync_point_picker))
        self.scale_tool.clicked.connect(lambda *_:QTimer.singleShot(0,self,self._focus_calibration_picker))

    def _focus_calibration_picker(self):
        self._sync_point_picker()
        if self._precision_capture_kind()=='calibration':
            self.graph.setFocus(Qt.OtherFocusReason)
            self.vm.update_status_text.emit('Choose two calibration points. Arrows / keypad: 1 px; Shift: 0.1 px. Click or Enter to confirm each point.')

    def _begin_guided_tracking(self,method,object_id=None):
        if self._guide:self._cancel_guided_setup()
        if self.vm.track_type!='Auto':self.track_type_auto.click()
        track=self.vm.track_data.get(object_id)
        self._guide=dict(method=method,object_id=object_id,new=track is None,
            backup=deepcopy(track),previous_object=self.vm.active_object,
            step=1 if track else 0,capture=None,graphs=self._graphs_open,
            hidden=set(self.hidden_cine_indices),details=self._details_open,
            details_index=self.main_tab.currentIndex())
        self._stop_playback();self._collapse_details();self.path_fade_panel.hide()
        self._show_edge_detection=True;self.path_show_edge_detection.setChecked(True)
        self._set_pan_mode(False)
        self.hidden_cine_indices=set(range(len(self.vm.workspace_contexts)))-{self.vm.active_cine_index}
        self._configure_workspace_grid(len(self.vm.workspace_contexts))
        self.tracking_guide.show();self.guide_escape.setEnabled(True)
        if track:self._go_to_tracking_anchor()
        else:
            self.tracking_guide.loading=True;self.tracking_guide.target.setCurrentIndex(0);self.tracking_guide.loading=False
        self._sync_guide();self._sync_video_layout();self.graph.setFocus(Qt.OtherFocusReason)

    def _guide_track(self):
        return self.vm.track_data.get(self._guide['object_id']) if self._guide else None

    def _guide_target_mode(self,*args):
        if not self._guide or self.tracking_guide.loading:return
        self._guide['capture']=None;self._sync_guide();self.graph.setFocus(Qt.OtherFocusReason)

    def _sync_guide(self,*args):
        if not self._guide:return
        g=self._guide;panel=self.tracking_guide;track=self._guide_track();step=g['step'];tumbling=g['method']==TUMBLING_TRACKING_METHOD
        panel.loading=True
        panel.pages.setCurrentIndex(step)
        panel.title.setText(f"{step+1}/4 · {('Target','Region','Search','Review')[step]}")
        panel.title.setToolTip('Tumbling' if tumbling else 'Edge Assist' if g['method']==EDGE else 'Classic')
        panel.anchor.setVisible(track is not None)
        if track:panel.anchor.setText(f"Frame {int(track['anchor_frame'])+int(self.first_fr):,} ↩")
        hints=[
            'Move over a distinctive point. Arrows move 1 pixel; Shift+arrows move 0.1 pixel. Click or Enter to select, or choose Draw region.',
            'Fit the pink region to useful detail. Drag it or its handles. The point may be inside or outside the region; its center is also available.',
            'Move, resize or rotate the yellow search area. It expands to contain the pink region on release. A straight edge may need Vertical or Horizontal only.',
            'Check the range and optional fixed displacement reference, then Complete & Track. Motion plots offer Local fit or raw derivatives.'
        ]
        if tumbling:
            hints[0]='Choose a point inside the object or draw its whole region. Arrows: 1 px; Shift+arrows: 0.1 px; Enter: select. Tracking measures the silhouette centroid.'
            hints[1]='Fit the green region around the whole object and check its mask in Settings. The measured point is the silhouette centroid.'
            hints[2]='Set the largest movement between frames and space for the silhouette to change shape. The search follows the predicted object.'
        panel.hint.setText(hints[step]);panel.back.setEnabled(step>0)
        panel.next.setText('Complete & Track' if step==3 else 'Next →')
        panel.next.setEnabled(track is not None)
        panel.target.setItemText(0,'Click object · silhouette centroid' if tumbling else 'Select point + region (recommended)')
        panel.target.setItemText(1,'Draw whole object region' if tumbling else 'Draw region · use its center')
        panel.edge_accuracy.setVisible(g['method']==EDGE)
        state=track or getattr(self,'_pending_tracking_settings',{}) or {}
        preset=edge_accuracy_name(state)
        if panel.edge_accuracy.findData('custom')<0:panel.edge_accuracy.addItem('Custom settings','custom')
        panel.edge_accuracy.setCurrentIndex(panel.edge_accuracy.findData(preset))
        panel.edge_accuracy.setToolTip('Finer position/angle grids take longer, especially when rotation is enabled. Settings remain editable; resolution is not guaranteed measurement accuracy.')
        panel.pick_point.setVisible(not tumbling);panel.use_center.setVisible(not tumbling);panel.preview_mask.setVisible(tumbling)
        panel.roi_angle.setVisible(not tumbling);panel.roi_angle_label.setVisible(not tumbling)
        panel.roi_angle.setEnabled(g['method']==EDGE)
        panel.rigid_search.setVisible(not tumbling);panel.adaptive_search.setVisible(tumbling)
        if track:
            for widget,value in [(panel.roi_w,track['tpl_rng'][0]),(panel.roi_h,track['tpl_rng'][1]),(panel.roi_angle,-track.get('template_angle',0)),
                (panel.search_w,track['search_area'][0]),(panel.search_h,track['search_area'][1]),(panel.search_angle,track.get('search_angle',0))]:widget.setValue(value)
            panel.motion.setCurrentIndex(max(0,panel.motion.findData(track.get('motion_axis','xy'))))
            if tumbling:
                panel.max_motion.setValue(track['tumbling']['max_motion']);panel.growth.setValue(track['tumbling']['growth'])
        panel.loading=False
        panel.fit_page();QTimer.singleShot(0,panel,panel.fit_page)
        self._sync_point_picker()
        self.graph.viewport().update()

    def _guide_step(self,delta):
        if not self._guide:return
        if delta>0 and self._guide['step']==3:self._complete_guided_tracking();return
        if delta>0 and not self._guide_track():return
        if self._guide['step']==3:self._guide_store_review()
        self._guide['step']=max(0,min(3,self._guide['step']+delta));self._guide['capture']=None
        self._collapse_details();self._go_to_tracking_anchor()
        if self._guide['step']==3:self._guide_load_review()
        self._sync_guide();self._sync_video_layout()

    def _guide_accuracy_changed(self,*args):
        if not self._guide or self.tracking_guide.loading or self._guide['method']!=EDGE:return
        key=self.tracking_guide.edge_accuracy.currentData()
        if key not in EDGE_ACCURACY_PRESETS:return
        state=deepcopy(self._guide_track() or self._pending_tracking_settings)
        state.update(EDGE_ACCURACY_PRESETS[key]['settings'])
        self._save_tracking_setup(state)
        self._sync_tracking_setup();self._sync_guide()

    def _guide_load_review(self):
        track=self._guide_track();p=self.tracking_guide
        if not track:return
        p.name.setText(track['name']);p.first.setValue(track['start']);p.last.setValue(track['end']);p.confidence.setValue(track['acceptable_score'])
        mode='pixel' if track.get('reference_pixel') is not None else 'frame' if track.get('origin_frame') is not None else 'first'
        p.reference.setCurrentIndex(p.reference.findData(mode));p.ref_frame.setRange(int(self.first_fr),int(self.last_fr))
        p.ref_frame.setValue(int(self.first_fr)+int(track.get('origin_frame') if track.get('origin_frame') is not None else track.get('anchor_frame',track['frames'][0])))
        meta=self.vm.cine_handler.metadata;p.ref_x.setRange(0,meta.ImWidth-1);p.ref_y.setRange(0,meta.ImHeight-1)
        point=track.get('reference_pixel') or tuple(track['points'][0]);p.ref_x.setValue(point[0]);p.ref_y.setValue(point[1])
        draft=self._guide.get('review')
        if draft:
            p.name.setText(draft['name']);p.first.setValue(draft['first']);p.last.setValue(draft['last']);p.confidence.setValue(draft['confidence'])
            p.reference.setCurrentIndex(p.reference.findData(draft['mode']));p.ref_frame.setValue(draft['frame']);p.ref_x.setValue(draft['x']);p.ref_y.setValue(draft['y'])

    def _guide_store_review(self):
        p=self.tracking_guide
        self._guide['review']=dict(name=p.name.text(),first=p.first.value(),last=p.last.value(),confidence=p.confidence.value(),mode=p.reference.currentData(),frame=p.ref_frame.value(),x=p.ref_x.value(),y=p.ref_y.value())

    def _guide_settings(self):
        if not self._guide:return
        self._tracking_editor_open=True;self.track_pages.setTabVisible(0,True);self.track_pages.setCurrentIndex(0)
        self._sync_tracking_setup();self.main_tab.setCurrentIndex(2);self._ensure_details_panel_open(force=True)

    def _guide_pick(self,kind):
        if not self._guide:return
        self._guide['capture']=kind;self._stop_playback();self._collapse_details();self._go_to_tracking_anchor()
        self.tracking_guide.hint.setText(('Choose a fixed reference pixel; it will not follow anything. ' if kind=='reference' else 'Choose the measured point; the pink region stays in place. ')+
            'Arrows: 1 px. Shift+arrows: 0.1 px. Click or Enter to confirm.')
        self._sync_point_picker();self.graph.setFocus(Qt.OtherFocusReason)

    def _guide_cancel_capture(self):
        if self._guide and self._guide.get('capture'):
            self._guide['capture']=None;self._sync_guide()
        elif self._guide:self._cancel_guided_setup()

    def _guide_capture_kind(self):
        if not self._guide:return None
        if self._guide.get('capture'):return self._guide['capture']
        if self._guide['step']==0:return 'region' if self.tracking_guide.target.currentData()=='region_center' else 'target'
        return None

    def _sync_point_picker(self):
        active=self._precision_capture_kind() in ('target','point','reference','calibration')
        if not active:
            if self._point_picker is not None:self._point_picker.hide()
            self._point_candidate=None;self._point_keyboard=False
            self.graph.viewport().unsetCursor()
            return
        if self._point_picker is None or self._point_picker.graph is not self.graph:
            if self._point_picker is not None:self._point_picker.hide();self._point_picker.deleteLater()
            self._point_picker=PointPickerOverlay(self.graph)
            self._point_candidate=None;self._point_keyboard=False
        if self._point_candidate is None:
            local=self.graph.viewport().mapFromGlobal(QCursor.pos())
            point=self.graph.mapToScene(local)
            if not self.graph.viewport().rect().contains(local) or not self.graph._within_bounds(point):
                point=self.graph.mapToScene(self.graph.viewport().rect().center())
            self._set_point_candidate(point)
        self._point_picker.show_point(self._point_candidate)

    def _precision_source_changed(self):
        self._point_candidate=None;self._point_keyboard=False;self._guide_press=None
        self._sync_point_picker()

    def _precision_capture_kind(self):
        kind=self._guide_capture_kind()
        if kind:return kind
        if (getattr(self,'current_tool',None) is getattr(self,'scale_tool',None)
                and getattr(self,'cine_path','') and not getattr(self,'_calibration_confirming',False)):
            from simplemeas_tools import ScaleTool
            if isinstance(getattr(self.vm,'active_tool',None),ScaleTool):return 'calibration'
        return None

    def _refresh_point_picker(self):
        picker=getattr(self,'_point_picker',None)
        if picker is not None and picker.isVisible():picker.update()

    def _set_point_candidate(self,point,keyboard=False):
        meta=self.vm.cine_handler.metadata
        point=QPointF(round(float(np.clip(point.x(),0,meta.ImWidth-1)),1),
                     round(float(np.clip(point.y(),0,meta.ImHeight-1)),1))
        self._point_candidate=point;self._point_keyboard=keyboard
        self.graph.mouse_pos=(point.x(),point.y())
        self.graph.viewport().setCursor(Qt.BlankCursor if keyboard else Qt.CrossCursor)
        if self._point_picker is not None:self._point_picker.show_point(point)
        if self._precision_capture_kind()=='calibration':self.graph.update_calibration_preview(point)

    def _guided_wants_key(self,event):
        kind=self._precision_capture_kind()
        if kind not in ('target','point','reference','calibration'):return False
        # Editing a number or name must keep normal text/spinbox keyboard behavior.
        focus=QApplication.focusWidget()
        if kind=='calibration' and (focus is None or (focus is not self.graph and not self.graph.isAncestorOf(focus))):return False
        while focus is not None and focus is not self:
            if isinstance(focus,(QLineEdit,QAbstractSpinBox,QComboBox,QTextEdit,QPlainTextEdit,QAbstractSlider)):
                return False
            focus=focus.parentWidget()
        if event.modifiers() & Qt.AltModifier:return False
        return (event.key() in (Qt.Key_Left,Qt.Key_Right,Qt.Key_Up,Qt.Key_Down,Qt.Key_Return,Qt.Key_Enter)
                or bool(event.modifiers() & Qt.KeypadModifier) and event.key() in (Qt.Key_2,Qt.Key_4,Qt.Key_6,Qt.Key_8))

    def _guided_key_press(self,event):
        if not self._guided_wants_key(event):return False
        kind=self._precision_capture_kind()
        directions={Qt.Key_Left:(-1,0),Qt.Key_Right:(1,0),Qt.Key_Up:(0,-1),Qt.Key_Down:(0,1)}
        if event.modifiers() & Qt.KeypadModifier:directions.update({Qt.Key_4:(-1,0),Qt.Key_6:(1,0),Qt.Key_8:(0,-1),Qt.Key_2:(0,1)})
        if event.key() in directions:
            self._sync_point_picker()
            step=.1 if event.modifiers() & Qt.ShiftModifier else 1.
            delta=image_step_for_arrow(self.graph.transform(),*directions[event.key()],step)
            self._set_point_candidate(self._point_candidate+delta,keyboard=True)
            self.graph.ensureVisible(QRectF(self._point_candidate,QPointF(self._point_candidate)+QPointF(1,1)),24,24)
            self._refresh_point_picker();event.accept();return True
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and self._point_candidate is not None:
            guide=self._guide;point=QPointF(self._point_candidate)
            calibration_tool=self.vm.active_tool if kind=='calibration' else None
            self._stop_playback()
            def confirm():
                if (self._guide is guide and self._precision_capture_kind()==kind
                        and (kind!='calibration' or self.vm.active_tool is calibration_tool)):
                    self._precision_pick_done(kind,point)
            QTimer.singleShot(0,self,confirm);event.accept();return True
        return False

    def _guided_mouse_press(self,graph,event):
        kind=self._precision_capture_kind()
        if not kind or graph is not self.graph or event.button()!=Qt.LeftButton:return False
        point=(QPointF(self._point_candidate) if self._point_keyboard and self._point_candidate is not None
               else graph.mapToScene(event.position().toPoint()))
        if not graph._within_bounds(point):return True
        graph.setFocus(Qt.MouseFocusReason)
        self._stop_playback();self._guide_press=(kind,QPointF(point),event.position().toPoint(),False)
        if kind=='region':
            pen=QPen(QColor('#f47efa'),2);pen.setCosmetic(True)
            self._guide_preview=graph.scene().addRect(QRectF(point,point),pen);self._guide_preview.setZValue(50)
        return True

    def _guided_mouse_move(self,graph,event):
        if graph is not self.graph:return False
        if not self._guide_press:
            if self._precision_capture_kind() not in ('target','point','reference','calibration'):return False
            point=graph.mapToScene(event.position().toPoint())
            if graph._within_bounds(point):self._set_point_candidate(point)
            return True
        kind,start,view_start,moved=self._guide_press
        pos=graph.mapToScene(event.position().toPoint());pos.setX(np.clip(pos.x(),0,graph.xMax));pos.setY(np.clip(pos.y(),0,graph.yMax))
        if kind=='region' and self._guide_preview is not None:self._guide_preview.setRect(QRectF(start,pos).normalized())
        elif graph._view_zoom>1 and (event.position().toPoint()-view_start).manhattanLength()>5:
            delta=event.position().toPoint()-view_start
            graph.horizontalScrollBar().setValue(graph.horizontalScrollBar().value()-delta.x());graph.verticalScrollBar().setValue(graph.verticalScrollBar().value()-delta.y())
            self._guide_press=(kind,start,event.position().toPoint(),True);graph.viewport().setCursor(Qt.ClosedHandCursor)
        return True

    def _guided_mouse_release(self,graph,event):
        if not self._guide_press or graph is not self.graph:return False
        kind,start,view_start,moved=self._guide_press;self._guide_press=None
        end=graph.mapToScene(event.position().toPoint())
        end.setX(np.clip(end.x(),0,graph.xMax));end.setY(np.clip(end.y(),0,graph.yMax))
        if self._guide_preview is not None:graph.scene().removeItem(self._guide_preview);self._guide_preview=None
        if kind=='region':
            rect=QRectF(start,end).normalized()
            if rect.width()<9 or rect.height()<9:self.tracking_guide.hint.setText('Draw a region at least 9 × 9 pixels.');return True
            QTimer.singleShot(0,self,lambda:self._guide_target_chosen(rect.center(),rect))
        elif not moved:
            if self._point_keyboard and self._point_candidate is not None:end=QPointF(self._point_candidate)
            self._set_point_candidate(end)
            point=QPointF(self._point_candidate);guide=self._guide
            calibration_tool=self.vm.active_tool if kind=='calibration' else None
            def confirm():
                if (self._guide is guide and self._precision_capture_kind()==kind
                        and (kind!='calibration' or self.vm.active_tool is calibration_tool)):
                    self._precision_pick_done(kind,point)
            QTimer.singleShot(0,self,confirm)
        return True

    def _precision_pick_done(self,kind,point):
        if kind!='calibration':self._guide_pick_done(kind,point);return
        if self._precision_capture_kind()!='calibration':return
        self._calibration_confirming=True
        try:
            self.graph.clear_calibration_preview()
            if self._point_picker is not None:self._point_picker.hide()
            self.vm.graph_click_cb((point.x(),point.y()))
        finally:
            self._calibration_confirming=False
            self._sync_point_picker()
            self.graph.update_calibration_preview(point)

    def _guide_pick_done(self,kind,point):
        if not self._guide:return
        if kind=='reference':
            self.tracking_guide.ref_x.setValue(point.x());self.tracking_guide.ref_y.setValue(point.y());self._guide['capture']=None;self._sync_guide()
        elif kind=='point':
            self._set_guided_point(point);self._guide['capture']=None;self._sync_guide()
        else:self._guide_target_chosen(point)

    def _guide_target_chosen(self,point,rect=None):
        if not self._guide:return
        g=self._guide;method=g['method']
        if self._guide_track():self._set_guided_point(point)
        else:
            before=set(self.vm.track_data)
            self.vm.pending_tracking_method=method
            if method==EDGE:
                self._hybrid_selection_graph=self.graph;self.create_hybrid_track_at(point)
            elif method==CLASSIC:self.create_classic_track_at(point)
            else:
                if rect is not None:self._pending_tracking_settings['tpl_rng']=(self._odd_dimension(rect.width(),9),self._odd_dimension(rect.height(),9))
                self.create_tumbling_track_at(point)
            if self.vm.active_object not in self.vm.track_data or self.vm.active_object in before:
                self.tracking_guide.hint.setText(self.status_bar.text() or 'Choose another region; no object was created.')
                return
            g['object_id']=self.vm.active_object
        track=self._guide_track()
        track.setdefault('motion_filter',{'mode':'local_polynomial','window':21})
        track.update(search_geometry_version=1,search_follow_rotation=method==EDGE,search_angle=0.,motion_axis='xy')
        track['anchor_point']=tuple(track['points'][np.flatnonzero(track['frames']==track['anchor_frame'])[0]])
        if rect is not None and method!=TUMBLING_TRACKING_METHOD:
            track['tpl_rng']=(self._odd_dimension(rect.width(),9),self._odd_dimension(rect.height(),9))
            self._guide_use_center()
        else:track['tracking_target']='centroid' if method==TUMBLING_TRACKING_METHOD else 'point'
        self._fit_track_search(track)
        g['step']=1;g['capture']=None;self._collapse_details();self._sync_guide()
        self.vm.new_track_data.emit(self.vm.track_data,self.vm.active_object);self.vm.redraw_cb();self._sync_video_layout()

    def _set_guided_point(self,point,center_mode=False):
        track=self._guide_track()
        if not track:return
        anchor=int(track['anchor_frame']);pose=pose_at(track,anchor)
        if pose is None:return
        center,angle=pose;point=np.array([point.x(),point.y()])
        for key,frames in [('points','frames'),('t_points','t_frames')]:
            track[key]=np.asarray(track[key],dtype=float)
            for index in np.flatnonzero(track[frames]==anchor):track[key][index]=point
        track['anchor_point']=tuple(point)
        if track['tracking_method']==EDGE:
            track.setdefault('hybrid_candidates',{})[anchor]=dict(point=tuple(point),score='N/A',angle=angle)
        track['template_offset']=tuple(rotate(center-point,angle if track['tracking_method']==EDGE else 0))
        track['tracking_target']='region_center' if center_mode else 'point'
        track['anchor_refinement_enabled']=not center_mode
        self.vm.new_track_data.emit(self.vm.track_data,self.vm.active_object);self.vm.redraw_cb()

    def _guide_use_center(self):
        track=self._guide_track()
        if not track:return
        self._go_to_tracking_anchor();pose=pose_at(track,int(track['anchor_frame']))
        if pose is not None:self._set_guided_point(QPointF(*pose[0]),True);self._sync_guide()

    def _guide_roi_values(self,*args):
        if not self._guide or self.tracking_guide.loading:return
        track=self._guide_track()
        if not track:return
        from tracking_geometry import RegionSnapshot
        center,angle=pose_at(track,int(track['anchor_frame']));p=self.tracking_guide
        geometry=RegionSnapshot(self._guide['object_id'],QRectF(0,0,p.roi_w.value(),p.roi_h.value()),QPointF(*center),p.roi_angle.value())
        self._go_to_tracking_anchor();self.commit_hybrid_region(geometry)
        self._sync_guide()

    def _guide_search_values(self,*args):
        if not self._guide or self.tracking_guide.loading:return
        track=self._guide_track()
        if not track:return
        center,pose=pose_at(track,int(track['anchor_frame']));center,angle,_=search_pose(track,center,pose);p=self.tracking_guide
        size=(p.search_w.value(),p.search_h.value());angle=p.search_angle.value();self._go_to_tracking_anchor()
        self._commit_search_region(self._guide['object_id'],center,size,angle)

    def _guide_adaptive_search(self,*args):
        if not self._guide or self.tracking_guide.loading:return
        track=self._guide_track()
        if track:
            track['tumbling'].update(max_motion=self.tracking_guide.max_motion.value(),growth=self.tracking_guide.growth.value())
            track['tumbling_dirty']=True;self._sync_tracking_setup()

    def _guide_motion(self,*args):
        if self._guide and not self.tracking_guide.loading and self._guide_track():self._guide_track()['motion_axis']=self.tracking_guide.motion.currentData()

    def _guide_fit_search(self):
        track=self._guide_track()
        if not track:return
        self._go_to_tracking_anchor();track['search_offset']=(0.,0.);track['search_angle']=-track.get('template_angle',0)
        track['search_area']=tuple(self._odd_dimension(v*1.5,11) for v in track['tpl_rng']);self._fit_track_search(track);self._sync_guide();self.vm.redraw_cb()

    def _fit_track_search(self,track):
        pose=pose_at(track,int(track.get('anchor_frame',track['frames'][0])))
        if pose is None:return
        center,angle=pose;search,theta,_=search_pose(track,center,angle)
        track['search_area']=contain_region(search,track['search_area'],theta,center,track['tpl_rng'],-angle)
        track['search_area_manual']=True

    def _finish_guide_layout(self):
        g=self._guide
        if not g:return
        if self._guide_preview is not None:
            self.graph.scene().removeItem(self._guide_preview);self._guide_preview=None
        self._pending_tracking_settings=None
        self._guide=None;self._guide_press=None;self.tracking_guide.hide();self.guide_escape.setEnabled(False)
        self._sync_point_picker()
        self.vm.pending_tracking_method=None;self._hybrid_selection_graph=None
        if hasattr(self.vm.active_tool,'track_select'):self.vm.active_tool.track_select=False
        with QSignalBlocker(self.add_object_button):self.add_object_button.setChecked(False)
        self._tracking_editor_open=False;self.track_pages.setCurrentIndex(1);self.track_pages.setTabVisible(0,False)
        self.hidden_cine_indices=g['hidden'];self._configure_workspace_grid(len(self.vm.workspace_contexts));self._collapse_details()
        self._sync_video_layout()

    def _cancel_guided_setup(self):
        if not self._guide:return
        if self._tracking_busy():self.process_autotrack('Cancel');return
        g=self._guide;object_id=g['object_id']
        if object_id is not None:
            if g['new']:self.vm.track_data.pop(object_id,None)
            else:self.vm.track_data[object_id]=g['backup']
        old=g['previous_object'];self.vm.active_object=old if old in self.vm.track_data else next(iter(self.vm.track_data),None)
        self._finish_guide_layout();self._sync_tracking_setup();self.vm.new_track_data.emit(self.vm.track_data,self.vm.active_object);self.vm.redraw_cb()
        self.status_bar.setText('Setup cancelled; previous tracks preserved.')

    def _complete_guided_tracking(self):
        track=self._guide_track()
        if not track:return
        p=self.tracking_guide
        if self._guide['step']!=3:self._guide['step']=3;self._guide_load_review();self._sync_guide();return
        from tracking_geometry import corners
        center,angle=pose_at(track,int(track['anchor_frame']))
        vertices=corners(center,track['tpl_rng'],-angle)
        meta=self.vm.cine_handler.metadata
        if np.any(vertices<0) or np.any(vertices[:,0]>meta.ImWidth-1) or np.any(vertices[:,1]>meta.ImHeight-1):
            p.hint.setText('Move or resize the tracking region so it fits entirely inside the image.');return
        anchor=int(track['anchor_frame'])+int(self.first_fr)
        if not(int(self.first_fr)<=p.first.value()<=anchor<=p.last.value()<=int(self.last_fr)):
            p.hint.setText('The processing range must be inside this Cine and include the setup frame.');return
        track.update(name=p.name.text().strip() or track['name'],start=p.first.value(),end=p.last.value(),acceptable_score=p.confidence.value(),frames_enable=True)
        mode=p.reference.currentData();track['reference_pixel']=(p.ref_x.value(),p.ref_y.value()) if mode=='pixel' else None
        track['origin_frame']=p.ref_frame.value()-int(self.first_fr) if mode=='frame' else None;track['relative_to']=None
        if track['tracking_method']==TUMBLING_TRACKING_METHOD:track['tumbling']['confidence']=p.confidence.value()
        self._fit_track_search(track);self.vm.sync_active_workspace_context()
        self._finish_guide_layout();self._sync_tracking_setup();self.process_autotrack('Process')
