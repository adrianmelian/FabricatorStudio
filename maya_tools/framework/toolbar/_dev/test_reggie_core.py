"""Reggie core offscreen tests (bridge client, tool specs, keys, API client,
prime, agent). Pure Python + sockets; no Maya, no Qt at module scope. Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/toolbar/_dev/test_reggie_core.py
"""
import json
import os
import socket
import sys
import threading
import time
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


class _Drainer:
    """Pump svc._drain() from a side thread so blocking client.call()
    round-trips complete without a Qt event loop."""

    def __init__(self, svc):
        self._svc = svc
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self._svc._drain()
            time.sleep(0.01)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(timeout=2.0)
        assert not self._t.is_alive(), "drainer thread failed to stop"


class _FakeBridge:
    """One-shot raw-bytes fake bridge on an ephemeral loopback port.

    Accepts exactly one connection, reads one NDJSON request line, records
    it in .requests, and writes back the canned response chunk(s) it was
    constructed with — each chunk via its own sendall(), staggered, so
    tests can simulate a reply arriving across multiple recv() chunks or
    carrying arbitrary raw bytes. Adapted from fabricator-mcp/tests/
    test_bridge_client.py's FakeBridge to this file's hand-rolled harness.
    """

    def __init__(self, response_chunks):
        self._response_chunks = response_chunks
        self.requests = []
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        conn, _addr = self._server.accept()
        with conn:
            buf = bytearray()
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
            line, _, _rest = bytes(buf).partition(b"\n")
            if line:
                self.requests.append(line.decode("utf-8"))
            for i, part in enumerate(self._response_chunks):
                if i > 0:
                    time.sleep(0.05)
                conn.sendall(part)
        self._server.close()

    def join(self, timeout=5.0):
        self._thread.join(timeout)
        assert not self._thread.is_alive(), "fake bridge thread failed to stop"


def test_client_roundtrip_ok():
    from maya_tools.framework.toolbar.ai_bridge import service
    from maya_tools.framework.toolbar.ai_bridge import client
    calls = []

    def fake_dispatch(op, params):
        calls.append((op, params))
        return {"echo": op}

    svc = service.BridgeService(dispatch=fake_dispatch, port=0)
    svc.start(use_qt_timer=False)
    try:
        with _Drainer(svc):
            result = client.call("hello", {}, port=svc.port, timeout=5.0)
        assert result == {"echo": "hello"}, result
        assert calls == [("hello", {})], calls
    finally:
        svc.stop()


def test_client_op_error_raises_bridge_op_error():
    from maya_tools.framework.toolbar.ai_bridge import service, protocol
    from maya_tools.framework.toolbar.ai_bridge import client

    def fake_dispatch(op, params):
        raise protocol.BridgeError("not_found", "nope")

    svc = service.BridgeService(dispatch=fake_dispatch, port=0)
    svc.start(use_qt_timer=False)
    try:
        with _Drainer(svc):
            try:
                client.call("build_report", {}, port=svc.port, timeout=5.0)
                raise AssertionError("expected BridgeOpError")
            except client.BridgeOpError as exc:
                assert exc.code == "not_found", exc.code
                assert exc.message == "nope", exc.message
    finally:
        svc.stop()


def test_client_nothing_listening_raises_bridge_down():
    from maya_tools.framework.toolbar.ai_bridge import client
    import socket as _socket
    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()  # nothing listening on dead_port now
    try:
        client.call("hello", {}, port=dead_port, timeout=1.0)
        raise AssertionError("expected BridgeDownError")
    except client.BridgeDownError:
        pass


def test_client_malformed_json_reply_raises_bridge_call_error():
    from maya_tools.framework.toolbar.ai_bridge import client
    fake = _FakeBridge([b"not json\n"])
    try:
        try:
            client.call("hello", {}, port=fake.port, timeout=2.0)
            raise AssertionError("expected BridgeCallError")
        except client.BridgeCallError as exc:
            # Must be the base content-level error, not one of its
            # subclasses (Down/Op) - a garbled reply is a content problem.
            assert type(exc) is client.BridgeCallError, type(exc)
    finally:
        fake.join()


def test_client_non_utf8_reply_raises_bridge_call_error():
    from maya_tools.framework.toolbar.ai_bridge import client
    fake = _FakeBridge([b"\xff\xfe\xfd\n"])
    try:
        try:
            client.call("hello", {}, port=fake.port, timeout=2.0)
            raise AssertionError("expected BridgeCallError")
        except client.BridgeCallError as exc:
            assert type(exc) is client.BridgeCallError, type(exc)
    finally:
        fake.join()


def test_client_reply_split_across_two_chunks():
    from maya_tools.framework.toolbar.ai_bridge import client
    raw = (json.dumps({"id": "x", "ok": True, "result": {"a": 1}})
           + "\n").encode("utf-8")
    fake = _FakeBridge([raw[:10], raw[10:]])  # staggered by _serve's sleep
    try:
        result = client.call("hello", {}, port=fake.port, timeout=5.0)
    finally:
        fake.join()
    assert result == {"a": 1}, result


