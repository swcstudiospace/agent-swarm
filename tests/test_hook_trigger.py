"""fire_if_ready must not detach hooks/autonomous_run.py unless a real signing key,
SWARM_ALLOW_AUTONOMOUS=1 and no cloud-agent marker are all present.

These tests patch subprocess.Popen. They never start the runner.
"""
import json
import subprocess
from pathlib import Path

import pytest

from swarm.envelope import insecure_dev_key
from swarm.hook_trigger import fire_if_ready

_COMPLETE = "a01-orchestrator task.result state IN_REVIEW"
_CLOUD_MARKERS = ("CURSOR_AGENT", "CURSOR_CLOUD_AGENT", "CLOUD_AGENT")
_KEY_VARS = (
    "SWARM_SIGNING_KEY",
    "SWARM_ED25519_KEY",
    "SWARM_REQUIRE_KEY",
    "SWARM_ALLOW_INSECURE_DEV_KEY",
)


class _NotAProcess:
    """Stand-in for Popen. It does not start a process."""

    pid = 0


@pytest.fixture()
def state(tmp_path, monkeypatch):
    path = tmp_path / "swarm-state"
    monkeypatch.setenv("SWARM_DIR", str(path))
    monkeypatch.setenv("AIO_SWARM_AFTER_ORCH", "1")
    monkeypatch.delenv("SWARM_AFTER_ORCH", raising=False)
    return path


def _clear_cloud(monkeypatch):
    for name in _CLOUD_MARKERS:
        monkeypatch.delenv(name, raising=False)


def _clear_keys(monkeypatch):
    for name in _KEY_VARS:
        monkeypatch.delenv(name, raising=False)


def _record_popen(monkeypatch):
    calls = []

    def _fake(argv, **kwargs):
        calls.append(argv)
        return _NotAProcess()

    monkeypatch.setattr(subprocess, "Popen", _fake)
    return calls


def _reasons(state: Path) -> list[str]:
    log = state / "events.jsonl"
    if not log.exists():
        return []
    reasons = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("type") != "hook-fired":
            continue
        reason = (rec.get("payload") or {}).get("reason")
        if reason:
            reasons.append(reason)
    return reasons


def test_no_real_key_does_not_spawn(state, monkeypatch):
    _clear_keys(monkeypatch)
    _clear_cloud(monkeypatch)
    monkeypatch.setenv("SWARM_ALLOW_AUTONOMOUS", "1")
    calls = _record_popen(monkeypatch)

    result = fire_if_ready(_COMPLETE, session_id="s", corr="c", root=state)

    assert calls == []
    assert result["fired"] is False
    assert result["reason"] == "no-signing-key"
    assert _reasons(state) == ["no-signing-key"]


def test_public_dev_key_is_not_a_real_key(state, monkeypatch):
    _clear_keys(monkeypatch)
    _clear_cloud(monkeypatch)
    monkeypatch.setenv("SWARM_ALLOW_INSECURE_DEV_KEY", "1")
    monkeypatch.setenv("SWARM_SIGNING_KEY", insecure_dev_key())
    monkeypatch.setenv("SWARM_ALLOW_AUTONOMOUS", "1")
    calls = _record_popen(monkeypatch)

    result = fire_if_ready(_COMPLETE, session_id="s", corr="c", root=state)

    assert calls == []
    assert result["reason"] == "no-signing-key"
    assert _reasons(state) == ["no-signing-key"]


def test_without_second_opt_in_does_not_spawn(state, monkeypatch):
    _clear_cloud(monkeypatch)
    monkeypatch.delenv("SWARM_ALLOW_AUTONOMOUS", raising=False)
    calls = _record_popen(monkeypatch)

    result = fire_if_ready(_COMPLETE, session_id="s", corr="c", root=state)

    assert calls == []
    assert result["fired"] is False
    assert result["reason"] == "autonomous-not-allowed"
    assert _reasons(state) == ["autonomous-not-allowed"]


def test_cloud_agent_marker_does_not_spawn(state, monkeypatch):
    _clear_cloud(monkeypatch)
    monkeypatch.setenv("SWARM_ALLOW_AUTONOMOUS", "1")
    monkeypatch.setenv("CURSOR_AGENT", "1")
    calls = _record_popen(monkeypatch)

    result = fire_if_ready(_COMPLETE, session_id="s", corr="c", root=state)

    assert calls == []
    assert result["fired"] is False
    assert result["reason"] == "cloud-agent"
    assert _reasons(state) == ["cloud-agent"]


def test_real_key_and_second_opt_in_reaches_spawn(state, monkeypatch):
    _clear_cloud(monkeypatch)
    monkeypatch.setenv("SWARM_ALLOW_AUTONOMOUS", "1")
    calls = _record_popen(monkeypatch)

    result = fire_if_ready(_COMPLETE, session_id="s", corr="c", root=state)

    assert result["fired"] is True
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "python3"
    assert argv[1].endswith("hooks/autonomous_run.py")
    assert "autonomous_run.py" in Path(argv[1]).name
