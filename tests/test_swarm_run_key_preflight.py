"""A keyless swarm_run refuses before it claims a task (r4222165933 / SPE-8545).

These tests import the runner and call preflight_signing() and run(). They never spawn
scripts/swarm_run.py and never call main().
"""
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import ROOT, run_script
from swarm.script_base import Ctx

_KEY_VARS = (
    "SWARM_SIGNING_KEY",
    "SWARM_ED25519_KEY",
    "SWARM_REQUIRE_KEY",
    "SWARM_ALLOW_INSECURE_DEV_KEY",
)


def _load(monkeypatch, swarm_dir: Path):
    monkeypatch.setenv("SWARM_DIR", str(swarm_dir))
    for name in _KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("swarm_run_key_preflight", ROOT / "scripts" / "swarm_run.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plan(repo: Path, swarm: Path) -> str:
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    r = run_script(
        "orch_plan.py", "--repo", str(repo), "--brief-text", "x", "--pattern", "hotfix", "--json",
        env={"SWARM_DIR": str(swarm)},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)["correlation_id"]


def _guard(mod, monkeypatch):
    """Claims, the lease bridge, dispatch, and the runtime entry must not run."""
    def reached(*_a, **_k):
        raise AssertionError("reached")

    monkeypatch.setattr(mod, "lease_bridge", reached)
    monkeypatch.setattr(mod, "execute_one", reached)
    monkeypatch.setattr(mod, "dispatchable", reached)
    monkeypatch.setattr(mod, "headless_command", reached)
    from swarm.taskstore import TaskStore
    real = TaskStore.transition

    def guarded(self, task_id, to_state, *args, **kwargs):
        if str(to_state) == "CLAIMED":
            raise AssertionError("reached")
        return real(self, task_id, to_state, *args, **kwargs)

    monkeypatch.setattr(TaskStore, "transition", guarded)


def _args(repo: Path, *, dry_run: bool) -> SimpleNamespace:
    return SimpleNamespace(
        repo=str(repo), dry_run=dry_run, runtime="claude",
        claude_bin="claude", grok_bin="grok", omp_bin="omp",
        max_parallel=1, max_rounds=1, once=True, max_turns=1, task_timeout=30,
        permission_mode="default", model=None, allowed_tools="",
    )


def test_preflight_signing_refuses_when_keyless(tmp_path, monkeypatch):
    from swarm.errors import ErrorCode, SwarmError
    mod = _load(monkeypatch, tmp_path / ".swarm")
    with pytest.raises(SwarmError, match="fail-closed: no signing key configured") as ei:
        mod.preflight_signing()
    assert ei.value.code == ErrorCode.E_POLICY


def test_preflight_signing_allows_explicit_dev_key(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / ".swarm")
    monkeypatch.setenv("SWARM_ALLOW_INSECURE_DEV_KEY", "1")
    mod.preflight_signing()


@pytest.mark.parametrize("dry_run", [True, False])
def test_keyless_run_leaves_tasks_unclaimed(tmp_path, monkeypatch, dry_run):
    from swarm.errors import ErrorCode, SwarmError
    repo = tmp_path / "repo"
    repo.mkdir()
    swarm = repo / ".swarm"
    corr = _plan(repo, swarm)
    mod = _load(monkeypatch, swarm)
    if not dry_run:
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/bin/true")
        monkeypatch.setattr(mod, "preflight_auth", lambda *_a, **_k: None)
    _guard(mod, monkeypatch)
    ctx = Ctx("A01", "swarm_run", None, corr, dry_run, repo)
    with pytest.raises(SwarmError, match="fail-closed: no signing key configured") as ei:
        mod.run(_args(repo, dry_run=dry_run), ctx)
    assert ei.value.code == ErrorCode.E_POLICY
    from swarm.taskstore import TaskStore
    tasks = TaskStore(swarm / "tasks.db").list(correlation_id=corr)
    assert tasks
    assert all(t["attempt"] == 0 for t in tasks)
    assert all(t["state"] == "PLANNED" for t in tasks)
