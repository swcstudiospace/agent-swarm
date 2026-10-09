"""Desk verification receipts exported from .swarm state.

The behaviour tests subprocess scripts/receipt_export.py. On a checkout where that
script is absent they fail; they pass once the exporter reads the fixture store.
"""
from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "receipt"
SCRIPT = ROOT / "scripts" / "receipt_export.py"
DESK_FIELDS = json.loads((FIXTURES / "desk_fields.json").read_text(encoding="utf-8"))
SAMPLE = json.loads((FIXTURES / "sample_state.json").read_text(encoding="utf-8"))
CREDENTIAL = json.loads((FIXTURES / "credential_state.json").read_text(encoding="utf-8"))
SECRET_TEXT = "notarealsecretkey"


def _bun() -> str | None:
    found = shutil.which("bun")
    if found:
        return found
    candidate = Path.home() / ".bun" / "bin" / "bun"
    return str(candidate) if candidate.exists() else None


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _advance(store, task_id: str) -> None:
    for state in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        store.transition(task_id, state)


def materialize(spec: dict, *, signing_key: str) -> None:
    """Build the fixture's .swarm store in the process's SWARM_DIR."""
    from swarm.gates import make_verdict
    from swarm.runlog import emit
    from swarm.taskstore import TaskStore

    store = TaskStore()
    corr = spec["correlation_id"]
    for task in spec["tasks"]:
        store.create(
            task_id=task["task_id"], correlation_id=corr, capability=task["capability"],
            agent_id=task["agent_id"], title=task["title"], risk_class=task["risk_class"],
            notes=task.get("notes") or {})
        _advance(store, task["task_id"])
        for art in task.get("artifacts") or []:
            store.add_artifact(task["task_id"], kind=art["kind"], uri=art["uri"], producer=task["agent_id"])
    for event in spec["events"]:
        emit(event["type"], event["payload"], source="fixture", correlation_id=corr,
             task_id=event["task_id"], tee=False)
    other_key = secrets.token_hex(32)
    saved = os.environ.get("SWARM_SIGNING_KEY")
    try:
        for verdict in spec["verdicts"]:
            mode = verdict["mode"]
            if mode == "unsigned":
                now = time.time()
                store.conn.execute(
                    "INSERT INTO verdicts (task_id, gate, verdict, agent_id, findings, expires_at, ts,"
                    " envelope_json, sig) VALUES (?,?,?,?,?,?,?,?,?)",
                    (verdict["task_id"], verdict["gate"], "pass", verdict["agent_id"], "[]",
                     now + 86400, now, None, None))
                store.conn.commit()
                continue
            os.environ["SWARM_SIGNING_KEY"] = signing_key if mode == "signed" else other_key
            env = make_verdict(gate=verdict["gate"], task_id=verdict["task_id"], agent_id=verdict["agent_id"],
                               verdict="pass", correlation_id=corr, audit=False)
            store.record_verdict(verdict["task_id"], env)
    finally:
        if saved is None:
            os.environ.pop("SWARM_SIGNING_KEY", None)
        else:
            os.environ["SWARM_SIGNING_KEY"] = saved
    latest = store.path.parent / "latest_correlation"
    latest.write_text(corr, encoding="utf-8")


def run_export(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    child = os.environ.copy()
    if env:
        child.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--root", str(root), *args],
        cwd=ROOT, capture_output=True, text=True, env=child)


def _receipt(proc: subprocess.CompletedProcess[str]) -> dict:
    assert proc.returncode == 0, proc.stderr[-800:] + proc.stdout[-800:]
    body = json.loads(proc.stdout)
    assert body["status"] == "ok"
    assert body["agent"] == "A01"
    assert body["script"] == "receipt_export"
    return body["receipt"]


def _assert_desk_shape(receipt: dict) -> None:
    assert list(receipt.keys()) == DESK_FIELDS["required_fields"]
    assert receipt["approved_by"] == ""
    assert receipt["approvals"] == []
    assert receipt["loop_acks"] == []
    assert receipt["contract_changes"] == []
    assert isinstance(receipt["unverified"], list) and receipt["unverified"]
    assert isinstance(receipt["files_changed"], list)
    assert isinstance(receipt["commands"], list)
    assert isinstance(receipt["claims"], list)
    for cmd in receipt["commands"]:
        assert set(DESK_FIELDS["command_required"]) <= set(cmd)
        assert set(cmd) <= set(DESK_FIELDS["command_required"] + DESK_FIELDS["command_optional"])
    for claim in receipt["claims"]:
        assert set(DESK_FIELDS["claim_required"]) <= set(claim)
        assert set(claim) <= set(DESK_FIELDS["claim_required"] + DESK_FIELDS["claim_optional"])
        assert 0 <= claim["evidence_command_index"] < len(receipt["commands"])
    assert "APPROVED" not in json.dumps(receipt)


def test_desk_fixture_pins_skill_fields():
    assert DESK_FIELDS["required_fields"][0] == "task_id"
    assert "approved_by" in DESK_FIELDS["required_fields"]
    assert DESK_FIELDS["command_required"] == ["cmd", "exit_code"]
    assert "evidence_command_index" in DESK_FIELDS["claim_required"]


