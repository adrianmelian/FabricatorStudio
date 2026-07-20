"""Toolbar-owned AI bridge — start/stop/is_running singleton.

The wire protocol (protocol.py) and the socket/QTimer service (service.py)
are both maya-free at module scope by design, so the offscreen test suite
(_dev/test_ai_bridge.py) can exercise their framing and threading logic
under plain mayapy with no maya.standalone.init() and no QApplication. This
module is the other side of that line: it DOES import handlers.py (and,
transitively, maya) — that is fine, because start() is only ever called
from inside a running Maya session (wired into toolbar_app.launch()/
teardown() in Task 6), and the offscreen suite never imports this module.
"""
__author__ = "Adrian Melian"

import threading

_SERVICE = None

# Guards the start()/stop() lifecycle transition on _SERVICE ONLY — the
# check-then-assign in start() and the grab-then-clear in stop(). Both
# critical sections below are quick (BridgeService.start() itself is
# fast: bind + spawn threads, non-blocking); the one call that DOES block
# (BridgeService.stop(), which joins the accept thread and closes live
# connections) is always made with the lock already released — see
# stop()'s own comment for exactly where.
_LOCK = threading.Lock()


def start(port: int | None = None) -> int:
    """Start the bridge (idempotent — returns the already-bound port if
    one is running). port=None resolves the port from the toolbar prefs'
    'connect_ai' entry (DEFAULT_PORT if that key or the whole prefs entry
    is absent — toolbar_prefs.load_prefs() passes unknown keys through
    unchanged, so 'connect_ai' works before any prefs-schema change lands).
    Pass an explicit port to override prefs. Returns the bound port."""
    global _SERVICE
    with _LOCK:
        if _SERVICE is not None and _SERVICE.is_running():
            return _SERVICE.port

        # Lazy, runtime-only imports: handlers.py pulls in maya.cmds, so
        # this branch must never execute at module import time or from
        # the offscreen suite — only when a live Maya session calls
        # start().
        from maya_tools.framework.toolbar import toolbar_prefs
        from maya_tools.framework.toolbar.ai_bridge import handlers, protocol, service

        if port is None:
            prefs = toolbar_prefs.load_prefs()
            connect_ai = prefs.get('connect_ai') or {}
            port = int(connect_ai.get('port', protocol.DEFAULT_PORT))

        svc = service.BridgeService(dispatch=handlers.dispatch, port=port)
        svc.start()          # fast (bind + spawn threads) — fine under the lock
        _SERVICE = svc
        return svc.port


def stop() -> None:
    """Idempotent — safe to call even if the bridge was never started."""
    global _SERVICE
    with _LOCK:
        svc = _SERVICE
        _SERVICE = None
    if svc is not None:
        svc.stop()  # OUTSIDE the lock: joins the accept thread and closes
                    # live connections — never call this while holding _LOCK


def is_running() -> bool:
    return _SERVICE is not None and _SERVICE.is_running()


def current_port() -> int | None:
    return _SERVICE.port if is_running() else None
