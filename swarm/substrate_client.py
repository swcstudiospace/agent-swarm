"""Minimal fail-open client for Agent Substrate (stdlib only).

Two doors, both optional: ``rest_post`` (POST {SUBSTRATE_URL}/events and friends) and ``mcp_call``
(JSON-RPC 2.0 ``tools/call`` on POST {SUBSTRATE_URL}/mcp). Everything here is off unless SUBSTRATE_URL is set
and SUBSTRATE_DISABLED is not ``1``; when off, no socket is opened. Every failure (no network, timeout, non-2xx,
bad body, ``isError``) yields ``None``: the caller's behaviour never depends on substrate being reachable.
A bearer token comes from env SUBSTRATE_TOKEN (or, for an explicit ``surface``, an opted-in SUBSTRATE_TOKEN_<SURFACE>) and is
only ever placed in the Authorization header, as an unredirected header so a redirect never carries it to another URL:
it is never logged, printed or returned. Redirects are never followed (a 3xx is a failed answer). HTTP_PROXY, HTTPS_PROXY
and NO_PROXY are honoured. The caller waits at most TIMEOUT_S + DEADLINE_SLACK_S for one request (DNS, connect, headers
and body, run on a daemon worker); at that deadline the request's socket is shut down.
"""
from __future__ import annotations
import functools
import http.client
import io
import json
import os
import queue
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Mapping

TIMEOUT_S = 1.5  # per socket operation; the whole request is also bounded, see DEADLINE_SLACK_S
DEADLINE_SLACK_S = 0.5  # the caller's total wait for one request (DNS + connect + read) is TIMEOUT_S + this
MAX_BODY_BYTES = 1 << 20  # a larger reply is a failure
BACKOFF_S = 30.0  # after a network-level failure this process stops trying for a while, so a dead host costs one timeout
_down_until = 0.0


def reset() -> None:
    """Forget a recorded outage (tests)."""
    global _down_until
    _down_until = 0.0


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def base_url(env: Mapping[str, str] | None = None) -> str | None:
    """The normalised substrate base URL, or None when the integration is off."""
    e = _env(env)
    if (e.get("SUBSTRATE_DISABLED") or "").strip() == "1":
        return None
    url = (e.get("SUBSTRATE_URL") or "").strip().rstrip("/")
    if not url.lower().startswith(("http://", "https://")):  # also keeps urllib off file:// and friends
        return None
    return url


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return base_url(env) is not None


def _token(env: Mapping[str, str], surface: str | None) -> str:
    """SUBSTRATE_TOKEN_<SURFACE> when `surface` is given and that variable is set and non-blank, else SUBSTRATE_TOKEN."""
    if surface:
        own = (env.get("SUBSTRATE_TOKEN_" + surface.upper().replace("-", "_")) or "").strip()
        if own:
            return own
    return (env.get("SUBSTRATE_TOKEN") or "").strip()


class _Response:
    """One HTTP answer handed back by `_open`: the status and the (already capped) body, held in memory."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._buf = io.BytesIO(body)

    def getcode(self) -> int:
        return self.status

    def read(self, amt: int | None = -1) -> bytes:
        return self._buf.read(amt)

    def close(self) -> None:
        self._buf.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _shut(sock: socket.socket) -> None:
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)  # the plain socket call, also on an SSLSocket: ends any blocked I/O
    except OSError:
        pass


class _Holder:
    """Per-request record of the live socket; once `dead`, any socket it is handed is killed on the spot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sock: socket.socket | None = None
        self.dead = False

    def adopt(self, sock: socket.socket) -> socket.socket:
        with self._lock:
            self.sock = sock
            dead = self.dead
        if dead:  # the caller gave up while this connect was still under way
            _shut(sock)
            sock.close()
            raise OSError("substrate deadline")
        return sock

    def kill(self) -> None:
        with self._lock:
            self.dead = True
            sock = self.sock
        if sock is not None:  # shut down only: the worker that owns the socket closes it
            _shut(sock)


class _TrackedHTTPConnection(http.client.HTTPConnection):
    """Registers its socket in `holder` as soon as TCP is up (before any proxy CONNECT) and again once connected."""

    def __init__(self, *args, holder: _Holder, **kwargs):
        super().__init__(*args, **kwargs)
        self._holder = holder
        self._create_connection = self._create_tracked

    def _create_tracked(self, *args, **kwargs) -> socket.socket:
        return self._holder.adopt(socket.create_connection(*args, **kwargs))

    def connect(self) -> None:
        super().connect()
        self._holder.adopt(self.sock)


