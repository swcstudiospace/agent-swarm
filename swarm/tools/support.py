"""Shared helpers for lane and pool handlers. Not a registered tool."""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS: dict[str, Any] = {}


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
        time.sleep(0.2)
    if probe == "dependency":
        raise OSError("declared dependency is unavailable")
    if probe == "partial":
        return {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    return work()
