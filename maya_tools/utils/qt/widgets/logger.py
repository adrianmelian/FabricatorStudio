# Python/utils/qt/widgets/logger.py
"""LoggerWidget — colored, filterable, append-only log panel.

API:
    log = LoggerWidget()
    log.info('starting')
    log.warning('history found')
    log.error('export failed')
    log.success('wrote: D:/.../foo.fbx')
    log.clear()

Designed to be wrapped in a CollapsibleSection at the bottom of a tool window.
Always also writes to Maya's Script Editor via OpenMaya.MGlobal — supplements,
never replaces, the project's traceback.print_exc() convention.
"""
from collections import deque
from datetime import datetime

from PySide6 import QtCore, QtGui, QtWidgets

import maya.api.OpenMaya as om

from maya_tools.utils.qt.mindmeld import mindmeld_style


_LEVEL_INFO    = 'INFO'
_LEVEL_WARN    = 'WARN'
_LEVEL_ERROR   = 'ERROR'
_LEVEL_SUCCESS = 'SUCCESS'

_COLOR = {
    _LEVEL_INFO:    mindmeld_style.BONE_DIM,    # neutral [INFO]
    _LEVEL_WARN:    mindmeld_style.EMBER,       # ember   [WARN]
    _LEVEL_ERROR:   mindmeld_style.EMBER,       # ember   [ ERR ]
    _LEVEL_SUCCESS: mindmeld_style.PLASMA,      # plasma  [ OK  ]
}

_TAG = {
    _LEVEL_INFO:    '[INFO]',
    _LEVEL_WARN:    '[WARN]',
    _LEVEL_ERROR:   '[ ERR ]',
    _LEVEL_SUCCESS: '[ OK  ]',
}

_BUFFER_CAP = 1000


class LoggerWidget(QtWidgets.QWidget):
    """Colored, filterable log panel."""

    # Emitted on every append. (level: str, message: str)
    # Level is one of the module-level _LEVEL_* constants
    # ('INFO' / 'WARN' / 'ERROR' / 'SUCCESS').
    line_appended = QtCore.Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._buffer: deque = deque(maxlen=_BUFFER_CAP)
        self._show_info    = True
        self._show_warn    = True
        self._show_error   = True

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(8)
        self._cb_info  = QtWidgets.QCheckBox('Info')
        self._cb_warn  = QtWidgets.QCheckBox('Warnings')
        self._cb_error = QtWidgets.QCheckBox('Errors')
        for cb, attr in (
            (self._cb_info,  '_show_info'),
            (self._cb_warn,  '_show_warn'),
            (self._cb_error, '_show_error'),
        ):
            cb.setChecked(True)
            cb.toggled.connect(self._on_filter_toggled)
            toolbar.addWidget(cb)
        toolbar.addStretch()

        self._clear_btn = mindmeld_style.button('Clear', kind='ghost')
        self._clear_btn.setFixedWidth(76)
        self._clear_btn.clicked.connect(self.clear)
        toolbar.addWidget(self._clear_btn)

        self._scroll_btn = mindmeld_style.button('↓', kind='ghost')
        self._scroll_btn.setFixedWidth(32)
        self._scroll_btn.setToolTip('Scroll to bottom')
        self._scroll_btn.clicked.connect(self._scroll_to_bottom)
        toolbar.addWidget(self._scroll_btn)

        layout.addLayout(toolbar)

        self._view = QtWidgets.QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(_BUFFER_CAP)
        font = QtGui.QFont(mindmeld_style.TOKENS['font_body'])
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        self._view.setFont(font)
        layout.addWidget(self._view)

    # ---------- public API ----------

    def info(self, msg: str) -> None:
        self._append(_LEVEL_INFO, msg)
        om.MGlobal.displayInfo(msg)

    def warning(self, msg: str) -> None:
        self._append(_LEVEL_WARN, msg)
        om.MGlobal.displayWarning(msg)

    def error(self, msg: str) -> None:
        self._append(_LEVEL_ERROR, msg)
        om.MGlobal.displayError(msg)

    def success(self, msg: str) -> None:
        self._append(_LEVEL_SUCCESS, msg)
        om.MGlobal.displayInfo(msg)

    def clear(self) -> None:
        self._buffer.clear()
        self._view.clear()

    # ---------- internal ----------

    def _append(self, level: str, msg: str) -> None:
        ts = datetime.now().strftime('%H:%M:%S')
        self._buffer.append((ts, level, msg))
        self.line_appended.emit(level, msg)
        if not self._is_level_visible(level):
            return
        was_at_bottom = self._is_at_bottom()
        html = self._format_line(ts, level, msg)
        self._view.appendHtml(html)
        if was_at_bottom:
            self._scroll_to_bottom()

    def _format_line(self, ts: str, level: str, msg: str) -> str:
        tag_color = _COLOR[level]
        tag = _TAG[level]
        safe_msg = (
            msg.replace('&', '&amp;')
               .replace('<', '&lt;')
               .replace('>', '&gt;')
        )
        # Three-segment colored line: faint timestamp, level-colored tag, bone msg.
        return (
            f'<span style="color: {mindmeld_style.BONE_FAINT};">{ts}</span>'
            f'  '
            f'<span style="color: {tag_color}; font-weight: 500;">{tag}</span>'
            f'  '
            f'<span style="color: {mindmeld_style.BONE};">{safe_msg}</span>'
        )

    def _is_level_visible(self, level: str) -> bool:
        if level == _LEVEL_INFO or level == _LEVEL_SUCCESS:
            return self._show_info
        if level == _LEVEL_WARN:
            return self._show_warn
        if level == _LEVEL_ERROR:
            return self._show_error
        return True

    def _is_at_bottom(self) -> bool:
        bar = self._view.verticalScrollBar()
        return bar.value() == bar.maximum()

    def _scroll_to_bottom(self) -> None:
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_filter_toggled(self, _checked: bool) -> None:
        self._show_info  = self._cb_info.isChecked()
        self._show_warn  = self._cb_warn.isChecked()
        self._show_error = self._cb_error.isChecked()
        self._rerender()

    def _rerender(self) -> None:
        self._view.clear()
        for ts, level, msg in self._buffer:
            if self._is_level_visible(level):
                self._view.appendHtml(self._format_line(ts, level, msg))
        self._scroll_to_bottom()
