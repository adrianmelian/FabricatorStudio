# maya_tools/rigging/fabricator/build_report.py
"""The last-build report — stamped on the fab registry node.

Keep-last-only by design (Adrian's call 2026-07-06): this is not a build
history or a log, it's a single snapshot of what happened the last time
Build Modules or Unbuild Modules ran. Every build/unbuild overwrites the
previous report outright. That keeps the scene footprint flat forever —
one string attr, size-capped — instead of an ever-growing log that bloats
every saved scene whether or not anyone ever reads it.

Written even for FAILED builds, on purpose: a failed build leaves no rig
to inspect, so its report IS the evidence. Without this, the only trace
of a build failure is whatever scrolled past in the Script Editor. With
it, the AI assistant (or Adrian) can read exactly what action was
attempted, what broke, and how long it ran, straight off the registry —
no scrollback required.

write_report() never raises. A report is diagnostic sugar; a failure
while stamping it must never turn into a second, unrelated failure on
top of (or instead of) the real build error.
"""
__author__ = "Adrian Melian"

import json
import time

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes
from maya_tools.utils.maya import network_nodes


ATTR = 'build_report'          # string attr on the fab registry node
MAX_REPORT_BYTES = 64_000


def write_report(action: str, outcome: str, components: list,
                 error_tail: str = '', duration_s: float = 0.0) -> None:
    """Stamp the last-build report on the registry. No-op if there is no
    registry (nothing to stamp on). Overwrites whatever report was there
    before — keep-last-only.

    action: 'build' | 'unbuild'.
    outcome: 'ok' | 'failed'.
    components: list of {'id': ..., 'status': ..., 'note': ...} (note is
                optional per entry). Caller-shaped; not validated here.
    error_tail: free-form error text; only the LAST 4000 chars are kept
                (the tail is where the actual exception message lives —
                a chain of re-raises tends to accumulate boilerplate at
                the front).
    duration_s: wall-clock seconds for the action; rounded to 2dp.

    Size-capped at MAX_REPORT_BYTES: if the encoded report would exceed
    it, components is cut to the first 20 and error_tail to the last
    1000 chars, and truncated=True is set. As a final guard (a single
    pathological entry could still overflow), the JSON string itself is
    hard-sliced to MAX_REPORT_BYTES — read_report() degrades a torn
    payload gracefully rather than raising.

    Never raises: any exception while building or writing the report is
    caught and reported via cmds.warning — a report failure must never
    break (or mask) the build it's reporting on. The never-raises
    contract is structural, not merely a side effect of nodes.get_registry
    staying benign — that check lives INSIDE this same try below, not
    ahead of it. The worst call site is reporting.__exit__ while an
    exception is already propagating; a secondary raise there would mask
    the real build error instead of just failing to report on it.

    The actual attr write is deliberately excluded from the undo queue
    (stateWithoutFlush toggled off around it) — otherwise it lands as
    its own un-chunked undo entry after undo_chunk closes, so a user's
    first ctrl+Z after a build would silently eat the report write
    instead of undoing the build (a phantom-feeling undo press). Kept
    out of the queue, the diagnostic report survives undo of the build
    it describes.
    """
    try:
        reg = nodes.get_registry()
        if not reg:
            return

        from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION

        report = {
            'stamped_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'fabricator_version': FABRICATOR_VERSION,
            'action': action,
            'outcome': outcome,
            'components': list(components or []),
            'error_tail': (error_tail or '')[-4000:],
            'duration_s': round(duration_s, 2),
        }

        payload = json.dumps(report)
        if len(payload.encode('utf-8')) > MAX_REPORT_BYTES:
            report['components'] = report['components'][:20]
            report['error_tail'] = report['error_tail'][-1000:]
            report['truncated'] = True
            payload = json.dumps(report)
        if len(payload.encode('utf-8')) > MAX_REPORT_BYTES:
            # Final guard — a torn payload degrades gracefully in
            # read_report() rather than never happening at all.
            # json.dumps' default ensure_ascii=True means payload is
            # pure ASCII today, so this can't actually split a
            # multibyte char yet — the byte-safe slice (decode
            # errors='ignore') is future-proofing against a later
            # ensure_ascii=False, not an active tear risk right now.
            payload = payload.encode('utf-8')[:MAX_REPORT_BYTES].decode(
                'utf-8', errors='ignore')

        undo_was_on = cmds.undoInfo(query=True, stateWithoutFlush=True)
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            network_nodes.ensure_string_attr(reg, ATTR, default='')
            cmds.setAttr(f'{reg}.{ATTR}', payload, type='string')
        finally:
            cmds.undoInfo(stateWithoutFlush=undo_was_on)
    except Exception as exc:
        cmds.warning(f"build_report: could not stamp report: {exc}")


def read_report() -> dict | None:
    """Return the last-build report dict, or None if there is no
    registry, the attr doesn't exist, or it's empty (pre-report scene).

    A torn/hard-sliced payload (see write_report's final size guard)
    degrades to {'outcome': 'unreadable', 'raw_prefix': raw[:200]}
    instead of raising — read_report is a read-only inspection surface,
    it never gets to break the caller.
    """
    reg = nodes.get_registry()
    if not reg:
        return None
    if not cmds.attributeQuery(ATTR, node=reg, exists=True):
        return None
    raw = cmds.getAttr(f'{reg}.{ATTR}') or ''
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {'outcome': 'unreadable', 'raw_prefix': raw[:200]}


class reporting:
    """Context manager — stamps the last-build report on exit.

    Example:
        with reporting('build') as rep:
            ...
            rep.components = [{'id': inst.id, 'status': 'ok'}
                              for inst in sorted_instances]

    Success (no exception) stamps outcome='ok' with whatever the caller
    left on rep.components (default: empty list — components is opt-in,
    callers that don't touch rep.components just get []). A raised
    exception stamps outcome='failed' (error_tail=str(exc),
    components=[]) and the exception is RE-RAISED unchanged — this
    never swallows the real error, it only piggybacks a report on the
    way out. duration_s is measured enter-to-exit either way. Stamping
    itself never raises (write_report guarantees that on its own).
    """
    def __init__(self, action: str):
        self.action = action
        self.components = []

    def __enter__(self):
        self._t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            write_report(action=self.action, outcome='ok',
                         components=self.components,
                         duration_s=time.time() - self._t0)
        else:
            write_report(action=self.action, outcome='failed',
                         components=[], error_tail=str(exc),
                         duration_s=time.time() - self._t0)
        return False
