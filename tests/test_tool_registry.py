"""Registry validation, the call gate, and generated docs/tests."""
import json
from pathlib import Path

import pytest

from swarm.tool_generate import SECTIONS, render_docs
from swarm.tool_registry import call_tool, validate_registry

ROOT = Path(__file__).resolve().parent.parent


def test_live_registry_accepts_the_pilot():
    assert validate_registry() == []


def test_pilot_success_echoes_the_token():
    assert call_tool("orch_registry_ping", "A01", {"token": "ping"}) == {
        "state": "SUCCESS",
        "token": "ping",
        "owner": "A01",
    }


def test_invalid_input_names_the_field():
    result = call_tool("orch_registry_ping", "A01", {})
    assert result["state"] == "INVALID_INPUT"
    assert result["field"] == "token"


def test_nan_and_infinity_are_refused_for_every_number_field():
    """A NaN compares false against every bound, so handlers written for finite input failed OPEN on it (a NaN canary error
    rate promoted a release, a NaN measurement passed a budget): the registry now refuses non-finite numbers once."""
    data = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    tool = next(t for a in data["agents"] for t in a["registered_tools"] if t["name"] == "rel_canary_verdict")
    valid = dict(tool["examples"]["success"])
    numeric = [k for k, spec in tool["input_schema"]["properties"].items() if spec.get("type") == "number"]
    assert numeric, "rel_canary_verdict should declare number fields"
    for field in numeric:
        for bad in (float("nan"), float("inf"), float("-inf")):
            result = call_tool("rel_canary_verdict", "A12", {**valid, field: bad}, data=data)
            assert result["state"] == "INVALID_INPUT" and result["field"] == field, (field, bad, result)
            assert "finite" in result["message"], result
    assert call_tool("rel_canary_verdict", "A12", valid, data=data)["state"] == "SUCCESS"



