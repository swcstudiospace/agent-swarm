"""T-05-23: AgentScript parsers take exact flags only. A prefix such as `orch_status.py --ing` or `--tr` is a usage
error (exit 2, no result printed, no state change) instead of reaching --ingest / --transition."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_runner_gates import _clean_env

ROOT = Path(__file__).resolve().parent.parent


def _orch_status(swarm: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "orch_status.py"), *args, "--json"],
                          capture_output=True, text=True, env=_clean_env(SWARM_DIR=str(swarm)), cwd=ROOT)


def _task(ts, tid: str, *states: str) -> None:
    ts.create(task_id=tid, correlation_id="c", capability="code.backend", notes={"gates": []})
    for s in ("VALIDATED", "PLANNED", *states):
        ts.transition(tid, s)


def _assert_usage_error(r: subprocess.CompletedProcess) -> None:
    assert r.returncode == 2, r.stdout + r.stderr
    assert r.stdout == ""  # rejected by the parser: nothing ran, so no result object


@pytest.mark.parametrize("flag", ["--ing", "--inge"])
def test_ingest_prefix_rejected(tmp_path, swarm_dir, flag):
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    _task(ts, "A-1")
    f = tmp_path / "result.json"
    f.write_text(json.dumps({"task_id": "A-1", "state": "IN_PROGRESS"}))
    _assert_usage_error(_orch_status(swarm_dir, flag, str(f)))
    assert ts.get("A-1")["state"] == "PLANNED"
    r = _orch_status(swarm_dir, "--ingest", str(f))
    assert r.returncode == 0, r.stdout + r.stderr
    assert ts.get("A-1")["state"] == "IN_PROGRESS"


@pytest.mark.parametrize("flag", ["--tr", "--trans"])
def test_transition_prefix_rejected(swarm_dir, flag):
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    _task(ts, "A-2", "CLAIMED", "IN_PROGRESS", "IN_REVIEW", "APPROVED")
    _assert_usage_error(_orch_status(swarm_dir, flag, "A-2", "DONE"))
    assert ts.get("A-2")["state"] == "APPROVED"
    r = _orch_status(swarm_dir, "--transition", "A-2", "DONE")
    assert r.returncode == 0, r.stdout + r.stderr
    assert ts.get("A-2")["state"] == "DONE"
