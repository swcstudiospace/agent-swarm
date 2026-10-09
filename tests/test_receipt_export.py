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
    assert receipt["completed_at"]
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    sha = head.stdout.strip()
    for claim in receipt["claims"]:
        assert "commit not recorded" in claim["claim"]
        assert sha not in claim["claim"]
    assert any("not the commit the gates checked" in line and sha in line for line in receipt["unverified"])
    assert any("state: IN_REVIEW" in line for line in receipt["unverified"])


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


def _seed(corr: str, tasks: list[dict], events: list[dict] | None = None, *, stop: str = "IN_REVIEW"):
    from swarm.runlog import emit
    from swarm.taskstore import TaskStore

    store = TaskStore()
    path = ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW", "APPROVED")
    states = path[:path.index(stop) + 1]
    for task in tasks:
        store.create(
            task_id=task["task_id"], correlation_id=corr, capability=task["capability"],
            agent_id=task["agent_id"], title=task.get("title") or "", risk_class=task.get("risk_class", "low"),
            notes=task.get("notes") or {})
        for state in states:
            store.transition(task["task_id"], state)
    for event in events or []:
        emit(event["type"], event["payload"], source="fixture", correlation_id=corr,
             task_id=event["task_id"], tee=False)
    (store.path.parent / "latest_correlation").write_text(corr, encoding="utf-8")
    return store


def test_json_credential_shape_refuses_to_write(swarm_dir, tmp_path):
    spec = json.loads(json.dumps(CREDENTIAL))
    spec["events"][0]["payload"]["summary"] = '{"api_key":"notarealsecretkey"}'
    materialize(spec, signing_key=os.environ["SWARM_SIGNING_KEY"])
    out = tmp_path / "receipt.json"
    proc = run_export(tmp_path, "--correlation-id", spec["correlation_id"], "--out", str(out))
    assert proc.returncode != 0
    assert "E-POLICY" in proc.stdout
    assert SECRET_TEXT not in proc.stdout
    assert SECRET_TEXT not in proc.stderr
    assert not out.exists()


def test_prefixed_finding_redacts_lease_token(swarm_dir):
    materialize(SAMPLE, signing_key=os.environ["SWARM_SIGNING_KEY"])
    from swarm.taskstore import TaskStore

    store = TaskStore()
    summary = json.dumps({"lease_id": "lease-token-should-vanish", "note": "finding-kept"})
    store.conn.execute(
        "UPDATE verdicts SET findings=? WHERE task_id=? AND gate=?",
        (json.dumps([{"id": "F1", "summary": summary}]), "T-be", "quality"))
    store.conn.commit()
    store.update("T-be", title=json.dumps({"lease_id": "lease-token-should-vanish", "note": "title-kept"}))
    receipt = _receipt(run_export(ROOT, "--correlation-id", SAMPLE["correlation_id"]))
    blob = json.dumps(receipt)
    assert "lease-token-should-vanish" not in blob
    assert "finding-kept" in blob
    assert "title-kept" in blob
    assert any(line.startswith("finding T-be quality:") for line in receipt["unverified"])


