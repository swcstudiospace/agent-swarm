import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "hooks" / "autonomous_run.py"


def test_autonomous_dry_run(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    r = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--cwd",
            str(tmp_path),
            "--brief",
            "implement a billing feature",
            "--runtime",
            "auto",
            "--swarm-root",
            str(ROOT),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    log = json.loads((tmp_path / ".swarm" / "autonomous.log").read_text())
    assert log["plan_rc"] == 0
    assert log["run_rc"] == 0
    assert "DONE" in log["run_out"] or "complete" in log["run_out"].lower() or '"complete": true' in log["run_out"]


def test_autonomous_skips_trivia(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    r = subprocess.run(
        [sys.executable, str(RUNNER), "--cwd", str(tmp_path), "--brief", "what is a monad", "--swarm-root", str(ROOT), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert r.returncode == 0
    assert not (tmp_path / ".swarm" / "autonomous.log").exists()
