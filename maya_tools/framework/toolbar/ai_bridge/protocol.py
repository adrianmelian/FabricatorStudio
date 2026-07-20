# maya_tools/framework/toolbar/ai_bridge/protocol.py
"""Bridge wire protocol — pure Python (no maya, no Qt) on purpose, so the
offscreen suite covers every framing/error path.

NDJSON over loopback TCP. One JSON object per line, strict RFC 8259: every
json.dumps in this module passes allow_nan=False, so a NaN/Infinity float
raises ValueError instead of emitting the non-standard tokens Python's
json module allows by default. Named ops only; the op registry lives in
handlers.py and contains ONLY reads — that is the product's auditable
read-only guarantee (spec section 3.1).

Wire shapes (one JSON object per line):
  request: {'id': str, 'op': str, 'params': dict}
  ok:      {'id': str, 'ok': True, 'result': dict}
  error:   {'id': str, 'ok': False, 'error': {'code': str, 'message': str}}
"""
__author__ = "Adrian Melian"

import json
from dataclasses import dataclass

PROTOCOL = 1
DEFAULT_PORT = 6292            # 'MAYA' on a phone keypad
MAX_RESPONSE_BYTES = 256_000
ERROR_CODES = ('unknown_op', 'bad_params', 'not_found',
               'maya_busy', 'too_large', 'internal')


class BridgeError(Exception):
    """Protocol-level failure carrying one of ERROR_CODES."""
    def __init__(self, code: str, message: str):
        if code not in ERROR_CODES:
            raise ValueError(f'unknown error code {code!r}')
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Request:
    id: str
    op: str
    params: dict


def parse_request(line: str) -> Request:
    """Parse one NDJSON line into a Request. Raises BridgeError('bad_params',
    <specific message>) for anything that is not valid JSON, not a JSON
    object, or missing/mistyped the required id/op fields."""
    try:
        data = json.loads(line)
    except (ValueError, TypeError) as exc:
        raise BridgeError('bad_params', f'Line is not valid JSON: {exc}') from exc

    if not isinstance(data, dict):
        raise BridgeError('bad_params', 'Request must be a JSON object.')

    request_id = data.get('id')
    if not isinstance(request_id, str) or not request_id:
        raise BridgeError('bad_params', 'Request "id" must be a non-empty string.')

    op = data.get('op')
    if not isinstance(op, str) or not op:
        raise BridgeError('bad_params', 'Request "op" must be a non-empty string.')

    params = data.get('params', {})
    if not isinstance(params, dict):
        raise BridgeError('bad_params', 'Request "params" must be an object.')

    return Request(id=request_id, op=op, params=params)


def ok_line(request_id: str, result: dict) -> str:
    """Serialize a successful response. Falls back to a 'too_large' error
    line if the serialized result would exceed MAX_RESPONSE_BYTES — a
    single giant response can't wedge the socket.

    Raises TypeError (non-serializable value somewhere in result) or
    ValueError (a NaN/Infinity float — allow_nan=False, strict RFC 8259)
    if json.dumps can't produce a line at all. Those are the SERVICE
    drain's problem, not this module's: the drain must catch them and
    emit error_line(request_id, 'internal', ...) itself."""
    line = json.dumps({'id': request_id, 'ok': True, 'result': result},
                       allow_nan=False)
    if len(line.encode('utf-8')) > MAX_RESPONSE_BYTES:
        return error_line(
            request_id, 'too_large',
            'Result exceeded the response size cap; narrow the query '
            '(fewer nodes / lower size).')
    return line


def error_line(request_id: str, code: str, message: str) -> str:
    """Serialize an error response. code must be one of ERROR_CODES, else
    ValueError. message is truncated to 4000 chars — this is the size-cap
    invariant for EVERY line this module emits (Task 5's 'internal' path
    passes raw exception text, which can be arbitrarily long); request_id
    is echoed back unbounded and deliberately so, since it is bounded by
    the client's own request and echoing it back is non-amplifying."""
    if code not in ERROR_CODES:
        raise ValueError(f'unknown error code {code!r}')
    return json.dumps({'id': request_id, 'ok': False,
                        'error': {'code': code, 'message': message[:4000]}},
                       allow_nan=False)


def hello_result() -> dict:
    """Result payload for the 'hello' op. FABRICATOR_VERSION is imported
    LAZILY here (not at module scope) so this module stays maya-free even
    if some future revision of fabricator's package chain grows a maya
    import; if that import fails for any reason, fall back to 'unknown'
    rather than let the bridge's own handshake take the whole connection
    down."""
    try:
        from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
        bridge_version = FABRICATOR_VERSION
    except Exception:
        bridge_version = 'unknown'
    return {'protocol': PROTOCOL, 'bridge_version': bridge_version}