class _TrackedHTTPSConnection(_TrackedHTTPConnection, http.client.HTTPSConnection):
    def connect(self) -> None:
        http.client.HTTPConnection.connect(self)  # TCP and any proxy CONNECT tunnel, the raw socket tracked
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._tunnel_host or self.host,
                                              do_handshake_on_connect=False)
        self._holder.adopt(self.sock)  # tracked before the handshake, so a stalled handshake is killable too
        self.sock.do_handshake()


def _holder_of(req: urllib.request.Request) -> _Holder:
    holder = getattr(req, "_substrate_holder", None)
    return holder if isinstance(holder, _Holder) else _Holder()


class _TrackingHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(functools.partial(_TrackedHTTPConnection, holder=_holder_of(req)), req)


class _TrackingHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(functools.partial(_TrackedHTTPSConnection, holder=_holder_of(req)), req, context=self._context)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # a 3xx surfaces as an HTTPError with that status; nothing is re-sent


@functools.lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


MAX_IN_FLIGHT = 4  # workers alive at once; only one stuck in DNS (no socket to kill yet) can outlive its deadline
_slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)
_workers_lock = threading.Lock()
_workers: set[threading.Thread] = set()


def _read_upto(f, n: int) -> bytes:
    parts: list[bytes] = []
    got = 0
    while got < n:
        chunk = f.read(n - got)
        if not chunk:
            break
        parts.append(chunk)
        got += len(chunk)
    return b"".join(parts)


def _exchange(opener: urllib.request.OpenerDirector, req: urllib.request.Request, timeout: float,
              out: queue.SimpleQueue) -> None:
    """Worker: the whole exchange, body capped at MAX_BODY_BYTES + 1; hands back a _Response or the exception."""
    got: object = OSError("substrate request worker stopped")
    try:
        try:
            with opener.open(req, timeout=timeout) as resp:
                got = _Response(int(resp.status), _read_upto(resp, MAX_BODY_BYTES + 1))
        except urllib.error.HTTPError as e:  # non-2xx (3xx included: never followed) is still an answer
            try:
                body = _read_upto(e, MAX_BODY_BYTES + 1)
            except Exception:  # noqa: BLE001
                body = b""
            finally:
                e.close()
            got = _Response(int(e.code), b"" if len(body) > MAX_BODY_BYTES else body)
    except Exception as exc:  # noqa: BLE001 - delivered to the caller, who decides
        got = exc
    finally:
        _slots.release()  # before the hand-off: a caller holding the answer never finds its own slot still taken
        out.put(got)


def _open(req: urllib.request.Request, timeout: float) -> _Response:
    """Send `req` through a per-call urllib opener on a daemon worker; never follows redirects.

    The opener honours HTTP_PROXY / HTTPS_PROXY / NO_PROXY as they are now. `timeout` bounds each socket operation; the
    caller waits at most TIMEOUT_S + DEADLINE_SLACK_S from entry over DNS, connect, headers and body, then shuts the
    request's socket down (ending the worker's blocked I/O) and raises TimeoutError. At most MAX_IN_FLIGHT workers exist;
    with none free this raises at once without starting one. Raises on any network failure.
    """
    started = time.monotonic()
    if not _slots.acquire(blocking=False):
        raise OSError("substrate: too many requests still in flight")
    try:
        holder = _Holder()
        req._substrate_holder = holder  # type: ignore[attr-defined]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(), _NoRedirect(), _TrackingHTTPHandler(),
                                             _TrackingHTTPSHandler(context=_ssl_context()))
        out: queue.SimpleQueue = queue.SimpleQueue()
        worker = threading.Thread(target=_exchange, args=(opener, req, timeout, out), name="substrate-request", daemon=True)
        with _workers_lock:
            _workers.difference_update([t for t in _workers if not t.is_alive()])
            _workers.add(worker)
        worker.start()
    except BaseException:
        _slots.release()
        raise
    try:
        got = out.get(timeout=max(0.0, started + TIMEOUT_S + DEADLINE_SLACK_S - time.monotonic()))
    except queue.Empty:
        holder.kill()
        raise TimeoutError("substrate request exceeded its deadline") from None
    if isinstance(got, BaseException):
        raise got
    return got


