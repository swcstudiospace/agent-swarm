"""TypeScript twins exist and --dry-run --json matches the shared contract."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUN = shutil.which("bun") or "/root/.local/share/reflex/bun/bin/bun"
HAS_BUN = Path(BUN).exists()


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_every_py_script_has_ts_twin():
    py = {p.stem for p in (ROOT / "scripts").glob("*.py") if not p.name.startswith("_")}
    ts = {p.stem for p in (ROOT / "scripts" / "ts").glob("*.ts")} - {"script_base", "passthrough"}
    assert py <= ts, f"missing ts twins: {sorted(py - ts)}"


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_ts_dry_run_json(tmp_path, monkeypatch):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    skip = {"build_agents", "swarm_run", "orch_status", "script_base", "passthrough"}
    for ts in sorted((ROOT / "scripts" / "ts").glob("*.ts")):
        if ts.stem in skip or ts.name.startswith("_"):
            continue
        r = subprocess.run([BUN, str(ts), "--dry-run", "--json"], cwd=ROOT, capture_output=True, text=True, env=env)
        assert r.returncode in (0, 1), f"{ts.name} rc={r.returncode}\n{r.stderr[-800:]}\n{r.stdout[-400:]}"
        data = json.loads(r.stdout)
        assert data["status"] in ("ok", "fail"), ts.name
        assert "agent" in data and "script" in data