def test_rerun_claims_bind_their_own_command_and_verdict(swarm_dir, tmp_path):
    from swarm.gates import make_verdict

    corr = "corr-rerun"
    store = _seed(corr, [
        {"task_id": "T-be", "capability": "code.backend", "agent_id": "A05", "title": "Implement",
         "notes": {"gates": []}},
        {"task_id": "T-rev", "capability": "gate.review", "agent_id": "A09", "title": "Review",
         "notes": {"gate": "review", "gate_for": ["T-be"], "gates": []}},
        {"task_id": "T-rev.r1", "capability": "gate.review", "agent_id": "A09", "title": "Review rerun",
         "notes": {"gate": "review", "gate_for": ["T-be"], "gates": []}},
    ], [
        {"type": "script.rev_gate", "task_id": "T-rev", "payload": {
            "status": "fail", "cmd": "python3 scripts/rev_gate.py --attempt 1",
            "exit_code": 1, "duration_s": 0.1, "summary": "first fail"}},
        {"type": "script.rev_gate", "task_id": "T-rev.r1", "payload": {
            "status": "ok", "cmd": "python3 scripts/rev_gate.py --attempt 2",
            "exit_code": 0, "duration_s": 0.2, "summary": "second pass"}},
    ])
    fail = make_verdict(gate="review", task_id="T-be", agent_id="A09", verdict="fail",
                        correlation_id=corr, extra={"gate_task": "T-rev"}, audit=False)
    passed = make_verdict(gate="review", task_id="T-be", agent_id="A09", verdict="pass",
                          correlation_id=corr, extra={"gate_task": "T-rev.r1"}, audit=False)
    store.record_verdict("T-be", fail)
    store.record_verdict("T-be", passed)
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr))
    _assert_desk_shape(receipt)
    first = next(c for c in receipt["claims"] if "gate task T-rev for" in c["claim"])
    second = next(c for c in receipt["claims"] if "gate task T-rev.r1 for" in c["claim"])
    assert first["evidence_command_index"] != second["evidence_command_index"]
    assert "attempt 1" in receipt["commands"][first["evidence_command_index"]]["cmd"]
    assert "attempt 2" in receipt["commands"][second["evidence_command_index"]]["cmd"]
    assert "recorded fail" in first["claim"]
    assert "recorded pass" in second["claim"]
    assert "signature signed" in first["claim"]
    assert "signature signed" in second["claim"]


def test_keyless_advisory_file_is_not_reported_missing(swarm_dir, tmp_path):
    from swarm.verdicts import advisory_envelope

    corr = "corr-keyless"
    _seed(corr, [
        {"task_id": "T-be", "capability": "code.backend", "agent_id": "A05", "title": "Implement",
         "notes": {"gates": []}},
        {"task_id": "T-qa", "capability": "gate.quality", "agent_id": "A08", "title": "Quality",
         "notes": {"gate": "quality", "gate_for": ["T-be"], "gates": []}},
    ], [
        {"type": "script.qa_gate", "task_id": "T-qa", "payload": {
            "status": "ok", "cmd": "python3 scripts/qa_gate.py", "exit_code": 0,
            "duration_s": 0.3, "summary": "advisory preview"}},
    ])
    env = advisory_envelope(
        gate="quality", task_id="T-qa", agent_id="A08", findings=[], runs={}, expires_s=86400,
        correlation_id=corr, extra=None, reason="fail-closed: no signing key configured")
    vdir = swarm_dir / "verdicts"
    vdir.mkdir()
    (vdir / "T-qa.quality.json").write_text(json.dumps(env), encoding="utf-8")
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr))
    _assert_desk_shape(receipt)
    claim = next(c["claim"] for c in receipt["claims"] if "quality" in c["claim"] and "T-qa" in c["claim"])
    assert "advisory" in claim
    assert "unsigned keyless" in claim
    assert "not run" not in claim
    assert "signature signed" not in claim
    assert receipt["approved_by"] == ""


def test_dry_run_gate_event_is_a_simulation(swarm_dir, tmp_path):
    corr = "corr-sim"
    _seed(corr, [
        {"task_id": "T-be", "capability": "code.backend", "agent_id": "A05", "title": "Implement",
         "notes": {"gates": []}},
        {"task_id": "T-qa", "capability": "gate.quality", "agent_id": "A08", "title": "Quality",
         "notes": {"gate": "quality", "gate_for": ["T-be"], "gates": []}},
    ], [
        {"type": "script.qa_gate", "task_id": "T-qa", "payload": {
            "status": "ok", "dry_run": True, "cmd": "python3 scripts/qa_gate.py"}},
    ])
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr))
    _assert_desk_shape(receipt)
    cmd = next(c for c in receipt["commands"] if "qa_gate.py" in c["cmd"])
    assert cmd["cmd"] == "python3 scripts/qa_gate.py --dry-run"
    assert cmd["exit_code"] == 0
    claim = next(c["claim"] for c in receipt["claims"] if "quality" in c["claim"])
    assert "simulation" in claim
    assert "not run" not in claim


