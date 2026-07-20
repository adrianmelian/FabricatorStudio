"""DPI scaling for the toolbar. Computed lazily, cached, test-overridable.
(Animo computes at import time; we do not — spec section 3.1 fix d.)
Screen-change refresh (rest of spec fix d) is deferred past Phase A; a
mid-session DPI change keeps the cached scale until Maya restarts."""
from __future__ import annotations

_SCALE: float | None = None


def _compute_scale() -> float:
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is None:
            return 1.0
        screen = app.primaryScreen()
        if screen is None:
            return 1.0
        dots = float(screen.logicalDotsPerInch())
        return max(0.6, min(2.0, dots / 96.0))
    except Exception:
        # Display probe must never break tool startup; unscaled beats dead.
        return 1.0


def get_scale() -> float:
    global _SCALE
    if _SCALE is None:
        _SCALE = _compute_scale()
    return _SCALE


def scaled(value: float) -> int:
    return int(round(value * get_scale()))


def _set_scale_for_tests(value: float) -> None:
    global _SCALE
    _SCALE = float(value)