def test_client_trailing_bytes_after_newline_ignored():
    # Regression test for the partition fix: bytes after the first "\n"
    # in the same chunk must be discarded, not fed to json.loads.
    from maya_tools.framework.toolbar.ai_bridge import client
    raw = (json.dumps({"id": "x", "ok": True, "result": {"a": 1}})
           + "\n").encode("utf-8")
    fake = _FakeBridge([raw + b"trailing garbage after the reply line"])
    try:
        result = client.call("hello", {}, port=fake.port, timeout=5.0)
    finally:
        fake.join()
    assert result == {"a": 1}, result


def test_tool_specs_shape_and_names():
    from maya_tools.framework.toolbar.ai_bridge import tool_specs
    expected = {
        "get_scene_summary", "get_node_details", "get_viewport_screenshot",
        "get_fabricator_status", "describe_rig", "get_component_details",
        "get_rig_binding", "run_build_checks", "validate_blueprint",
        "get_scene_report", "get_build_report", "read_doc", "get_skill",
        "report_bug",
    }
    names = {t["name"] for t in tool_specs.TOOLS}
    assert names == expected, names ^ expected
    for t in tool_specs.TOOLS:
        assert t["description"], t["name"]
        assert "READ-ONLY" in t["description"], t["name"]
        schema = t["input_schema"]
        assert schema["type"] == "object", t["name"]
        assert isinstance(schema.get("properties"), dict), t["name"]
        # every tool maps to a bridge op except report_bug (local)
        if t["name"] == "report_bug":
            assert t["op"] is None
        else:
            assert isinstance(t["op"], str) and t["op"], t["name"]
    # wire-contract drift guard: every op the specs cite must exist in
    # the bridge's OPS registry
    from maya_tools.framework.toolbar.ai_bridge import handlers
    ops = {t["op"] for t in tool_specs.TOOLS if t["op"] is not None}
    assert ops <= set(handlers.OPS), ops - set(handlers.OPS)


def test_tool_specs_anthropic_projection():
    from maya_tools.framework.toolbar.ai_bridge import tool_specs
    tools = tool_specs.anthropic_tools()
    assert len(tools) == len(tool_specs.TOOLS)
    for t in tools:
        assert set(t) == {"name", "description", "input_schema"}, t
    assert tool_specs.by_name("read_doc")["op"] == "read_doc"
    assert tool_specs.by_name("nope") is None
    # pin the deep-copy isolation: mutating a projection must not leak back
    tools[0]["input_schema"]["properties"]["injected"] = {"type": "string"}
    from maya_tools.framework.toolbar.ai_bridge import tool_specs as _ts
    assert "injected" not in _ts.TOOLS[0]["input_schema"]["properties"]


