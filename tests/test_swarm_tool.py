"""scripts/swarm_tool.py: caller resolution, catalog filtering and the call bridge over the registry."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from swarm import tool_registry

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("swarm_tool_under_test", ROOT / "scripts" / "swarm_tool.py")
tool_script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = tool_script
_spec.loader.exec_module(tool_script)


def _run(capsys, monkeypatch, *argv):
    """Run the script's main like the bridge does (`--json`); return (exit code, parsed stdout or None)."""
    monkeypatch.chdir(ROOT)
    code = tool_script.AgentScript("shared", "swarm_tool", tool_script.run, add_args=tool_script.add_args).main([*argv, "--json"])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    # not the shared `swarm_dir` fixture: it evicts every swarm module, which would orphan the script module loaded above
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))


def test_catalog_lists_only_the_callers_tools_with_tier_and_timeout(capsys, monkeypatch):
    code, out = _run(capsys, monkeypatch, "catalog", "--caller", "A05", "--kind", "tools")
    assert code == 0 and out["caller"] == "A05" and out["caller_slug"] == "a05-backend"
    assert "skills" not in out
    names = {t["name"] for t in out["tools"]}
    assert "be_route_norm" in names and "req_gwt_check" not in names
    row = next(t for t in out["tools"] if t["name"] == "be_route_norm")
    assert row["tier"] == "READ" and row["timeout_s"] > 0 and row["purpose"]
    assert [t["name"] for t in out["tools"]] == sorted(names)


def test_caller_is_accepted_as_id_or_slug_and_unknown_is_e_input(capsys, monkeypatch):
    by_id = _run(capsys, monkeypatch, "catalog", "--caller", "A05", "--kind", "tools")[1]["tools"]
    by_slug = _run(capsys, monkeypatch, "catalog", "--caller", "a05-backend", "--kind", "tools")[1]["tools"]
    assert by_id == by_slug
    for bad in ("A99", "a05", "", "A05 "[:-1] + "x"):
        code, out = _run(capsys, monkeypatch, "catalog", "--caller", bad)
        assert code == 2 and out["error"]["code"] == "E-INPUT", bad
    code, out = _run(capsys, monkeypatch, "catalog")
    assert code == 2 and out["error"]["code"] == "E-INPUT" and "--caller" in out["error"]["message"]


def test_query_requires_every_word_in_name_or_purpose(capsys, monkeypatch):
    words = ("route", "normalize")
    everything = _run(capsys, monkeypatch, "catalog", "--caller", "A05", "--kind", "tools")[1]["tools"]

    def has(tool, word):
        return word in f"{tool['name']} {tool['purpose']}".lower()

    _code, out = _run(capsys, monkeypatch, "catalog", "--caller", "A05", "--kind", "tools", "--query", "ROUTE normalize")
    names = [t["name"] for t in out["tools"]]
    assert "be_route_norm" in names
    assert all(has(t, w) for t in out["tools"] for w in words)  # every returned tool carries every word
    # nothing is dropped: exactly the callable tools that carry every word, and a tool missing one word is excluded
    assert names == [t["name"] for t in everything if all(has(t, w) for w in words)]
    partial = [t["name"] for t in everything if has(t, "route") and not has(t, "normalize")]
    assert partial, "the registry must hold a route tool without 'normalize' for this check to bite"
    assert not set(partial) & set(names)
    _code, none = _run(capsys, monkeypatch, "catalog", "--caller", "A05", "--kind", "tools", "--query", "route zzzz")
    assert none["tools"] == []


def test_skills_come_from_the_skill_registry_own_plus_shared(capsys, monkeypatch):
    skill_registry = tool_script.skill_registry  # the module the script holds: other tests' fixtures evict fresh imports of swarm*

    rows = {
        "a05-backend": [{"name": "a05-backend--routes", "description": "Use when checking routes", "owner": "a05-backend", "tools": ["be_route_norm"]}],
        "shared": [{"name": "swarm-handover-intake", "description": "Use when taking a handover", "owner": "shared", "tools": []}],
    }
    monkeypatch.setattr(skill_registry, "skills_for", lambda owner, **_: rows.get(owner, []))
    _code, out = _run(capsys, monkeypatch, "catalog", "--caller", "A05", "--kind", "skills")
    assert "tools" not in out
    assert [s["name"] for s in out["skills"]] == ["a05-backend--routes", "swarm-handover-intake"]
    _code, filtered = _run(capsys, monkeypatch, "catalog", "--caller", "A05", "--kind", "skills", "--query", "handover")
    assert [s["name"] for s in filtered["skills"]] == ["swarm-handover-intake"]

    def broken(owner, **_):
        raise OSError("disk gone")

    monkeypatch.setattr(skill_registry, "skills_for", broken)
    _code, degraded = _run(capsys, monkeypatch, "catalog", "--caller", "A05", "--kind", "all")
    assert degraded["skills"] == [] and "disk gone" in degraded["skills_unavailable"] and degraded["tools"]


def test_call_returns_the_registry_object_unchanged(capsys, monkeypatch):
    code, out = _run(capsys, monkeypatch, "call", "--caller", "a05-backend", "--name", "be_route_norm", "--input", '{"path": "/users/:id/"}')
    assert code == 0 and out["status"] == "ok"
    assert out["result"] == tool_registry.call_tool("be_route_norm", "A05", {"path": "/users/:id/"})


