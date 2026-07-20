"""Tests for fabricator_mcp.bridge_client against a one-shot fake bridge:
a tiny socket server that accepts one connection, reads one NDJSON line,
and writes back one canned response line. No real Maya involved."""
__author__ = "Adrian Melian"

import json
import socket
import threading
import time

import pytest

from fabricator_mcp import bridge_client
from fabricator_mcp.bridge_client import (
    BridgeDownError,
    BridgeError,
    BridgeOpError,
    ProtocolMismatchError,
)


class FakeBridge:
    """A one-shot fake bridge server on an ephemeral loopback port.

    Accepts exactly one connection, reads one NDJSON request line, hands
    it to the caller (via .requests), and writes back the canned
    response line(s) it was constructed with.
    """

    def __init__(self, response_chunks):
        """response_chunks: list[bytes] sent, in order, each via its own
        sock.sendall() call — lets tests simulate a reply arriving across
        multiple recv() chunks."""
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


def _make_fake(result=None, error=None, protocol=1, raw=None):
    """Build a FakeBridge's canned response as a single bytes chunk list,
    unless raw bytes/lines are given directly."""
    if raw is not None:
        chunks = raw if isinstance(raw, list) else [raw]
        return FakeBridge(chunks)
    if error is not None:
        payload = {"id": "whatever", "ok": False, "error": error}
    else:
        payload = {"id": "whatever", "ok": True, "result": result or {}}
    line = (json.dumps(payload) + "\n").encode("utf-8")
    return FakeBridge([line])


def test_call_hello_ok():
    fake = _make_fake(result={"protocol": 1, "bridge_version": "1.2.3"})
    try:
        result = bridge_client.call("hello", port=fake.port)
    finally:
        fake.join()
    assert result == {"protocol": 1, "bridge_version": "1.2.3"}


def test_call_ok_false_raises_bridge_op_error():
    fake = _make_fake(error={"code": "not_found", "message": "no such node"})
    try:
        with pytest.raises(BridgeOpError) as excinfo:
            bridge_client.call("get_node", params={"path": "|nope"}, port=fake.port)
    finally:
        fake.join()
    assert excinfo.value.code == "not_found"
    assert "no such node" in str(excinfo.value)


def test_call_nothing_listening_raises_bridge_down_error():
    # Bind then immediately close an ephemeral port so nothing is
    # listening there for the actual call.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    with pytest.raises(BridgeDownError) as excinfo:
        bridge_client.call("hello", port=dead_port, timeout=2.0)

    message = str(excinfo.value)
    assert "DevBot toolbar" in message
    assert "Connect your AI" in message


def test_call_reply_across_two_chunks():
    line = json.dumps({"id": "x", "ok": True, "result": {"n": 1}}) + "\n"
    encoded = line.encode("utf-8")
    split = len(encoded) // 2
    fake = _make_fake(raw=[encoded[:split], encoded[split:]])
    try:
        result = bridge_client.call("hello", port=fake.port)
    finally:
        fake.join()
    assert result == {"n": 1}


def test_call_malformed_reply_raises_bridge_error_not_hang():
    fake = _make_fake(raw=b"not json\n")
    try:
        with pytest.raises(BridgeError) as excinfo:
            bridge_client.call("hello", port=fake.port, timeout=2.0)
    finally:
        fake.join()
    assert not isinstance(excinfo.value, (BridgeDownError, BridgeOpError))


def test_call_peer_closes_without_newline_raises_bridge_error():
    # No trailing '\n' at all: the fake server sends a truncated line and
    # then closes. Must not hang, must not surface as BridgeDownError (the
    # connection itself succeeded), and must not leak a raw ValueError.
    fake = _make_fake(raw=b'{"id": "x"')
    try:
        with pytest.raises(BridgeError) as excinfo:
            bridge_client.call("hello", port=fake.port, timeout=2.0)
    finally:
        fake.join()
    assert not isinstance(excinfo.value, (BridgeDownError, BridgeOpError))


def test_call_non_utf8_reply_raises_bridge_error():
    # Invalid UTF-8 bytes followed by a newline. Regression test: this
    # used to leak a raw UnicodeDecodeError out of call() because decode()
    # ran inside the socket-error try/except, uncaught by either that
    # except (not an OSError) or the later `except ValueError` around
    # json.loads (which ran after the with-block had already exited).
    fake = _make_fake(raw=b"\xff\xfe\x00bad\n")
    try:
        with pytest.raises(BridgeError) as excinfo:
            bridge_client.call("hello", port=fake.port, timeout=2.0)
    finally:
        fake.join()
    assert not isinstance(excinfo.value, (BridgeDownError, BridgeOpError))


def test_call_reply_missing_ok_key_raises_bridge_error():
    line = json.dumps({"id": "x", "result": {"n": 1}}) + "\n"
    fake = _make_fake(raw=line.encode("utf-8"))
    try:
        with pytest.raises(BridgeError) as excinfo:
            bridge_client.call("hello", port=fake.port, timeout=2.0)
    finally:
        fake.join()
    assert not isinstance(excinfo.value, (BridgeDownError, BridgeOpError))


def test_call_ok_false_with_malformed_error_object_raises_bridge_error():
    line = json.dumps({"id": "x", "ok": False, "error": {"code": "not_found"}}) + "\n"
    fake = _make_fake(raw=line.encode("utf-8"))
    try:
        with pytest.raises(BridgeError) as excinfo:
            bridge_client.call("hello", port=fake.port, timeout=2.0)
    finally:
        fake.join()
    assert not isinstance(excinfo.value, (BridgeDownError, BridgeOpError))


def test_check_handshake_protocol_match():
    fake = _make_fake(result={"protocol": 1, "bridge_version": "1.2.3"})
    try:
        result = bridge_client.check_handshake(port=fake.port)
    finally:
        fake.join()
    assert result == {"protocol": 1, "bridge_version": "1.2.3"}


def test_check_handshake_protocol_mismatch():
    fake = _make_fake(result={"protocol": 2, "bridge_version": "9.9.9"})
    try:
        with pytest.raises(ProtocolMismatchError) as excinfo:
            bridge_client.check_handshake(port=fake.port)
    finally:
        fake.join()
    message = str(excinfo.value)
    assert "1" in message and "2" in message
    assert "update" in message.lower()


def test_request_line_shape():
    fake = _make_fake(result={"protocol": 1, "bridge_version": "1.2.3"})
    try:
        bridge_client.call("hello", params={"foo": "bar"}, port=fake.port)
    finally:
        fake.join()

    assert len(fake.requests) == 1
    sent = json.loads(fake.requests[0])
    assert isinstance(sent, dict)
    assert isinstance(sent.get("id"), str) and sent["id"]
    assert sent.get("op") == "hello"
    assert "params" in sent and sent["params"] == {"foo": "bar"}