def test_gate_event_without_a_command_is_not_invented(swarm_dir, tmp_path):
    corr = "corr-nocmd"
    _seed(corr, [
        {"task_id": "T-be", "capability": "code.backend", "agent_id": "A05", "title": "Implement",
         "notes": {"gates": []}},
        {"task_id": "T-qa", "capability": "gate.quality", "agent_id": "A08", "title": "Quality",
         "notes": {"gate": "quality", "gate_for": ["T-be"], "gates": []}},
    ], [
        {"type": "script.qa_gate", "task_id": "T-qa", "payload": {
            "status": "fail",
            "verdict": "fail",
            "executed": [{"runner": "pytest", "returncode": 1}],
            "summary": "quality gate FAIL — runners: ['pytest']"}},
    ])
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr))
    _assert_desk_shape(receipt)
    assert receipt["claims"] == []
    assert all("python3 scripts/qa_gate.py" not in c["cmd"] for c in receipt["commands"])
    quality = [line for line in receipt["unverified"] if "T-qa" in line or "script qa_gate result" in line]
    assert quality
    assert any("no recorded cmd" in line for line in receipt["unverified"])
    assert any("command line was not recorded" in line and "recorded fail" in line for line in quality)
    assert any("script qa_gate result: quality gate FAIL" in line for line in receipt["unverified"])
    assert all("not run" not in line for line in quality)
    assert all("recorded none" not in line for line in quality)


def test_status_summary_with_approved_still_exports(swarm_dir, tmp_path):
    corr = "corr-status"
    _seed(corr, [
        {"task_id": "T-be", "capability": "code.backend", "agent_id": "A05", "title": "Finished",
         "notes": {"gates": []}},
    ], [
        {"type": "script.orch_status", "task_id": "T-be", "payload": {
            "status": "ok",
            "cmd": "python3 scripts/orch_status.py --correlation-id corr-status",
            "exit_code": 0,
            "summary": "T-be          A05   APPROVED           1   0      -"}},
    ], stop="APPROVED")
    out = tmp_path / "receipt.json"
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr, "--out", str(out)))
    assert out.is_file()
    tail = receipt["commands"][0]["output_tail"]
    assert "APPROVED" not in tail
    assert "advisory-complete" in tail
    assert "APPROVED" not in json.dumps(receipt)


def test_image_artifact_is_not_a_changed_file(swarm_dir, tmp_path):
    corr = "corr-image"
    store = _seed(corr, [
        {"task_id": "T-be", "capability": "code.backend", "agent_id": "A05", "title": "Implement",
         "notes": {"gates": []}},
    ])
    image = "example.com/app@sha256:" + ("ab" * 32)
    record_path = "/work/job@2/.swarm/artifacts/build.json"
    store.add_artifact("T-be", kind="code", uri="src/feature.py", producer="A05")
    store.add_artifact("T-be", kind="build.artifact", uri=".swarm/artifacts/record.json", producer="A11")
    store.add_artifact("T-be", kind="build.artifact", uri=record_path, producer="A11")
    store.add_artifact("T-be", kind="build.artifact", uri=image, producer="A11")
    artifacts = swarm_dir / "artifacts"
    artifacts.mkdir()
    (artifacts / "build.json").write_text(
        json.dumps({"kind": "build.artifact", "image": image}), encoding="utf-8")
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr))
    _assert_desk_shape(receipt)
    assert receipt["files_changed"] == [
        ".swarm/artifacts/record.json", "/work/job@2/.swarm/artifacts/build.json", "src/feature.py"]
    blob = json.dumps(receipt)
    assert image in blob
    assert record_path in blob
    assert any(image in line and "non-file artifact" in line for line in receipt["unverified"])