def test_a_result_must_be_strict_json_and_within_the_output_limit(monkeypatch):
    """SEC-02: NaN/Infinity in a result are refused (DEPENDENCY_UNAVAILABLE), an oversized result is WITHHELD as a
    PARTIAL_SUCCESS (never truncated), SWARM_TOOL_OUTPUT_MAX_BYTES is clamped to [4 KiB, 16 MiB]."""
    import swarm.tool_registry as registry

    def returning(payload):
        monkeypatch.setattr("swarm.tools.orch_registry_ping.call", lambda _args, _p=payload: dict(_p))

    returning({"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5, "extra": float("nan")})  # SUCCESS is schema-checked first
    bad = call_tool("orch_registry_ping", "A01", {"token": "t"})
    assert bad["state"] == "DEPENDENCY_UNAVAILABLE" and "strict JSON" in bad["message"], bad

    monkeypatch.setenv("SWARM_TOOL_OUTPUT_MAX_BYTES", "1")  # clamps UP to the 4 KiB floor
    assert registry.output_limit() == 4096
    monkeypatch.setenv("SWARM_TOOL_OUTPUT_MAX_BYTES", str(10**12))
    assert registry.output_limit() == 16 * 1024 * 1024
    monkeypatch.setenv("SWARM_TOOL_OUTPUT_MAX_BYTES", "junk")
    assert registry.output_limit() == registry.OUTPUT_LIMIT_DEFAULT

    monkeypatch.setenv("SWARM_TOOL_OUTPUT_MAX_BYTES", "4096")
    returning({"state": "SUCCESS", "token": "y" * 10_000, "owner": "A01"})
    big = call_tool("orch_registry_ping", "A01", {"token": "t"})
    assert big["state"] == "PARTIAL_SUCCESS" and big["completed_fraction"] == 0.0 and big["output_withheld"] is True, big
    assert big["limit_bytes"] == 4096 and big["output_bytes"] > 10_000 and "token" not in big

    returning({"state": "SUCCESS", "token": "y" * 3000, "owner": "A01"})
    assert call_tool("orch_registry_ping", "A01", {"token": "t"})["state"] == "SUCCESS"  # under the limit: untouched


@pytest.mark.parametrize("payload", [
    {"state": "INVALID_INPUT", "field": "token", "message": "m" * 10_000},
    {"state": "UNAUTHORIZED", "caller": "A02", "target": "orch_registry_ping", "message": "m" * 10_000},
    {"state": "TIMEOUT", "message": "m" * 10_000},
    {"state": "DEPENDENCY_UNAVAILABLE", "message": "m" * 10_000},
])
def test_an_oversized_error_result_keeps_its_state(monkeypatch, payload):
    """P2-2: only the bulky body of a failed call is withheld; it stays a failure (never a PARTIAL_SUCCESS)."""
    monkeypatch.setenv("SWARM_TOOL_OUTPUT_MAX_BYTES", "4096")
    monkeypatch.setattr("swarm.tools.orch_registry_ping.call", lambda _args: dict(payload))
    out = call_tool("orch_registry_ping", "A01", {"token": "t"})
    assert out["state"] == payload["state"] and out["output_withheld"] is True, out
    assert out["limit_bytes"] == 4096 and out["output_bytes"] > 10_000
    assert out["message"] == f"{payload['state']} result of {out['output_bytes']} bytes exceeds the 4096-byte output limit"
    for key in ("field", "caller", "target"):
        assert out.get(key) == payload.get(key)
    assert "completed_fraction" not in out


def test_oversized_routing_fields_are_dropped_but_short_ones_kept(monkeypatch):
    monkeypatch.setenv("SWARM_TOOL_OUTPUT_MAX_BYTES", "4096")
    big = {"state": "INVALID_INPUT", "field": "f" * 300, "caller": "A01", "target": 7, "message": "m" * 10_000}
    monkeypatch.setattr("swarm.tools.orch_registry_ping.call", lambda _args: dict(big))
    out = call_tool("orch_registry_ping", "A01", {"token": "t"})
    assert out["state"] == "INVALID_INPUT" and out["caller"] == "A01" and "field" not in out and "target" not in out


def test_unauthorized_echoes_the_caller_and_does_not_run():
    result = call_tool("orch_registry_ping", "A02", {"token": "ping"})
    assert result["state"] == "UNAUTHORIZED"
    assert result["caller"] == "A02"
    assert result["target"] == "orch_registry_ping"


def test_unregistered_name_does_not_resolve_a_handler():
    result = call_tool("not_a_registered_tool", "A01", {"token": "ping"})
    assert result["state"] == "INVALID_INPUT"
    assert result["field"] == "name"
    assert "not registered" in result["message"]


def test_registry_pages_have_the_six_sections_and_no_placeholders():
    data = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    pages = render_docs(data)
    agent_pages = [text for rel, text in pages.items() if rel.startswith("docs/tools/agents/")]
    assert len(agent_pages) == 15
    for text in agent_pages:
        for heading in SECTIONS:
            assert heading in text
        lowered = text.lower()
        assert "todo" not in lowered
        assert "tbd" not in lowered
        assert "placeholder" not in lowered
    catalog = pages["docs/tools/catalog.md"]
    lane = sum(len(agent.get("registered_tools") or []) for agent in data["agents"])
    shared = len(data.get("shared_tools") or [])
    assert "`orch_registry_ping`" in catalog
    assert f"Registered tools: {lane + shared}" in catalog
    assert f"Shared tools: {shared}" in catalog


def test_colliding_and_stub_contracts_are_rejected(tmp_path):
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    pilot = document["agents"][0]["registered_tools"][0]
    stub = json.loads(json.dumps(pilot))
    stub["name"] = "orch_stubbed"
    stub["stub"] = True
    other = json.loads(json.dumps(pilot))
    other["owner"] = "A02"
    document["agents"][0]["registered_tools"].append(stub)
    document["agents"][1]["registered_tools"] = [other]
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    errors = validate_registry(path)
    assert any("stub contracts cannot be registered" in error for error in errors)
    assert any("name must match req_" in error for error in errors)


def test_duplicate_names_are_rejected(tmp_path):
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    duplicate = json.loads(json.dumps(document["agents"][0]["registered_tools"][0]))
    document["agents"][0]["registered_tools"].append(duplicate)
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    errors = validate_registry(path)
    assert any("collides" in error for error in errors)


def test_null_contract_fields_are_reported_and_do_not_raise(tmp_path):
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    tool = document["agents"][0]["registered_tools"][0]
    tool["errors"] = None
    tool["entrypoint"] = None
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    errors = validate_registry(path)
    assert any("errors must list" in error for error in errors)
    assert any("entrypoint must be a repo-relative file path" in error for error in errors)


def test_unsupported_schema_keywords_are_rejected(tmp_path):
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    tool = document["agents"][0]["registered_tools"][0]
    tool["input_schema"]["properties"]["token"]["pattern"] = "^ping$"
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    errors = validate_registry(path)
    assert any("pattern" in error and "not valid for string" in error for error in errors)


def test_schema_keywords_must_apply_to_their_type(tmp_path):
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    tool = document["agents"][0]["registered_tools"][0]
    tool["input_schema"]["properties"]["token"]["minimum"] = 1
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    errors = validate_registry(path)
    assert any("minimum" in error and "not valid for string" in error for error in errors)


def test_schema_type_list_is_reported_without_raising(tmp_path):
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    tool = document["agents"][0]["registered_tools"][0]
    tool["input_schema"]["properties"]["token"] = {"type": ["string", "null"]}
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    errors = validate_registry(path)
    assert any("type must be one of" in error and "token" in error for error in errors)


def test_declared_bounds_items_and_nested_required_are_enforced():
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    tool = document["agents"][0]["registered_tools"][0]
    tool["input_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["count", "tags", "doc"],
        "properties": {
            "count": {"type": "integer", "minimum": 1},
            "tags": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "doc": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        },
    }

    def call(payload):
        return call_tool("orch_registry_ping", "A01", payload, data=document)

    low = call({"count": 0, "tags": ["a"], "doc": {"id": "x"}})
    assert low["state"] == "INVALID_INPUT" and low["field"] == "count"
    blank = call({"count": 1, "tags": [""], "doc": {"id": "x"}})
    assert blank["state"] == "INVALID_INPUT" and blank["field"] == "tags"
    nested = call({"count": 1, "tags": ["a"], "doc": {}})
    assert nested["state"] == "INVALID_INPUT" and nested["field"] == "doc"


def test_sleep_probe_does_not_run_the_tool():
    from swarm.tools.support import respond

    ran = []
    result = respond({"probe": "sleep"}, lambda: ran.append("work") or {"state": "SUCCESS"})
    assert result["state"] == "TIMEOUT"
    assert ran == []


def test_timed_out_call_does_not_overlap_or_continue_into_the_tool(monkeypatch):
    import threading
    import time

    active = {"n": 0, "max": 0}
    lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()
    worked = []

    def call(payload):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        entered.set()
        try:
            if payload.get("probe") == "sleep":
                release.wait(1)
                return {"state": "TIMEOUT", "message": "probe sleep"}
            worked.append(payload["token"])
            return {"state": "SUCCESS", "token": payload["token"], "owner": "A01"}
        finally:
            with lock:
                active["n"] -= 1

    monkeypatch.setattr("swarm.tools.orch_registry_ping.call", call)
    timed_out = call_tool("orch_registry_ping", "A01", {"token": "ping", "probe": "sleep"}, timeout_s=0.05)
    assert entered.wait(1)
    overlap = call_tool("orch_registry_ping", "A01", {"token": "ping"}, timeout_s=0.05)
    assert timed_out["state"] == "TIMEOUT"
    assert overlap["state"] == "TIMEOUT"
    assert overlap["message"] == "previous call is still running"
    assert worked == []
    assert active["max"] == 1
    release.set()
    finished = {"state": "TIMEOUT"}
    for _ in range(50):
        finished = call_tool("orch_registry_ping", "A01", {"token": "ping"}, timeout_s=1)
        if finished["state"] == "SUCCESS":
            break
        time.sleep(0.01)
    assert finished["state"] == "SUCCESS"
    assert worked == ["ping"]
    assert active["max"] == 1


def test_incomplete_contract_names_the_missing_field(tmp_path):
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    tool = document["agents"][0]["registered_tools"][0]
    del tool["timeout_s"]
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    errors = validate_registry(path)
    assert any("missing timeout_s" in error for error in errors)


def test_orch_run_reports_dependency_instead_of_claiming_dispatch():
    result = call_tool("orch_run", "A01", {"correlation_id": "Tdemo"})
    assert result["state"] == "DEPENDENCY_UNAVAILABLE"
    assert "scripts/swarm_run.py" in result["message"]
    assert "dispatched" not in result


def test_orch_reconcile_success_fields_match_output_schema(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SWARM_DIR", raising=False)
    result = call_tool("orch_reconcile", "A01", {"correlation_id": "Tdemo"})
    assert result["state"] == "SUCCESS"
    assert set(result) <= {"state", "correlation_id", "reassigned", "aggregated", "summary"}
    assert result["reassigned"] == 0
    assert result["aggregated"] == []


# ---- v2.2: side-effect tiers, capability policy, shared baseline, caller context ----


def _errors_for(tmp_path, mutate):
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    mutate(document)
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return validate_registry(path)


def _first_tool(document):
    return document["agents"][0]["registered_tools"][0]


def test_tier_absent_read_and_write_are_accepted(tmp_path):
    from swarm.tool_registry import tool_tier

    assert tool_tier({}) == "READ"
    assert _errors_for(tmp_path, lambda d: _first_tool(d).update(side_effect_tier="READ")) == []
    assert _errors_for(tmp_path, lambda d: _first_tool(d).update(side_effect_tier="WRITE")) == []


def test_destructive_and_external_tiers_are_rejected_with_a_way_out(tmp_path):
    for tier in ("DESTRUCTIVE", "EXTERNAL"):
        errors = _errors_for(tmp_path, lambda d, tier=tier: _first_tool(d).update(side_effect_tier=tier))
        assert any(tier in e and "not allowed in v2.2" in e and "swarm state dir" in e for e in errors), errors


def test_unknown_tier_names_the_allowed_values(tmp_path):
    errors = _errors_for(tmp_path, lambda d: _first_tool(d).update(side_effect_tier="read"))
    assert any("side_effect_tier must be one of READ, WRITE, DESTRUCTIVE, EXTERNAL" in e for e in errors)


def test_catalog_and_agent_pages_show_the_tier():
    data = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    _first_tool(data)["side_effect_tier"] = "WRITE"
    pages = render_docs(data)
    name = _first_tool(data)["name"]
    assert "| Tier |" in pages["docs/tools/catalog.md"]
    assert any(line.startswith(f"| `{name}` |") and "| WRITE |" in line for line in pages["docs/tools/catalog.md"].splitlines())
    first_page = next(text for rel, text in pages.items() if rel.startswith("docs/tools/agents/"))
    assert "| Name | Purpose | Tier |" in first_page


def test_capability_policy_must_be_present_and_well_formed(tmp_path):
    assert any("capability_policy must be an object" in e for e in _errors_for(tmp_path, lambda d: d.pop("capability_policy")))
    bad = _errors_for(tmp_path, lambda d: d["capability_policy"].update(tools={"min": 9, "max": 3}))
    assert any("capability_policy.tools needs integer min and max" in e for e in bad)
    flag = _errors_for(tmp_path, lambda d: d["capability_policy"]["tools"].update(enforce="yes"))
    assert any("enforce must be true or false" in e for e in flag)
    combined = _errors_for(tmp_path, lambda d: d["capability_policy"].update(combined={"min": 5}))
    assert any("capability_policy.combined" in e for e in combined)


def test_tool_count_max_is_always_enforced_and_min_only_when_enforced(tmp_path):
    def too_many(document):
        agent = document["agents"][1]
        template = agent["registered_tools"][0]
        for n in range(20):
            clone = json.loads(json.dumps(template))
            clone["name"] = f"{template['name']}_{n}"
            agent["registered_tools"].append(clone)

    def over(document):
        too_many(document)
        document["capability_policy"]["tools"] = {"min": 10, "max": 15, "enforce": False}

    errors = _errors_for(tmp_path, over)  # the ceiling holds even while the floor is not enforced
    assert any("exceed capability_policy.tools.max 15" in e for e in errors)

    def policy(document, **tools):
        document["capability_policy"]["tools"] = {"min": 10, "max": 15, **tools}

    def thin(document, **tools):  # an agent below the minimum, whatever the live tree holds
        document["agents"][1]["registered_tools"] = document["agents"][1]["registered_tools"][:3]
        policy(document, **tools)

    def below(errors):
        return any("below capability_policy.tools.min 10" in e for e in errors)

    assert not below(_errors_for(tmp_path, lambda d: thin(d, enforce=False)))  # tolerated while enforce is false
    assert below(_errors_for(tmp_path, lambda d: thin(d, enforce=True)))  # refused once it flips
    assert below(_errors_for(tmp_path, lambda d: thin(d)))  # enforce defaults to true


def test_shared_baseline_tools_must_be_shared_and_callable_by_every_agent(tmp_path):
    assert any("shared_baseline must be an object" in e for e in _errors_for(tmp_path, lambda d: d.pop("shared_baseline")))
    missing = _errors_for(tmp_path, lambda d: d["shared_baseline"].update(tools=["nope_tool"]))
    assert any("'nope_tool' is not a shared tool" in e for e in missing)
    lane = _errors_for(tmp_path, lambda d: d["shared_baseline"].update(tools=[_first_tool(d)["name"]]))
    assert any("is not a shared tool" in e for e in lane)

    def narrow(document):
        document["shared_baseline"]["tools"] = [document["shared_tools"][0]["name"]]
        document["shared_tools"][0]["permitted_callers"] = ["A01", "A02"]

    assert any("must permit every agent; missing A03" in e for e in _errors_for(tmp_path, narrow))

    def broad(document):
        document["shared_baseline"]["tools"] = [document["shared_tools"][0]["name"]]
        document["shared_tools"][0]["permitted_callers"] = [a["id"] for a in document["agents"]]
        document["shared_tools"][0]["examples"]["unauthorized_caller"] = "ghost"

    # every agent is permitted, so the refused-caller example is a name that is no agent at all
    assert _errors_for(tmp_path, broad) == []

    def ghost_on_a_narrow_tool(document):
        document["shared_tools"][0]["examples"]["unauthorized_caller"] = "ghost"

    assert any("unauthorized_caller must be an agent id" in e for e in _errors_for(tmp_path, ghost_on_a_narrow_tool))


def current_caller():
    """The accessor of the support module `call_tool` itself uses.

    conftest's swarm_dir fixture purges every swarm* module between tests, so a fresh
    `from swarm.tools.support import current_caller` would read a different ContextVar than the registry sets.
    """
    return call_tool.__globals__["invoke_bounded"].__globals__["current_caller"]()


def _caller_recorder(seen):
    def handler(payload):
        seen.append(current_caller())
        return {"state": "SUCCESS", "token": payload["token"], "owner": "A01"}

    return handler


def test_handler_sees_the_authorized_caller_and_it_does_not_leak(monkeypatch):
    seen = []
    monkeypatch.setattr("swarm.tools.orch_registry_ping.call", _caller_recorder(seen))
    data = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    _first_tool(data)["permitted_callers"] = ["A01", "A02"]
    assert current_caller() is None
    assert call_tool("orch_registry_ping", "A01", {"token": "t"}, data=data)["state"] == "SUCCESS"
    assert current_caller() is None
    assert call_tool("orch_registry_ping", "A02", {"token": "t"}, data=data)["state"] == "SUCCESS"
    assert seen == ["A01", "A02"]
    # a refused caller never reaches the handler and leaves nothing behind
    assert call_tool("orch_registry_ping", "A03", {"token": "t"}, data=data)["state"] == "UNAUTHORIZED"
    assert seen == ["A01", "A02"] and current_caller() is None


def test_caller_is_restored_after_a_timeout_and_a_handler_error(monkeypatch):
    import threading

    release = threading.Event()
    seen = []

    def slow(payload):
        seen.append(current_caller())
        release.wait(1)
        return {"state": "TIMEOUT", "message": "late"}

    monkeypatch.setattr("swarm.tools.orch_registry_ping.call", slow)
    assert call_tool("orch_registry_ping", "A01", {"token": "t"}, timeout_s=0.05)["state"] == "TIMEOUT"
    assert current_caller() is None  # the calling thread is restored even though the worker still runs
    release.set()

    def boom(payload):
        seen.append(current_caller())
        raise OSError("down")

    monkeypatch.setattr("swarm.tools.orch_registry_ping.call", boom)
    for _ in range(100):
        result = call_tool("orch_registry_ping", "A01", {"token": "t"}, timeout_s=1)
        if result["state"] == "DEPENDENCY_UNAVAILABLE":
            break
        threading.Event().wait(0.01)
    assert result["state"] == "DEPENDENCY_UNAVAILABLE"
    assert current_caller() is None
    assert seen[0] == "A01" and seen[-1] == "A01"
