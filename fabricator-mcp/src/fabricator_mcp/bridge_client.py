"""Socket client for the Fabricator/Maya bridge's NDJSON-over-loopback-TCP
wire protocol. Stdlib only — no maya, no mcp, no third-party deps. See
_wire.py for the vendored protocol constants this module is built against.
"""
__author__ = "Adrian Melian"

import json
import socket
import uuid

from ._wire import DEFAULT_PORT, ERROR_CODES, PROTOCOL  # noqa: F401 (re-exported)

__all__ = [
    "DEFAULT_PORT",
    "PROTOCOL",
    "BridgeError",
    "BridgeDownError",
    "BridgeOpError",
    "ProtocolMismatchError",
    "call",
    "check_handshake",
]

_HOST = "127.0.0.1"
_DOWN_MESSAGE = (
    "Maya isn't listening. In Maya, open the DevBot toolbar > Connect "
    "your AI > Start. (Then retry.)"
)


class BridgeError(Exception):
    """Base class for every fabricator-mcp bridge-client failure."""


class BridgeDownError(BridgeError):
    """Raised when Maya's bridge server can't be reached at all (nothing
    listening on the port, connection refused/timed out mid-exchange)."""


class BridgeOpError(BridgeError):
    """Raised when the bridge answered a request with ok: false."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ProtocolMismatchError(BridgeError):
    """Raised when the bridge's protocol version doesn't match ours."""


def _recv_line(sock: socket.socket) -> bytes:
    """Read from sock, looping across as many recv() chunks as it takes,
    until a full NDJSON line (terminated by b'\\n') has arrived. Returns
    the line's RAW bytes without the trailing newline (decoding/parsing is
    deliberately left to the caller). If the peer closes the connection
    before ever sending a newline, returns whatever (possibly empty,
    possibly incomplete) bytes were received rather than raising — an
    incomplete/absent line is a content problem for the caller to turn
    into BridgeError, not a socket-level BridgeDownError."""
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
    line, _, _rest = bytes(buf).partition(b"\n")
    return line


def call(op: str, params: dict | None = None, *, port: int = DEFAULT_PORT,
          timeout: float = 15.0) -> dict:
    """Make one request to the Maya bridge and return its result dict.

    One connection per call: stateless, and the simplest correct thing.
    Keep-alive is a later optimization, not this function's job.

    Note: `timeout` bounds each individual socket operation (connect,
    sendall, each recv), not the cumulative wall-clock time of the whole
    exchange. A slow trickle-sender could keep a call alive well past
    `timeout` seconds overall. Fine for a trusted single-user loopback
    bridge; a real wall-clock deadline is unnecessary complexity here.

    Raises:
        BridgeDownError: the bridge can't be reached (connect refused/timed
            out, or the connection dropped mid-exchange).
        BridgeOpError: the bridge answered ok: false. .code is one of the
            bridge's ERROR_CODES, .message is the human-readable text.
        BridgeError: the bridge's reply line was malformed (not JSON, not
            an object, or missing the fields the wire protocol requires).
            This indicates a bug or a version skew, not a normal op
            failure.
    """
    request_id = uuid.uuid4().hex
    request = {"id": request_id, "op": op, "params": params or {}}
    request_line = (json.dumps(request) + "\n").encode("utf-8")

    try:
        with socket.create_connection((_HOST, port), timeout=timeout) as sock:
            sock.sendall(request_line)
            raw_line = _recv_line(sock)
    except (OSError, socket.timeout) as exc:
        raise BridgeDownError(_DOWN_MESSAGE) from exc

    # Decoding and parsing are deliberately OUTSIDE the socket try/except
    # above: a garbled/non-UTF-8/incomplete reply is a content problem
    # (BridgeError), never a "can't reach Maya" problem (BridgeDownError),
    # even though decode() can raise (UnicodeDecodeError is a ValueError
    # subclass, caught here alongside json.JSONDecodeError).
    try:
        response = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BridgeError(
            f"Bridge sent a malformed reply (non-UTF-8, corrupted, "
            f"incomplete, or otherwise not valid JSON): {exc!r}; this is "
            "a bug or a version skew, not a normal op failure."
        ) from exc

    if not isinstance(response, dict):
        raise BridgeError(
            "Bridge reply was valid JSON but not a JSON object (got "
            f"{type(response).__name__}); this is a bug or a version "
            "skew, not a normal op failure."
        )

    ok = response.get("ok")

    if ok is True:
        result = response.get("result")
        if not isinstance(result, dict):
            raise BridgeError(
                "Bridge reply was ok:true but missing a 'result' object; "
                "this is a bug or a version skew, not a normal op failure."
            )
        return result

    if ok is False:
        error = response.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if not isinstance(code, str) or not isinstance(message, str):
            raise BridgeError(
                "Bridge reply was ok:false but its 'error' object is "
                "missing 'code'/'message' strings; this is a bug or a "
                "version skew, not a normal op failure."
            )
        raise BridgeOpError(code, message)

    raise BridgeError(
        "Bridge reply is missing a boolean 'ok' field; this is a bug or a "
        "version skew, not a normal op failure."
    )


def check_handshake(*, port: int = DEFAULT_PORT, timeout: float = 15.0) -> dict:
    """Call the 'hello' op and verify the bridge's protocol version matches
    ours (PROTOCOL). Returns the hello result dict on a match.

    Raises:
        ProtocolMismatchError: the bridge's protocol differs from ours.
        BridgeDownError / BridgeOpError / BridgeError: see call().
    """
    result = call("hello", port=port, timeout=timeout)
    bridge_protocol = result.get("protocol")
    if bridge_protocol != PROTOCOL:
        raise ProtocolMismatchError(
            "Protocol mismatch: fabricator-mcp speaks protocol "
            f"{PROTOCOL}, but the running Maya bridge speaks protocol "
            f"{bridge_protocol!r} (bridge_version="
            f"{result.get('bridge_version')!r}). Update whichever side is "
            "behind: the Fabricator toolset in Maya, or the fabricator-mcp "
            "package your AI client is running."
        )
    return result
