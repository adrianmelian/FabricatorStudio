# maya_tools/framework/toolbar/ai_bridge/client.py
"""In-process loopback client for the Fabricator AI bridge.

The Reggie panel's worker thread executes tool calls through this module:
one blocking NDJSON round-trip per call to the bridge service running in
this same Maya session. The bridge marshals the actual scene read onto
Maya's main thread; this client only talks to the socket, so it MUST be
called off the main thread (a main-thread caller would deadlock waiting
for a reply that the main thread itself has to produce).

Mirrors fabricator-mcp's bridge_client (the external consumer). Stdlib only.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

import json
import socket
import uuid

from maya_tools.framework.toolbar.ai_bridge.protocol import DEFAULT_PORT


class BridgeCallError(Exception):
    """Base: anything wrong with a bridge round-trip."""


class BridgeDownError(BridgeCallError):
    """Nothing listening (bridge not started, or died mid-exchange)."""


class BridgeOpError(BridgeCallError):
    """The bridge answered ok=false."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def call(op: str, params: dict | None = None, *, port: int | None = None,
         timeout: float = 15.0) -> dict:
    """One blocking request; returns the result dict.

    port=None resolves the live bridge port, falling back to DEFAULT_PORT.
    timeout bounds each socket operation, not total wall clock.

    Raises:
        BridgeDownError: the bridge can't be reached (nothing listening,
            connect/send/recv failed or timed out), or it closed the
            connection before finishing its reply.
        BridgeOpError: the bridge answered ok=false; .code and .message
            carry the wire error.
        BridgeCallError: the bridge answered, but with a line this client
            can't interpret (malformed/non-UTF-8 JSON, missing required
            fields) — a bug or version skew, not a normal op failure.
            Also the base class of the two above, so `except
            BridgeCallError` catches everything this function raises.
    """
    if port is None:
        from maya_tools.framework.toolbar import ai_bridge
        port = ai_bridge.current_port() or DEFAULT_PORT
    request = {"id": uuid.uuid4().hex, "op": op, "params": params or {}}
    line = (json.dumps(request, allow_nan=False) + "\n").encode("utf-8")
    buf = b""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(line)
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    # Deliberate divergence from the external mirror's
                    # _recv_line (which returns partial bytes for the caller
                    # to classify): in-process, an empty recv mid-reply means
                    # this session's bridge service was torn down, so Down is
                    # the accurate classification here.
                    raise BridgeDownError("Bridge closed the connection mid-reply.")
                buf += chunk
    except BridgeCallError:
        raise
    except OSError as exc:
        raise BridgeDownError(
            f"Bridge is not reachable on port {port}. Open the Reggie panel "
            f"or Bridge toolbar settings > Connect AI > Start, then retry. "
            f"({exc})") from exc
    # Exactly one reply line is expected per request; partition at the FIRST
    # newline and parse only that line. _rest (anything after it) is
    # deliberately discarded — mirrors fabricator-mcp's bridge_client.
    raw_reply, _, _rest = buf.partition(b"\n")
    try:
        reply = json.loads(raw_reply.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BridgeCallError(f"Malformed bridge reply: {exc}") from exc
    if not isinstance(reply, dict) or "ok" not in reply:
        raise BridgeCallError("Bridge reply is missing the 'ok' field.")
    if reply["ok"] is True:
        result = reply.get("result")
        if not isinstance(result, dict):
            raise BridgeCallError("ok reply is missing its result object.")
        return result
    error = reply.get("error") or {}
    code, message = error.get("code"), error.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        raise BridgeCallError("error reply is missing code/message.")
    raise BridgeOpError(code, message)