def test_finished_gates_are_advisory(swarm_dir):
    materialize(SAMPLE, signing_key=os.environ["SWARM_SIGNING_KEY"])
    # --root is this repo so the observed commit is HEAD; SWARM_DIR keeps the fixture store off the worktree.
    proc = run_export(ROOT, "--correlation-id", SAMPLE["correlation_id"])
    receipt = _receipt(proc)
    _assert_desk_shape(receipt)
    assert receipt["task_id"] == SAMPLE["correlation_id"]
    assert receipt["bot"] == "agent-swarm"
    blob = json.dumps(receipt)
    assert "APPROVED" not in blob
    for gate, task in (("quality", "T-qa"), ("review", "T-rev"), ("security", "T-sec")):
        assert any(gate in c["claim"] and task in c["claim"] and "advisory" in c["claim"] for c in receipt["claims"])
    assert any("signature signed" in c["claim"] and "review" in c["claim"] for c in receipt["claims"])
    assert any("signature unsigned" in c["claim"] and "quality" in c["claim"] and "keyless" not in c["claim"]
               for c in receipt["claims"])
    assert any("keyless" in c["claim"] and "security" in c["claim"] and "signature signed" not in c["claim"]
               for c in receipt["claims"])
    assert any("release" in line and "not run" in line for line in receipt["unverified"])
    assert receipt["files_changed"] == ["src/feature.py", "tests/test_feature.py"]
    cmds = {c["cmd"]: c for c in receipt["commands"]}
    assert "python3 -m pytest -q tests/test_feature.py" in cmds
    assert cmds["python3 -m pytest -q tests/test_feature.py"]["exit_code"] == 0
    assert cmds["python3 -m pytest -q tests/test_feature.py"]["duration_s"] == 1.25
    assert "lease-token-should-vanish" not in blob
    assert "4 passed" in blob
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    assert head.stdout.strip() in blob


def test_credential_shape_refuses_to_write(swarm_dir, tmp_path):
    materialize(CREDENTIAL, signing_key=os.environ["SWARM_SIGNING_KEY"])
    out = tmp_path / "receipt.json"
    proc = run_export(tmp_path, "--correlation-id", CREDENTIAL["correlation_id"], "--out", str(out))
    assert proc.returncode != 0
    assert "E-POLICY" in proc.stdout
    assert "credential" in proc.stdout.lower()
    assert SECRET_TEXT not in proc.stdout
    assert SECRET_TEXT not in proc.stderr
    assert not out.exists()


def test_credential_past_the_output_cap_still_refuses(swarm_dir, tmp_path):
    spec = json.loads(json.dumps(CREDENTIAL))
    spec["events"][0]["payload"]["summary"] = ("x" * 600) + ' api_key = "notarealsecretkey"'
    materialize(spec, signing_key=os.environ["SWARM_SIGNING_KEY"])
    out = tmp_path / "receipt.json"
    proc = run_export(tmp_path, "--correlation-id", spec["correlation_id"], "--out", str(out))
    assert proc.returncode != 0
    assert "E-POLICY" in proc.stdout
    assert SECRET_TEXT not in proc.stdout
    assert SECRET_TEXT not in proc.stderr
    assert not out.exists()


def test_missing_swarm_dir_is_e_input(tmp_path, monkeypatch):
    missing = tmp_path / "no-swarm"
    monkeypatch.setenv("SWARM_DIR", str(missing))
    out = tmp_path / "receipt.json"
    proc = run_export(tmp_path, "--out", str(out), env={"SWARM_DIR": str(missing)})
    assert proc.returncode == 2
    body = json.loads(proc.stdout)
    assert body["error"]["code"] == "E-INPUT"
    assert "no .swarm directory" in body["error"]["message"]
    assert not out.exists()
    assert not (missing / "tasks.db").exists()


def test_empty_store_invents_nothing(swarm_dir, tmp_path):
    from swarm.taskstore import TaskStore

    TaskStore()  # tasks.db with no rows
    proc = run_export(tmp_path)
    receipt = _receipt(proc)
    _assert_desk_shape(receipt)
    assert receipt["commands"] == []
    assert receipt["claims"] == []
    assert receipt["files_changed"] == []
    assert receipt["approved_by"] == ""
    assert any("no tasks" in line for line in receipt["unverified"])
    assert "APPROVED" not in json.dumps(receipt)


def test_dry_run_is_canned_and_writes_nothing(tmp_path, monkeypatch):
    missing = tmp_path / "absent"
    monkeypatch.setenv("SWARM_DIR", str(missing))
    out = tmp_path / "receipt.json"
    proc = run_export(tmp_path, "--dry-run", "--out", str(out), env={"SWARM_DIR": str(missing)})
    receipt = _receipt(proc)
    _assert_desk_shape(receipt)
    assert any("advisory" in c["claim"] for c in receipt["claims"])
    assert not out.exists()
    # The script emits its own event after a dry run; it must not open a task store to do that.
    assert not (missing / "tasks.db").exists()


def test_credential_patterns_match_review_gate():
    rev = _load(ROOT / "scripts" / "rev_gate.py", "rev_gate_for_receipt")
    export = _load(SCRIPT, "receipt_export_for_patterns")
    assert [p.pattern for p in export.CREDENTIAL_RES] == [p.pattern for p in rev.SECRET_RES]


@pytest.mark.skipif(_bun() is None, reason="bun not installed")
def test_ts_twin_byte_identical(swarm_dir, tmp_path):
    materialize(SAMPLE, signing_key=os.environ["SWARM_SIGNING_KEY"])
    args = ["--json", "--root", str(ROOT), "--correlation-id", SAMPLE["correlation_id"]]

    def once(cmd: list[str], dest: Path) -> subprocess.CompletedProcess[str]:
        shutil.copytree(swarm_dir, dest)
        env = os.environ.copy()
        env["SWARM_DIR"] = str(dest)
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)

    py = once([sys.executable, str(SCRIPT), *args], tmp_path / "py")
    ts = once([_bun(), str(ROOT / "scripts" / "ts" / "receipt_export.ts"), *args], tmp_path / "ts")
    assert py.returncode == 0, py.stderr[-800:]
    assert ts.returncode == py.returncode
    assert ts.stdout == py.stdout