def test_vendored_bug_url_parity():
    import ast
    vendored = REPO_ROOT / ("maya_tools/framework/toolbar/reggie/_bug_url.py")
    original = REPO_ROOT / "fabricator-mcp" / "src" / "fabricator_mcp" / "bug_url.py"
    assert vendored.is_file() and original.is_file()
    # Compare code structure, not bytes: the vendored file carries its own
    # vendoring header docstring; everything below must match exactly.
    def body_dump(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # drop the module docstring node if present
        body = tree.body
        if body and isinstance(body[0], ast.Expr):
            body = body[1:]
        return [ast.dump(node) for node in body]
    assert body_dump(vendored) == body_dump(original)


def test_vendored_bug_url_behaves():
    from maya_tools.framework.toolbar.reggie import _bug_url
    url = _bug_url.build_issue_url(
        title="t", description="d", repro_steps="r",
        attempted_solutions="tried X", diagnostics={"mode": "empty"})
    assert url.startswith("https://github.com/adrianmelian/KinematicSolutions/issues/new")
    assert len(url) < 8000
    try:
        _bug_url.build_issue_url(title="t", description="d", repro_steps="r",
                                 attempted_solutions="   ", diagnostics={})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_keys_env_detection_and_masking():
    from maya_tools.framework.toolbar.reggie import keys
    saved = os.environ.pop(keys.ENV_VAR, None)
    try:
        assert keys.get_api_key() is None
        os.environ[keys.ENV_VAR] = "  sk-ant-test-abcd1234  "
        assert keys.get_api_key() == "sk-ant-test-abcd1234"
        m = keys.masked("sk-ant-test-abcd1234")
        assert m.endswith("1234") and "sk-ant-test-abcd" not in m, m
        assert keys.masked("short") == "(set)"
    finally:
        if saved is None:
            os.environ.pop(keys.ENV_VAR, None)
        else:
            os.environ[keys.ENV_VAR] = saved


def test_keys_refresh_injects_registry_value():
    from maya_tools.framework.toolbar.reggie import keys
    saved = os.environ.pop(keys.ENV_VAR, None)
    real_reader = keys._read_user_env_var
    try:
        keys._read_user_env_var = lambda name: "sk-ant-test-fromreg"
        val = keys.refresh_from_registry()
        assert val == "sk-ant-test-fromreg"
        assert os.environ[keys.ENV_VAR] == "sk-ant-test-fromreg"
        # env already set wins without touching the registry reader
        keys._read_user_env_var = lambda name: (_ for _ in ()).throw(AssertionError("must not be called"))
        assert keys.refresh_from_registry() == "sk-ant-test-fromreg"
    finally:
        keys._read_user_env_var = real_reader
        if saved is None:
            os.environ.pop(keys.ENV_VAR, None)
        else:
            os.environ[keys.ENV_VAR] = saved


class _FakeAPIServer:
    """One-shot loopback HTTP server: captures the request, replies with a
    canned status + SSE body. Runs on a daemon thread."""

    def __init__(self, status=200, sse_events=None, raw_body=b"",
                 sse_raw=None, stall=0.0):
        import socket as _socket
        self._sock = _socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self.status = status
        self.sse_events = sse_events or []
        self.raw_body = raw_body
        self.sse_raw = sse_raw      # verbatim 200 payload when given
        self.stall = stall          # seconds of silence after the headers
        self.captured = b""
        self._t = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        conn, _ = self._sock.accept()
        conn.settimeout(5.0)
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(65536)
        header, _, rest = data.partition(b"\r\n\r\n")
        length = 0
        for hline in header.split(b"\r\n"):
            if hline.lower().startswith(b"content-length:"):
                length = int(hline.split(b":", 1)[1])
        body = rest
        while len(body) < length:
            body += conn.recv(65536)
        self.captured = header + b"\r\n\r\n" + body
        if self.status == 200:
            if self.sse_raw is not None:
                payload = self.sse_raw
            else:
                payload = b"".join(
                    b"data: " + json.dumps(e).encode() + b"\n\n"
                    for e in self.sse_events)
            conn.sendall(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
                         b"content-length: " + str(len(payload)).encode()
                         + b"\r\n\r\n")
            if self.stall:
                time.sleep(self.stall)
            try:
                conn.sendall(payload)
            except OSError:
                pass    # client gave up during a stall; expected
        else:
            conn.sendall(b"HTTP/1.1 " + str(self.status).encode()
                         + b" NO\r\ncontent-length: "
                         + str(len(self.raw_body)).encode() + b"\r\n\r\n"
                         + self.raw_body)
        conn.close()

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._sock.close()


def _text_events(*chunks, stop_reason="end_turn"):
    events = [{"type": "message_start", "message": {}}]
    events.append({"type": "content_block_start", "index": 0,
                   "content_block": {"type": "text", "text": ""}})
    for c in chunks:
        events.append({"type": "content_block_delta", "index": 0,
                       "delta": {"type": "text_delta", "text": c}})
    events.append({"type": "content_block_stop", "index": 0})
    events.append({"type": "message_delta",
                   "delta": {"stop_reason": stop_reason}, "usage": {}})
    events.append({"type": "message_stop"})
    return events


def _client(port, timeout=5.0):
    from maya_tools.framework.toolbar.reggie import api_client
    return api_client.AnthropicClient(
        "sk-ant-test-key", host="127.0.0.1", port=port, use_tls=False,
        timeout=timeout)


def test_api_client_streams_text_and_sends_correct_request():
    with _FakeAPIServer(sse_events=_text_events("Hel", "lo")) as srv:
        got = list(_client(srv.port).stream_message(
            system="SYS", messages=[{"role": "user", "content": "hi"}],
            tools=[]))
    kinds = [k for k, _ in got]
    assert kinds == ["text", "text", "done"], kinds
    assert got[0][1] == "Hel" and got[1][1] == "lo"
    assert got[2][1]["stop_reason"] == "end_turn"
    header, _, body = srv.captured.partition(b"\r\n\r\n")
    assert b"x-api-key: sk-ant-test-key" in header
    assert b"anthropic-version:" in header
    sent = json.loads(body)
    assert sent["system"] == "SYS" and sent["stream"] is True
    assert sent["model"], sent


def test_api_client_assembles_tool_use_across_json_deltas():
    events = [
        {"type": "message_start", "message": {}},
        {"type": "content_block_start", "index": 0, "content_block":
            {"type": "tool_use", "id": "tu_1", "name": "read_doc", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta":
            {"type": "input_json_delta", "partial_json": '{"na'}},
        {"type": "content_block_delta", "index": 0, "delta":
            {"type": "input_json_delta", "partial_json": 'me": "troubleshooting"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {}},
        {"type": "message_stop"},
    ]
    with _FakeAPIServer(sse_events=events) as srv:
        got = list(_client(srv.port).stream_message(
            system="s", messages=[{"role": "user", "content": "x"}], tools=[]))
    tool_events = [p for k, p in got if k == "tool_use"]
    assert tool_events == [{"id": "tu_1", "name": "read_doc",
                            "input": {"name": "troubleshooting"}}], tool_events
    assert got[-1] == ("done", {"stop_reason": "tool_use"})


def test_api_client_auth_error():
    from maya_tools.framework.toolbar.reggie import api_client
    body = json.dumps({"error": {"message": "invalid x-api-key"}}).encode()
    with _FakeAPIServer(status=401, raw_body=body) as srv:
        try:
            list(_client(srv.port).stream_message(
                system="s", messages=[{"role": "user", "content": "x"}], tools=[]))
            raise AssertionError("expected ApiAuthError")
        except api_client.ApiAuthError as exc:
            assert "sk-ant-test-key" not in str(exc)


def test_api_client_stop_event_cuts_stream():
    stop = threading.Event()
    with _FakeAPIServer(sse_events=_text_events("a", "b", "c")) as srv:
        gen = _client(srv.port).stream_message(
            system="s", messages=[{"role": "user", "content": "x"}],
            tools=[], stop_event=stop)
        first = next(gen)
        assert first == ("text", "a")
        stop.set()
        rest = list(gen)
    assert rest and rest[-1] == ("stopped", None), rest


def test_api_client_error_event_raises():
    from maya_tools.framework.toolbar.reggie import api_client
    events = [{"type": "error",
               "error": {"type": "overloaded_error", "message": "later"}}]
    with _FakeAPIServer(sse_events=events) as srv:
        try:
            list(_client(srv.port).stream_message(
                system="s", messages=[{"role": "user", "content": "x"}], tools=[]))
            raise AssertionError("expected ApiError")
        except api_client.ApiError:
            pass


def test_api_client_truncated_stream_raises():
    from maya_tools.framework.toolbar.reggie import api_client
    events = _text_events("Hel", "lo")[:-1]    # message_stop never arrives
    with _FakeAPIServer(sse_events=events) as srv:
        try:
            list(_client(srv.port).stream_message(
                system="s", messages=[{"role": "user", "content": "x"}],
                tools=[]))
            raise AssertionError("expected ApiError")
        except api_client.ApiError as exc:
            assert "ended before" in str(exc), exc


def test_api_client_crlf_lines_parse():
    raw = (
        b'data: {"type": "message_start", "message": {}}\r\n\r\n'
        b'data: {"type": "content_block_start", "index": 0,'
        b' "content_block": {"type": "text", "text": ""}}\r\n\r\n'
        b'data: {"type": "content_block_delta", "index": 0,'
        b' "delta": {"type": "text_delta", "text": "Hi"}}\r\n\r\n'
        b'data: {"type": "content_block_stop", "index": 0}\r\n\r\n'
        b'data: {"type": "message_delta",'
        b' "delta": {"stop_reason": "end_turn"}, "usage": {}}\r\n\r\n'
        b'data: {"type": "message_stop"}\r\n\r\n')
    with _FakeAPIServer(sse_raw=raw) as srv:
        got = list(_client(srv.port).stream_message(
            system="s", messages=[{"role": "user", "content": "x"}],
            tools=[]))
    assert got == [("text", "Hi"),
                   ("done", {"stop_reason": "end_turn"})], got


def test_api_client_transport_error_wrapped():
    from maya_tools.framework.toolbar.reggie import api_client
    import socket as _socket
    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()  # nothing listening on dead_port now
    try:
        list(_client(dead_port).stream_message(
            system="s", messages=[{"role": "user", "content": "x"}],
            tools=[]))
        raise AssertionError("expected ApiError")
    except api_client.ApiError as exc:
        assert str(exc).startswith("Could not reach the provider"), exc
        assert "sk-ant-test-key" not in str(exc)


def test_api_client_stall_raises_and_stop_during_stall():
    from maya_tools.framework.toolbar.reggie import api_client
    # A stalled stream (headers arrive, then silence) must fail fast with
    # the stall message instead of hanging the caller.
    with _FakeAPIServer(sse_events=_text_events("x"), stall=3.0) as srv:
        try:
            list(_client(srv.port, timeout=1.0).stream_message(
                system="s", messages=[{"role": "user", "content": "x"}],
                tools=[]))
            raise AssertionError("expected ApiError")
        except api_client.ApiError as exc:
            assert "stalled" in str(exc), exc
    # stop_event set before iteration: the generator must yield
    # ('stopped', None) at loop entry without waiting out the stall.
    stop = threading.Event()
    stop.set()
    with _FakeAPIServer(sse_events=_text_events("x"), stall=3.0) as srv:
        t0 = time.time()
        got = list(_client(srv.port, timeout=1.0).stream_message(
            system="s", messages=[{"role": "user", "content": "x"}],
            tools=[], stop_event=stop))
        elapsed = time.time() - t0
    assert got == [("stopped", None)], got
    assert elapsed < 2.0, elapsed


def test_prime_system_prompt_carries_skill_and_etiquette():
    from maya_tools.framework.toolbar.reggie import prime
    system = prime.build_system_prompt()
    assert "You are Reggie" in system
    assert "fabricator-assistant" in system          # adapter paragraph
    assert "Assistant Etiquette" in system           # etiquette.md heading
    assert "Script Editor" in system                 # the one fix channel
    # SKILL.md's raw YAML frontmatter must not survive assembly
    assert "name: fabricator-assistant" not in system
    # the embedded-panel adapter paragraph must be present
    assert "no resource-fetch mechanism" in system


def test_prime_system_prompt_degrades_on_unreadable_files():
    import shutil
    import tempfile
    from maya_tools.framework.toolbar.reggie import prime
    saved_skill = prime._SKILL_PATH
    saved_etiquette = prime._ETIQUETTE_PATH
    tmpdir = Path(tempfile.mkdtemp(prefix="reggie_prime_test_"))
    try:
        # both bundled files missing: assemble with inline notes, no raise
        missing = tmpdir / "does_not_exist.md"
        prime._SKILL_PATH = missing
        prime._ETIQUETTE_PATH = missing
        system = prime.build_system_prompt()
        assert "(bundled file unreadable or missing:" in system, system
        assert "You are Reggie" in system
        # non-UTF-8 bytes in a bundled file: degrade the same way, no raise
        bad = tmpdir / "bad_encoding.md"
        bad.write_bytes(b"\xff\xfe caf\xe9")
        prime._SKILL_PATH = bad
        system = prime.build_system_prompt()
        assert "(bundled file unreadable or missing: bad_encoding.md" in system
    finally:
        prime._SKILL_PATH = saved_skill
        prime._ETIQUETTE_PATH = saved_etiquette
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_prime_scan_block_runs_all_ops_and_degrades_per_op():
    from maya_tools.framework.toolbar.reggie import prime
    seen = []

    def fake_call(op, params):
        seen.append(op)
        if op == "build_report":
            raise RuntimeError("not_found: no build has run\nsecond line")
        return {"op": op, "ok": 1}

    block = prime.build_scan_block(fake_call)
    assert seen == list(prime.SCAN_OPS), seen
    assert block.startswith("<scene_scan>") and block.endswith("</scene_scan>")
    assert '"ok": 1' in block
    assert "build_report: (unavailable:" in block
    # exception text is newline-flattened: one line per op + the two tags
    assert len(block.splitlines()) == len(prime.SCAN_OPS) + 2, block
    wrapped = prime.wrap_user_message("my rig broke", block)
    assert wrapped.endswith("my rig broke") and wrapped.startswith("<scene_scan>")


class _ScriptedClient:
    """Fake AnthropicClient: pops one scripted event list per API call."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls = []          # captured messages per call

    def stream_message(self, *, system, messages, tools, max_tokens=4096,
                       stop_event=None):
        self.calls.append([json.loads(json.dumps(m)) for m in messages])
        for ev in self.turns.pop(0):
            yield ev


def _collector():
    out = {"tokens": [], "status": [], "done": [], "errors": []}
    cbs = dict(on_token=out["tokens"].append, on_status=out["status"].append,
               on_done=out["done"].append, on_error=out["errors"].append)
    return out, cbs


def test_agent_plain_text_turn():
    from maya_tools.framework.toolbar.reggie import agent
    client = _ScriptedClient([
        ("text", "It is "), ("text", "fine."),
        ("done", {"stop_reason": "end_turn"}),
    ])
    session = agent.ChatSession(client, bridge_call=lambda op, p: {"op": op})
    out, cbs = _collector()
    session.run_turn("all good?", stop_event=threading.Event(), **cbs)
    assert out["errors"] == [], out["errors"]
    assert "".join(out["tokens"]) == "It is fine."
    assert out["done"] and out["done"][0]["stopped"] is False
    # history: primed user message + assistant reply
    assert session.history[0]["role"] == "user"
    assert session.history[0]["content"].startswith("<scene_scan>")
    assert session.history[1] == {"role": "assistant", "content":
        [{"type": "text", "text": "It is fine."}]}


def test_agent_tool_use_cycle():
    from maya_tools.framework.toolbar.reggie import agent
    client = _ScriptedClient(
        [("tool_use", {"id": "tu_1", "name": "get_build_report", "input": {}}),
         ("done", {"stop_reason": "tool_use"})],
        [("text", "Fixed idea."), ("done", {"stop_reason": "end_turn"})],
    )
    bridge_ops = []

    def bridge_call(op, params):
        bridge_ops.append(op)
        return {"answer": op}

    session = agent.ChatSession(client, bridge_call=bridge_call)
    out, cbs = _collector()
    session.run_turn("why did build fail?", stop_event=threading.Event(), **cbs)
    assert out["errors"] == [], out["errors"]
    # scan ops ran once + the tool's own op
    assert bridge_ops.count("build_report") == 2   # scan + tool call
    # second API call carried the tool_result
    second = client.calls[1]
    assert second[-1]["role"] == "user"
    tr = second[-1]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "tu_1"
    assert out["done"][0]["checked"] == ["get_build_report"]


def test_agent_report_bug_executes_locally():
    from maya_tools.framework.toolbar.reggie import agent
    client = _ScriptedClient(
        [("tool_use", {"id": "tu_2", "name": "report_bug", "input": {
            "title": "t", "description": "d", "repro_steps": "r",
            "attempted_solutions": "tried unlock script",
            "include_diagnostics": True}}),
         ("done", {"stop_reason": "tool_use"})],
        [("text", "Filed URL ready."), ("done", {"stop_reason": "end_turn"})],
    )
    session = agent.ChatSession(
        client, bridge_call=lambda op, p: {"mode": "empty"})
    out, cbs = _collector()
    session.run_turn("file it", stop_event=threading.Event(), **cbs)
    assert out["errors"] == [], out["errors"]
    tr = client.calls[1][-1]["content"][0]
    assert "github.com/adrianmelian/KinematicSolutions/issues/new" in tr["content"]


def test_agent_trim_keeps_plain_user_first():
    from maya_tools.framework.toolbar.reggie import agent
    session = agent.ChatSession(_ScriptedClient(), bridge_call=lambda o, p: {})
    # simulate a long history; first trimmed survivor lands on an ASSISTANT
    # message, so the pop-until-plain-user loop must also run
    session.history = [{"role": "user", "content": [{"type": "tool_result",
                        "tool_use_id": "x", "content": "{}"}]}]
    session.history += [{"role": "user", "content": f"u{i}"} if i % 2 == 0
                        else {"role": "assistant", "content":
                              [{"type": "text", "text": f"a{i}"}]}
                        for i in range(39)]
    session._trim()
    assert len(session.history) <= agent.HISTORY_LIMIT
    first = session.history[0]
    assert first["role"] == "user" and isinstance(first["content"], str)


def test_agent_stop_event_mid_stream():
    from maya_tools.framework.toolbar.reggie import agent
    stop = threading.Event()
    client = _ScriptedClient([("text", "part"), ("stopped", None)])
    session = agent.ChatSession(client, bridge_call=lambda o, p: {})
    out, cbs = _collector()
    session.run_turn("q", stop_event=stop, **cbs)
    assert out["done"] and out["done"][0]["stopped"] is True
    assert session.busy is False


def test_agent_api_error_reported_friendly():
    from maya_tools.framework.toolbar.reggie import agent, api_client

    class _Boom:
        def stream_message(self, **kw):
            raise api_client.ApiAuthError("The provider rejected your API key.")
            yield  # pragma: no cover

    session = agent.ChatSession(_Boom(), bridge_call=lambda o, p: {})
    out, cbs = _collector()
    session.run_turn("q", stop_event=threading.Event(), **cbs)
    assert out["errors"] and "rejected" in out["errors"][0]
    assert session.busy is False
    # failed turn rolled back: no dangling user message (two consecutive
    # user turns would 400 on the next send)
    assert session.history == [], session.history


def test_agent_stop_after_tool_use_leaves_legal_history_for_next_turn():
    from maya_tools.framework.toolbar.reggie import agent
    client = _ScriptedClient(
        [("tool_use", {"id": "tu_1", "name": "get_scene_summary", "input": {}}),
         ("stopped", None)],
        [("text", "resumed"), ("done", {"stop_reason": "end_turn"})],
    )
    session = agent.ChatSession(client, bridge_call=lambda op, p: {"op": op})
    out, cbs = _collector()
    session.run_turn("do a thing", stop_event=threading.Event(), **cbs)
    assert out["done"][0]["stopped"] is True
    assert session.history[-1]["role"] == "assistant", session.history
    out2, cbs2 = _collector()
    session.run_turn("continue", stop_event=threading.Event(), **cbs2)
    roles = [m["role"] for m in client.calls[-1]]
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles


def test_agent_stop_between_tool_calls_in_one_round_stays_legal():
    from maya_tools.framework.toolbar.reggie import agent
    client = _ScriptedClient(
        [("tool_use", {"id": "tu_a", "name": "get_scene_summary", "input": {}}),
         ("tool_use", {"id": "tu_b", "name": "get_fabricator_status", "input": {}}),
         ("done", {"stop_reason": "tool_use"})],
    )
    stop = threading.Event()
    seen = []

    def bridge_call(op, p):
        if len(seen) == 4:   # first real tool call, after the 4 scan ops
            stop.set()
        seen.append(op)
        return {"op": op}

    session = agent.ChatSession(client, bridge_call=bridge_call)
    out, cbs = _collector()
    session.run_turn("do two things", stop_event=stop, **cbs)
    assert session.history[-1]["role"] == "assistant", session.history
    # the tool that actually ran must carry its REAL result, not "cancelled"
    tool_results = [m for m in session.history
                    if m["role"] == "user" and isinstance(m["content"], list)]
    flat = [b for m in tool_results for b in m["content"]]
    real = [b for b in flat if b["tool_use_id"] == "tu_a"]
    assert real and "cancelled" not in str(real[0]["content"]), real


def test_agent_stop_before_any_content_stays_legal():
    from maya_tools.framework.toolbar.reggie import agent
    # Case 1: stop fires before ANY event streams (user hit Stop during the
    # scan). _append_assistant adds nothing, so the tail would be this
    # turn's plain-string user message — the guard must close it, or the
    # next send makes user,user (400) and rollback can never remove it.
    client = _ScriptedClient(
        [("stopped", None)],                                     # nothing at all
        [("text", "hi"), ("done", {"stop_reason": "end_turn"})],  # next turn
    )
    session = agent.ChatSession(client, bridge_call=lambda op, p: {"op": op})
    out, cbs = _collector()
    session.run_turn("q1", stop_event=threading.Event(), **cbs)
    assert out["done"] and out["done"][0]["stopped"] is True, out
    assert session.history[-1]["role"] == "assistant", session.history
    out2, cbs2 = _collector()
    session.run_turn("q2", stop_event=threading.Event(), **cbs2)
    assert out2["errors"] == [], out2["errors"]
    roles = [m["role"] for m in client.calls[-1]]
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles
    # Case 2: degenerate empty completion — done arrives with zero content
    # blocks. Same tail shape, same guard; the turn reports stopped=False.
    client = _ScriptedClient(
        [("done", {"stop_reason": "end_turn"})],                 # empty message
        [("text", "hi"), ("done", {"stop_reason": "end_turn"})],
    )
    session = agent.ChatSession(client, bridge_call=lambda op, p: {"op": op})
    out, cbs = _collector()
    session.run_turn("q1", stop_event=threading.Event(), **cbs)
    assert out["errors"] == [], out["errors"]
    assert out["done"] and out["done"][0]["stopped"] is False, out
    assert session.history[-1]["role"] == "assistant", session.history
    out2, cbs2 = _collector()
    session.run_turn("q2", stop_event=threading.Event(), **cbs2)
    assert out2["errors"] == [], out2["errors"]
    roles = [m["role"] for m in client.calls[-1]]
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles


def test_execute_survives_non_finite_bridge_result():
    from maya_tools.framework.toolbar.reggie import agent
    client = _ScriptedClient(
        [("tool_use", {"id": "tu_1", "name": "get_scene_summary", "input": {}}),
         ("done", {"stop_reason": "tool_use"})],
        [("text", "ok"), ("done", {"stop_reason": "end_turn"})],
    )
    session = agent.ChatSession(client, bridge_call=lambda op, p: {"value": float("nan")})
    out, cbs = _collector()
    session.run_turn("go", stop_event=threading.Event(), **cbs)
    # fixed behavior: the turn completes; the bad result became readable
    # error content inside the tool_result, and nothing was rolled back
    assert out["errors"] == [], out["errors"]
    assert out["done"] and out["done"][0]["stopped"] is False
    tr = client.calls[1][-1]["content"][0]
    assert "not JSON-serializable" in tr["content"], tr
    assert session.history, "turn must not be rolled back"


def test_key_never_leaks_into_outputs():
    from maya_tools.framework.toolbar.reggie import agent, api_client, keys, prime
    canary = "sk-ant-test-LEAKCANARYXYZW"
    outputs = []
    # The canary sits in the environment for ALL output generation below,
    # proving that mere env presence can't leak into any rendered surface
    # (error text, status text, masked display, scan block, system prompt).
    saved = os.environ.get(keys.ENV_VAR)
    os.environ[keys.ENV_VAR] = canary
    try:
        # auth-failure path
        outputs.append(str(
            api_client.AnthropicClient(canary)._status_error(401, b"{}")))
        outputs.append(str(
            api_client.AnthropicClient(canary)._status_error(529, b"{}")))
        # generic-status path: a proxy/MITM error page that echoes the
        # request headers (the key) back must come out redacted
        echoed = json.dumps({"error": {
            "message": f"upstream saw x-api-key: {canary}"}}).encode()
        generic = str(
            api_client.AnthropicClient(canary)._status_error(500, echoed))
        assert "[redacted]" in generic, generic[:160]
        outputs.append(generic)
        # masked display
        outputs.append(keys.masked(canary))
        # scan-block failure notes
        def boom(op, params):
            raise RuntimeError("bridge down")
        outputs.append(prime.build_scan_block(boom))
        # system prompt
        outputs.append(prime.build_system_prompt())
        # agent error path: no callback (errors or status) may leak it
        class _Boom:
            def stream_message(self, **kw):
                raise api_client.ApiAuthError(
                    "The provider rejected your API key. Check "
                    "ANTHROPIC_API_KEY and click Refresh in the panel.")
                yield  # pragma: no cover

        session = agent.ChatSession(_Boom(), bridge_call=lambda o, p: {})
        out, cbs = _collector()
        session.run_turn("q", stop_event=threading.Event(), **cbs)
        outputs.append(" ".join(out["errors"]))
        outputs.append(" ".join(out["status"]))
    finally:
        if saved is None:
            os.environ.pop(keys.ENV_VAR, None)
        else:
            os.environ[keys.ENV_VAR] = saved
    for text in outputs:
        assert canary not in text, text[:120]


def test_malformed_key_with_newline_does_not_leak_via_header_error():
    """A key containing \\r or \\n makes http.client's putheader raise a
    bare ValueError whose message contains THE RAW KEY. ValueError is
    neither OSError nor HTTPException, so it escapes stream_message's
    transport-error wrapper, and run_turn's generic Exception handler
    reports it to on_error as repr(exc) — key and all. The guard at the
    top of stream_message must reject such a key with a clean
    ApiAuthError before any header is built."""
    from maya_tools.framework.toolbar.reggie import agent, api_client
    canary = "sk-ant-test-LEAKCANARY\nXYZW"
    client = api_client.AnthropicClient(canary, host="127.0.0.1", port=1)
    session = agent.ChatSession(client, bridge_call=lambda o, p: {})
    out, cbs = _collector()
    session.run_turn("q", stop_event=threading.Event(), **cbs)
    assert out["errors"], "expected the turn to report an error"
    for text in out["errors"]:
        assert "LEAKCANARY" not in text, text[:160]


def test_keys_masked_length_boundary():
    from maya_tools.framework.toolbar.reggie import keys
    # the >= 12 threshold, pinned exactly
    assert keys.masked("x" * 12).startswith("..."), keys.masked("x" * 12)
    assert keys.masked("x" * 12).endswith("xxxx")
    assert keys.masked("x" * 11) == "(set)"


def test_reggie_dock_importable_without_maya_side_effects():
    import importlib
    mod = importlib.import_module(
        "maya_tools.framework.toolbar.reggie.reggie_dock")
    assert mod.DOCK_NAME == "FSReggieDock"
    assert callable(mod.toggle) and callable(mod.open_docked)
    assert callable(mod.close_dock) and callable(mod.is_open)
    assert "reggie_dock._populate()" in mod._UI_SCRIPT


def main():
    check("test_client_roundtrip_ok", test_client_roundtrip_ok)
    check("test_client_op_error_raises_bridge_op_error",
          test_client_op_error_raises_bridge_op_error)
    check("test_client_nothing_listening_raises_bridge_down",
          test_client_nothing_listening_raises_bridge_down)
    check("test_client_malformed_json_reply_raises_bridge_call_error",
          test_client_malformed_json_reply_raises_bridge_call_error)
    check("test_client_non_utf8_reply_raises_bridge_call_error",
          test_client_non_utf8_reply_raises_bridge_call_error)
    check("test_client_reply_split_across_two_chunks",
          test_client_reply_split_across_two_chunks)
    check("test_client_trailing_bytes_after_newline_ignored",
          test_client_trailing_bytes_after_newline_ignored)
    check("test_tool_specs_shape_and_names", test_tool_specs_shape_and_names)
    check("test_tool_specs_anthropic_projection",
          test_tool_specs_anthropic_projection)
    check("test_vendored_bug_url_parity", test_vendored_bug_url_parity)
    check("test_vendored_bug_url_behaves", test_vendored_bug_url_behaves)
    check("test_keys_env_detection_and_masking",
          test_keys_env_detection_and_masking)
    check("test_keys_refresh_injects_registry_value",
          test_keys_refresh_injects_registry_value)
    check("test_api_client_streams_text_and_sends_correct_request",
          test_api_client_streams_text_and_sends_correct_request)
    check("test_api_client_assembles_tool_use_across_json_deltas",
          test_api_client_assembles_tool_use_across_json_deltas)
    check("test_api_client_auth_error", test_api_client_auth_error)
    check("test_api_client_stop_event_cuts_stream",
          test_api_client_stop_event_cuts_stream)
    check("test_api_client_error_event_raises",
          test_api_client_error_event_raises)
    check("test_api_client_truncated_stream_raises",
          test_api_client_truncated_stream_raises)
    check("test_api_client_crlf_lines_parse", test_api_client_crlf_lines_parse)
    check("test_api_client_transport_error_wrapped",
          test_api_client_transport_error_wrapped)
    check("test_api_client_stall_raises_and_stop_during_stall",
          test_api_client_stall_raises_and_stop_during_stall)
    check("test_prime_system_prompt_carries_skill_and_etiquette",
          test_prime_system_prompt_carries_skill_and_etiquette)
    check("test_prime_system_prompt_degrades_on_unreadable_files",
          test_prime_system_prompt_degrades_on_unreadable_files)
    check("test_prime_scan_block_runs_all_ops_and_degrades_per_op",
          test_prime_scan_block_runs_all_ops_and_degrades_per_op)
    check("test_agent_plain_text_turn", test_agent_plain_text_turn)
    check("test_agent_tool_use_cycle", test_agent_tool_use_cycle)
    check("test_agent_report_bug_executes_locally",
          test_agent_report_bug_executes_locally)
    check("test_agent_trim_keeps_plain_user_first",
          test_agent_trim_keeps_plain_user_first)
    check("test_agent_stop_event_mid_stream", test_agent_stop_event_mid_stream)
    check("test_agent_api_error_reported_friendly",
          test_agent_api_error_reported_friendly)
    check("test_agent_stop_after_tool_use_leaves_legal_history_for_next_turn",
          test_agent_stop_after_tool_use_leaves_legal_history_for_next_turn)
    check("test_agent_stop_between_tool_calls_in_one_round_stays_legal",
          test_agent_stop_between_tool_calls_in_one_round_stays_legal)
    check("test_agent_stop_before_any_content_stays_legal",
          test_agent_stop_before_any_content_stays_legal)
    check("test_execute_survives_non_finite_bridge_result",
          test_execute_survives_non_finite_bridge_result)
    check("test_key_never_leaks_into_outputs",
          test_key_never_leaks_into_outputs)
    check("test_malformed_key_with_newline_does_not_leak_via_header_error",
          test_malformed_key_with_newline_does_not_leak_via_header_error)
    check("test_keys_masked_length_boundary",
          test_keys_masked_length_boundary)
    check("test_reggie_dock_importable_without_maya_side_effects",
          test_reggie_dock_importable_without_maya_side_effects)

    if FAILURES:
        print(f"TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("TESTS: OK")


if __name__ == "__main__":
    main()