def _wait_idle(timeout: float) -> bool:
    """Wait for every request worker to finish (tests); True when none is left alive."""
    until = time.monotonic() + timeout
    with _workers_lock:
        workers = list(_workers)
    for t in workers:
        t.join(max(0.0, until - time.monotonic()))
    with _workers_lock:
        _workers.difference_update([t for t in _workers if not t.is_alive()])
        return not _workers


def _read_capped(resp) -> bytes:
    data = resp.read(MAX_BODY_BYTES + 1)
    if len(data) > MAX_BODY_BYTES:
        raise ValueError("substrate reply larger than MAX_BODY_BYTES")
    return data


def _request(url: str, body: dict, env: Mapping[str, str], accept: str, surface: str | None = None) -> tuple[int, str] | None:
    global _down_until
    if time.monotonic() < _down_until:
        return None
    got: tuple[int, str] | None
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Accept": accept}, method="POST")
        token = _token(env, surface)
        if token:  # unredirected: never copied onto another request; _open does not follow redirects anyway
            req.add_unredirected_header("Authorization", f"Bearer {token}")
        try:
            with _open(req, TIMEOUT_S) as resp:
                data = _read_capped(resp)
                status = getattr(resp, "status", None)
                if status is None:
                    status = resp.getcode()
                got = (int(status), data.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:  # an HTTP answer is still an answer (the real _open returns a non-2xx _Response)
            try:
                text = _read_capped(e).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text = ""
            got = (int(e.code), text)
    except Exception:  # noqa: BLE001 - fail open: nothing may escape this client
        got = None
    if got is None:  # network failure, deadline, oversized or malformed reply: back off
        _down_until = time.monotonic() + BACKOFF_S
    return got


def rest_post(path: str, body: dict, env: Mapping[str, str] | None = None, *, surface: str | None = None) -> tuple[int, str] | None:
    """POST JSON to {SUBSTRATE_URL}{path}. ``(status, text)`` on any HTTP answer, None when off or unreachable.

    `surface` opts into that surface's own token (SUBSTRATE_TOKEN_<SURFACE>), falling back to SUBSTRATE_TOKEN.
    """
    try:
        e = _env(env)
        base = base_url(e)
        if base is None:
            return None
        return _request(base + "/" + path.lstrip("/"), body, e, "application/json", surface)
    except Exception:  # noqa: BLE001
        return None


def _rpc_response(text: str) -> dict | None:
    """The JSON-RPC response in `text`: a plain JSON body, or an SSE body whose `data:` line holds it."""
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates = [stripped]
    else:
        candidates = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    for cand in reversed(candidates):
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict) and ("result" in obj or "error" in obj):
            return obj
    return None


def mcp_call_json(tool: str, arguments: dict, env: Mapping[str, str] | None = None, *,
                  surface: str | None = None) -> dict | list | None:
    """Call an MCP tool statelessly; return the JSON value (object or array) in ``result.content[0].text``, None on any failure.

    `surface` selects the token as in `rest_post`.
    """
    try:
        e = _env(env)
        base = base_url(e)
        if base is None:
            return None
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        got = _request(base + "/mcp", rpc, e, "application/json, text/event-stream", surface)
        if got is None or not 200 <= got[0] < 300:
            return None
        resp = _rpc_response(got[1])
        result = resp.get("result") if resp else None
        if not isinstance(result, dict) or result.get("isError"):
            return None
        content = result.get("content")
        if not isinstance(content, list) or not content or not isinstance(content[0], dict):
            return None
        parsed = json.loads(content[0].get("text", ""))
        return parsed if isinstance(parsed, (dict, list)) else None
    except Exception:  # noqa: BLE001
        return None


def mcp_call(tool: str, arguments: dict, env: Mapping[str, str] | None = None, *, surface: str | None = None) -> dict | None:
    """Call an MCP tool statelessly; return the JSON object in ``result.content[0].text``, None on any failure or non-object.

    `surface` selects the token as in `rest_post`.
    """
    parsed = mcp_call_json(tool, arguments, env, surface=surface)
    return parsed if isinstance(parsed, dict) else None
