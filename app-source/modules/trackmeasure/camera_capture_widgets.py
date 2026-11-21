"""Compact preset editors and time labels used by the Camera Control workspace."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractSpinBox, QDoubleSpinBox, QHBoxLayout, QMenu,
                               QSizePolicy, QToolButton, QWidget)


def number(value):
    return f'{value:,.3f}'.rstrip('0').rstrip('.')


def duration(seconds):
    if seconds == 0:
        return '0 s'
    if seconds < .001:
        return f'{number(seconds * 1_000_000)} µs'
    if seconds < 1:
        return f'{number(seconds * 1000)} ms'
    if seconds < 60:
        return f'{number(seconds)} s'
    minutes, rest = divmod(round(seconds, 2), 60)
    return f'{int(minutes)} min {number(rest)} s'


def geometry_label(mode):
    width, height, binning = mode
    return f'{width:,} × {height:,}' + (f' · {binning}×{binning} binning' if binning > 1 else '')


class CaptureNumber(QDoubleSpinBox):
    def textFromValue(self, value):
        # Retain the locale's decimal and grouping separators, with no redundant zeros.
        text = super().textFromValue(value)
        separator = self.locale().decimalPoint()
        if separator in text:
            text = text.rstrip('0').removesuffix(separator)
        return text


def preset_field(name, suffix, populate):
    editor = CaptureNumber()
    editor.setAccessibleName(name)
    editor.setSuffix(suffix)
    editor.setDecimals(3)
    editor.setRange(.001, 1e9)
    editor.setGroupSeparatorShown(True)
    editor.setButtonSymbols(QAbstractSpinBox.NoButtons)
    editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    presets = QToolButton()
    presets.setAccessibleName(f'{name} presets')
    presets.setToolTip(f'Choose {name.lower()} or type an exact value in the field.')
    presets.setArrowType(Qt.DownArrow)
    presets.setFixedWidth(30)
    presets.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    presets.setPopupMode(QToolButton.InstantPopup)
    presets.setStyleSheet('QToolButton::menu-indicator { width: 0px; }')
    menu = QMenu(presets)
    menu.aboutToShow.connect(lambda: populate(menu))
    presets.setMenu(menu)

    field = QWidget()
    layout = QHBoxLayout(field)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(editor, 1)
    layout.addWidget(presets)
    return editor, presets, field


def add_custom(menu, editor):
    menu.addSeparator()

    def select():
        editor.setFocus(Qt.OtherFocusReason)
        editor.selectAll()

    menu.addAction('Enter exact value…', select)


TRIGGER_STYLE = """
QSlider::groove:horizontal { height: 7px; border-radius: 3px; background: #404b57; }
QSlider::sub-page:horizontal { background: #3caa84; border-radius: 3px; }
QSlider::add-page:horizontal { background: #d46a6d; border-radius: 3px; }
QSlider::handle:horizontal { width: 10px; margin: -4px 0; border: 1px solid #f0f4f7;
                            background: #e4e9f0; border-radius: 4px; }
"""
