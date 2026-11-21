"""Bounded, trigger-synchronized preview of the visible original Cine recordings."""
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, QSignalBlocker, QEvent, Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QStyle

from playback_frames import PlaybackFrameBuffer
from playback_preview import PreparedFrame


def image_settings(context):
    return {name: context['_'+name] for name in
            ('image_brightness', 'image_contrast', 'image_gamma', 'toggle_ftone', 'raw_enabled')}


@dataclass
class GroupFrame:
    master_frame: int
    seconds: float
    panes: dict


class SynchronizedFrames:
    """One independent reader per visible source; no file access on the caller."""
    def __init__(self, buffer_factory=None):
        self.buffers = {}
        self.buffer_factory = buffer_factory or (lambda: PlaybackFrameBuffer(max_bytes=48*1024*1024, max_frames=8))

    @staticmethod
    def key(context):
        return id(context['cine_handler']), context['cine_path']

    def prepare(self, contexts, master, visible, frames):
        keys = {self.key(contexts[index]) for index in visible}
        for key in set(self.buffers)-keys:
            self.buffers.pop(key).close()
        clock = contexts[master].get('capture_clock')
        if clock is None:
            raise ValueError(contexts[master].get('capture_clock_error') or 'Capture timing is unavailable.')
        seconds = [clock.time_at(frame) for frame in frames]
        panes = {}; pending = False
        for index in visible:
            context = contexts[index]; source_clock = context.get('capture_clock')
            if source_clock is None:
                raise ValueError(f'C{index+1}: '+(context.get('capture_clock_error') or 'Capture timing is unavailable.'))
            indices = [source_clock.index_at(second) for second in seconds]
            key = self.key(context)
            if key not in self.buffers:
                self.buffers[key] = self.buffer_factory()
            buffer = self.buffers[key]; settings = image_settings(context)
            buffer.prime(context['cine_handler'])
            wanted = [frame for frame in indices if frame is not None]
            buffer.request(context['cine_path'], wanted, settings)
            selected = indices[0]
            if selected is None:
                panes[index] = None
                continue
            result = buffer.get(context['cine_path'], selected, settings)
            if result is None:
                pending = True
            else:
                panes[index] = (selected, *result, buffer.metadata(context['cine_path'], settings))
        return None if pending else GroupFrame(frames[0], seconds[0], panes)

    def close(self):
        for buffer in self.buffers.values():buffer.close()
        self.buffers.clear()

    def clear(self):
        for buffer in self.buffers.values():buffer.clear()


