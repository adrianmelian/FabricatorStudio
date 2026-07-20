"""Minimal stdlib streaming client for the Anthropic Messages API.

Deliberately NOT the official `anthropic` package: it drags in httpx,
anyio, and pydantic (pydantic-core is a compiled per-platform Rust wheel
that cannot ship inside one toolset zip). The Messages API is HTTPS plus
server-sent events; this whole client is auditable in one sitting, which
is part of the read-only-and-open story.

Provider seam: OpenAI-compatible/local providers slot in later as a
sibling class exposing the same stream_message() generator contract.

SECURITY: the API key must never appear in any exception message, log
line, or status text (enforced by test_reggie_core.py).
"""
from __future__ import annotations

import http.client
import json
import os

__author__ = "Adrian Melian"

DEFAULT_MODEL = "claude-sonnet-5"
MODEL_ENV_VAR = "FABRICATOR_AI_MODEL"
_API_VERSION = "2023-06-01"


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        self.status = status
        super().__init__(message)


class ApiAuthError(ApiError):
    """401/403: bad or missing key."""


class ApiBusyError(ApiError):
    """429/529: rate limited or overloaded."""


class AnthropicClient:

    def __init__(self, api_key: str, *, model: str | None = None,
                 host: str = "api.anthropic.com", port: int = 443,
                 use_tls: bool = True, timeout: float = 60.0):
        self._api_key = api_key
        self.model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
        self._host = host
        self._port = port
        self._use_tls = use_tls
        self._timeout = timeout

    def stream_message(self, *, system, messages, tools, max_tokens=4096,
                       stop_event=None):
        """Generator of events:
          ('text', str)                          streamed text delta
          ('tool_use', {'id','name','input'})    one complete tool call
          ('done', {'stop_reason': str})         normal end of the message
          ('stopped', None)                      stop_event fired; ends stream
        Raises ApiAuthError / ApiBusyError / ApiError.

        A silent socket (no bytes before the timeout) raises ApiError
        instead of blocking the caller for good; Anthropic emits periodic
        `ping` events while generating, so a healthy stream never sits
        quiet for the full socket timeout.
        """
        # A key carrying \r or \n would make http.client's putheader raise
        # a bare ValueError CONTAINING THE RAW KEY, which is neither
        # OSError nor HTTPException and so would escape the transport
        # wrapper below and reach the panel via repr(exc). Reject it
        # cleanly before any header is built.
        if any(c in self._api_key for c in ("\r", "\n")):
            raise ApiAuthError(
                "ANTHROPIC_API_KEY contains invalid characters (a stray "
                "newline?). Re-copy the key and click Refresh in the panel.")
        body = json.dumps(
            {"model": self.model, "max_tokens": max_tokens, "system": system,
             "messages": messages, "tools": tools, "stream": True},
            allow_nan=False).encode("utf-8")
        cls = (http.client.HTTPSConnection if self._use_tls
               else http.client.HTTPConnection)
        conn = cls(self._host, self._port, timeout=self._timeout)
        try:
            conn.request("POST", "/v1/messages", body=body, headers={
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
                "accept": "text/event-stream",
            })
            resp = conn.getresponse()
            if resp.status != 200:
                raise self._status_error(resp.status, resp.read())
            yield from self._parse_sse(resp, stop_event)
        except (OSError, http.client.HTTPException) as exc:
            # Transport-level failures (DNS, refused, reset, TLS, timeout
            # on connect) surface as one typed error. The ApiError raise
            # sites above subclass neither OSError nor HTTPException, so
            # they pass through unwrapped; read-stall TimeoutErrors are
            # already converted inside _parse_sse; GeneratorExit is
            # BaseException and is never caught here.
            raise ApiError(f"Could not reach the provider: {exc}") from exc
        finally:
            conn.close()

    def _status_error(self, status: int, raw: bytes) -> ApiError:
        try:
            detail = json.loads(raw.decode("utf-8"))["error"]["message"]
        except Exception:
            detail = raw[:200].decode("utf-8", "replace")
        # The generic branch below echoes server bytes into user-facing
        # text; a MITM/proxy error page can echo request headers (the key)
        # back. Redact the configured key before message construction.
        if self._api_key:
            detail = detail.replace(self._api_key, "[redacted]")
        if status in (401, 403):
            return ApiAuthError(
                "The provider rejected your API key. Check ANTHROPIC_API_KEY "
                "and click Refresh in the panel.", status=status)
        if status in (429, 529):
            return ApiBusyError(
                "The provider is busy or rate-limited right now. Wait a "
                "moment and try again.", status=status)
        return ApiError(f"API error {status}: {detail}", status=status)

    @staticmethod
    def _parse_sse(resp, stop_event):
        tool = None    # in-flight tool_use block: {'id','name','buf','index'}
        stop_reason = None
        while True:
            if stop_event is not None and stop_event.is_set():
                yield ("stopped", None)
                return
            try:
                raw_line = resp.readline()
            except TimeoutError as exc:
                # Never resume reading after a socket timeout: a timeout
                # mid-read leaves the buffered/chunked HTTPResponse parser
                # in an undefined state. Honor a pending stop, else fail.
                if stop_event is not None and stop_event.is_set():
                    yield ("stopped", None)
                    return
                raise ApiError("The stream stalled (no data before the "
                               "timeout). Try again.") from exc
            if not raw_line:
                break        # EOF
            line = raw_line.strip()
            if not line.startswith(b"data:"):
                continue     # 'event:' lines, comments, keep-alives
            try:
                event = json.loads(line[5:].strip().decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ApiError(f"Malformed stream event from provider: {exc}")
            etype = event.get("type")
            if etype == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    tool = {"id": block.get("id", ""),
                            "name": block.get("name", ""), "buf": "",
                            "index": event.get("index")}
            elif etype == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    yield ("text", delta.get("text", ""))
                elif (delta.get("type") == "input_json_delta"
                        and tool is not None
                        and event.get("index") == tool["index"]):
                    tool["buf"] += delta.get("partial_json", "")
            elif etype == "content_block_stop":
                if tool is not None and event.get("index") == tool["index"]:
                    try:
                        tool_input = (json.loads(tool["buf"])
                                      if tool["buf"].strip() else {})
                    except ValueError as exc:
                        # Distinguish protocol corruption from a genuinely
                        # empty input; never silently drop a broken call.
                        tool_input = {"_parse_error": str(exc)}
                    yield ("tool_use", {"id": tool["id"], "name": tool["name"],
                                        "input": tool_input})
                    tool = None
            elif etype == "message_delta":
                stop_reason = ((event.get("delta") or {}).get("stop_reason")
                               or stop_reason)
            elif etype == "message_stop":
                yield ("done", {"stop_reason": stop_reason or "end_turn"})
                return
            elif etype == "error":
                err = event.get("error") or {}
                raise ApiError(
                    f"Provider stream error: {err.get('message', 'unknown')}")
            # message_start, ping: ignored
        # EOF without message_stop: the connection dropped or a proxy cut
        # the stream mid-message. Never report truncation as success.
        raise ApiError(
            "The stream ended before the provider signaled completion "
            "(connection dropped?). The answer may be incomplete.")
