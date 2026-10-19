"""Trusted controller kickoff authenticates persisted plans, never planning previews."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import ROOT, run_script

ED_SEED = "11" * 32


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    for name in ("SWARM_SIGNING_KEY", "SWARM_ED25519_KEY", "SWARM_ED25519_KEY_FILE",
                 "SWARM_REQUIRE_KEY", "SWARM_ALLOW_INSECURE_DEV_KEY", "SWARM_AGENT_SESSION", "SWARM_CHILD"):
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("orch_plan_bootstrap", ROOT / "scripts" / "orch_plan.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _signer(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    directory = tmp_path / "signer"
    directory.mkdir(mode=0o700)
    path = directory / "signing-key.hex"
    path.write_text(ED_SEED)
    path.chmod(0o600)
    monkeypatch.setenv("SWARM_ED25519_KEY_FILE", str(path))
    return path


def _args():
    return SimpleNamespace(graph_id=None, brief=None, brief_text="fixture controller plan",
                           plan=None, slices=None, slices_json=None, pattern="hotfix",
                           prefix="BOOT", risk_class="medium", priority="P1", acceptance=["fixture acceptance"])


def _ctx(tmp_path, corr=None, dry_run=False):
    from swarm.script_base import Ctx
    return Ctx("A01", "orch_plan", "ignored-caller-task", corr, dry_run, tmp_path)


def _assert_signed_result(result, tmp_path):
    from swarm.envelope import verify_envelope
    from swarm.taskstore import TaskStore
    store = TaskStore(root=tmp_path)
    corr = result["correlation_id"]
    tasks = store.list(correlation_id=corr)
    assignment = result["execution_assignment"]
    payload = assignment["payload"]
    assert result["execution_assignment_verified"] is True
    assert verify_envelope(assignment)
    assert assignment["sig"].startswith("ed25519:")
    assert assignment["schema"] == "swarm.v1.task.assign" and assignment["type"] == "task.assign"
    assert assignment["source"] == "A01@controller" and assignment["target"] == "A01"
    assert assignment["correlation_id"] == payload["correlation_id"] == payload["task_id"] == corr
    assert payload["agent_id"] == "A01" and payload["capability"] == "plan.execute"
    assert payload["ready_tasks"] == [t["task_id"] for t in store.ready(corr)]
    assert payload["inputs"][0]["tasks"] == result["tasks"]
    assert payload["inputs"][0]["correlation_id"] == corr
    assert payload["acceptance"] == ["fixture acceptance"]
    assert payload["risk_class"] == assignment["risk_class"] == "medium"
    assert payload["budget"] == {name: sum(t["budget"].get(name, 0) for t in tasks)
                                  for name in ("max_tokens", "max_wall_s", "max_cost_usd")}
    assert ED_SEED not in json.dumps(result)
    return store


def test_live_controller_assignment_uses_persisted_plan_not_caller_identity(controller, tmp_path, monkeypatch):
    _signer(tmp_path, monkeypatch)
    result = controller.run(_args(), _ctx(tmp_path, "fixture-correlation"))
    store = _assert_signed_result(result, tmp_path)
    snapshot = json.loads(Path(result["plan_file"]).read_text())
    assert snapshot["tasks"] == result["tasks"]
    assert result["execution_assignment"]["payload"]["inputs"] == [snapshot]
    assert result["execution_assignment"]["payload"]["task_id"] != "ignored-caller-task"
    assert all(t["state"] == "PLANNED" and t["attempt"] == 0 for t in store.list())


def test_reused_controller_assignment_uses_returned_correlation_and_current_tasks(controller, tmp_path, monkeypatch):
    from swarm.taskstore import TaskStore
    _signer(tmp_path, monkeypatch)
    first = controller.run(_args(), _ctx(tmp_path, "persisted-correlation"))
    store = TaskStore(root=tmp_path)
    first_task = first["tasks"][0]["task_id"]
    for state in ("CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        store.transition(first_task, state)
    before = store.list()
    history = {t["task_id"]: store.history(t["task_id"]) for t in before}
    result = controller.run(_args(), _ctx(tmp_path))
    assert result["reused"] is True and result["correlation_id"] == first["correlation_id"]
    _assert_signed_result(result, tmp_path)
    assert result["tasks"][0]["state"] == "IN_REVIEW"
    assert result["execution_assignment"]["payload"]["ready_tasks"] == ["BOOT-patch"]
    assert store.list() == before
    assert {t["task_id"]: store.history(t["task_id"]) for t in before} == history


def test_immediate_reuse_retains_authenticated_payload(controller, tmp_path, monkeypatch):
    _signer(tmp_path, monkeypatch)
    first = controller.run(_args(), _ctx(tmp_path, "stable-correlation"))
    result = controller.run(_args(), _ctx(tmp_path))
    assert result["reused"] is True
    assert result["execution_assignment"]["payload"] == first["execution_assignment"]["payload"]
    assert result["execution_assignment"]["msg_id"] != first["execution_assignment"]["msg_id"]
    _assert_signed_result(result, tmp_path)


def test_reuse_refuses_persisted_snapshot_correlation_mismatch(controller, tmp_path, monkeypatch):
    from swarm.errors import SwarmError
    _signer(tmp_path, monkeypatch)
    first = controller.run(_args(), _ctx(tmp_path, "snapshot-correlation"))
    path = Path(first["plan_file"])
    snapshot = json.loads(path.read_text())
    snapshot["correlation_id"] = "other-correlation"
    path.write_text(json.dumps(snapshot))
    with pytest.raises(SwarmError, match="persisted controller plan correlation mismatch"):
        controller.run(_args(), _ctx(tmp_path))


@pytest.mark.parametrize("field", ["task_id", "correlation_id", "capability", "ready_tasks", "inputs", "budget"])
def test_controller_assignment_rejects_payload_tampering(controller, tmp_path, monkeypatch, field):
    from swarm.envelope import verify_envelope
    _signer(tmp_path, monkeypatch)
    result = controller.run(_args(), _ctx(tmp_path, "tamper-correlation"))
    assignment = copy.deepcopy(result["execution_assignment"])
    assignment["payload"][field] = "tampered"
    assert not verify_envelope(assignment)


@pytest.mark.parametrize("dev", [False, True])
def test_keyless_or_advisory_plan_never_claims_authenticated_authority(controller, tmp_path, monkeypatch, dev):
    if dev:
        monkeypatch.setenv("SWARM_ALLOW_INSECURE_DEV_KEY", "1")
    first = controller.run(_args(), _ctx(tmp_path, "unsigned-correlation"))
    reused = controller.run(_args(), _ctx(tmp_path))
    assert reused["reused"]
    for result in (first, reused):
        assert "execution_assignment" not in result and "execution_assignment_verified" not in result
    from swarm.taskstore import TaskStore
    assert all(t["state"] == "PLANNED" for t in TaskStore(root=tmp_path).list())


def test_signed_configuration_does_not_authenticate_or_persist_dry_run(controller, tmp_path, monkeypatch):
    _signer(tmp_path, monkeypatch)
    result = controller.run(_args(), _ctx(tmp_path, "preview-correlation", dry_run=True))
    assert result["dry_run"] is True
    assert "execution_assignment" not in result and "execution_assignment_verified" not in result
    assert not (tmp_path / ".swarm").exists()


@pytest.mark.parametrize("flag", ["SWARM_AGENT_SESSION", "SWARM_CHILD"])
def test_specialist_plan_cannot_auto_sign_with_private_reference(controller, tmp_path, monkeypatch, flag):
    from swarm import envelope
    _signer(tmp_path, monkeypatch)
    monkeypatch.setenv(flag, "1")
    reads = []
    monkeypatch.setattr(envelope, "_private_seed", lambda *a, **k: reads.append((a, k)))
    result = controller.run(_args(), _ctx(tmp_path, "child-correlation"))
    assert "execution_assignment" not in result and "execution_assignment_verified" not in result
    assert reads == []


def test_invalid_signer_is_redacted_before_live_plan_persistence(controller, tmp_path, monkeypatch):
    from swarm.errors import SwarmError, ErrorCode
    monkeypatch.setenv("SWARM_ED25519_KEY_FILE", str(tmp_path / "missing-private-file"))
    with pytest.raises(SwarmError) as exc:
        controller.run(_args(), _ctx(tmp_path, "invalid-correlation"))
    assert exc.value.code is ErrorCode.E_POLICY
    assert "private Ed25519 signing reference cannot be loaded safely" in str(exc.value)
    assert str(tmp_path) not in str(exc.value) and ED_SEED not in str(exc.value)
    assert not (tmp_path / ".swarm").exists()


def test_controller_never_marks_an_unverified_signature(controller, tmp_path, monkeypatch):
    from swarm.errors import SwarmError
    _signer(tmp_path, monkeypatch)
    monkeypatch.setattr(controller, "verify_envelope", lambda _assignment: False)
    with pytest.raises(SwarmError, match="controller assignment signature invalid"):
        controller.run(_args(), _ctx(tmp_path, "unverified-correlation"))


def test_live_script_output_and_audit_do_not_expose_private_seed(controller, tmp_path, monkeypatch):
    path = _signer(tmp_path, monkeypatch)
    result = run_script("orch_plan.py", "--root", str(tmp_path), "--brief-text", "fixture controller plan",
                        "--pattern", "hotfix", "--correlation-id", "output-correlation", "--json",
                        env={"SWARM_ED25519_KEY_FILE": str(path)})
    assert result.returncode == 0, result.stdout + result.stderr
    body = json.loads(result.stdout)
    assert body["execution_assignment_verified"] is True
    assert ED_SEED not in result.stdout + result.stderr
    assert ED_SEED not in (tmp_path / ".swarm" / "events.jsonl").read_text()
    assert ED_SEED not in json.dumps(result.args)
