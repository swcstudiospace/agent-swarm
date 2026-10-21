"""Behaviour of the four v2.2 A11 tools: provenance gate, deploy gate, rollback target, Terraform blast radius."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm import tool_registry
from swarm.tool_registry import call_tool

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a11-devops.tools.json"
NEW_NAMES = ("devops_provenance_gate", "devops_deploy_gate", "devops_rollback_target", "devops_tf_blast_radius")
SHA = "sha256:" + "a1" * 32
IMAGE = "oci://registry.example/app@sha256:" + "b2" * 32


def _registry() -> dict:
    """The live registry plus any fragment entry the merge has not applied yet, so tests pass before and after."""
    data = copy.deepcopy(tool_registry.load_document())
    fragment = json.loads(FRAGMENT.read_text(encoding="utf-8")) if FRAGMENT.exists() else []
    agent = next(a for a in data["agents"] if a["id"] == "A11")
    present = {t["name"] for t in agent["registered_tools"]}
    agent["registered_tools"].extend(copy.deepcopy(e) for e in fragment if e["name"] not in present)
    return data


@pytest.fixture(scope="module")
def registry() -> dict:
    return _registry()


def _call(registry: dict, name: str, payload: dict, caller: str = "A11") -> dict:
    return call_tool(name, caller, payload, data=registry)


def _record(**overrides) -> dict:
    record = {
        "kind": "build.artifact",
        "sha": "9f2c1ab7d0e34b56a8c91d2e3f405a6b7c8d9e0f",
        "dirty": False,
        "tree_digest": SHA,
        "image": IMAGE,
        "provenance": {"builder": "A11/devops_build_record", "attestation": "oci://registry.example/app.att", "reproducible": True},
    }
    record.update(overrides)
    return record


# ---- registry contract -------------------------------------------------------------------------------------------


def test_fragment_entries_validate_against_the_registry(tmp_path):
    copy_doc = _registry()
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(copy_doc), encoding="utf-8")
    assert tool_registry.validate_registry(path) == []
    names = {t["name"] for t in next(a for a in copy_doc["agents"] if a["id"] == "A11")["registered_tools"]}
    assert set(NEW_NAMES) <= names


def test_new_entries_declare_read_tools_for_the_owner_only(registry):
    # The fragment is deleted by the intake merge; afterwards the same entries live in the registry.
    a11 = next(a for a in registry["agents"] if a["id"] == "A11")
    entries = [t for t in a11["registered_tools"] if t["name"] in NEW_NAMES]
    assert sorted(e["name"] for e in entries) == sorted(NEW_NAMES)
    for entry in entries:
        assert entry["owner"] == "A11" and entry["permitted_callers"] == ["A11"]
        assert entry["side_effect_tier"] == "READ" and entry["idempotency"] == "idempotent"
        assert entry["handler"] == f"swarm.tools.a11_tools:{entry['name'][len('devops_'):]}"


@pytest.mark.parametrize("name", NEW_NAMES)
def test_probes_and_unauthorized_caller_follow_the_contract(registry, name):
    entry = next(t for t in next(a for a in registry["agents"] if a["id"] == "A11")["registered_tools"] if t["name"] == name)
    examples = entry["examples"]
    assert _call(registry, name, examples["dependency"])["state"] == "DEPENDENCY_UNAVAILABLE"
    assert _call(registry, name, examples["timeout"])["state"] == "TIMEOUT"
    assert _call(registry, name, examples["partial"])["state"] == "PARTIAL_SUCCESS"
    assert _call(registry, name, examples["success"])["state"] == "SUCCESS"
    assert _call(registry, name, examples["invalid_input"])["state"] == "INVALID_INPUT"
    assert _call(registry, name, examples["success"], caller=examples["unauthorized_caller"])["state"] == "UNAUTHORIZED"


# ---- devops_provenance_gate --------------------------------------------------------------------------------------


def test_provenance_gate_accepts_a_complete_clean_record(registry):
    result = _call(registry, "devops_provenance_gate", {"record": _record()})
    assert result == {"state": "SUCCESS", "decision": "accept", "missing": [], "warnings": []}


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"provenance": {"builder": "A11/devops_build_record", "reproducible": True}}, ["provenance.attestation"]),
        ({"provenance": {"attestation": "   "}}, ["provenance.attestation"]),
        ({"provenance": {}}, ["provenance.attestation"]),
        ({"tree_digest": ""}, ["tree_digest"]),
        ({"tree_digest": "sha256:" + "A1" * 32}, ["tree_digest"]),
        ({"tree_digest": "sha256:abc"}, ["tree_digest"]),
    ],
)
def test_provenance_gate_rejects_each_missing_or_malformed_evidence_field(registry, overrides, expected):
    result = _call(registry, "devops_provenance_gate", {"record": _record(**overrides)})
    assert result["decision"] == "reject"
    assert result["missing"] == expected


def test_provenance_gate_rejects_a_record_with_no_provenance_object_at_all(registry):
    record = _record()
    del record["provenance"]
    result = _call(registry, "devops_provenance_gate", {"record": record})
    assert result["decision"] == "reject"
    assert result["missing"] == ["provenance.attestation"]
    assert "builder-missing" in result["warnings"] and "not-reproducible" in result["warnings"]


@pytest.mark.parametrize(
    "image",
    [
        "oci://registry.example/app:1.4.2",
        "oci://registry.example/app:latest",
        "registry.example/app",
        "oci://registry.example/app@sha256:" + "b2" * 31,
        "oci://registry.example/app@sha256:" + "B2" * 32,
        "oci://registry.example/app @sha256:" + "b2" * 32,
        "@sha256:" + "b2" * 32,
    ],
)
def test_provenance_gate_rejects_an_unpinned_or_malformed_image(registry, image):
    result = _call(registry, "devops_provenance_gate", {"record": _record(image=image)})
    assert result["decision"] == "reject"
    assert result["missing"] == ["image"]


def test_provenance_gate_accepts_an_unprefixed_digest_pinned_image(registry):
    result = _call(registry, "devops_provenance_gate", {"record": _record(image="registry.example/app@sha256:" + "b2" * 32)})
    assert result["decision"] == "accept"


def test_provenance_gate_treats_no_image_as_a_warning_because_build_record_makes_it_optional(registry):
    record = _record()
    del record["image"]
    result = _call(registry, "devops_provenance_gate", {"record": record})
    assert result["decision"] == "accept" and result["warnings"] == ["image-absent"]


def test_provenance_gate_dirty_tree_warns_but_does_not_reject(registry):
    result = _call(registry, "devops_provenance_gate", {"record": _record(dirty=True)})
    assert result["decision"] == "accept" and result["warnings"] == ["dirty-tree"]


def test_provenance_gate_unknown_dirty_state_and_non_reproducible_build_warn(registry):
    record = _record(provenance={"builder": "A11/devops_build_record", "attestation": "oci://x/a.att", "reproducible": False})
    del record["dirty"]
    result = _call(registry, "devops_provenance_gate", {"record": record})
    assert result["decision"] == "accept"
    assert result["warnings"] == ["dirty-unknown", "not-reproducible"]


def test_provenance_gate_lists_every_missing_field_in_a_fixed_order(registry):
    result = _call(registry, "devops_provenance_gate", {"record": {"image": "app:1", "tree_digest": "nope"}})
    assert result["decision"] == "reject"
    assert result["missing"] == ["provenance.attestation", "tree_digest", "image"]


def test_provenance_gate_empty_record_fails_closed(registry):
    result = _call(registry, "devops_provenance_gate", {"record": {}})
    assert result["decision"] == "reject"
    assert result["missing"] == ["provenance.attestation", "tree_digest"]


@pytest.mark.parametrize("payload, field", [({}, "record"), ({"record": "x"}, "record"), ({"record": {"dirty": "no"}}, "record"), ({"record": {}, "extra": 1}, "extra")])
def test_provenance_gate_malformed_input_names_the_field(registry, payload, field):
    result = _call(registry, "devops_provenance_gate", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_provenance_gate_matches_the_build_record_dry_run_shape(registry):
    # devops_build_record.py --dry-run emits an image-less record whose provenance has no attestation: fail closed.
    record = {"kind": "build.artifact", "dirty": False, "tree_digest": "sha256:" + "0" * 64, "image": None,
              "provenance": {"builder": "A11/devops_build_record", "reproducible": True}}
    result = _call(registry, "devops_provenance_gate", {"record": {k: v for k, v in record.items() if v is not None}})
    assert result["decision"] == "reject" and result["missing"] == ["provenance.attestation"]


# ---- devops_deploy_gate ------------------------------------------------------------------------------------------


def _gate(**overrides) -> dict:
    payload = {"security_verdict": "pass", "frozen": False, "drift_frozen": False, "provenance_ok": True, "env_tier": "staging"}
    payload.update(overrides)
    return payload


def test_deploy_gate_allows_a_clean_non_production_deploy(registry):
    assert _call(registry, "devops_deploy_gate", _gate()) == {"state": "SUCCESS", "decision": "allow", "blockers": []}
    assert _call(registry, "devops_deploy_gate", _gate(env_tier="ephemeral"))["decision"] == "allow"


def test_deploy_gate_production_needs_human_approval_even_when_clean(registry):
    result = _call(registry, "devops_deploy_gate", _gate(env_tier="production"))
    assert result["decision"] == "needs-human-approval" and result["blockers"] == []


def test_deploy_gate_missing_verdict_fails_closed(registry):
    result = _call(registry, "devops_deploy_gate", _gate(security_verdict="missing"))
    assert result["decision"] == "block" and result["blockers"] == ["security-verdict-missing"]


def test_deploy_gate_failed_verdict_blocks(registry):
    result = _call(registry, "devops_deploy_gate", _gate(security_verdict="fail"))
    assert result["decision"] == "block" and result["blockers"] == ["security-verdict-fail"]


@pytest.mark.parametrize(
    "override, blocker",
    [({"frozen": True}, "freeze-window"), ({"drift_frozen": True}, "drift-freeze"), ({"provenance_ok": False}, "provenance")],
)
def test_deploy_gate_each_single_blocker_blocks_in_every_tier(registry, override, blocker):
    for tier in ("ephemeral", "staging", "production"):
        result = _call(registry, "devops_deploy_gate", _gate(env_tier=tier, **override))
        assert result["decision"] == "block" and result["blockers"] == [blocker]


def test_deploy_gate_blockers_accumulate_in_a_fixed_order_and_block_beats_human_approval(registry):
    result = _call(
        registry,
        "devops_deploy_gate",
        _gate(security_verdict="missing", frozen=True, drift_frozen=True, provenance_ok=False, env_tier="production"),
    )
    assert result["decision"] == "block"
    assert result["blockers"] == ["security-verdict-missing", "freeze-window", "drift-freeze", "provenance"]


@pytest.mark.parametrize(
    "payload, field",
    [
        ({k: v for k, v in _gate().items() if k != "security_verdict"}, "security_verdict"),
        (_gate(security_verdict="maybe"), "security_verdict"),
        (_gate(frozen="yes"), "frozen"),
        (_gate(env_tier="qa"), "env_tier"),
        (_gate(provenance_ok=None), "provenance_ok"),
    ],
)
def test_deploy_gate_malformed_input_names_the_field(registry, payload, field):
    result = _call(registry, "devops_deploy_gate", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


# ---- devops_rollback_target --------------------------------------------------------------------------------------


def _snap(id_: str, at: str, ok: bool) -> dict:
    return {"id": id_, "at": at, "applied_ok": ok}


GOOD_AND_BAD = [_snap("s1", "2026-03-01T10:00:00Z", True), _snap("s2", "2026-03-02T10:00:00Z", True), _snap("s3", "2026-03-03T10:00:00Z", False)]


@pytest.mark.parametrize("attempts", [0, 1])
def test_rollback_target_retries_once_before_rolling_back(registry, attempts):
    result = _call(registry, "devops_rollback_target", {"snapshots": GOOD_AND_BAD, "attempts": attempts})
    assert result == {"state": "SUCCESS", "action": "retry", "target_id": "s2"}


@pytest.mark.parametrize("attempts", [2, 3, 1000])
def test_rollback_target_rolls_back_to_the_newest_good_snapshot_after_the_retry(registry, attempts):
    result = _call(registry, "devops_rollback_target", {"snapshots": GOOD_AND_BAD, "attempts": attempts})
    assert result == {"state": "SUCCESS", "action": "rollback", "target_id": "s2"}


def test_rollback_target_escalates_when_no_snapshot_is_good(registry):
    bad = [_snap("s1", "2026-03-01T10:00:00Z", False)]
    assert _call(registry, "devops_rollback_target", {"snapshots": bad, "attempts": 2}) == {
        "state": "SUCCESS", "action": "escalate", "target_id": "",
    }
    assert _call(registry, "devops_rollback_target", {"snapshots": [], "attempts": 5})["action"] == "escalate"


def test_rollback_target_still_retries_with_no_snapshot_because_the_retry_is_unspent(registry):
    result = _call(registry, "devops_rollback_target", {"snapshots": [], "attempts": 1})
    assert result == {"state": "SUCCESS", "action": "retry", "target_id": ""}


def test_rollback_target_orders_by_instant_across_offsets_not_by_text(registry):
    snaps = [_snap("early-text", "2026-03-02T09:00:00+05:00", True), _snap("late-instant", "2026-03-02T06:00:00Z", True)]
    # 09:00+05:00 is 04:00Z, so the 06:00Z snapshot is newer even though its text sorts first.
    result = _call(registry, "devops_rollback_target", {"snapshots": snaps, "attempts": 2})
    assert result["target_id"] == "late-instant"


def test_rollback_target_equal_instants_break_by_id_regardless_of_list_order(registry):
    a, b = _snap("snap-a", "2026-03-02T10:00:00Z", True), _snap("snap-b", "2026-03-02T10:00:00Z", True)
    assert _call(registry, "devops_rollback_target", {"snapshots": [a, b], "attempts": 2})["target_id"] == "snap-b"
    assert _call(registry, "devops_rollback_target", {"snapshots": [b, a], "attempts": 2})["target_id"] == "snap-b"


def test_rollback_target_ignores_a_newer_failed_snapshot(registry):
    snaps = [_snap("ok", "2026-03-01T00:00:00Z", True), _snap("new-bad", "2026-04-01T00:00:00Z", False)]
    assert _call(registry, "devops_rollback_target", {"snapshots": snaps, "attempts": 2})["target_id"] == "ok"


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"snapshots": [_snap("s1", "yesterday", True)], "attempts": 2}, "snapshots"),
        ({"snapshots": [_snap("s1", "2026-03-01T10:00:00", True)], "attempts": 2}, "snapshots"),
        ({"snapshots": [_snap("s1", "2026-03-01T10:00:00Z", True), _snap("s1", "2026-03-02T10:00:00Z", False)], "attempts": 2}, "snapshots"),
        ({"snapshots": [], "attempts": -1}, "attempts"),
        ({"snapshots": [], "attempts": 1001}, "attempts"),
        ({"snapshots": [], "attempts": "2"}, "attempts"),
        ({"snapshots": [{"id": "s1", "at": "2026-03-01T10:00:00Z"}], "attempts": 2}, "snapshots"),
        ({"snapshots": [_snap("s", "2026-03-01T10:00:00Z", True)] * 101, "attempts": 2}, "snapshots"),
        ({"attempts": 2}, "snapshots"),
    ],
)
def test_rollback_target_malformed_input_names_the_field(registry, payload, field):
    result = _call(registry, "devops_rollback_target", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


# ---- devops_tf_blast_radius --------------------------------------------------------------------------------------


def _rc(type_: str, address: str, *actions: str) -> dict:
    return {"type": type_, "address": address, "actions": list(actions)}


def test_blast_radius_counts_mixed_actions_and_flags_destructive_changes(registry):
    changes = [
        _rc("aws_instance", "aws_instance.web[0]", "create"),
        _rc("aws_instance", "aws_instance.web[1]", "create"),
        _rc("aws_s3_bucket", "aws_s3_bucket.logs", "update"),
        _rc("aws_db_instance", "aws_db_instance.main", "delete"),
        _rc("aws_lb", "aws_lb.edge", "delete", "create"),
        _rc("aws_vpc", "aws_vpc.main", "no-op"),
    ]
    result = _call(registry, "devops_tf_blast_radius", {"resource_changes": changes})
    assert (result["creates"], result["updates"], result["deletes"], result["replaces"]) == (2, 1, 1, 1)
    assert result["destructive"] is True
    assert result["note"] == (
        "5 resource change(s): 2 create, 1 update, 1 delete, 1 replace; "
        "types: aws_db_instance, aws_instance, aws_lb, aws_s3_bucket; "
        "destructive: aws_db_instance.main (delete), aws_lb.edge (replace)"
    )


def test_blast_radius_create_before_destroy_replace_counts_as_a_replace(registry):
    result = _call(registry, "devops_tf_blast_radius", {"resource_changes": [_rc("aws_lb", "aws_lb.edge", "create", "delete")]})
    assert (result["creates"], result["deletes"], result["replaces"]) == (0, 0, 1)
    assert result["destructive"] is True


def test_blast_radius_empty_and_no_op_plans_report_no_changes(registry):
    for changes in ([], [_rc("aws_vpc", "aws_vpc.main", "no-op"), _rc("aws_ami", "data.aws_ami.base", "read")]):
        result = _call(registry, "devops_tf_blast_radius", {"resource_changes": changes})
        assert result == {
            "state": "SUCCESS", "creates": 0, "updates": 0, "deletes": 0, "replaces": 0,
            "destructive": False, "note": "no resource changes",
        }


def test_blast_radius_create_and_update_only_is_not_destructive(registry):
    changes = [_rc("kubernetes_deployment", "kubernetes_deployment.api", "update"), _rc("aws_sqs_queue", "aws_sqs_queue.q", "create")]
    result = _call(registry, "devops_tf_blast_radius", {"resource_changes": changes})
    assert result["destructive"] is False and "destructive" not in result["note"]


@pytest.mark.parametrize(
    "actions",
    [["frobnicate"], ["create", "update"], ["no-op", "create"], ["create", "create"], ["delete", "delete"], ["read", "delete"]],
)
def test_blast_radius_rejects_malformed_action_lists(registry, actions):
    result = _call(registry, "devops_tf_blast_radius", {"resource_changes": [_rc("t", "t.a", *actions)]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "resource_changes"


def test_blast_radius_rejects_empty_actions_and_missing_fields(registry):
    assert _call(registry, "devops_tf_blast_radius", {"resource_changes": [{"type": "t", "address": "t.a", "actions": []}]})["field"] == "resource_changes"
    assert _call(registry, "devops_tf_blast_radius", {"resource_changes": [{"type": "t", "actions": ["create"]}]})["field"] == "resource_changes"
    assert _call(registry, "devops_tf_blast_radius", {})["field"] == "resource_changes"


def test_blast_radius_oversized_plan_is_rejected_at_the_boundary(registry):
    ok = [_rc("t", f"t.a{i}", "update") for i in range(500)]
    assert _call(registry, "devops_tf_blast_radius", {"resource_changes": ok})["updates"] == 500
    result = _call(registry, "devops_tf_blast_radius", {"resource_changes": ok + [_rc("t", "t.extra", "update")]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "resource_changes"


def test_blast_radius_note_stays_inside_the_change_gate_blast_radius_limit(registry):
    long_address = "module." + "m" * 280
    changes = [_rc(f"type_{i:03d}", f"{long_address}.r{i:03d}", "delete") for i in range(60)]
    result = _call(registry, "devops_tf_blast_radius", {"resource_changes": changes})
    assert result["deletes"] == 60 and len(result["note"]) <= 2000
    assert "+50 more" in result["note"]
    # The note is the blast_radius input of devops_change_gate: production with a ticket, plan and note needs a human.
    gate = _call(registry, "devops_change_gate", {"env_tier": "production", "change_ticket": "CHG-1", "blast_radius": result["note"], "rollback_plan": "revert state"})
    assert gate["decision"] == "needs-human-approval"


def test_blast_radius_destructive_list_is_sorted_and_truncated(registry):
    changes = [_rc("t", f"t.r{i:02d}", "delete") for i in range(12)][::-1]
    note = _call(registry, "devops_tf_blast_radius", {"resource_changes": changes})["note"]
    assert "t.r00 (delete), t.r01 (delete)" in note and note.endswith("+2 more")


def test_provenance_gate_accepts_the_record_build_record_writes_without_an_image_or_git(registry):
    """Reviewer repro (RV-E 2): devops_build_record.py writes `"image": null` (no --image) and `"dirty": null`
    (no git); the registry rejected them with INVALID_INPUT 'image: expected string' / 'dirty: expected boolean'."""
    record = _record(image=None, dirty=None)
    result = _call(registry, "devops_provenance_gate", {"record": record})
    assert result == {"state": "SUCCESS", "decision": "accept", "missing": [], "warnings": ["image-absent", "dirty-unknown"]}


def test_provenance_gate_still_rejects_wrongly_typed_image_and_dirty(registry):
    """With the schema types dropped the handler is the type check: a number or string there is INVALID_INPUT."""
    for record, text in ((_record(image=123), "record.image"), (_record(dirty="yes"), "record.dirty")):
        result = _call(registry, "devops_provenance_gate", {"record": record})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "record" and text in result["message"]


def test_provenance_gate_success_example_is_the_null_image_record(registry):
    tool = next(t for a in registry["agents"] if a["id"] == "A11" for t in a["registered_tools"] if t["name"] == "devops_provenance_gate")
    record = tool["examples"]["success"]["record"]
    assert record["image"] is None and record["dirty"] is None
    assert _call(registry, "devops_provenance_gate", tool["examples"]["success"])["decision"] == "accept"
