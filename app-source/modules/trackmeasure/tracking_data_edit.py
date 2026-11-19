"""Delete measured samples without detaching their per-frame metadata."""
from copy import deepcopy
import hashlib
import numpy as np


def tracking_signature(track):
    """Small undo guard covering settings as well as measured arrays."""
    digest = hashlib.sha256()
    def add(value):
        if isinstance(value, np.ndarray):
            digest.update(repr((value.dtype.str,value.shape)).encode())
            if value.dtype.hasobject: add(value.tolist())
            else: digest.update(value.tobytes())
        elif isinstance(value, dict):
            for key in sorted(value,key=repr): add(key); add(value[key])
        elif isinstance(value, (list,tuple)):
            digest.update(type(value).__name__.encode())
            for item in value: add(item)
        else:
            digest.update(repr(value).encode('utf-8'))
        digest.update(b'\0')
    add(track)
    return digest.digest()


def delete_samples(track, frame_ids, frame_range=None):
    """Return an edited copy and removal count; None means remove the object.

    Source frame indices, rather than table rows or camera frame labels, are
    the identity. Range edits also remove rejected candidates in that range,
    so later confidence changes cannot resurrect explicitly deleted data.
    """
    frames = np.asarray(track.get('frames', []), dtype=int)
    removed = {int(frame) for frame in frame_ids}
    if frame_range is not None:
        low, high = sorted(map(int, frame_range))
        keep = (frames < low) | (frames > high)
        is_removed = lambda frame: low <= int(frame) <= high
    else:
        keep = ~np.isin(frames, list(removed))
        is_removed = lambda frame: int(frame) in removed
    count = int(np.count_nonzero(~keep))
    if not count:
        return deepcopy(track), 0
    if not np.any(keep):
        return None, count
    edited = deepcopy(track)
    for key in ('points', 'frames', 'scores', 'angles'):
        if key in track:
            values = np.asarray(track[key])
            if len(values) != len(frames):
                raise ValueError('Tracking '+key+' are not aligned with their frames.')
            edited[key] = values[keep].copy()
    old_rows = np.flatnonzero(keep)
    edited['notes'] = {new: track.get('notes', {})[int(old)]
                       for new, old in enumerate(old_rows)
                       if int(old) in track.get('notes', {})}
    for key in ('hybrid_candidates', 'hybrid_notes_by_frame',
                'confidence_components', 'tumbling_observations'):
        if isinstance(track.get(key), dict):
            edited[key] = {frame: deepcopy(value) for frame, value in track[key].items()
                           if not is_removed(frame)}
    templates = np.asarray(track.get('t_frames', []), dtype=int)
    retain_templates = np.array([not is_removed(frame) for frame in templates], dtype=bool)
    if retain_templates.any():
        for key in ('t_frames', 't_points', 't_angles'):
            if key in track:
                values = np.asarray(track[key])
                if len(values) != len(templates):
                    raise ValueError('Tracking template data are not aligned.')
                edited[key] = values[retain_templates].copy()
    else:
        edited['t_frames'] = edited['frames'][:1].copy()
        edited['t_points'] = edited['points'][:1].copy()
        edited['t_angles'] = np.asarray(edited.get('angles', [0.]))[:1].copy()
    # Preserve a deliberately chosen origin frame. If it was deleted, the
    # motion calculation reports an unavailable reference, rather than silently
    # changing the user's displacement zero to a different measurement.
    return edited, count


class TrackingDataEditMixin:
    def delete_graph_tracking_samples(self, cine_index, object_id, frame_ids, frame_range=None):
        if (self.vm is None or not 0 <= cine_index < len(self.vm.workspace_contexts)
                or self._tracking_busy() or getattr(self, '_guide', None)):
            return False
        if cine_index != self.vm.active_cine_index:
            self.activate_cine_pane(cine_index)
        if object_id not in self.vm.track_data:
            return False
        edited, count = delete_samples(self.vm.track_data[object_id], frame_ids, frame_range)
        if not count:
            return False
        self._stop_playback()
        context = self.vm.workspace_contexts[cine_index]
        self._graph_delete_undo = dict(context=context, object_id=object_id,
            track=deepcopy(self.vm.track_data[object_id]), active=self.vm.active_object,
            relatives={key: object_id for key, value in self.vm.track_data.items()
                       if edited is None and value.get('relative_to') == object_id},
            preview_order=list(self._preview_object_order), suppressed=set(self._preview_suppressed))
        if edited is None:
            del self.vm.track_data[object_id]
            for track in self.vm.track_data.values():
                if track.get('relative_to') == object_id:
                    track['relative_to'] = None
            if self.vm.active_object == object_id:
                self.vm.active_object = next(iter(self.vm.track_data), None)
        else:
            self.vm.track_data[object_id] = edited
        self._graph_delete_undo['after_track'] = edited
        self._finish_graph_data_edit()
        self._graph_delete_undo['after_signature'] = tracking_signature(edited)
        self.vm.update_status_text.emit(f'Deleted {count:,} tracked point(s). Undo is available in the graph.')
        return True

    def undo_graph_tracking_delete(self):
        saved = getattr(self, '_graph_delete_undo', None)
        if (not saved or self.vm is None or self._tracking_busy() or getattr(self, '_guide', None)
                or not 0 <= self.vm.active_cine_index < len(self.vm.workspace_contexts)
                or self.vm.workspace_contexts[self.vm.active_cine_index] is not saved['context']):
            return False
        current = self.vm.track_data.get(saved['object_id'])
        if current is not saved['after_track'] or tracking_signature(current) != saved['after_signature']:
            self._graph_delete_undo = None
            self.vm.update_status_text.emit('Tracking changed after that deletion; its Undo is no longer available.')
            return False
        self._stop_playback()
        self.vm.track_data[saved['object_id']] = saved['track']
        self.vm.track_data = dict(sorted(self.vm.track_data.items()))
        for key, relative in saved['relatives'].items():
            if key in self.vm.track_data and self.vm.track_data[key].get('relative_to') is None:
                self.vm.track_data[key]['relative_to'] = relative
        self.vm.active_object = saved['active']
        self._preview_object_order = saved['preview_order']
        self._preview_suppressed = saved['suppressed']
        self._graph_delete_undo = None
        self._finish_graph_data_edit()
        self.vm.update_status_text.emit('Restored deleted tracking points.')
        return True

    def _finish_graph_data_edit(self):
        from PySide6.QtCore import QSignalBlocker
        with QSignalBlocker(self.vm):
            for track in self.vm.track_data.values():
                self.vm.track_fig_calculations(obj=track)
        if not self.vm.track_data and self.vm.active_tool is not None:
            self.vm.active_tool.track_select = False
            self.vm.pending_tracking_method = None
            self.add_object_button.setChecked(False)
        self.vm.sync_active_workspace_context()
        self.vm.new_track_data.emit(self.vm.track_data, self.vm.active_object)
        self.vm.track_fig_changed.emit(self.vm.track_data)
        self.vm.redraw_cb()
