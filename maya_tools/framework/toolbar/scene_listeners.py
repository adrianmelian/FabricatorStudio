"""Scene-event listener registry for the toolbar (Phase C).

om2 MMessage callbacks ONLY - the repo's standardized idiom (five
precedents; scriptJob ids live in a killable global table and fire on
idle; MEventMessage removal is deterministic by opaque id). Rules,
inherited from the pose_library/curve_o_matic/LiveBackend precedents:
- callback bodies NEVER raise (Maya crash risk): every body is wrapped
  here so subscribers don't have to remember;
- stop() paths are idempotent; every removeCallback sits in
  try/except RuntimeError ("callback may already be removed");
- ALL state lives in this module, inside the toolbar package, so
  toolbar_app.teardown() drains it BEFORE the dev-reload purge
  (a leaked cid in a purged module = an orphan callback).
"""
from __future__ import annotations

import traceback

import maya.api.OpenMaya as om
import maya.cmds as cmds

_CALLBACK_IDS: list = []


def on_selection_changed(fn):
    """Register fn for SelectionChanged; returns the callback id."""
    def _safe(*_args):
        try:
            fn()
        except Exception:
            cmds.warning("[FS toolbar] selection listener error (see script editor)")
            traceback.print_exc()
    cid = om.MEventMessage.addEventCallback("SelectionChanged", _safe)
    _CALLBACK_IDS.append(cid)
    return cid


def is_registered(cid) -> bool:
    """True while cid is live in this registry. State-holders (the paint
    toggle) use this so a drained registry (teardown, dock-switch, hide,
    dev-reload) reads as OFF automatically - no cross-module resync."""
    return cid in _CALLBACK_IDS


def on_scene_change(fn):
    """Register fn for scene open/new + network-node add/remove (the
    context-slot events). Handlers re-probe from scratch and must be
    cheap; node-removed fires DURING deletion, so defer real work."""
    cids = []

    def _safe(*_args):
        try:
            cmds.evalDeferred(fn)      # deferred drain (LiveBackend pattern)
        except Exception:
            traceback.print_exc()

    for ev in (om.MSceneMessage.kAfterOpen, om.MSceneMessage.kAfterNew):
        cids.append(om.MSceneMessage.addCallback(ev, _safe))
    cids.append(om.MDGMessage.addNodeAddedCallback(_safe, "network"))
    cids.append(om.MDGMessage.addNodeRemovedCallback(_safe, "network"))
    _CALLBACK_IDS.extend(cids)
    return cids


def remove(cid_or_cids) -> None:
    """Remove one id or a list of ids; tolerates already-removed."""
    cids = cid_or_cids if isinstance(cid_or_cids, (list, tuple)) else [cid_or_cids]
    for cid in cids:
        try:
            om.MMessage.removeCallback(cid)
        except RuntimeError:
            pass
        if cid in _CALLBACK_IDS:
            _CALLBACK_IDS.remove(cid)


def stop_all() -> None:
    """Idempotent full drain - toolbar_app.teardown()'s hook."""
    while _CALLBACK_IDS:
        cid = _CALLBACK_IDS.pop()
        try:
            om.MMessage.removeCallback(cid)
        except RuntimeError:
            pass
