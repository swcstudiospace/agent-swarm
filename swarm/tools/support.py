"""Shared helpers for lane and pool handlers. Not a registered tool."""
from __future__ import annotations

import contextvars
import importlib.util
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS: dict[str, Any] = {}
_inflight: set[int] = set()
_inflight_lock = threading.Lock()
_cancel: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar("swarm_call_cancel", default=None)


class StillRunning(Exception):
    """The call exceeded its limit and the worker has not finished.

    The worker is not killed. The cancel flag is set, and another call of the same
    function is rejected until this one returns.
    """


class Busy(Exception):
    """A previous call of this function is still running."""


def target_root() -> Path:
    """Workspace the swarm is operating on. Script modules still load from ROOT."""
    return Path.cwd().resolve()


def sleep_until_cancelled(seconds: float) -> bool:
    """Sleep up to ``seconds``. Return True when the caller has given up."""
    flag = _cancel.get()
    if flag is None:
        time.sleep(seconds)
        return False
    return flag.wait(seconds)


def invoke_bounded(fn, arg, timeout_s: float):
    """Run ``fn(arg)`` until ``timeout_s``.

    A still-running call raises StillRunning and sets the cancel flag. A second call
    of the same function raises Busy until that worker returns.
    """
    key = id(fn)
    with _inflight_lock:
        if key in _inflight:
            raise Busy(getattr(fn, "__name__", "handler"))
        _inflight.add(key)
    done = threading.Event()
    outcome: dict[str, Any] = {}
    cancel = threading.Event()

    def target() -> None:
        token = _cancel.set(cancel)
        try:
            outcome["value"] = fn(arg)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            _cancel.reset(token)
            with _inflight_lock:
                _inflight.discard(key)
            done.set()

    threading.Thread(target=target, daemon=True).start()
    if not done.wait(timeout_s):
        cancel.set()
        # A handler that watches the cancel flag can leave before the next call.
        # One that does not stays in flight, and the next call is rejected.
        done.wait(0.05)
        raise StillRunning(f"exceeded {timeout_s}s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def script(stem: str) -> Any:
    """Load scripts/<stem>.py once. Scripts are not a package and only run main under __main__.

    The return is Any because each stem is a loose script, not a typed module.
    Callers reach functions that exist on that file; a static checker cannot see them.
    """
    cached = _SCRIPTS.get(stem)
    if cached is not None:
        return cached
    path = ROOT / "scripts" / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"_swarm_lane_{stem}", path)
    if spec is None or spec.loader is None:
        raise OSError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _SCRIPTS[stem] = module
    return module


def respond(payload: dict, work):
    """Contract-test probe, then the real call. Probe values never reach the lane function."""
    probe = payload.get("probe")
    if probe == "sleep":
        sleep_until_cancelled(0.2)
        return {"state": "TIMEOUT", "message": "probe sleep"}
    if probe == "dependency":
        raise OSError("declared dependency is unavailable")
    if probe == "partial":
        return {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    return work()
