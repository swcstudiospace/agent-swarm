"""Registry validation, the call gate, and generated docs/tests."""
import json
from pathlib import Path

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
