"""Minimal fail-open client for Agent Substrate (stdlib only).

Two doors, both optional: ``rest_post`` (POST {SUBSTRATE_URL}/events and friends) and ``mcp_call``
(JSON-RPC 2.0 ``tools/call`` on POST {SUBSTRATE_URL}/mcp). Everything here is off unless SUBSTRATE_URL is set
and SUBSTRATE_DISABLED is not ``1``; when off, no socket is opened. Every failure (no network, timeout, non-2xx,
bad body, ``isError``) yields ``None``: the caller's behaviour never depends on substrate being reachable.
A bearer token comes from env SUBSTRATE_TOKEN and is only ever placed in the Authorization header: it is
never logged, printed or returned.
"""
from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request
from typing import Mapping

TIMEOUT_S = 1.5
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


def _request(url: str, body: dict, env: Mapping[str, str], accept: str) -> tuple[int, str] | None:
    global _down_until
    if time.monotonic() < _down_until:
        return None
    headers = {"Content-Type": "application/json", "Accept": accept}
    token = (env.get("SUBSTRATE_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:  # an HTTP answer is still an answer: let the caller see the status
        try:
            return int(e.code), e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return int(e.code), ""
    except Exception:  # noqa: BLE001 - fail open: nothing may escape this client
        _down_until = time.monotonic() + BACKOFF_S
        return None


def rest_post(path: str, body: dict, env: Mapping[str, str] | None = None) -> tuple[int, str] | None:
    """POST JSON to {SUBSTRATE_URL}{path}. ``(status, text)`` on any HTTP answer, None when off or unreachable."""
    try:
        e = _env(env)
        base = base_url(e)
        if base is None:
            return None
        return _request(base + "/" + path.lstrip("/"), body, e, "application/json")
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


def mcp_call_json(tool: str, arguments: dict, env: Mapping[str, str] | None = None) -> dict | list | None:
    """Call an MCP tool statelessly; return the JSON value (object or array) in ``result.content[0].text``, None on any failure."""
    try:
        e = _env(env)
        base = base_url(e)
        if base is None:
            return None
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        got = _request(base + "/mcp", rpc, e, "application/json, text/event-stream")
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


def mcp_call(tool: str, arguments: dict, env: Mapping[str, str] | None = None) -> dict | None:
    """Call an MCP tool statelessly; return the JSON object in ``result.content[0].text``, None on any failure or non-object."""
    parsed = mcp_call_json(tool, arguments, env)
    return parsed if isinstance(parsed, dict) else None
