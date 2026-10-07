"""Minimal fail-open client for Agent Substrate (stdlib only).

Two doors, both optional: ``rest_post`` (POST {SUBSTRATE_URL}/events and friends) and ``mcp_call``
(JSON-RPC 2.0 ``tools/call`` on POST {SUBSTRATE_URL}/mcp). Everything here is off unless SUBSTRATE_URL is set
and SUBSTRATE_DISABLED is not ``1``; when off, no socket is opened. Every failure (no network, timeout, non-2xx,
bad body, ``isError``) yields ``None``: the caller's behaviour never depends on substrate being reachable.
A bearer token comes from env SUBSTRATE_TOKEN (or, for an explicit ``surface``, an opted-in SUBSTRATE_TOKEN_<SURFACE>) and is
only ever placed in the Authorization header, as an unredirected header so a redirect never carries it to another URL:
it is never logged, printed or returned. Redirects are never followed (a 3xx is a failed answer). One request (connect,
headers and body) takes at most TIMEOUT_S + DEADLINE_SLACK_S: a timer shuts the socket down at that deadline.
"""
from __future__ import annotations
import http.client
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping

TIMEOUT_S = 1.5  # per socket operation; the whole request is also bounded, see DEADLINE_SLACK_S
DEADLINE_SLACK_S = 0.5  # total budget of one request (connect + read) is TIMEOUT_S + this
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
    """One HTTP answer from `_open`; its connection stays under the request deadline until close()."""

    def __init__(self, conn: http.client.HTTPConnection, resp: http.client.HTTPResponse, timer: threading.Timer,
                 killed: threading.Event):
        self._conn, self._resp, self._timer, self._killed = conn, resp, timer, killed
        self.status = resp.status

    def getcode(self) -> int:
        return self.status

    def read(self, amt: int | None = None) -> bytes:
        try:
            if amt is None:
                data = self._resp.read()
            else:
                parts: list[bytes] = []
                got = 0
                while got < amt:
                    chunk = self._resp.read(amt - got)
                    if not chunk:
                        break
                    parts.append(chunk)
                    got += len(chunk)
                data = b"".join(parts)
        except Exception:
            if self._killed.is_set():
                raise TimeoutError("substrate request exceeded its deadline") from None
            raise
        if self._killed.is_set():
            raise TimeoutError("substrate request exceeded its deadline")
        return data

    def close(self) -> None:
        self._timer.cancel()
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _open(req: urllib.request.Request, timeout: float) -> _Response:
    """Send `req` with http.client in the calling thread; never follows redirects.

    `timeout` bounds each socket operation (connect included); a timer armed before the request shuts the socket down at
    TIMEOUT_S + DEADLINE_SLACK_S, which ends a blocked connect, header read or body read at once. The timer stays armed
    until the returned response is closed. Raises on any network failure or deadline hit.
    """
    u = urllib.parse.urlsplit(req.full_url)
    if u.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(u.hostname, u.port, timeout=timeout,
                                                                       context=ssl.create_default_context())
    elif u.scheme == "http":
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
    else:
        raise ValueError("unsupported scheme")
    killed = threading.Event()

    def kill() -> None:
        killed.set()
        sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    timer = threading.Timer(TIMEOUT_S + DEADLINE_SLACK_S, kill)
    timer.daemon = True  # never keeps the process alive
    timer.start()
    try:
        conn.request(req.get_method(), req.selector, body=req.data, headers=dict(req.header_items()))
        resp = conn.getresponse()
    except BaseException:
        timer.cancel()
        conn.close()
        if killed.is_set():
            raise TimeoutError("substrate request exceeded its deadline") from None
        raise
    return _Response(conn, resp, timer, killed)


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
        except urllib.error.HTTPError as e:  # an HTTP answer is still an answer: let the caller see the status
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
