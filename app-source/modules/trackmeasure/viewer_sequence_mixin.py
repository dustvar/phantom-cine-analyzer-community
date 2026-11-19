"""Connect repeated/new videos, crop editing, and text to the native workspace."""
from copy import deepcopy
from collections import OrderedDict
import os
import numpy as np
from PySide6.QtCore import Qt,QPointF,QRectF,QSignalBlocker
from PySide6.QtGui import QColor,QPen,QBrush
from PySide6.QtWidgets import QFileDialog,QMessageBox
from viewer_timeline import (append_video,total_frames,copy_index,copy_options,copy_count,copy_offset,
                             frame_label,zoom_options,section_at)
from viewer_effects import VisualTimeline,apply_effects,crop_rect,default_image_settings
from viewer_media import open_media,media_descriptor,VideoReader
from viewer_track_fade import default_track_fade
from viewer_track_fade_ui import TrackFadeDialog
from viewer_processing import apply_grade,render_cine_image
from video_export import video_frame


class ViewerSequenceMixin:
    def _viewer_frame(self):
        return getattr(self.vm,'_viewer_frame',self.vm.active_frame) if self.vm else 0

    def _viewer_copy(self):
        return copy_index(self.vm.viewer_edit,self._viewer_frame())

    def _current_copy_options(self,edit=None):
        return copy_options(edit or self.vm.viewer_edit,self._viewer_copy())

    def _sync_sequence_range(self):
        if not self._has_viewer_edit():return
        edit=self.vm.viewer_edit;last=total_frames(edit)-1
        self.vm._viewer_frame=min(last,max(0,self._viewer_frame()))
        viewer=self.current_tool==self.viewer_tool
        blocker=QSignalBlocker(self.frame_slider)
        self.frame_slider.setRange(0,last if viewer else self.vm.cine_handler.metadata.ImageCount-1)
        self.frame_slider.setValue(self._viewer_frame() if viewer else self.vm.active_frame)
        del blocker
        selection=edit.get('selection',[0,last])
        blocker=QSignalBlocker(self.clip_range);self.clip_range.setRange(0,last);self.clip_range.setValues(*selection);del blocker
        self.clip_range.setEnabled(last>0)
        self.workspace_clip_ranges[self.vm.active_cine_index]=tuple(selection)
        self.clip_start_label.setText('In: '+frame_label(edit,selection[0],self.first_fr))
        self.clip_end_label.setText('Out: '+frame_label(edit,selection[1],self.first_fr))
        self.first_frame_disp.setText(frame_label(edit,0,self.first_fr) if viewer else str(self.first_fr))
        self.last_frame_disp.setText(frame_label(edit,last,self.first_fr) if viewer else str(self.last_fr))
        self.update_active_frame(self.frame_slider.value())

    def _sequence_frame_changed(self,value):
        if self.current_tool==self.viewer_tool and self._has_viewer_edit():
            value=min(total_frames(self.vm.viewer_edit)-1,max(0,int(value)))
            self.vm._viewer_frame=value
            ci=copy_index(self.vm.viewer_edit,value)
            self._mapping_viewer_frame=True
            try:self.vm.active_frame=value-copy_offset(self.vm.viewer_edit,ci)
            finally:self._mapping_viewer_frame=False
            self.vm.sync_active_workspace_context()
            self._sync_copy_controls()
        else:
            self.vm.active_frame=value

    def _on_vm_frame_changed(self,value):
        if getattr(self,'_mapping_viewer_frame',False):return
        if self.current_tool==self.viewer_tool and self._has_viewer_edit():
            ci=self._viewer_copy();value=copy_offset(self.vm.viewer_edit,ci)+min(value,copy_count(self.vm.viewer_edit,ci)-1)
            self.vm._viewer_frame=value
        self.frame_slider.setValue(value)

    def _sync_copy_controls(self):
        if not self._has_viewer_edit():return
        ci=self._viewer_copy();edit=self.vm.viewer_edit
        blocker=QSignalBlocker(self.video_selector)
        names=[f'Video {i+1} · {copy.get("name",os.path.basename(self.vm.cine_path))}' for i,copy in enumerate(edit['copies'])]
        if [self.video_selector.itemText(i) for i in range(self.video_selector.count())]!=names:
            self.video_selector.clear()
            self.video_selector.addItems(names)
        self.video_selector.setCurrentIndex(ci);del blocker
        options=copy_options(edit,ci)
        grade=options.get('grade',self.vm.viewer_grade)
        if getattr(self,'_last_grade_copy',None)!=(self.vm.active_cine_index,ci,id(grade)):
            self.grade_panel.set_grade(grade,f'Video {ci+1} · Levels & Curves')
            self._last_grade_copy=(self.vm.active_cine_index,ci,id(grade))

    def _select_sequence_video(self,index):
        if index>=0 and self._has_viewer_edit():
            offset=copy_offset(self.vm.viewer_edit,index)
            trim=self.vm.viewer_edit['copies'][index].get('trim',[0,copy_count(self.vm.viewer_edit,index)-1])
            self._timeline_seek(offset+max(0,trim[0]))

    def _media_reader(self,context,ci):
        edit=self.vm.viewer_edit if context is self.vm.workspace_contexts[self.vm.active_cine_index] else context['viewer_edit']
        path=edit['copies'][ci].get('source_path')
        if not path:return context['cine_handler']
        if not hasattr(self,'_sequence_readers'):self._sequence_readers=OrderedDict()
        if path not in self._sequence_readers:
            self._sequence_readers[path]=open_media(path)
            while len(self._sequence_readers)>8:
                _,reader=self._sequence_readers.popitem(last=False);reader.close()
        self._sequence_readers.move_to_end(path)
        return self._sequence_readers[path]

    def _close_sequence_readers(self):
        for reader in getattr(self,'_sequence_readers',{}).values():reader.close()
        if hasattr(self,'_sequence_readers'):self._sequence_readers.clear()

    def _append_sequence_video(self,new=False):
        if not self._has_viewer_edit():return
        self._stop_playback();self.editor_panel._commit_text()
        context=self.vm.workspace_contexts[self.vm.active_cine_index];ci=self._viewer_copy()
        reader=None;info=None
        try:
            if new:
                path,_=QFileDialog.getOpenFileName(self,'Add New Video',os.path.dirname(self.vm.cine_path),
                    'Videos (*.cine *.mp4 *.mov *.avi *.m4v *.mkv);;All files (*)')
                if not path:return
                reader=open_media(path);info=media_descriptor(path,reader)
                reader.get_img(0)  # Do not append a clip whose first frame cannot be decoded.
                settings=default_image_settings(reader.metadata)
            else:
                selected=self._media_reader(context,ci)
                settings=default_image_settings(selected.metadata)
            edit=append_video(self.vm.viewer_edit,settings,self.clip_range.lowerValue(),self.clip_range.upperValue(),info,ci)
            new_start=total_frames(self.vm.viewer_edit)
            self._apply_viewer_edit(edit)
            self._timeline_seek(new_start)
            self.timeline_button.setChecked(True);self.editor_panel.tabs.setCurrentIndex(0)
            self.status_bar.setText(f'Video {len(edit["copies"])} added with fresh settings. Cyan links mark joins between videos.')
        except Exception as error:
            QMessageBox.warning(self,'Could Not Add Video',str(error))
        finally:
            if reader:reader.close()

    def _visual_timeline(self,edit):
        cache=getattr(self,'_visual_plan_cache',None)
        if cache is None or cache[0] is not edit:
            cache=(edit,VisualTimeline(edit));self._visual_plan_cache=cache
        return cache[1]

    def _crop_edit_active(self):
        return (self.current_tool==self.viewer_tool and not self.editor_panel.isHidden()
                and self.editor_panel.tabs.currentIndex()==2 and self.editor_panel.crop_edit_button.isChecked())

    def _crop_edit_changed(self):
        if not self._has_viewer_edit():return
        self._stop_playback()
        self.vm.redraw_cb()

    def _seek_custom_text(self,seconds):
        if not self._has_viewer_edit():return
        visual=self._visual_timeline(self.vm.viewer_edit)
        time=visual.copy_starts[self._viewer_copy()]+seconds
        if not visual.plan.parts or time>=visual.plan.duration:
            self.status_bar.setText('This text starts after the video ends. Move its start earlier to preview it.');return
        frame,_=visual.plan.sample(time);self._timeline_seek(frame)
        plan=self._viewer_timeline()
        self._viewer_preview_time=max(0,time-visual.offset_for(plan));self.vm.redraw_cb()

    def _tracks_for_copy(self, context, edit, index):
        # A repeated pass has no extra path and uses its owning Cine. An added
        # Cine can borrow measurements only from the matching loaded source.
        extra = edit['copies'][index].get('source_path')
        if extra:
            source = os.path.normcase(os.path.realpath(extra))
            context = next((c for c in self.vm.workspace_contexts
                            if os.path.normcase(os.path.realpath(c['cine_path'])) == source), None)
        if context is None:
            return {}
        if context is self.vm.workspace_contexts[self.vm.active_cine_index]:
            return self.vm.track_data
        return context['track_data']

    def _movie_tracks_snapshot(self):
        """Capture only visible measurements needed by enabled reveals."""
        edit = self.vm.viewer_edit
        context = self.vm.workspace_contexts[self.vm.active_cine_index]
        result = {}
        sources = {}
        for index in range(len(edit['copies'])):
            if not copy_options(edit, index).get('track_fade', {}).get('enabled'):
                continue
            tracks = self._tracks_for_copy(context, edit, index)
            identity = id(tracks)
            if identity not in sources:
                sources[identity] = {
                    key: {k: deepcopy(track[k]) for k in ('name', 'color', 'frames', 'points')}
                    for key, track in tracks.items() if track.get('enabled', True) and len(track['frames'])
                }
            result[index] = sources[identity]
        return result

    def _edit_tracking_fade(self):
        if not self._has_viewer_edit():
            return
        self._stop_playback()
        edit = self.vm.viewer_edit
        index = self._viewer_copy()
        local = self._viewer_frame() - copy_offset(edit, index)
        context = self.vm.workspace_contexts[self.vm.active_cine_index]
        source = edit['copies'][index].get('source_path') or context['cine_path']
        origin = edit['copies'][index].get('first_frame', self.vm.cine_handler.metadata.FirstImageNo)
        tracks = self._tracks_for_copy(context, edit, index)
        count = sum(1 for t in tracks.values() if t.get('enabled', True) and len(t['frames']))
        initial = copy_options(edit, index).get('track_fade')
        if initial is None:
            initial = dict(default_track_fade(local), enabled=True)
        dialog = TrackFadeDialog(self, initial, f'Video {index + 1} · {os.path.basename(source)}',
                                 int(origin), copy_count(edit, index), local, count)
        if dialog.exec() == dialog.DialogCode.Accepted:
            updated = deepcopy(edit)
            copy_options(updated, index)['track_fade'] = dialog.options()
            self._apply_viewer_edit(updated)
        dialog.deleteLater()

    def _add_viewer_fps_overlay(self,graph,image):
        graph._fps_rect=None;graph._data_rects={};graph._crop_rect=None
        if self.current_tool!=self.viewer_tool or not self._has_viewer_edit():return image
        index=graph.pane_index;context=self.vm.workspace_contexts[index];active=index==self.vm.active_cine_index
        edit=self.vm.viewer_edit if active else context['viewer_edit']
        frame=self._viewer_frame() if active else context.get('_viewer_frame',context['_active_frame'])
        ci=copy_index(edit,frame);options=copy_options(edit,ci)
        visual=self._visual_timeline(edit)
        plan=self._viewer_timeline() if active else visual.plan
        if not plan.parts:return image
        seconds=self._viewer_preview_time if active else None
        if seconds is None or plan.sample(seconds)[0]!=frame:seconds=plan.time_for_source(frame)
        rate=plan.sample(seconds)[1];absolute=visual.offset_for(plan)+seconds
        prepared = getattr(self, '_prepared_frame', None) if active else None
        if prepared is not None:
            md = prepared.metadata
            if ci:
                image = apply_grade(prepared.image, options['grade'])
        else:
            reader=self._media_reader(context,ci);md=reader.metadata
            if ci:
                settings=options.get('image_settings') or default_image_settings(md)
                key=(id(reader),frame-copy_offset(edit,ci),repr(settings))
                cached=getattr(graph,'_sequence_image_cache',None)
                if cached is None or cached[0]!=key:
                    raw=reader.get_img(key[1])
                    rendered=raw if isinstance(reader,VideoReader) else render_cine_image(raw,md,settings,context['image_tools'])
                    cached=(key,rendered);graph._sequence_image_cache=cached
                image=apply_grade(cached[1],options['grade'])
        show_crop=active and self._crop_edit_active()
        # Convert once, before drawing text, just as the encoder does.
        image=video_frame(image, pad_even=False)
        tracks = self._tracks_for_copy(context, edit, ci) if ci in visual.fade_starts else None
        image,rects,source_rects,crop=apply_effects(image,edit,md,visual,absolute,frame,rate,show_crop,tracks=tracks)
        graph._data_rects={key:QRectF(*rect) for key,rect in rects.items()}
        graph._data_source_rects=source_rects;graph._effect_crop=crop
        graph._fps_rect=graph._data_rects.get('playback')
        if active:
            self.editor_panel.text_seconds=max(0,absolute-visual.copy_starts[ci])
        if show_crop:
            zoom=zoom_options(edit,self.editor_panel.selected);x,y,w,h=crop_rect(zoom)
            graph._crop_rect=QRectF(x*image.shape[1],y*image.shape[0],w*image.shape[1],h*image.shape[0])
        return image

    def _draw_viewer_crop_guides(self,graph):
        rect=getattr(graph,'_crop_rect',None)
        if rect is None:return
        pen=QPen(QColor('#ffdf3d'),2);pen.setCosmetic(True)
        graph.scene().addRect(rect,pen,QBrush(Qt.BrushStyle.NoBrush))
        size=max(6,8/max(.01,graph.transform().m11()))
        for point in (rect.topLeft(),rect.topRight(),rect.bottomLeft(),rect.bottomRight()):
            graph.scene().addRect(QRectF(point.x()-size/2,point.y()-size/2,size,size),pen,QBrush(QColor('#ffdf3d')))

    def _begin_fps_drag(self,graph,position):
        if self.current_tool!=self.viewer_tool or graph is not self.graph or not self._has_viewer_edit():return False
        rect=getattr(graph,'_crop_rect',None)
        if self._crop_edit_active() and rect is not None:
            points=[rect.topLeft(),rect.topRight(),rect.bottomLeft(),rect.bottomRight()]
            nearest=min(range(4),key=lambda i:(points[i]-position).manhattanLength())
            resize=(points[nearest]-position).manhattanLength()<20/max(.01,graph.transform().m11())
            if resize or rect.contains(position):
                self._stop_playback()
                self._zoom_drag_state=(deepcopy(self.vm.viewer_edit),self.editor_panel.selected,position,rect,points[3-nearest] if resize else None)
                return True
            return False
        target=next(((key,r) for key,r in reversed(list(graph._data_rects.items())) if r.contains(position)),None)
        if target is None:return False
        key,rect=target;self._stop_playback();self.editor_panel._commit_text()
        if key.startswith('text:'):
            self.editor_panel.selected_text_id=key[5:];self.editor_panel.tabs.setCurrentIndex(3)
        else:self.editor_panel.set_data_box(key)
        self._overlay_drag_state=(deepcopy(self.vm.viewer_edit),position,key,self._viewer_copy(),graph._data_source_rects[key],graph._effect_crop.copy())
        graph.viewport().setCursor(Qt.CursorShape.ClosedHandCursor);return True

    def _move_fps_drag(self,graph,position):
        zoom_state=getattr(self,'_zoom_drag_state',None)
        if zoom_state is not None and graph is self.graph:
            original,index,start,rect,anchor=zoom_state
            edit=deepcopy(original);zoom=zoom_options(edit,index);zoom['inherit']=False
            if anchor is None:
                center=rect.center()+position-start
                zoom['cx']=center.x()/graph.xMax;zoom['cy']=center.y()/graph.yMax
            else:
                dx,dy=(position.x()-anchor.x())/graph.xMax,(position.y()-anchor.y())/graph.yMax
                size=max(.05,min(1,max(abs(dx),abs(dy))))
                sx=1 if rect.center().x()>anchor.x() else -1;sy=1 if rect.center().y()>anchor.y() else -1
                limitx=(graph.xMax-anchor.x())/graph.xMax if sx>0 else anchor.x()/graph.xMax
                limity=(graph.yMax-anchor.y())/graph.yMax if sy>0 else anchor.y()/graph.yMax
                size=max(.05,min(size,limitx,limity));zoom['factor']=1/size
                zoom['cx']=anchor.x()/graph.xMax+sx*size/2;zoom['cy']=anchor.y()/graph.yMax+sy*size/2
            half=.5/zoom['factor'];zoom['cx']=min(1-half,max(half,zoom['cx']));zoom['cy']=min(1-half,max(half,zoom['cy']))
            edit['sections'][index]['zoom']=zoom;self.vm.viewer_edit=edit
        elif self._overlay_drag_state is not None and graph is self.graph:
            original,start,key,ci,source_rect,crop=self._overlay_drag_state
            edit=deepcopy(original);options=copy_options(edit,ci)
            style=next(t for t in options['texts'] if t['id']==key[5:]) if key.startswith('text:') else options['camera_overlay' if key=='camera' else 'overlay']
            dx,dy=position.x()-start.x(),position.y()-start.y()
            if not style.get('pinned',True):dx*=crop[2];dy*=crop[3]
            style['x']=min(1,max(0,style['x']+dx/max(1,graph.xMax-source_rect[2])))
            style['y']=min(1,max(0,style['y']+dy/max(1,graph.yMax-source_rect[3])))
            self.vm.viewer_edit=edit
        else:return False
        self.vm.sync_active_workspace_context();self._viewer_plan_cache=None
        if not self.grade_timer.isActive():self.grade_timer.start()
        return True

    def _end_fps_drag(self,graph):
        state=getattr(self,'_zoom_drag_state',None) or self._overlay_drag_state
        if state is None:return False
        original=state[0];self._zoom_drag_state=None;self._overlay_drag_state=None
        if original!=self.vm.viewer_edit:
            self.vm._viewer_edit_undo.append(original);self.vm._viewer_edit_undo=self.vm._viewer_edit_undo[-50:]
            self.vm._viewer_edit_redo.clear();self.vm.sync_active_workspace_context()
        graph.viewport().unsetCursor();self._sync_viewer_editor();self.vm.redraw_cb();return True