def test_call_failures_are_data_with_exit_1(capsys, monkeypatch):
    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "req_gwt_check", "--input", '{"text": "x"}')
    assert code == 1 and out["status"] == "fail"
    assert out["result"]["state"] == "UNAUTHORIZED" and out["result"]["caller"] == "A05"
    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "be_route_norm", "--input", "{}")
    assert code == 1 and out["result"]["state"] == "INVALID_INPUT" and out["result"]["field"] == "path"
    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "nope_tool")
    assert code == 1 and out["result"]["state"] == "INVALID_INPUT"


def _serving(monkeypatch, payload):
    monkeypatch.setattr(tool_registry, "_import_handler", lambda _handler: lambda _args: dict(payload))
    monkeypatch.setenv("SWARM_TOOL_OUTPUT_MAX_BYTES", "4096")


def test_an_oversized_failure_stays_a_failure_and_an_oversized_success_is_withheld(capsys, monkeypatch):
    """P2-2: an oversized INVALID_INPUT still exits 1 (and is audited with its error_state); a SUCCESS is withheld as a
    PARTIAL_SUCCESS that exits 0."""
    _serving(monkeypatch, {"state": "INVALID_INPUT", "field": "path", "message": "m" * 10_000})
    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "be_route_norm", "--input", '{"path": "/a/"}')
    assert code == 1 and out["status"] == "fail"
    assert out["result"]["state"] == "INVALID_INPUT" and out["result"]["output_withheld"] is True
    assert out["result"]["field"] == "path"

    _serving(monkeypatch, {"state": "SUCCESS", "normalized": "x" * 10_000})
    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "be_route_norm", "--input", '{"path": "/a/"}')
    assert code == 0 and out["status"] == "ok"
    assert out["result"]["state"] == "PARTIAL_SUCCESS" and out["result"]["output_withheld"] is True


@pytest.mark.parametrize("argv, needle", [(["--input", "{oops"], "not valid JSON"), (["--input", "[1]"], "must be a JSON object"), ([], "requires --name")])
def test_call_input_errors_are_e_input(capsys, monkeypatch, argv, needle):
    base = ["call", "--caller", "A05"]
    if "requires --name" not in needle:
        base += ["--name", "be_route_norm"]
    code, out = _run(capsys, monkeypatch, *base, *argv)
    assert code == 2 and out["error"]["code"] == "E-INPUT" and needle in out["error"]["message"]


def test_oversized_input_is_refused_before_the_registry(capsys, monkeypatch):
    big = json.dumps({"path": "x" * (tool_script.MAX_INPUT_BYTES + 1)})
    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "be_route_norm", "--input", big)
    assert code == 2 and "exceeds" in out["error"]["message"]


def test_dry_run_never_calls_the_tool(capsys, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("dry-run reached the registry call path")

    monkeypatch.setattr(tool_registry, "call_tool", boom)
    code, out = _run(capsys, monkeypatch, "--dry-run")
    assert code == 0 and out["dry_run"] is True
    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "be_route_norm", "--input", '{"path": "/a"}', "--dry-run")
    assert code == 0 and out["dry_run"] is True and "result" not in out


def _registry_with_write_tool(tmp_path, monkeypatch):
    document = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    for agent in document["agents"]:
        for tool in agent["registered_tools"]:
            if tool["name"] == "be_route_norm":
                tool["side_effect_tier"] = "WRITE"
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(tool_registry, "AGENTS_FILE", path)


def test_plan_mode_refuses_non_read_tools_before_the_handler_and_catalog_shows_the_tier(capsys, monkeypatch, tmp_path):
    _registry_with_write_tool(tmp_path, monkeypatch)
    imported = []
    real = tool_registry._import_handler
    monkeypatch.setattr(tool_registry, "_import_handler", lambda handler: imported.append(handler) or real(handler))

    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "be_route_norm", "--input", '{"path": "/a/"}', "--plan-mode")
    assert code == 2 and out["error"]["code"] == "E-POLICY" and "plan mode" in out["error"]["message"]
    assert imported == []
    # READ tools still run in plan mode, and outside plan mode the WRITE tool runs
    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "be_route_collision",
                     "--input", '{"routes": [{"method": "GET", "path": "/a"}]}', "--plan-mode")
    assert code == 0 and out["result"]["state"] == "SUCCESS"
    code, out = _run(capsys, monkeypatch, "call", "--caller", "A05", "--name", "be_route_norm", "--input", '{"path": "/a/"}')
    assert code == 0 and out["result"]["normalized"] == "/a"
    _code, listing = _run(capsys, monkeypatch, "catalog", "--caller", "A05", "--kind", "tools", "--query", "route_norm")
    assert [t["tier"] for t in listing["tools"]] == ["WRITE"]


def test_call_runs_in_the_workspace_root_not_the_install(capsys, monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(ROOT)
    seen = []
    monkeypatch.setattr(tool_script.os, "chdir", lambda path: seen.append(Path(path)))
    code = tool_script.AgentScript("shared", "swarm_tool", tool_script.run, add_args=tool_script.add_args).main(
        ["call", "--caller", "A05", "--name", "be_route_norm", "--input", '{"path": "/a"}', "--root", str(work), "--json"])
    capsys.readouterr()
    assert code == 0 and seen == [work.resolve()]
    shutil.rmtree(work)
