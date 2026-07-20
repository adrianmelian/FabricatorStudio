# maya_tools/framework/toolbar/ai_bridge/service.py
"""BridgeService — loopback socket service with main-thread execution.

Two-layer architecture, the LiveBackend idiom (see
maya_tools/utils/maya/live_tool.py for the house pattern this mirrors): a
socket accept thread plus one reader thread per connection ONLY parse and
queue requests — no maya, no Qt, no `dispatch` call happens on those
threads. A QTimer on the Qt main thread (or, in tests, a hand-driven call
to `_drain()`) is the ONLY layer that invokes `dispatch`, which is where
maya.cmds actually gets touched. Replies cross back from the Qt thread to
the waiting reader thread through a per-request reply queue.

`dispatch` is injected at construction — callable(op, params) -> dict —
so this module never imports maya or handlers itself. That is what lets
the offscreen suite (_dev/test_ai_bridge.py) construct a BridgeService
with a fake dispatch and drive `_drain()` by hand, with zero maya imports
anywhere in this file's own import chain. The real handlers.dispatch is
wired in at runtime by this package's __init__.py singleton, which IS
maya-aware and is only ever invoked from inside a running Maya session.
"""
__author__ = "Adrian Melian"

import queue
import socket
import threading

from maya_tools.framework.toolbar.ai_bridge import protocol

_DRAIN_MS = 50                   # QTimer interval on the Qt main thread
_ACCEPT_TIMEOUT_S = 0.5          # server-socket accept() timeout, so the
                                 # accept loop notices stop() promptly
_JOIN_TIMEOUT_S = 2.0            # stop() waits at most this long for the
                                 # accept thread to exit
_DEFAULT_REPLY_TIMEOUT_S = 10.0  # default wait for a queued request's
                                 # reply before the reader thread gives up
                                 # and answers 'maya_busy' itself
_DRAIN_MAX_PER_TICK = 64         # cap on requests answered per _drain() call
                                 # (M1: a burst across many connections must
                                 # not stall the Qt main thread for the sum
                                 # of every one of their dispatch costs in a
                                 # single 50ms tick — leftovers wait for the
                                 # timer's next tick instead)

# Sanity bound on a single un-newlined request buffer. protocol/handlers
# already cap the RESPONSE side (MAX_RESPONSE_BYTES); request lines are
# client-controlled and there is no product-level cap to enforce on their
# *content* here — but a connection that never sends a newline must not be
# allowed to grow this buffer without limit forever. Generous on purpose;
# this exists to bound a runaway/misbehaving client, not to constrain any
# legitimate request shape.
_MAX_LINE_BYTES = 1_000_000


