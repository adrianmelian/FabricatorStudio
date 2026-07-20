"""The Reggie chat session: an embedded, auto-primed agent loop.

Pure Python by design (no Qt imports): the panel owns the worker thread
and marshals these plain callbacks into Qt signals. run_turn() is
blocking; run it OFF Maya's main thread. Tool execution goes through
ai_bridge.client (a loopback socket round-trip; the bridge marshals the
scene read onto the main thread itself), so nothing here touches cmds.
"""
from __future__ import annotations

import json
import threading

__author__ = "Adrian Melian"

from maya_tools.framework.toolbar.ai_bridge import tool_specs
from maya_tools.framework.toolbar.ai_bridge.client import (
    BridgeCallError, call as _bridge_call_default)
from maya_tools.framework.toolbar.reggie import _bug_url, prime
from maya_tools.framework.toolbar.reggie.api_client import ApiError

MAX_TOOL_ROUNDS = 8          # API round-trips per turn; prevents runaway spend
HISTORY_LIMIT = 30           # messages kept; trimmed at turn end
_TOOL_RESULT_CHAR_CAP = 60_000


class ChatSession:
    """One conversation. Single-flight: one run_turn at a time."""

    def __init__(self, client, bridge_call=None):
        self._client = client
        self._bridge_call = bridge_call or _bridge_call_default
        self._system = None      # built lazily, once per session
        self.history = []
        self.busy = False        # observability only; the panel reads it
        self._busy_lock = threading.Lock()   # the actual single-flight gate

    def new_chat(self) -> None:
        if self.busy:
            return    # a live turn owns history; the panel disables the
                      # button, this guards any future non-UI caller
        self.history = []

    # -- the turn ---------------------------------------------------------

    def run_turn(self, user_text, *, on_token, on_status, on_done, on_error,
                 stop_event) -> None:
        """Blocking. Reports through callbacks; never raises."""
        if not self._busy_lock.acquire(blocking=False):
            on_error("A turn is already running.")
            return
        self.busy = True
        mark = len(self.history)
        try:
            self._run_turn(user_text, on_token, on_status, on_done, stop_event)
        except (ApiError, BridgeCallError) as exc:
            del self.history[mark:]      # roll back: the API rejects two
            on_error(str(exc))           # consecutive user turns, so a failed
        except Exception as exc:         # turn must vanish from history
            del self.history[mark:]
            on_error(f"Unexpected error: {exc!r}")
        finally:
            self.busy = False
            self._busy_lock.release()

    def _run_turn(self, user_text, on_token, on_status, on_done, stop_event):
        if self._system is None:
            self._system = prime.build_system_prompt()
        on_status("scanning scene...")
        scan = prime.build_scan_block(self._bridge_call)
        self.history.append({"role": "user",
                             "content": prime.wrap_user_message(user_text, scan)})
        tools = tool_specs.anthropic_tools()
        checked = []
        for _round in range(MAX_TOOL_ROUNDS):
            text_parts, tool_calls, stop_reason = [], [], "end_turn"
            stopped = False
            for kind, payload in self._client.stream_message(
                    system=self._system, messages=self.history, tools=tools,
                    stop_event=stop_event):
                if kind == "text":
                    text_parts.append(payload)
                    on_token(payload)
                elif kind == "tool_use":
                    tool_calls.append(payload)
                elif kind == "done":
                    stop_reason = payload["stop_reason"]
                elif kind == "stopped":
                    stopped = True
            self._append_assistant(text_parts, tool_calls)
            if not text_parts and not tool_calls and (
                    self.history and self.history[-1]["role"] == "user"
                    and isinstance(self.history[-1]["content"], str)):
                # Nothing streamed this round (stop hit before any content,
                # or a degenerate empty completion): _append_assistant added
                # no message, so the tail is still this turn's plain user
                # text — a shape _close_open_turn deliberately leaves alone.
                # Close it here, or the next send makes user,user (400) and
                # rollback can never remove the wedged tail.
                self.history.append({"role": "assistant", "content": [
                    {"type": "text",
                     "text": "(turn interrupted; ask me to continue)"}]})
            if stopped:
                self._close_open_turn()
                self._trim()
                on_done({"stopped": True, "checked": checked})
                return
            if stop_reason != "tool_use" or not tool_calls:
                break
            results = []
            for tc in tool_calls:
                if stop_event.is_set():
                    # Keep the real answers already gathered; close only the
                    # ids that never ran as cancelled — one combined user
                    # message so every tool_use above stays resolved. The
                    # slice is evaluated before the extend, so it covers the
                    # current tc and everything after it.
                    results += [{"type": "tool_result",
                                 "tool_use_id": rem["id"],
                                 "content": "(cancelled: stopped before this "
                                            "tool ran)"}
                                for rem in tool_calls[len(results):]]
                    self.history.append({"role": "user", "content": results})
                    self._close_open_turn()
                    self._trim()
                    on_done({"stopped": True, "checked": checked})
                    return
                on_status(f"checking {tc['name']}...")
                checked.append(tc["name"])
                results.append({"type": "tool_result",
                                "tool_use_id": tc["id"],
                                "content": self._execute(tc["name"], tc["input"])})
            self.history.append({"role": "user", "content": results})
        self._close_open_turn()
        self._trim()
        on_done({"stopped": False, "checked": checked})

    # -- helpers ----------------------------------------------------------

    def _append_assistant(self, text_parts, tool_calls) -> None:
        content = []
        text = "".join(text_parts)
        if text:
            content.append({"type": "text", "text": text})
        for tc in tool_calls:
            content.append({"type": "tool_use", "id": tc["id"],
                            "name": tc["name"], "input": tc["input"]})
        if content:
            self.history.append({"role": "assistant", "content": content})

    def _close_open_turn(self) -> None:
        """Guard every early-return against leaving history in an
        API-illegal state. Two shapes need closing, and the first FALLS
        THROUGH into the second (its synthesized cancelled tool_results
        are themselves a trailing user message that must be closed with
        an assistant text, or the next send makes user,user and 400s):

        1. The just-appended assistant message ends in one or more
           unresolved tool_use blocks - the stream was cut before those
           calls could run. The API requires a tool_result for every
           tool_use before the next turn, so synthesize a cancelled
           tool_result for each, then re-check the (new) tail.
        2. The trailing message is a user tool_result list with no
           assistant reply yet (tool budget exhausted mid-cycle, stop
           between tool executions, or the synthesis above). Synthesize a
           short assistant close-out so the next send opens on a fresh,
           plain user turn instead of two user turns in a row.
        """
        if not self.history:
            return
        last = self.history[-1]
        if last["role"] == "assistant":
            pending = [b for b in last["content"]
                       if isinstance(b, dict) and b.get("type") == "tool_use"]
            if pending:
                self.history.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b["id"],
                     "content": "(cancelled: stopped before this tool ran)"}
                    for b in pending]})
                last = self.history[-1]
        if last["role"] == "user" and not isinstance(last["content"], str):
            self.history.append({"role": "assistant", "content": [
                {"type": "text",
                 "text": "(turn interrupted; ask me to continue)"}]})

    def _execute(self, name, tool_input):
        """Run one tool; ALWAYS returns tool_result content (str or blocks).
        Errors become content the model can read, never exceptions."""
        spec = tool_specs.by_name(name)
        if spec is None:
            return json.dumps({"error": f"unknown tool {name!r}"})
        try:
            if name == "report_bug":
                return self._report_bug(tool_input or {})
            result = self._bridge_call(spec["op"], tool_input or {})
        except BridgeCallError as exc:
            return json.dumps({"error": str(exc)})
        if name == "get_viewport_screenshot" and "png_base64" in result:
            return [{"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": result["png_base64"]}}]
        try:
            text = json.dumps(result, allow_nan=False)
        except (TypeError, ValueError) as exc:
            # e.g. NaN/Infinity: the bridge's json.loads accepts those
            # tokens, but allow_nan=False refuses to re-emit them. A bad
            # result must become readable error content, never an
            # exception that nukes the whole turn.
            return json.dumps({"error": f"tool result not JSON-serializable: "
                                        f"{exc}"})
        if len(text) > _TOOL_RESULT_CHAR_CAP:
            text = text[:_TOOL_RESULT_CHAR_CAP] + "...(truncated)"
        return text

    def _report_bug(self, tool_input) -> str:
        diagnostics = {}
        if tool_input.get("include_diagnostics", True):
            try:
                diagnostics = self._bridge_call("diagnostics", {})
            except BridgeCallError as exc:
                diagnostics = {"unavailable": str(exc)}
        try:
            url = _bug_url.build_issue_url(
                title=str(tool_input.get("title", "")),
                description=str(tool_input.get("description", "")),
                repro_steps=str(tool_input.get("repro_steps", "")),
                attempted_solutions=str(tool_input.get("attempted_solutions", "")),
                diagnostics=diagnostics)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"issue_url": url,
                           "note": "Give this URL to the user to review and "
                                   "submit under their own GitHub account."})

    def _trim(self) -> None:
        """Bound history; never let a tool_result or assistant message lead
        (the API requires the conversation to open with a plain user turn)."""
        if len(self.history) > HISTORY_LIMIT:
            self.history = self.history[-HISTORY_LIMIT:]
        while self.history and not (
                self.history[0]["role"] == "user"
                and isinstance(self.history[0]["content"], str)):
            self.history.pop(0)
