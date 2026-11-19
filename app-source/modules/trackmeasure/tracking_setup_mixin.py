"""PCA-only bridge between visible setup controls and independent engines."""
from copy import deepcopy
import numpy as np
from PySide6.QtCore import Qt,QPointF,QRectF,QSignalBlocker,QTimer
from PySide6.QtGui import QColor,QPen,QPainterPath,QBrush
from PySide6.QtWidgets import QMessageBox
from tracking_setup_ui import setup_defaults,CLASSIC,EDGE,EDGE_ACCURACY_PRESETS
from tumbling_tracker import TUMBLING_TRACKING_METHOD,TrackerSettings
from tumbling_pca_adapter import setup_tracker


class TrackingSetupMixin:
    def _tracking_busy(self):
        worker=getattr(self,'_tracking_thread',None)
        return worker is not None and worker.is_alive()

    def _connect_tracking_setup(self):
        panel=self.tracking_setup
        panel.methodChosen.connect(self._start_tracking_method)
        panel.changed.connect(self._save_tracking_setup)
        panel.objectChosen.connect(self._select_tracking_object)
        panel.processRequested.connect(lambda:self._complete_guided_tracking() if getattr(self,'_guide',None) else self.process_autotrack('Process'))
        panel.cancelRequested.connect(self._cancel_tracking_setup)
        panel.previewRequested.connect(self._preview_tumbling_mask)
        panel.anchorRequested.connect(self._go_to_tracking_anchor)
        panel.removeRequested.connect(self._remove_tracking_object)
        panel.helpRequested.connect(self._show_tracking_help)
        self.vm.new_track_data.connect(self._sync_tracking_setup)
        for signal in (self.vm.new_cine_load,self.vm.cine_activated):
            signal.connect(lambda *_:QTimer.singleShot(0,self,self._precision_source_changed))
        self.vm.update_progress.connect(panel.progress.setValue)
        self.vm.track_complete.connect(lambda *args:QTimer.singleShot(30,self,self._tracking_finished_ui))
        self.track_type_bg.buttonClicked.connect(self._sync_tracking_setup)

    def _begin_tracking_setup(self,method):
        self._tracking_editor_open=True
        self._pending_tracking_settings=setup_defaults(method,int(self.first_fr),int(self.last_fr))
        if method==EDGE:
            preset=getattr(self,'_chosen_edge_accuracy','normal')
            self._pending_tracking_settings.update(EDGE_ACCURACY_PRESETS.get(preset,EDGE_ACCURACY_PRESETS['normal'])['settings'])
        self._tumbling_preview=None
        self.tracking_setup.load(self._pending_tracking_settings,None,self.vm.track_data,True)
        if hasattr(self,'_begin_guided_tracking'):
            self._begin_guided_tracking(method)
            return
        self.main_tab.setCurrentIndex(2);self.track_pages.setTabVisible(0,True)
        self.track_pages.setCurrentIndex(0);self._ensure_details_panel_open(force=True)

    def _cancel_tracking_setup(self):
        if getattr(self,'_guide',None):self._cancel_guided_setup();return
        if self._tracking_busy():
            self.process_autotrack('Cancel')
            return
        self._cancel_new_object_creation()
        self._tracking_editor_open=False
        self.track_pages.setCurrentIndex(1);self.track_pages.setTabVisible(0,False)
        self._collapse_details()
        if getattr(self,'_layout_ready',False):self._sync_video_layout()

    def _start_tracking_method(self,method):
        if self.vm is None or not self.cine_path or self._tracking_busy():return
        if getattr(self,'_guide',None):self._cancel_guided_setup()
        self._stop_playback();self._cancel_new_object_creation(message='')
        if self.vm.track_type!='Auto':self.track_type_auto.click()
        self.vm.add_new_template_cb()
        blocker=QSignalBlocker(self.add_object_button);self.add_object_button.setChecked(True);del blocker
        if method==CLASSIC:self.begin_classic_point_selection(self.graph)
        elif method==EDGE:self.begin_hybrid_region_selection(self.graph)
        else:self.begin_tumbling_selection(self.graph)

    def begin_tumbling_selection(self,graph):
        self._hybrid_selection_graph=None;self.vm.pending_tracking_method=TUMBLING_TRACKING_METHOD
        self._begin_tracking_setup(TUMBLING_TRACKING_METHOD)
        self.vm.update_status_text.emit('Tumbling: set the region size, then click inside the whole object. The green mask will show its measured silhouette.')
        QTimer.singleShot(0,self,self.vm.redraw_cb)

    def _sync_tracking_setup(self,*args):
        if self.vm is None or not hasattr(self,'tracking_setup'):return
        pending=getattr(self.vm,'pending_tracking_method',None)
        active=self.vm.active_object;track=self.vm.track_data.get(active)
        if pending and getattr(self,'_pending_tracking_settings',None):
            state=self._pending_tracking_settings;active=None
        elif track:
            state=setup_defaults(track.get('tracking_method',CLASSIC),int(self.first_fr),int(self.last_fr));state.update({k:deepcopy(track[k]) for k in state if k in track})
            if state['tracking_method']==TUMBLING_TRACKING_METHOD:state['acceptable_score']=state.get('tumbling',{}).get('confidence',.6)
        else:state=setup_defaults(CLASSIC,int(self.first_fr),int(self.last_fr));active=None
        self.tracking_setup.load(state,active,self.vm.track_data,bool(pending))
        guided=bool(getattr(self,'_guide',None))
        for widget in (*self.tracking_setup.methods.values(),self.tracking_setup.objects,self.tracking_setup.run,self.tracking_setup.remove,self.tracking_setup.progress):widget.setVisible(not guided)
        self.tracking_setup.cancel.setText('Cancel setup' if guided else 'Cancel')
        # The stacked widget, not a refresh callback, controls page visibility.
        if self.vm.track_type != 'Auto':self.track_pages.setCurrentIndex(1)
        if track and not pending and track.get('tracking_method')==TUMBLING_TRACKING_METHOD:
            observations=track.get('tumbling_observations',{})
            accepted=sum(r['accepted'] for r in observations.values());rejected=len(observations)-accepted
            self.tracking_setup.status.setText('Settings changed · rerun to update measurements.' if track.get('tumbling_dirty') else f'Projected area centroid · {accepted} measured / {rejected} rejected. Angle is not measured.')

    def _normalized_tracking_settings(self,state):
        result={key:deepcopy(state[key]) for key in setup_defaults(state['tracking_method']) if key in state}
        result['tpl_rng']=tuple(self._odd_dimension(v,9) for v in state['tpl_rng'])
        result['search_area']=tuple(self._odd_dimension(v,11) for v in state['search_area'])
        if state['tracking_method']==EDGE:
            if not state.get('search_area_manual',False):
                result['search_area']=self._hybrid_search_dimensions(result['tpl_rng'],result['search_area_multiplier'],result['rotation_allowed'])
            result['update_template_enable']=False
        if state['tracking_method']==TUMBLING_TRACKING_METHOD:
            result['tumbling']=TrackerSettings.from_dict(state['tumbling']).to_dict()
            result.update(rotation_allowed=False,update_template_enable=False,smart_frames=False,frames_enable=True)
        return result

    def _save_tracking_setup(self,state):
        if self._tracking_busy():return
        settings=self._normalized_tracking_settings(state)
        if getattr(self.vm,'pending_tracking_method',None):
            self._pending_tracking_settings=settings;return
        track=self.vm.track_data.get(self.vm.active_object)
        if not track:return
        method=track.get('tracking_method',CLASSIC)
        if method!=settings['tracking_method']:return
        threshold_changed=track.get('acceptable_score')!=settings['acceptable_score']
        if not settings['name'].strip():settings['name']=track['name']
        track.update(settings)
        if method==TUMBLING_TRACKING_METHOD:track['tumbling_dirty']=True;self._tumbling_preview=None
        elif method==EDGE and threshold_changed:
            from simplemeas_tools import TrackTool
            if TrackTool.apply_hybrid_threshold(track):self.vm.track_fig_calculations(obj=track)
        if getattr(self,'_guide',None):
            self._fit_track_search(track);self._sync_guide()
        self.vm.new_track_data.emit(self.vm.track_data,self.vm.active_object);self.vm.redraw_cb()

    def _apply_new_tracking_settings(self,track):
        pending=getattr(self,'_pending_tracking_settings',None)
        if pending and pending['tracking_method']==track['tracking_method']:
            settings=self._normalized_tracking_settings(pending)
            if not settings['name'].strip():settings['name']=track['name']
            track.update(settings)
        self._pending_tracking_settings=None
        self._sync_tracking_setup()

    def create_tumbling_track_at(self,position):
        if self._tracking_busy():return
        seed=(float(position.x()),float(position.y()))
        settings=self._normalized_tracking_settings(self._pending_tracking_settings)
        proposed=dict(settings,anchor_frame=int(self.vm.active_frame),tumbling_seed=seed,template_offset=(0.,0.))
        try:_,result=setup_tracker(self.vm,proposed)
        except Exception as error:
            self.tracking_setup.status.setText(str(error));self.vm.update_status_text.emit(str(error));return
        self.vm.pending_tracking_method=TUMBLING_TRACKING_METHOD
        try:self.vm.graph_click_cb(result.centroid)
        finally:self.vm.pending_tracking_method=None
        track=self.vm.track_data[self.vm.active_object]
        self._apply_new_tracking_settings(track)
        track.update(tumbling_seed=seed,template_offset=(0.,0.),measurement_kind='Projected silhouette centroid',
                     tumbling_observations={int(self.vm.active_frame):result.record()},tumbling_dirty=False)
        track['angles'][:]=np.nan;track['t_angles'][:]=np.nan
        self.add_object_button.setChecked(False);self._tumbling_preview=None
        self.vm.track_fig_calculations(obj=track)
        self.vm.new_track_data.emit(self.vm.track_data,self.vm.active_object);self.vm.redraw_cb()

    def _preview_tumbling_mask(self):
        track=self.vm.track_data.get(self.vm.active_object)
        if not track or track.get('tracking_method')!=TUMBLING_TRACKING_METHOD or self._tracking_busy():return
        self._go_to_tracking_anchor()
        try:
            _,result=setup_tracker(self.vm,track)
            self._tumbling_preview=(self.vm.active_object,result.record())
            self.tracking_setup.status.setText(f'Mask: {result.area:,} px² · centroid ({result.centroid[0]:.1f}, {result.centroid[1]:.1f}). Track object to apply settings.')
        except Exception as error:
            self._tumbling_preview=None;self.tracking_setup.status.setText(str(error))
        self.vm.redraw_cb()

    def _draw_tumbling_roi(self,track,object_id,kind):
        if kind!='template':return
        graph=self._graph_widget(self.vm._graph);frame=int(self.vm.active_frame)
        record=track.get('tumbling_observations',{}).get(frame)
        preview=getattr(self,'_tumbling_preview',None)
        if preview and preview[0]==object_id and preview[1]['frame']==frame:record=preview[1]
        if record:
            # One odd-even path preserves background holes inside the silhouette.
            path=QPainterPath();path.setFillRule(Qt.FillRule.OddEvenFill)
            for contour in record.get('contours',[]):
                if not contour:continue
                path.moveTo(QPointF(*contour[0]))
                for point in contour[1:]:path.lineTo(QPointF(*point))
                path.closeSubpath()
            color=QColor('#72d7ae' if record['accepted'] else '#e5b966')
            pen=QPen(color,1.5);pen.setCosmetic(True);color.setAlpha(45)
            item=graph.scene().addPath(path,pen,QBrush(color));item.setData(0,f'tumbling_mask_{object_id}')
            item.setZValue(12)
        if frame==track.get('anchor_frame') and object_id==self.vm.active_object:
            from simplemeas_ui import HybridRegionItem
            center=np.asarray(track['tumbling_seed'])+np.asarray(track.get('template_offset',(0,0)))
            w,h=track['tpl_rng'];box=HybridRegionItem(QRectF(0,0,w,h),center,0,object_id,self,editable=not self._tracking_busy())
            box.setData(0,f'tumbling_region_{object_id}')
            if box.rotate_handle:box.rotate_handle.hide()
            if box.scale_handle:box.scale_handle.setBrush(QBrush(QColor('#4a9278')))
            pen=QPen(QColor('#72d7ae'),1.5);pen.setCosmetic(True);box.setPen(pen);graph.scene().addItem(box)

    def _commit_tumbling_region(self,item,track):
        if self._tracking_busy():return
        track['tpl_rng']=(self._odd_dimension(item.rect().width(),9),self._odd_dimension(item.rect().height(),9))
        track['template_offset']=(item.pos().x()-track['tumbling_seed'][0],item.pos().y()-track['tumbling_seed'][1])
        track['tumbling_dirty']=True;self._tumbling_preview=None
        self._sync_tracking_setup()
        if getattr(self,'_guide',None):self._sync_guide()
        QTimer.singleShot(0,self,self._preview_tumbling_mask)

    def _select_tracking_object(self,object_id):
        if self._tracking_busy() or object_id not in self.vm.track_data:return
        if getattr(self,'_guide',None):return
        self._cancel_new_object_creation(message='')
        self._activate_track_row(self._track_row_for_object(object_id));self._sync_tracking_setup();self.vm.redraw_cb()

    def _go_to_tracking_anchor(self):
        track=self.vm.track_data.get(self.vm.active_object)
        if track:self.frame_slider.setValue(int(track.get('anchor_frame',track['frames'][0])))

    def _remove_tracking_object(self):
        row=self._track_row_for_object(self.vm.active_object)
        if row is not None and QMessageBox.question(self,'Remove object','Remove the selected object and its measurements?')==QMessageBox.Yes:
            self.remove_track_data_points(row,'track');self._sync_tracking_setup()

    def _tracking_finished_ui(self):
        # The engine and worker-finally paths can both notify completion. A
        # late duplicate must not close an inspector the user has just opened.
        if not self.tracking_setup.busy:return
        if self._tracking_busy():QTimer.singleShot(40,self,self._tracking_finished_ui);return
        if getattr(self,'_guide',None):self._finish_guide_layout()
        self.tracking_setup.set_busy(False)
        for widget in (self.track_table,self.track_type_auto,self.track_type_man,self.add_object_button,self.frame_slider):widget.setEnabled(True)
        self.analysis_tools.setEnabled(True)
        self.track_pages.widget(1).setEnabled(True)
        for button in self.transport_buttons:button.setEnabled(True)
        self.tracking_setup.progress.setFormat('Ready');self._sync_tracking_setup()
        self._tracking_editor_open=False
        self.track_pages.setCurrentIndex(1);self.track_pages.setTabVisible(0,False)
        self._collapse_details()
        if getattr(self,'_layout_ready',False):self._sync_video_layout()