def test_approval_token_inside_a_path_is_omitted(swarm_dir, tmp_path):
    corr = "corr-path"
    _seed(corr, [
        {"task_id": "T-be", "capability": "code.backend", "agent_id": "A05", "title": "Implement",
         "notes": {"gates": []}},
    ], [
        {"type": "script.qa_gate", "task_id": "T-be", "payload": {
            "status": "ok",
            "cmd": "python3 -m pytest docs/APPROVED.md",
            "exit_code": 0,
            "summary": "T-be          A05   APPROVED           1"}},
    ])
    from swarm.taskstore import TaskStore

    TaskStore().add_artifact("T-be", kind="code", uri="docs/APPROVED.md", producer="A05")
    TaskStore().add_artifact("T-be", kind="code", uri="src/feature.py", producer="A05")
    out = tmp_path / "receipt.json"
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr, "--out", str(out)))
    assert out.is_file()
    blob = json.dumps(receipt)
    assert "APPROVED" not in blob
    assert "advisory-complete.md" not in blob
    assert receipt["files_changed"] == ["src/feature.py"]
    assert receipt["commands"] == []
    assert any("files_changed" in line and "could not be preserved" in line for line in receipt["unverified"])
    assert any("command line contained the approval token" in line for line in receipt["unverified"])
    assert any("recorded output:" in line and "advisory-complete" in line for line in receipt["unverified"])
    assert all("docs/" not in line for line in receipt["unverified"])


def test_in_progress_task_is_not_completed(swarm_dir, tmp_path):
    corr = "corr-open"
    _seed(corr, [
        {"task_id": "T-open", "capability": "code.backend", "agent_id": "A05", "title": "Still going",
         "notes": {"gates": []}},
    ], stop="IN_PROGRESS")
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr))
    _assert_desk_shape(receipt)
    assert receipt["completed_at"] is None
    assert any("T-open" in line and "IN_PROGRESS" in line for line in receipt["unverified"])
    assert "APPROVED" not in json.dumps(receipt)


def test_approved_state_is_withheld(swarm_dir, tmp_path):
    corr = "corr-done"
    _seed(corr, [
        {"task_id": "T-done", "capability": "code.backend", "agent_id": "A05", "title": "Finished",
         "notes": {"gates": []}},
    ], stop="APPROVED")
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr))
    _assert_desk_shape(receipt)
    assert receipt["completed_at"]
    blob = json.dumps(receipt)
    assert "APPROVED" not in blob
    assert any("T-done" in line and "advisory-complete" in line for line in receipt["unverified"])


def test_verdict_without_gate_task_is_not_called_absent(swarm_dir, tmp_path):
    corr = "corr-row"
    store = _seed(corr, [
        {"task_id": "T-be", "capability": "code.backend", "agent_id": "A05", "title": "Implement",
         "risk_class": "low", "notes": {}},
    ])
    now = time.time()
    store.conn.execute(
        "INSERT INTO verdicts (task_id, gate, verdict, agent_id, findings, expires_at, ts,"
        " envelope_json, sig) VALUES (?,?,?,?,?,?,?,?,?)",
        ("T-be", "review", "pass", "A09", "[]", now + 86400, now, None, None))
    store.conn.commit()
    receipt = _receipt(run_export(tmp_path, "--correlation-id", corr))
    review = [line for line in receipt["unverified"] if "review" in line]
    assert review
    assert all("no verdict row" not in line for line in review)
    assert any("signature unsigned" in line and "no gate task" in line for line in review)


def test_recorded_event_commit_is_the_checked_commit(swarm_dir):
    spec = json.loads(json.dumps(SAMPLE))
    recorded = "a" * 40
    spec["events"][0]["payload"]["commit"] = recorded
    materialize(spec, signing_key=os.environ["SWARM_SIGNING_KEY"])
    receipt = _receipt(run_export(ROOT, "--correlation-id", SAMPLE["correlation_id"]))
    quality = next(c["claim"] for c in receipt["claims"] if "quality" in c["claim"])
    review = next(c["claim"] for c in receipt["claims"] if "gate task T-rev for" in c["claim"])
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    assert recorded in quality
    assert head.stdout.strip() not in quality
    assert "commit not recorded" in review
    assert recorded not in review


def test_concurrent_writes_do_not_share_a_temp_file(tmp_path):
    import threading

    export = _load(SCRIPT, "receipt_export_for_write")
    dest = tmp_path / "receipt.json"
    bodies = [{"n": i, "pad": "y" * 4000} for i in range(2)]
    errors: list[BaseException] = []

    def go(body: dict) -> None:
        try:
            export._write(str(dest), body)
        except BaseException as exc:  # noqa: BLE001 - the test records a race failure
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert json.loads(dest.read_text(encoding="utf-8")) in bodies
    assert list(dest.parent.glob("*.tmp")) == []


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
