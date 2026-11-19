"""Dedicated, undoable junction editing with a live speed-curve preview."""
from copy import deepcopy
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QComboBox, QDoubleSpinBox, QSpinBox, QSlider, QPushButton, QDialogButtonBox)
from viewer_timeline import Timeline, ease_value, move_cut, copy_index, copy_offset, is_video_join, total_frames


class TransitionPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.a, self.b, self.phase, self.duration, self.curve = 30, 30, 0, 0, 'smooth'
        self.setAccessibleName('Transition speed curve; dashed line marks the cut')

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(16, 25, max(1, self.width() - 32), max(1, self.height() - 55))
        cut = box.left() + box.width() * self.phase
        p.fillRect(box, QColor('#233b47'))
        p.fillRect(QRectF(box.left(), box.top(), box.width() * self.phase, box.height()), QColor('#44334e'))
        p.setPen(QPen(QColor('#f4db9d'), 1, Qt.PenStyle.DashLine))
        p.drawLine(int(cut), int(box.top() - 5), int(cut), int(box.bottom() + 5))
        p.setPen(QColor('#f4db9d'))
        p.drawText(QRectF(max(0, min(self.width() - 45, cut - 20)), 3, 45, 18), Qt.AlignmentFlag.AlignCenter, 'CUT')
        low, high = min(self.a, self.b), max(self.a, self.b)
        spread = max(high - low, high * .2, 1)
        path = QPainterPath()
        for i in range(101):
            u = i / 100
            rate = self.a + (self.b - self.a) * ease_value(self.curve, u)
            if self.duration == 0:
                rate = self.a if u < self.phase else self.b
            y = box.bottom() - 12 - (rate - low) / spread * (box.height() - 24)
            if high == low: y = box.center().y()
            x = box.left() + u * box.width()
            if i == 0: path.moveTo(x, y)
            else: path.lineTo(x, y)
        p.setPen(QPen(QColor('#ffdf8c'), 2.5)); p.drawPath(path)
        p.setPen(QColor('#eeeeee'))
        p.drawText(QRectF(16, box.bottom() + 8, box.width(), 20), Qt.AlignmentFlag.AlignLeft, '0 s')
        p.drawText(QRectF(16, box.bottom() + 8, box.width(), 20), Qt.AlignmentFlag.AlignRight, f'{self.duration:g} s')
        p.end()


