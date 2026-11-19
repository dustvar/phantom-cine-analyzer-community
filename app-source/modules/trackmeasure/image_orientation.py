"""Quarter-turn presentation without changing sensor pixels or measurements."""
import numpy as np


def rotate_image(image, degrees):
    return np.ascontiguousarray(np.rot90(image, -(degrees // 90))) if degrees else image


def rotate_points(points, degrees, width, height):
    points = np.asarray(points, dtype=float)
    if not degrees:
        return points
    x, y = points[..., 0], points[..., 1]
    if degrees == 90:
        return np.stack((height - 1 - y, x), axis=-1)
    if degrees == 180:
        return np.stack((width - 1 - x, height - 1 - y), axis=-1)
    return np.stack((y, width - 1 - x), axis=-1)


class OrientationMixin:
    def _sync_orientation_controls(self):
        from PySide6.QtCore import QSignalBlocker
        from viewer_timeline import copy_options
        if not hasattr(self, 'image_rotation'):
            return
        available = self._has_viewer_edit()
        self.orientation_controls.setEnabled(available)
        index = self._viewer_copy() if available and self.current_tool == self.viewer_tool else 0
        rotation = copy_options(self.vm.viewer_edit, index).get('rotation', 0) if available else 0
        with QSignalBlocker(self.image_rotation):
            self.image_rotation.setCurrentIndex(self.image_rotation.findData(rotation))

    def _set_image_rotation(self, degrees):
        from copy import deepcopy
        from viewer_timeline import copy_options
        if not self._has_viewer_edit():
            return
        edit = deepcopy(self.vm.viewer_edit)
        index = self._viewer_copy() if self.current_tool == self.viewer_tool else 0
        copy_options(edit, index)['rotation'] = int(degrees) % 360
        self._apply_viewer_edit(edit)
        self._sync_orientation_controls()

    def _turn_image(self, delta):
        self._set_image_rotation((self.image_rotation.currentData() or 0) + delta)

    def _orient_graph(self, graph):
        # Analysis remains in raw sensor coordinates: Qt maps clicks through
        # this transform. Viewer rotates its rendered canvas before annotations.
        context = self.vm.workspace_contexts[graph.pane_index]
        edit = self.vm.viewer_edit if graph.pane_index == self.vm.active_cine_index else context['viewer_edit']
        rotation = edit.get('rotation', 0) if self.current_tool != self.viewer_tool else 0
        if getattr(graph, '_pca_rotation', 0) != rotation:
            graph._pca_rotation = rotation
            graph.resetTransform()
            graph.rotate(rotation)
            graph.reset_view_zoom()
        if graph.pane_index == self.vm.active_cine_index:
            self._sync_orientation_controls()
