"""AI bridge wire protocol offscreen tests. Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/toolbar/_dev/test_ai_bridge.py

No Maya session required and no Qt import anywhere in this file's chain -
protocol.py is pure Python by design (see its module docstring), so this
suite covers every framing/error path without maya.standalone.init().

PYTHONNOUSERSITE=1 is LOAD-BEARING here too (see test_phase_a.py for the
numpy/shiboken6 collision this guards against).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "maya_tools" / "_vendor"))

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


def test_parse_request_good():
    from maya_tools.framework.toolbar.ai_bridge import protocol
    req = protocol.parse_request('{"id": "1", "op": "hello", "params": {}}')
    assert req.id == '1' and req.op == 'hello' and req.params == {}


def test_parse_request_params_optional():
    from maya_tools.framework.toolbar.ai_bridge import protocol
    req = protocol.parse_request('{"id": "2", "op": "x"}')
    assert req.params == {}


def test_parse_request_bad_json_and_shape():
    from maya_tools.framework.toolbar.ai_bridge import protocol
    for line in ('not json', '{"op": "x"}', '{"id": 5, "op": "x"}', '[]',
                 '{"id": "", "op": "x"}', '{"id": "1", "op": ""}',
                 '{"id": "1", "op": "x", "params": []}'):
        try:
            protocol.parse_request(line)
        except protocol.BridgeError as e:
            assert e.code == 'bad_params', (line, e.code)
        else:
            raise AssertionError(f'accepted bad line: {line!r}')


def test_response_lines():
    import json
    from maya_tools.framework.toolbar.ai_bridge import protocol
    ok = json.loads(protocol.ok_line('7', {'a': 1}))
    assert ok == {'id': '7', 'ok': True, 'result': {'a': 1}}
    err = json.loads(protocol.error_line('7', 'not_found', 'no registry'))
    assert err['ok'] is False and err['error']['code'] == 'not_found'
    assert err['error']['message'] == 'no registry'


def test_size_cap():
    import json
    from maya_tools.framework.toolbar.ai_bridge import protocol
    line = protocol.ok_line('1', {'big': 'x' * 500_000})
    obj = json.loads(line)
    assert len(line.encode('utf-8')) <= protocol.MAX_RESPONSE_BYTES
    assert obj['ok'] is False and obj['error']['code'] == 'too_large'


def test_error_line_rejects_unknown_code():
    from maya_tools.framework.toolbar.ai_bridge import protocol
    try:
        protocol.error_line('1', 'nonsense_code', 'x')
    except ValueError:
        pass
    else:
        raise AssertionError('unknown code must be rejected')
    try:
        protocol.BridgeError('nonsense_code', 'x')
    except ValueError:
        pass
    else:
        raise AssertionError('unknown code must be rejected by BridgeError too')


def test_error_line_truncates_message():
    import json
    from maya_tools.framework.toolbar.ai_bridge import protocol
    huge = 'x' * 10_000
    line = protocol.error_line('1', 'internal', huge)
    assert len(line.encode('utf-8')) <= protocol.MAX_RESPONSE_BYTES
    obj = json.loads(line)
    assert len(obj['error']['message']) == 4000
    assert obj['error']['message'] == huge[:4000]


def test_ok_line_rejects_non_finite():
    from maya_tools.framework.toolbar.ai_bridge import protocol
    try:
        protocol.ok_line('1', {'x': float('nan')})
    except ValueError:
        pass
    else:
        raise AssertionError('NaN result must be rejected (strict RFC 8259)')


def test_hello_shape():
    from maya_tools.framework.toolbar.ai_bridge import protocol
    h = protocol.hello_result()
    assert h['protocol'] == protocol.PROTOCOL == 1
    assert isinstance(h['bridge_version'], str) and h['bridge_version']


def test_no_maya_leak():
    # ORDERING CONSTRAINT: this must run BEFORE any test that calls
    # hello_result() (see main() below). hello_result()'s FABRICATOR_VERSION
    # import is lazy on purpose, but lazy still means "may execute during
    # this process" - if fabricator's package chain ever grows a maya
    # import, sys.modules would carry it for the rest of the process.
    # Investigated on this machine (plain offscreen mayapy, no
    # maya.standalone.init()): `from maya_tools.rigging.fabricator.version
    # import FABRICATOR_VERSION` imports cleanly with ZERO maya modules in
    # sys.modules, because maya_tools/__init__.py, maya_tools/rigging/__init__.py
    # and maya_tools/rigging/fabricator/__init__.py are all import-free
    # (docstring/comment/__author__ only) - nothing in that chain touches
    # maya. So today this assertion would pass regardless of test order.
    # Registering it first anyway is a cheap, permanent guard against that
    # chain quietly growing a maya import later.
    import sys as _sys
    from maya_tools.framework.toolbar.ai_bridge import protocol  # noqa
    assert not any(m == 'maya' or m.startswith('maya.') for m in _sys.modules), \
        'protocol import chain must stay maya-free'


def test_no_maya_leak_service():
    # Same guard as test_no_maya_leak, for service.py specifically: Task 5's
    # binding rule is that service.py must not import maya or handlers at
    # module scope (dispatch is injected), which is what lets the socket/
    # framing logic run under plain offscreen mayapy with no
    # maya.standalone.init(). Registered right after test_no_maya_leak, and
    # before any test that starts a BridgeService, for the same "cheap
    # permanent guard" reason.
    import sys as _sys
    from maya_tools.framework.toolbar.ai_bridge import service  # noqa
    assert not any(m == 'maya' or m.startswith('maya.') for m in _sys.modules), \
        'service import chain must stay maya-free'


# ─────────────────────────────────────────────
# ai_bridge/service.py — socket thread + QTimer drain (Task 5)
#
# All tests below construct BridgeService with a FAKE dispatch callable and
# start(use_qt_timer=False), driving _drain() by hand from the test thread -
# there is no QApplication anywhere in this file's import chain, and there
# must never be one. port=0 means an OS-assigned ephemeral port, so parallel
# test runs never collide on a fixed port.
# ─────────────────────────────────────────────

def _round_trip(svc, line, timeout=2.0):
    """Connect to svc.port, send one NDJSON request line, and read back
    exactly one reply line - driving svc._drain() repeatedly while polling
    for the reply instead of a fixed sleep-then-drain-once.

    Why a poll loop and not a fixed sleep: the request has to cross a real
    thread boundary (this test thread -> the service's reader thread ->
    self._pending) before a single _drain() call would see it, and how
    long that takes is a scheduler detail, not a fixed duration. A short
    sleep is either too short (flaky under load) or wastefully long. This
    loop instead drains on every pass (a no-op if nothing has been queued
    yet) and does a short-timeout recv, so it picks up the reply on
    whichever pass it first becomes available, with no hard-coded wait.
    """
    import socket
    import time
    deadline = time.time() + timeout
    with socket.create_connection(('127.0.0.1', svc.port), timeout=timeout) as s:
        s.sendall(line.encode('utf-8') + b'\n')
        s.settimeout(0.05)
        buf = b''
        while not buf.endswith(b'\n') and time.time() < deadline:
            svc._drain()
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
    if not buf.endswith(b'\n'):
        raise AssertionError(f'no complete reply line received before '
                              f'timeout: {buf!r}')
    return buf.decode('utf-8')


def test_service_roundtrip_with_fake_dispatch():
    import json
    from maya_tools.framework.toolbar.ai_bridge import service
    calls = []

    def fake_dispatch(op, params):
        calls.append((op, params))
        return {'echo': op}

    svc = service.BridgeService(dispatch=fake_dispatch, port=0)
    svc.start(use_qt_timer=False)
    try:
        resp = json.loads(_round_trip(
            svc, '{"id": "1", "op": "hello", "params": {}}'))
        assert resp['ok'] is True, resp
        assert resp['result'] == {'echo': 'hello'}, resp
        assert calls == [('hello', {})], calls
    finally:
        svc.stop()


def test_service_stop_idempotent_and_restartable():
    from maya_tools.framework.toolbar.ai_bridge import service
    svc = service.BridgeService(dispatch=lambda op, p: {}, port=0)
    svc.start(use_qt_timer=False)
    svc.stop()
    svc.stop()                          # idempotent - must not raise
    svc.start(use_qt_timer=False)       # restartable
    assert svc.is_running() is True
    svc.stop()
    assert svc.is_running() is False


def test_service_bad_line_gets_error_response():
    import json
    from maya_tools.framework.toolbar.ai_bridge import service
    svc = service.BridgeService(dispatch=lambda op, p: {}, port=0)
    svc.start(use_qt_timer=False)
    try:
        resp = json.loads(_round_trip(svc, 'not json'))
        assert resp['ok'] is False, resp
        assert resp['error']['code'] == 'bad_params', resp
    finally:
        svc.stop()


def test_service_non_serializable_result_becomes_internal_error():
    # Task 4 review's explicit ask: a handler result that ok_line() can't
    # serialize (here: a set(), which json.dumps rejects with TypeError)
    # must surface to the CLIENT as ok:false / code 'internal' - not hang
    # the connection and not kill the drain loop for subsequent requests.
    import json
    from maya_tools.framework.toolbar.ai_bridge import service

    def fake_dispatch(op, params):
        return {'bad': set()}

    svc = service.BridgeService(dispatch=fake_dispatch, port=0)
    svc.start(use_qt_timer=False)
    try:
        resp = json.loads(_round_trip(
            svc, '{"id": "1", "op": "x", "params": {}}'))
        assert resp['ok'] is False, resp
        assert resp['error']['code'] == 'internal', resp
        # Drain must survive the exception - a second, ordinary request on
        # a NEW connection must still get served normally.
        resp2 = json.loads(_round_trip(
            svc, '{"id": "2", "op": "x", "params": {}}'))
        assert resp2['ok'] is False, resp2
        assert resp2['error']['code'] == 'internal', resp2
    finally:
        svc.stop()


def test_service_nan_result_becomes_internal_error():
    # Same contract as the non-serializable case, for ok_line's OTHER raise
    # path: a NaN float is valid Python but json.dumps(..., allow_nan=False)
    # raises ValueError for it (strict RFC 8259) - that must also fold into
    # an 'internal' error line, not propagate out of _drain.
    import json
    from maya_tools.framework.toolbar.ai_bridge import service

    def fake_dispatch(op, params):
        return {'x': float('nan')}

    svc = service.BridgeService(dispatch=fake_dispatch, port=0)
    svc.start(use_qt_timer=False)
    try:
        resp = json.loads(_round_trip(
            svc, '{"id": "1", "op": "x", "params": {}}'))
        assert resp['ok'] is False, resp
        assert resp['error']['code'] == 'internal', resp
    finally:
        svc.stop()


def test_service_bridge_error_from_dispatch_propagates_code_and_message():
    import json
    from maya_tools.framework.toolbar.ai_bridge import protocol, service

    def fake_dispatch(op, params):
        raise protocol.BridgeError('not_found', 'nope')

    svc = service.BridgeService(dispatch=fake_dispatch, port=0)
    svc.start(use_qt_timer=False)
    try:
        resp = json.loads(_round_trip(
            svc, '{"id": "1", "op": "x", "params": {}}'))
        assert resp['ok'] is False, resp
        assert resp['error']['code'] == 'not_found', resp
        assert resp['error']['message'] == 'nope', resp
    finally:
        svc.stop()


def test_service_maya_busy_when_drain_never_runs():
    # Simulates the Qt main thread being blocked by a long task or a modal
    # dialog: a request is queued but _drain() is NEVER called. With a
    # short reply_timeout_s (constructor kwarg - see service.py's docstring
    # for why this is a kwarg rather than a bare module constant) the
    # client must get 'maya_busy' rather than hang forever.
    import json
    import socket
    from maya_tools.framework.toolbar.ai_bridge import service
    svc = service.BridgeService(dispatch=lambda op, p: {}, port=0,
                                reply_timeout_s=0.3)
    svc.start(use_qt_timer=False)
    try:
        with socket.create_connection(('127.0.0.1', svc.port), timeout=2) as s:
            s.sendall(b'{"id": "1", "op": "x", "params": {}}\n')
            # Deliberately no svc._drain() call anywhere on this path.
            s.settimeout(2)
            buf = b''
            while not buf.endswith(b'\n'):
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        resp = json.loads(buf.decode('utf-8'))
        assert resp['ok'] is False, resp
        assert resp['error']['code'] == 'maya_busy', resp
    finally:
        svc.stop()


def test_service_binds_loopback_only():
    from maya_tools.framework.toolbar.ai_bridge import service
    svc = service.BridgeService(dispatch=lambda op, p: {}, port=0)
    svc.start(use_qt_timer=False)
    try:
        assert svc.is_running() is True
        assert svc._server_sock.getsockname()[0] == '127.0.0.1', \
            'BridgeService must bind loopback only, never 0.0.0.0'
    finally:
        svc.stop()


# ─────────────────────────────────────────────
# Concurrency-review fold-in (H1/H2 mandatory, H3/M1/M2 hardening)
# ─────────────────────────────────────────────

def test_service_stop_closes_live_connections():
    # H1: a reader thread parked in conn.recv() must not be left hanging
    # by stop() - the client's connection must get a clean close (an
    # empty recv), and a subsequent start() must produce a fully working
    # service (no orphaned reader from the old session "rejoining" it).
    import json
    import socket
    import time
    from maya_tools.framework.toolbar.ai_bridge import service

    svc = service.BridgeService(dispatch=lambda op, p: {}, port=0)
    svc.start(use_qt_timer=False)
    s = socket.create_connection(('127.0.0.1', svc.port), timeout=2)
    try:
        svc.stop()
        # Poll for a close signal instead of a fixed sleep - exactly how
        # fast (and in what shape) the client notices is both a scheduler
        # detail and, here, a platform detail: a graceful close normally
        # surfaces as an empty recv(), but Windows Winsock can surface the
        # cross-thread close of a socket that has a pending BLOCKING recv()
        # on it as a hard RST (ConnectionResetError) instead of a clean FIN
        # - observed on this machine. Both are equally valid proof the
        # server actually closed the connection out from under us, which
        # is the thing this test is checking.
        s.settimeout(0.05)
        deadline = time.time() + 2.0
        got_close = False
        while time.time() < deadline:
            try:
                data = s.recv(4096)
            except socket.timeout:
                continue
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                got_close = True
                break
            if data == b'':
                got_close = True
                break
        assert got_close, 'stop() did not close a live connection'
    finally:
        s.close()

    # Restartable: a fresh roundtrip on a brand-new connection works.
    svc.start(use_qt_timer=False)
    try:
        resp = json.loads(_round_trip(
            svc, '{"id": "1", "op": "x", "params": {}}'))
        assert resp['ok'] is True, resp
    finally:
        svc.stop()


def test_service_drain_answers_before_reraising_base_exception():
    # H2: _drain() catches BaseException, not just Exception - a
    # KeyboardInterrupt/SystemExit raised inside dispatch (simulating one
    # surfacing from a Qt slot) must still answer the waiting reply_q
    # BEFORE propagating, so the client gets 'internal' rather than
    # waiting out the full reply_timeout_s for nothing.
    import json
    import socket
    import time
    from maya_tools.framework.toolbar.ai_bridge import service

    def fake_dispatch(op, params):
        raise KeyboardInterrupt('simulated Qt-slot interrupt')

    svc = service.BridgeService(dispatch=fake_dispatch, port=0)
    svc.start(use_qt_timer=False)
    try:
        with socket.create_connection(('127.0.0.1', svc.port), timeout=2) as s:
            s.sendall(b'{"id": "1", "op": "x", "params": {}}\n')
            deadline = time.time() + 2.0
            while svc._pending.empty() and time.time() < deadline:
                time.sleep(0.01)

            try:
                svc._drain()
            except KeyboardInterrupt:
                pass  # expected - this is the reraise H2 pins
            else:
                raise AssertionError(
                    '_drain() must re-raise a BaseException after '
                    'answering the reply_q, not swallow it')

            s.settimeout(2)
            buf = b''
            while not buf.endswith(b'\n'):
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        resp = json.loads(buf.decode('utf-8'))
        assert resp['ok'] is False, resp
        assert resp['error']['code'] == 'internal', resp
    finally:
        svc.stop()


def test_drain_caps_work_done_per_tick():
    # M1: _drain() answers at most _DRAIN_MAX_PER_TICK requests in one
    # call - queue more than that directly (bypassing sockets, since only
    # the queueing behavior is under test here) and confirm the first
    # _drain() call stops at the cap while the rest wait for a second call.
    import queue as _queue
    from maya_tools.framework.toolbar.ai_bridge import protocol, service
    calls = []

    def fake_dispatch(op, params):
        calls.append(op)
        return {}

    svc = service.BridgeService(dispatch=fake_dispatch, port=0)
    svc.start(use_qt_timer=False)
    try:
        total = service._DRAIN_MAX_PER_TICK + 5
        for i in range(total):
            req = protocol.Request(id=str(i), op='x', params={})
            reply_q = _queue.Queue(maxsize=1)
            svc._pending.put((req, reply_q))

        svc._drain()
        assert len(calls) == service._DRAIN_MAX_PER_TICK, len(calls)
        svc._drain()
        assert len(calls) == total, len(calls)
    finally:
        svc.stop()


def test_service_start_rolls_back_on_bind_failure():
    # M2: a start() that fails partway through must not leave running=True
    # or a stale port, and must not leak anything that would block a
    # LATER, successful start(). Forced here with an invalid port VALUE
    # (raises TypeError out of socket.bind() deterministically and
    # cross-platform) rather than an actual port collision - a real
    # bind-collision test is unreliable on Windows, where SO_REUSEADDR
    # (unlike POSIX) permits rebinding a port another socket is already
    # LISTENing on, so it would not reliably raise at all there.
    from maya_tools.framework.toolbar.ai_bridge import service
    svc = service.BridgeService(dispatch=lambda op, p: {}, port='not-a-port')
    try:
        svc.start(use_qt_timer=False)
    except (TypeError, OSError):
        pass
    else:
        raise AssertionError('expected start() to fail with an invalid port')
    assert svc.is_running() is False, \
        'a failed start() must not leave running=True'
    assert svc.port is None, \
        'a failed start() must not leave a stale port'

    svc._requested_port = 0  # retry with a real, valid (ephemeral) port
    svc.start(use_qt_timer=False)
    assert svc.is_running() is True
    svc.stop()


def main():
    # test_no_maya_leak FIRST - see its docstring-comment for why.
    check("test_no_maya_leak", test_no_maya_leak)
    check("test_no_maya_leak_service", test_no_maya_leak_service)
    check("test_parse_request_good", test_parse_request_good)
    check("test_parse_request_params_optional", test_parse_request_params_optional)
    check("test_parse_request_bad_json_and_shape", test_parse_request_bad_json_and_shape)
    check("test_response_lines", test_response_lines)
    check("test_size_cap", test_size_cap)
    check("test_error_line_rejects_unknown_code", test_error_line_rejects_unknown_code)
    check("test_error_line_truncates_message", test_error_line_truncates_message)
    check("test_ok_line_rejects_non_finite", test_ok_line_rejects_non_finite)
    check("test_hello_shape", test_hello_shape)

    check("test_service_roundtrip_with_fake_dispatch",
          test_service_roundtrip_with_fake_dispatch)
    check("test_service_stop_idempotent_and_restartable",
          test_service_stop_idempotent_and_restartable)
    check("test_service_bad_line_gets_error_response",
          test_service_bad_line_gets_error_response)
    check("test_service_non_serializable_result_becomes_internal_error",
          test_service_non_serializable_result_becomes_internal_error)
    check("test_service_nan_result_becomes_internal_error",
          test_service_nan_result_becomes_internal_error)
    check("test_service_bridge_error_from_dispatch_propagates_code_and_message",
          test_service_bridge_error_from_dispatch_propagates_code_and_message)
    check("test_service_maya_busy_when_drain_never_runs",
          test_service_maya_busy_when_drain_never_runs)
    check("test_service_binds_loopback_only", test_service_binds_loopback_only)

    check("test_service_stop_closes_live_connections",
          test_service_stop_closes_live_connections)
    check("test_service_drain_answers_before_reraising_base_exception",
          test_service_drain_answers_before_reraising_base_exception)
    check("test_drain_caps_work_done_per_tick", test_drain_caps_work_done_per_tick)
    check("test_service_start_rolls_back_on_bind_failure",
          test_service_start_rolls_back_on_bind_failure)

    if FAILURES:
        print(f"AI BRIDGE TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("AI BRIDGE TESTS: OK")


if __name__ == "__main__":
    main()