class TransitionDialog(QDialog):
    def __init__(self, parent, edit, right_index, first_frame=0):
        super().__init__(parent)
        self.original = deepcopy(edit)
        self.right_index = right_index
        ci=copy_index(edit,edit['sections'][right_index]['start'])
        offset=copy_offset(edit,ci)
        first_frame=edit['copies'][ci].get('first_frame',first_frame)
        self.first_frame=first_frame-offset
        first_frame=self.first_frame
        join=is_video_join(edit,right_index)
        self.left_index = next(i for i in range(right_index - 1, -1, -1) if edit['sections'][i]['keep'])
        left, right = edit['sections'][self.left_index], edit['sections'][right_index]
        self.setWindowTitle(f'Transition · Section {self.left_index + 1} → {right_index + 1}')
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        heading = QLabel(f'New video join · Video {ci} → Video {ci+1}' if join else f'Section {self.left_index+1} → Section {right_index+1}')
        if join:self.setWindowTitle(f'New video join · Video {ci} → Video {ci+1}')
        heading.setWordWrap(True); heading.setStyleSheet('font-weight: bold; font-size: 14px;')
        layout.addWidget(heading)
        form = QFormLayout()
        self.cut_frame = QSpinBox()
        self.cut_frame.setKeyboardTracking(False)
        self.cut_frame.setGroupSeparatorShown(True)
        lower = edit['sections'][right_index - 1]['start'] + 1 + first_frame
        upper = right['end'] + first_frame
        if join:lower=upper=right['start']+first_frame
        self.cut_frame.setRange(lower, upper)
        self.cut_frame.setEnabled(not join)
        self.cut_frame.setValue(right['start'] + first_frame)
        self.cut_frame.setToolTip('The first frame of the right section, using the Cine’s frame numbers. Each section keeps at least one frame.')
        self.cut_slider = QSlider(Qt.Orientation.Horizontal)
        self.cut_slider.setRange(lower, upper)
        self.cut_slider.setEnabled(not join)
        self.cut_slider.setValue(self.cut_frame.value())
        self.cut_slider.setAccessibleName('Cut at frame')
        cut_row = QHBoxLayout()
        cut_row.addWidget(self.cut_frame); cut_row.addWidget(self.cut_slider, 1)
        form.addRow('Cut at frame', cut_row)
        self.cut_frame.valueChanged.connect(self.cut_slider.setValue)
        self.cut_slider.valueChanged.connect(self.cut_frame.setValue)
        self.placement = QComboBox()
        for label, key in [('On left clip', 'left'), ('On right clip', 'right'), ('Half on each side', 'center')]:
            self.placement.addItem(label, key)
        self.placement.setCurrentIndex(self.placement.findData(right['ramp_place']))
        form.addRow('Placement', self.placement)
        self.duration = QDoubleSpinBox(); self.duration.setRange(0, 3600); self.duration.setDecimals(2)
        self.duration.setSuffix(' s'); self.duration.setSingleStep(.1); self.duration.setSpecialValueText('Cut · no ramp')
        self.duration.setValue(right['ramp'])
        form.addRow('Duration', self.duration)
        self.curve = QComboBox()
        for label, key in [('Smooth · ease in & out', 'smooth'), ('Linear', 'linear'), ('Ease in', 'ease_in'), ('Ease out', 'ease_out')]:
            self.curve.addItem(label, key)
        self.curve.setCurrentIndex(self.curve.findData(right['ramp_curve']))
        form.addRow('Speed curve', self.curve)
        self.from_fps, self.to_fps = QDoubleSpinBox(), QDoubleSpinBox()
        for spin, section in [(self.from_fps, left), (self.to_fps, right)]:
            spin.setRange(.1, 10_000_000); spin.setDecimals(2); spin.setSuffix(' fps'); spin.setValue(section['fps'])
        form.addRow('Left playback', self.from_fps); form.addRow('Right playback', self.to_fps)
        layout.addLayout(form)
        note = QLabel(('This join connects the end of one video to the start of the next. Its boundary stays fixed; speed effects work on both sides.\n\n' if join else '')+'Duration is in finished-video seconds. Half on each side places half of that time before the cut and half after it.')
        note.setWordWrap(True); layout.addWidget(note)
        self.preview = TransitionPreview(); layout.addWidget(self.preview)
        self.summary = QLabel(); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.remove_button = QPushButton('Remove Transition')
        row.addWidget(self.remove_button); row.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons); layout.addLayout(row)
        self.remove_button.clicked.connect(self._remove)
        for spin in (self.cut_frame, self.duration, self.from_fps, self.to_fps): spin.valueChanged.connect(self._refresh)
        self.placement.currentIndexChanged.connect(self._refresh); self.curve.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def result_edit(self):
        result = move_cut(self.original, self.right_index, self.cut_frame.value() - self.first_frame)
        result['sections'][self.left_index]['fps'] = self.from_fps.value()
        result['sections'][self.right_index].update(fps=self.to_fps.value(), ramp=self.duration.value(),
            ramp_place=self.placement.currentData(), ramp_curve=self.curve.currentData())
        return result

    def _refresh(self):
        edit = self.result_edit()
        plan = Timeline(edit, 0, edit['sections'][-1]['end'])
        transition = next((t for t in plan.transitions if t['index'] == self.right_index), None)
        phase = {'left': 1, 'right': 0, 'center': .5}[self.placement.currentData()]
        actual = transition['duration'] if transition else 0
        self.preview.a, self.preview.b = self.from_fps.value(), self.to_fps.value()
        self.preview.duration, self.preview.phase, self.preview.curve = actual, phase, self.curve.currentData()
        self.preview.update()
        before = f'{actual * phase:,.3f}'.rstrip('0').rstrip('.')
        after = f'{actual * (1 - phase):,.3f}'.rstrip('0').rstrip('.')
        text = f'{before} s before cut · {after} s after cut'
        if actual + 1e-8 < self.duration.value():
            text += f'\nShortened to {actual:,.3f} s to fit the footage and neighboring transitions.'
        if self.from_fps.value() == self.to_fps.value():
            text += '\nBoth clips have the same playback FPS; there is no speed change.'
        elif actual == 0:
            text += '\nPlayback FPS changes immediately at the cut.'
        self.summary.setText(text)

    def _remove(self):
        self.duration.setValue(0)
        self.accept()