class BridgeService:
    """Loopback (127.0.0.1-only) NDJSON socket service.

    dispatch: callable(op, params) -> dict, invoked ONLY from `_drain()`
    on the Qt main thread (or by a test calling `_drain()` directly).
    port: requested port. 0 means an OS-assigned ephemeral port (tests
    only — real callers pass the port resolved from toolbar prefs, or
    protocol.DEFAULT_PORT). self.port is None until start() has actually
    bound a socket, and is set back to None on stop().
    reply_timeout_s: how long a client's request line waits for the drain
    to answer before the reader thread gives up and replies 'maya_busy'.
    Exposed as a constructor kwarg (not a bare module constant) precisely
    so tests can drive it down to a fraction of a second instead of
    waiting out the real ~10s default to exercise that path.
    """

    def __init__(self, dispatch, port: int = protocol.DEFAULT_PORT,
                 reply_timeout_s: float = _DEFAULT_REPLY_TIMEOUT_S):
        self._dispatch = dispatch
        self._requested_port = port
        self._reply_timeout_s = reply_timeout_s

        self.port = None                 # bound port; set by start(), cleared by stop()
        self._server_sock = None
        self._accept_thread = None
        self._pending = queue.Queue()    # (protocol.Request, reply_q) tuples
        self._timer = None
        self._running = False

        # Guards two things ONLY, both quick/non-blocking: (1) the
        # start()/stop() lifecycle check-then-act plus their socket/timer/
        # conn-set mutations, and (2) add/discard on self._conns below.
        # NEVER held across a socket recv/send, a dispatch() call, or a
        # thread join — see start()/stop()'s own comments for exactly
        # where the lock is released before anything that could block.
        self._lock = threading.Lock()
        self._conns = set()              # live per-connection sockets, so
                                          # stop() can close them and unblock
                                          # any reader thread parked in recv()

    # -- lifecycle -------------------------------------------------------

    def start(self, use_qt_timer: bool = True) -> None:
        """Idempotent — a no-op if already running. Binds 127.0.0.1 ONLY;
        never 0.0.0.0 — this bridge is loopback-only by design (spec 3.1),
        never reachable off the local machine.

        use_qt_timer=True (the real Maya path) creates a QTimer on the Qt
        main thread that calls `_drain()` on an interval. Tests pass
        use_qt_timer=False and call `_drain()` by hand instead — this
        module must stay importable, and start()-able, with no
        QApplication running at all, which is why the PySide6 import
        below is local to this branch rather than at module scope.

        The whole method runs under self._lock (bind + spawning the accept
        thread is fast and non-blocking, so this is safe — see the
        `_lock` comment in __init__ for what must NEVER happen under it).
        """
        with self._lock:
            if self._running:
                return

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', self._requested_port))
                sock.listen(5)
                sock.settimeout(_ACCEPT_TIMEOUT_S)  # lets the accept loop notice stop()
                self._server_sock = sock
                self.port = sock.getsockname()[1]
                self._running = True

                self._accept_thread = threading.Thread(
                    target=self._accept_loop, name='ai_bridge_accept', daemon=True)
                self._accept_thread.start()

                if use_qt_timer:
                    # Local import on purpose (see class docstring): keeps
                    # this module importable, and this method callable,
                    # with no QApplication anywhere in the process.
                    from PySide6 import QtCore
                    self._timer = QtCore.QTimer()
                    self._timer.setInterval(_DRAIN_MS)
                    self._timer.timeout.connect(self._drain)
                    self._timer.start()
            except Exception:
                # Rollback (M2): anything failing after the bind must not
                # leak the bound socket — a retried start() would
                # otherwise hit "address already in use". The accept
                # thread, if it got as far as spawning, is a daemon and
                # self._running is flipped False right here, so it
                # notices on its very next accept()-timeout/OSError and
                # exits on its own; deliberately NOT joined here (this is
                # still inside self._lock, and joining is exactly the
                # kind of call that must never happen while holding it —
                # the thread is abandoned, not leaked, since it is daemon
                # and self-terminating).
                self._running = False
                self._server_sock = None
                self.port = None
                self._accept_thread = None
                if self._timer is not None:
                    self._timer.stop()
                    self._timer = None
                try:
                    sock.close()
                except OSError:
                    pass
                raise

    def stop(self) -> None:
        """Idempotent — safe to call repeatedly, including on a service
        that was never started. Restartable afterward: a later start()
        binds a fresh socket and spins up a fresh accept thread.

        H1: also closes every live per-connection socket, so a reader
        thread parked in conn.recv() unblocks (OSError) and exits instead
        of lingering past this stop() and potentially "rejoining" a later
        session. The lock only guards the quick bookkeeping below — the
        actual conn.close() calls and the accept-thread join happen AFTER
        it is released, since both could block (H3's deadlock note)."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            if self._server_sock is not None:
                try:
                    self._server_sock.close()
                except OSError:
                    pass
                self._server_sock = None
            conns_snapshot = list(self._conns)
            self._conns.clear()
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
            accept_thread = self._accept_thread
            self._accept_thread = None
            self.port = None

        # Outside the lock from here down: closing a handful of sockets
        # and joining the accept thread are exactly the "could block"
        # operations self._lock must never be held across.
        for conn in conns_snapshot:
            try:
                conn.close()
            except OSError:
                pass
        if accept_thread is not None:
            accept_thread.join(timeout=_JOIN_TIMEOUT_S)

    def is_running(self) -> bool:
        return self._running

    # -- socket side (background threads; never touch Qt or maya) -------

    def _accept_loop(self) -> None:
        """Runs on a daemon thread. Accepts connections and spawns one
        daemon reader thread per connection. Exits when stop() flips
        self._running False and/or closes the server socket."""
        sock = self._server_sock
        while self._running:
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return  # server socket closed out from under us by stop()
            reader = threading.Thread(
                target=self._client_loop, args=(conn,),
                name='ai_bridge_client', daemon=True)
            reader.start()

    def _client_loop(self, conn: socket.socket) -> None:
        """Runs on a daemon thread, one per connection. Reads bytes,
        splits on b'\\n', and hands each complete line to `_handle_line`,
        sending its reply straight back on the same connection. On a
        dead/closed socket (OSError) or a client-initiated close (empty
        recv), this thread just exits.

        H1: registers itself in self._conns for the duration, so stop()
        can find and close this socket even while this thread is parked
        in a blocking conn.recv() — that close() is what makes the recv
        raise OSError and this thread notice and exit, instead of
        lingering past stop() (and possibly outliving a later restart)."""
        with self._lock:
            self._conns.add(conn)
        try:
            buf = b''
            with conn:
                while self._running:
                    try:
                        chunk = conn.recv(65536)
                    except OSError:
                        return
                    if not chunk:
                        return  # client closed the connection
                    buf += chunk

                    while b'\n' in buf:
                        raw, buf = buf.split(b'\n', 1)
                        line = raw.decode('utf-8', errors='replace')
                        reply = self._handle_line(line)
                        try:
                            conn.sendall(reply.encode('utf-8') + b'\n')
                        except OSError:
                            return

                    if len(buf) > _MAX_LINE_BYTES:
                        # Whatever's left after peeling off every complete
                        # line is an un-terminated line that has grown
                        # past the sanity bound above — reply bad_params
                        # and drop the connection rather than accumulate
                        # forever.
                        reply = protocol.error_line(
                            '', 'bad_params',
                            'Request line exceeded the maximum accepted '
                            'size without a terminating newline.')
                        try:
                            conn.sendall(reply.encode('utf-8') + b'\n')
                        except OSError:
                            pass
                        return
        finally:
            with self._lock:
                self._conns.discard(conn)

    def _handle_line(self, line: str) -> str:
        """Reader-thread side of one request/reply round trip. Parse
        failures answer immediately (never touch `dispatch`); anything
        that parses is handed to the drain via `self._pending` and this
        thread blocks on that request's own reply queue until the drain
        answers or `reply_timeout_s` elapses."""
        try:
            req = protocol.parse_request(line)
        except protocol.BridgeError as exc:
            return protocol.error_line('', exc.code, str(exc))

        reply_q = queue.Queue(maxsize=1)
        self._pending.put((req, reply_q))
        try:
            return reply_q.get(timeout=self._reply_timeout_s)
        except queue.Empty:
            return protocol.error_line(
                req.id, 'maya_busy',
                "Maya's main thread did not answer in time — a long-"
                "running task or an open modal dialog is likely blocking "
                "it. Try again once Maya is idle.")

    # -- Qt main-thread side ----------------------------------------------

    def _drain(self) -> None:
        """Runs on the Qt main thread — via the QTimer in real use, or by
        a direct hand call in tests (use_qt_timer=False). This is the
        ONLY place this class ever calls `self._dispatch`, which is
        where maya.cmds actually gets touched.

        M1: answers at most _DRAIN_MAX_PER_TICK requests per call — a
        burst queued across many connections must not stall the Qt main
        thread for the sum of every one of their dispatch costs in a
        single tick. Anything left over simply waits for the QTimer's
        next tick (or the next hand-driven call, in tests)."""
        answered = 0
        while answered < _DRAIN_MAX_PER_TICK:
            try:
                req, reply_q = self._pending.get_nowait()
            except queue.Empty:
                return
            answered += 1

            try:
                # CRITICAL (pinned per the Task 4 review): ok_line() MUST
                # stay INSIDE this same try as the dispatch() call. It
                # raises TypeError (a non-serializable value somewhere in
                # the handler's result, e.g. a set()) or ValueError (a
                # NaN/Infinity float — allow_nan=False, strict RFC 8259)
                # whenever a handler's result can't be turned into a wire
                # line at all. That is a genuine internal failure, and it
                # must surface to the client as one ('internal') — not
                # propagate out of _drain and kill the drain loop (or the
                # connection, or leave this request's reply queue answered
                # never). Do NOT hoist this call out of the try in a
                # future refactor; test_service_non_serializable_result_
                # becomes_internal_error and test_service_nan_result_
                # becomes_internal_error exist specifically to catch that
                # regression.
                result = self._dispatch(req.op, req.params)
                line = protocol.ok_line(req.id, result)
            except protocol.BridgeError as exc:
                line = protocol.error_line(req.id, exc.code, str(exc))
            except BaseException as exc:  # noqa: BLE001 - see below
                # H2: BaseException, not just Exception — a KeyboardInterrupt
                # or SystemExit raised inside a Qt slot (or any other
                # non-Exception BaseException from a misbehaving dispatch)
                # must not leave this request's reply_q unanswered for the
                # full reply_timeout_s; that would violate the very
                # invariant pinned above. Answer the waiting reader thread
                # FIRST, THEN re-raise if this is the kind of BaseException
                # that should actually propagate out of the drain (i.e.
                # anything that is not an ordinary Exception) — an
                # ordinary Exception is answered and the loop simply
                # continues, same as always.
                line = protocol.error_line(
                    req.id, 'internal', f'{type(exc).__name__}: {exc}')
                try:
                    reply_q.put_nowait(line)
                except queue.Full:
                    pass
                if not isinstance(exc, Exception):
                    raise
                continue

            try:
                reply_q.put_nowait(line)
            except queue.Full:
                pass  # the reader thread already gave up (maya_busy fired)
