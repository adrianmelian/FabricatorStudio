from PySide6 import QtWidgets, QtCore


# The collapsed header (title + latest log line) is capped to this many
# chars TOTAL so a long message clips cleanly instead of overflowing a
# narrow panel's width.
_HEADER_MAX = 80


class CollapsibleSection(QtWidgets.QWidget):
    """A titled toggle section — click the header arrow to expand or collapse the body.

    Usage:
        section = CollapsibleSection('Advanced Options')
        body_layout = QtWidgets.QVBoxLayout()
        body_layout.addWidget(some_widget)
        section.set_body_layout(body_layout)
        parent_layout.addWidget(section)

    Log preview (base feature): if the body contains a widget exposing a
    `line_appended(level, message)` Qt signal (e.g. LoggerWidget), the
    collapsed header automatically shows the latest line —
    `Title : [LEVEL] message`. When expanded the header reverts to the bare
    title (the full log panel is visible). Auto-wired in set_body_layout via
    duck typing, so every log section inherits it with no per-tool code.
    Only the first such widget found in the body is wired; bodies with
    multiple loggers drive the header from the first one.
    """

    def __init__(self, title: str, parent=None, compact: bool = False):
        super().__init__(parent)

        self._title = title
        # Latest log line, stored raw so the header can be re-formatted
        # against the title (and capped) on every refresh. Empty message
        # = no logger attached / nothing logged yet.
        self._last_level = ''
        self._last_message = ''

        self._toggle_btn = QtWidgets.QToolButton()
        self._toggle_btn.setText(title)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(False)
        self._toggle_btn.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self._toggle_btn.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._toggle_btn.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        # Styling lives in Mindmeld QSS — `CollapsibleSection > QToolButton`.
        # compact=True opts into the thinner-padding header variant
        # (`[mindmeld="compact-header"]` rule) for space-constrained stacks.
        # Set before first show so the stylesheet picks it up — no re-polish.
        if compact:
            self._toggle_btn.setProperty('mindmeld', 'compact-header')

        self._body = QtWidgets.QWidget()
        self._body.setVisible(False)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._toggle_btn)
        layout.addWidget(self._body)

        self._toggle_btn.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool) -> None:
        self._body.setVisible(checked)
        self._toggle_btn.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
        )
        self._refresh_header_text()

    def set_body_layout(self, layout: QtWidgets.QLayout) -> None:
        """Set the layout for the collapsible body, then auto-wire any
        logger inside it to the collapsed-header preview."""
        self._body.setLayout(layout)
        self._autowire_logger()

    def is_expanded(self) -> bool:
        return self._toggle_btn.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self._toggle_btn.setChecked(expanded)

    # ---------- log preview (base feature) ----------

    def _autowire_logger(self) -> None:
        """Connect the first body descendant exposing a `line_appended`
        signal to the collapsed-header preview. Duck-typed so this shared
        widget needn't import LoggerWidget; no-op when none is present."""
        for child in self._body.findChildren(QtWidgets.QWidget):
            sig = getattr(child, 'line_appended', None)
            if sig is None:
                continue
            try:
                sig.connect(self._on_logger_line)
            except (TypeError, RuntimeError):
                continue
            break

    def _on_logger_line(self, level: str, message: str) -> None:
        self._last_level = level
        self._last_message = message
        self._refresh_header_text()

    def _refresh_header_text(self) -> None:
        # Latest line shows only when collapsed; the expanded panel already
        # shows the full log, so the header reverts to the bare title. The
        # whole header (title + line) is capped to _HEADER_MAX so a long
        # message clips cleanly rather than overflowing a narrow panel.
        if self._last_message and not self._toggle_btn.isChecked():
            prefix = f'{self._title} : [{self._last_level}] '
            budget = max(12, _HEADER_MAX - len(prefix))
            msg = self._last_message
            if len(msg) > budget:
                msg = msg[:budget - 3] + '...'
            self._toggle_btn.setText(prefix + msg)
        else:
            self._toggle_btn.setText(self._title)