class GroupPlayback(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner=owner;self.frames=SynchronizedFrames();self.pending=None
        self.publishing=False;self.playing=False;self.last_batch=None;self.last_signature=None
        self.signature=None;self._refresh_signature=None;self._deadline=0.
        self._editor_positions={}
        # Keep one poll deadline while requests are pending. Repeated redraw/
        # restore signals must not postpone the same zero-delay timer forever.
        self.timer=QTimer(self);self.timer.setInterval(5);self.timer.timeout.connect(self._poll)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.refresh_timer=QTimer(self);self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.synchronize)
        self.toggle=QCheckBox('Sync visible');self.toggle.setChecked(True)
        self.toggle.setToolTip('Play and seek visible Cines together using each recording’s trigger as T0. '
                               'The selected Cine controls time. Uses original recordings; turn off for edited-sequence playback.')
        self._toggle_hint=self.toggle.toolTip()
        self.toggle.setAccessibleName('Synchronize visible Cines by trigger time')
        self.toggle.hide();owner.group_playback_toggle=self.toggle
        self.toggle.installEventFilter(self);self._size_toggle()
        owner.seek_actions.layout().insertWidget(0, self.toggle)
        self.toggle.toggled.connect(self._toggled)
        for name in ('timeline_button','fps_button','multiply_button','add_video_button',
                     'split_button','timeline_undo_button','timeline_redo_button'):
            getattr(owner,name).pressed.connect(self._leave_for_edits)
        for pane in owner.workspace_panes:
            label=QLabel(pane.graph.viewport());label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True);label.setAttribute(Qt.WA_TransparentForMouseEvents)
            label.setStyleSheet('background: #151b22; color: #d7e0e8; padding: 12px;')
            label.hide();pane.sync_status=label;pane.sync_frame_index=None
            pane.graph.viewport().installEventFilter(self)
        owner.destroyed.connect(self.frames.close)

    def eventFilter(self,obj,event):
        if obj is self.toggle and event.type() in (QEvent.FontChange,QEvent.StyleChange):self._size_toggle()
        if event.type()==QEvent.Resize:
            for pane in self.owner.workspace_panes:
                if obj is pane.graph.viewport():pane.sync_status.setGeometry(obj.rect())
        return False

    def _size_toggle(self):
        style=self.toggle.style()
        width=(self.toggle.fontMetrics().horizontalAdvance(self.toggle.text())
               +style.pixelMetric(QStyle.PM_IndicatorWidth)
               +style.pixelMetric(QStyle.PM_CheckBoxLabelSpacing)+14)
        self.toggle.setMinimumWidth(width)

    def _leave_for_edits(self):
        if self.enabled():self.toggle.setChecked(False)

    def visible(self):
        vm=self.owner.vm
        return [i for i in range(len(vm.workspace_contexts)) if i not in self.owner.hidden_cine_indices] if vm else []

    def enabled(self):
        w=self.owner
        return (self.toggle.isChecked() and w.vm is not None and len(self.visible())>1
                and all(w.vm.workspace_contexts[i].get('capture_clock') is not None for i in self.visible())
                and 0<=w.vm.active_cine_index<len(w.vm.workspace_contexts)
                and not getattr(w,'_guide',None) and not w._tracking_busy()
                and w.workflow_stack.currentWidget() is w.tab_splitter
                and w.current_tool is not w.scale_tool)

    def _state(self):
        w=self.owner
        return (tuple(SynchronizedFrames.key(c) for c in w.vm.workspace_contexts),
                w.vm.active_cine_index,tuple(self.visible()),id(w.current_tool),self.enabled())

    def schedule(self):
        self.refresh_timer.start(0)

    def synchronize(self):
        w=self.owner
        count=len(w.vm.workspace_contexts) if w.vm else 0
        self.toggle.setVisible(count>1)
        if not count:return
        clocks_ready=all(w.vm.workspace_contexts[i].get('capture_clock') is not None for i in self.visible())
        self.toggle.setEnabled(clocks_ready)
        self.toggle.setToolTip(self._toggle_hint if clocks_ready else
                              'Synchronized playback needs valid Cine capture timestamps or frame-rate metadata. '
                              'Single-Cine playback remains available.')
        state=self._state()
        if state==self._refresh_signature:return
        previous=self._refresh_signature
        self._refresh_signature=state
        if w._playback_active:w._stop_playback()
        else:self.stop()
        if self.enabled():
            self._original_range()
            self.seek(w.vm.active_frame)
            w.status_bar.setText('Sync visible compares original Cines at T0. Turn it off to preview timeline edits.')
        else:
            for pane in w.workspace_panes:pane.sync_status.hide()
            if previous and previous[-1]:
                self._restore_editor_range()
                w.vm.redraw_cb()
        w._sync_transport_fps()

    def _original_range(self):
        w=self.owner;last=int(w.vm.cine_handler.metadata.ImageCount)-1
        with QSignalBlocker(w.frame_slider):
            w.frame_slider.setRange(0,last);w.frame_slider.setValue(min(last,max(0,w.vm.active_frame)))

    def _remember_editor_positions(self):
        for context in self.owner.vm.workspace_contexts:
            if len(context.get('viewer_edit',{}).get('copies',()))>1:
                self._editor_positions.setdefault(SynchronizedFrames.key(context),context.get('_viewer_frame',0))

    def _restore_editor_range(self):
        w=self.owner
        for index,context in enumerate(w.vm.workspace_contexts):
            frame=self._editor_positions.pop(SynchronizedFrames.key(context),None)
            if frame is not None:
                context['_viewer_frame']=frame
                if index==w.vm.active_cine_index:w.vm._viewer_frame=frame
        w._sync_sequence_range()
        w._sequence_frame_changed(w.frame_slider.value())

    def _toggled(self,checked):
        w=self.owner;w._stop_playback();self._refresh_signature=None
        if not checked and w.vm is not None:
            for pane in w.workspace_panes:pane.sync_status.hide()
            self._restore_editor_range();w.vm.redraw_cb()
            w.refresh_workspace_previews()
        self.schedule()

    def start(self,step):
        if not self.enabled():return False
        w=self.owner;w._clear_preview_buffer();self.stop();self._original_range()
        self.refresh_timer.stop();self._refresh_signature=self._state()
        last=int(w.vm.cine_handler.metadata.ImageCount)-1
        current=min(last,max(0,int(w.vm.active_frame)))
        if step>0 and current>=last:current=0
        if step<0 and current<=0:current=last
        w._playback_step=int(step);w._playback_active=True;w._sync_playback_controls(True)
        self.playing=True;self._deadline=time.monotonic()
        self._rate_started=self._deadline;self._rate_count=0
        self.seek(current)
        return True

    def seek(self,frame):
        if not self.enabled() or self.publishing:return False
        w=self.owner;w.vm.sync_active_workspace_context()
        self._remember_editor_positions()
        last=int(w.vm.cine_handler.metadata.ImageCount)-1
        frame=max(0,min(last,int(frame)))
        self.pending=frame;self.signature=self._state()
        if not self.playing:
            for index in self.visible():self._message(index,'Synchronizing…')
        if not self.timer.isActive():self.timer.start()
        return True

    def stop(self):
        w=self.owner;restore_batch=None
        # Pause cancels a pending seek as well as a pending playback tick. Keep
        # the logical cursor on the pixels still shown, never on a cancelled request.
        if (self.pending is not None and self.last_batch is not None and w.vm is not None
                and self.last_signature==self._state()):
            restore_batch=self.last_batch
            frame=self.last_batch.master_frame
            with QSignalBlocker(w.frame_slider):w.frame_slider.setValue(frame)
            w._mapping_viewer_frame=True
            try:w.vm._viewer_frame=frame;w.vm.active_frame=frame
            finally:w._mapping_viewer_frame=False
            w.track_canvas.draw_frame_pos()
        self.timer.stop();self.pending=None;self.playing=False
        self.frames.clear()
        if w.vm is not None:w.update_active_frame(w.frame_slider.value())
        for pane in self.owner.workspace_panes:
            if pane.sync_status.text()=='Synchronizing…':pane.sync_status.hide()
        if restore_batch is not None:
            for index,data in restore_batch.panes.items():
                if data is None:self._message(index,f'No recorded frame\nt = {restore_batch.seconds*1000:+.4f} ms from trigger')

    def _message(self,index,message):
        pane=self.owner.workspace_panes[index];label=pane.sync_status
        label.setGeometry(pane.graph.viewport().rect());label.setText(message);label.show();label.raise_()
        controls=getattr(pane,'view_controls',None)
        if controls is not None:controls.bar.raise_()

    def _poll(self):
        w=self.owner
        if self.pending is None or not self.enabled() or self.signature!=self._state():
            if w._playback_active:w._stop_playback()
            else:self.stop()
            return
        if self.playing and time.monotonic()<self._deadline:return
        frame=self.pending;last=int(w.vm.cine_handler.metadata.ImageCount)-1
        step=w._playback_step if self.playing else 1
        indices=[max(0,min(last,frame+i*step)) for i in range(8)]
        try:
            w.vm.sync_active_workspace_context()
            batch=self.frames.prepare(w.vm.workspace_contexts,w.vm.active_cine_index,self.visible(),indices)
            if batch is None:
                if self.playing:w.active_frame_label.setText('Buffering visible Cines…')
                return
            self._publish(batch)
        except Exception as error:
            w._stop_playback()
            for index in self.visible():self._message(index,'Synchronization unavailable\n'+str(error))
            w.status_bar.setText('Sync stopped: '+str(error)+'. Turn off Sync visible for single-Cine playback.')
            return
        self.pending=None
        if self.playing:
            self._rate_count+=1;elapsed=time.monotonic()-self._rate_started
            if elapsed>=1:
                w.status_bar.setText(f'Display {self._rate_count/elapsed:.1f} fps · {len(batch.panes)} original Cines synced · t = {batch.seconds*1000:+.3f} ms')
                w.status_bar.setToolTip('Actual synchronized preview updates per second. The selected Cine supplies trigger-relative time; other sources use their nearest recorded capture. Hover a Cine for its capture-time difference.')
                self._rate_count=0;self._rate_started=time.monotonic()
            if frame==(last if step>0 else 0):w._stop_playback();return
            self.pending=max(0,min(last,frame+step))
            period=1/max(.1,float(w._analysis_fps))
            self._deadline=max(self._deadline+period,time.monotonic())
        else:
            self.timer.stop()

    def _publish(self,batch):
        w=self.owner;master=w.vm.active_cine_index;self.publishing=True
        try:
            for index,data in batch.panes.items():
                pane=w.workspace_panes[index]
                if data is None:
                    pane.sync_frame_index=None
                    self._message(index,f'No recorded frame\nt = {batch.seconds*1000:+.4f} ms from trigger')
                    continue
                local,raw,image,metadata=data
                context=w.vm.workspace_contexts[index]
                context['_active_frame']=local;context['_viewer_frame']=local
                context['transformed_img']=image
                context['cine_handler'].frame_i=local;context['cine_handler'].img=raw
                pane.sync_status.hide();pane.sync_frame_index=local
                pane.graph._displayed_source_index=local
                capture_seconds=context['capture_clock'].time_at(local)
                pane.graph.setToolTip(f'Frame {local+int(metadata.FirstImageNo):,} · captured t = {capture_seconds*1000:+.4f} ms from trigger\n'
                                     f'Target t = {batch.seconds*1000:+.4f} ms · difference {(capture_seconds-batch.seconds)*1e6:+.2f} µs\n'
                                     +context['capture_clock'].note)
                if index!=master:w._draw_image_to_graph(pane.graph,image,metadata.RealBPP,metadata.CFA)
            local,raw,image,metadata=batch.panes[master]
            w._prepared_frame=PreparedFrame(raw,image,metadata,True)
            w.vm._viewer_frame=local
            w._show_transport_frame(local,force=True)
            w.update_active_frame(local)
            self.last_batch=batch
            self.last_signature=self.signature
        finally:
            self.publishing=False;w._prepared_frame=None
