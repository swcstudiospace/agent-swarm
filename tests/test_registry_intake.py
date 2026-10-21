"""Registry intake (D8): fragments and grants merge into agents.json deterministically, or not at all."""
import copy
import difflib
import importlib.util
import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from swarm.tool_registry import validate_registry

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("registry_intake_under_test", ROOT / "scripts" / "_registry_intake.py")
intake = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = intake  # dataclasses resolves annotations through sys.modules
_spec.loader.exec_module(intake)


@pytest.fixture()
def reg(tmp_path):
    """A private copy of agents.json plus an empty intake dir; root stays the real repo so handlers resolve."""
    agents = tmp_path / "agents.json"
    shutil.copyfile(ROOT / "agents.json", agents)
    folder = tmp_path / "intake"
    folder.mkdir()
    return agents, folder


def _doc(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tool(document, name):
    for agent in document["agents"]:
        for tool in agent["registered_tools"]:
            if tool["name"] == name:
                return tool
    for tool in document["shared_tools"]:
        if tool["name"] == name:
            return tool
    raise KeyError(name)


def _clone(document, source, name, owner, callers=None):
    tool = copy.deepcopy(_tool(document, source))
    tool["name"] = name
    tool["owner"] = owner
    tool["permitted_callers"] = callers or [owner]
    tool["purpose"] = f"Fixture clone of {source} for the intake tests."
    ids = [a["id"] for a in document["agents"]]
    tool["examples"]["unauthorized_caller"] = next((i for i in reversed(ids) if i not in tool["permitted_callers"]), "ghost")
    return tool


def _write(folder, name, value):
    (folder / name).write_text(json.dumps(value), encoding="utf-8")


def _apply(agents, folder):
    return intake.apply_merge(agents, folder, ROOT)


def test_no_fragments_changes_nothing(reg):
    agents, folder = reg
    before = agents.read_bytes()
    plan = _apply(agents, folder)
    assert plan.errors == [] and plan.files == []
    assert agents.read_bytes() == before
    assert intake.main(["--check", "--agents-file", str(agents), "--intake-dir", str(folder)]) == 0


def test_apply_appends_with_only_additions_and_deletes_the_fragment(reg):
    agents, folder = reg
    before = agents.read_text(encoding="utf-8")
    base = _doc(agents)
    mine = _clone(base, "orch_registry_ping", "orch_intake_probe", "A01")
    mine = dict(reversed(list(mine.items())))  # key order of the fragment is not the file's order
    shared = _clone(base, "shared_verdict", "shared_intake_probe", "shared", callers=["A01", "A02"])
    _write(folder, "a01-orchestrator.tools.json", [mine])
    _write(folder, "shared.tools.json", [shared])

    assert intake.plan_merge(agents, folder, ROOT).errors == []
    assert agents.read_text(encoding="utf-8") == before  # --check never writes
    plan = _apply(agents, folder)
    assert plan.errors == []

    after = _doc(agents)
    assert [t["name"] for t in after["agents"][0]["registered_tools"]][-1] == "orch_intake_probe"
    assert [t["name"] for t in after["shared_tools"]][-1] == "shared_intake_probe"
    assert list(_tool(after, "orch_intake_probe"))[:4] == ["name", "owner", "purpose", "input_schema"]
    assert validate_registry(agents) == []
    assert not list(folder.glob("*.json"))
    # every untouched byte survives: at most the two closing braces that gained a comma differ
    old_new = list(difflib.unified_diff(before.splitlines(), agents.read_text(encoding="utf-8").splitlines(), lineterm="", n=0))
    removed = [line for line in old_new if line.startswith("-") and not line.startswith("---")]
    assert len(removed) <= 2 and all(line.strip() in ("-}", "-},") for line in removed), removed


def test_dotted_shard_names_merge_into_the_same_owner(reg):
    agents, folder = reg
    base = _doc(agents)
    first = _clone(base, "shared_verdict", "shared_intake_probe_one", "shared", callers=["A01", "A02"])
    second = _clone(base, "shared_verdict", "shared_intake_probe_two", "shared", callers=["A01", "A02"])
    _write(folder, "shared.handover.tools.json", [first])
    _write(folder, "shared.runtime.tools.json", [second])
    _write(folder, "ghost.shard.tools.json", [])

    errors = intake.plan_merge(agents, folder, ROOT).errors
    assert any("ghost" in e and "owner" in e for e in errors), errors  # the owner is the part before the first dot
    (folder / "ghost.shard.tools.json").unlink()

    plan = _apply(agents, folder)
    assert plan.errors == []
    names = [t["name"] for t in _doc(agents)["shared_tools"]]
    assert names[-2:] == ["shared_intake_probe_one", "shared_intake_probe_two"]


def test_apply_is_deterministic(reg, tmp_path):
    agents, folder = reg
    fragment = [_clone(_doc(agents), "orch_registry_ping", "orch_intake_probe", "A01")]
    other = tmp_path / "second"
    other.mkdir()
    agents2 = other / "agents.json"
    shutil.copyfile(agents, agents2)
    folder2 = other / "intake"
    folder2.mkdir()
    for target in (folder, folder2):
        _write(target, "A01.tools.json", fragment)
    assert _apply(agents, folder).errors == [] and _apply(agents2, folder2).errors == []
    assert agents.read_bytes() == agents2.read_bytes()


def test_an_empty_tool_list_is_filled_in_place(tmp_path):
    document = _doc(ROOT / "agents.json")
    clone = _clone(document, "docs_heading_order", "docs_intake_probe", "A15")
    document["agents"][-1]["registered_tools"] = []
    document["capability_policy"]["tools"]["enforce"] = False  # this test is about the in-place fill, not the count floor
    agents = tmp_path / "agents.json"
    agents.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    folder = tmp_path / "intake"
    folder.mkdir()
    _write(folder, "a15-docs.tools.json", [clone])
    assert _apply(agents, folder).errors == []
    assert [t["name"] for t in _doc(agents)["agents"][-1]["registered_tools"]] == ["docs_intake_probe"]


def test_any_error_refuses_everything(reg):
    agents, folder = reg
    before = agents.read_bytes()
    base = _doc(agents)
    good = _clone(base, "orch_registry_ping", "orch_intake_probe", "A01")
    wrong_prefix = _clone(base, "orch_registry_ping", "req_not_mine", "A01")
    _write(folder, "a01-orchestrator.tools.json", [good, wrong_prefix])
    plan = _apply(agents, folder)
    assert any("name must match orch_" in e for e in plan.errors)
    assert agents.read_bytes() == before
    assert (folder / "a01-orchestrator.tools.json").exists()
    assert intake.main(["--apply", "--agents-file", str(agents), "--intake-dir", str(folder)]) == 1


@pytest.mark.parametrize(
    "filename, payload, needle",
    [
        ("a99-ghost.tools.json", [], "owner 'a99-ghost' must be"),
        ("a01-orchestrator.tools.json", {"name": "x"}, "must be a JSON array"),
        ("a01-orchestrator.tools.json", ["nope"], "tool entry must be an object"),
    ],
)
def test_malformed_fragments_are_named(reg, filename, payload, needle):
    agents, folder = reg
    _write(folder, filename, payload)
    errors = intake.plan_merge(agents, folder, ROOT).errors
    assert any(needle in e for e in errors), errors


def test_unreadable_fragment_and_owner_mismatch_and_collisions(reg):
    agents, folder = reg
    base = _doc(agents)
    (folder / "a01-orchestrator.tools.json").write_text("[not json", encoding="utf-8")
    assert any("cannot read fragment" in e for e in intake.plan_merge(agents, folder, ROOT).errors)

    wrong_owner = _clone(base, "orch_registry_ping", "orch_intake_probe", "A01")
    wrong_owner["owner"] = "A02"
    _write(folder, "a01-orchestrator.tools.json", [wrong_owner])
    assert any("owner must be 'A01'" in e for e in intake.plan_merge(agents, folder, ROOT).errors)

    clash = _clone(base, "orch_registry_ping", "orch_registry_ping", "A01")
    _write(folder, "a01-orchestrator.tools.json", [clash])
    assert any("already exists" in e for e in intake.plan_merge(agents, folder, ROOT).errors)

    twin = _clone(base, "orch_registry_ping", "orch_twin", "A01")
    _write(folder, "a01-orchestrator.tools.json", [twin])
    _write(folder, "A01.tools.json", [twin])
    assert any("already exists in the registry or another fragment" in e for e in intake.plan_merge(agents, folder, ROOT).errors)


def test_fragment_that_breaks_the_registry_contract_is_refused(reg):
    agents, folder = reg
    broken = _clone(_doc(agents), "orch_registry_ping", "orch_intake_probe", "A01")
    del broken["timeout_s"]
    _write(folder, "a01-orchestrator.tools.json", [broken])
    assert any("missing timeout_s" in e for e in intake.plan_merge(agents, folder, ROOT).errors)
    destructive = _clone(_doc(agents), "orch_registry_ping", "orch_intake_probe", "A01")
    destructive["side_effect_tier"] = "DESTRUCTIVE"
    _write(folder, "a01-orchestrator.tools.json", [destructive])
    assert any("DESTRUCTIVE is not allowed in v2.2" in e for e in intake.plan_merge(agents, folder, ROOT).errors)


def test_an_existing_registry_error_blocks_the_merge(reg):
    agents, folder = reg
    document = _doc(agents)
    document["capability_policy"]["tools"] = {"min": 1, "max": 3}
    agents.write_text(json.dumps(document), encoding="utf-8")
    _write(folder, "a01-orchestrator.tools.json", [_clone(document, "orch_registry_ping", "orch_intake_probe", "A01")])
    before = agents.read_bytes()
    assert any("exceed capability_policy.tools.max 3" in e for e in _apply(agents, folder).errors)
    assert agents.read_bytes() == before


def _pick_grants(text, document):
    """One tool per caller-list shape (inline, multi-line) with agents it is not yet granted to, from the loaded text."""
    ids = [a["id"] for a in document["agents"]]
    picked = {}
    for agent in document["agents"]:
        for tool in agent["registered_tools"]:
            begin, end = intake._span(text, [*intake._tool_path(text, tool["name"]), "permitted_callers"])
            shape = "multi" if "\n" in text[begin:end] else "inline"
            probe = tool["examples"]["unauthorized_caller"]
            # the current refused-caller example comes first so granting it forces the probe to move; keep one agent refused
            free = sorted((i for i in ids if i not in tool["permitted_callers"]), key=lambda i: i != probe)
            if shape not in picked and len(free) >= 3:
                picked[shape] = (tool["name"], free[:2])
    assert set(picked) == {"inline", "multi"}, "the registry must carry both caller-list shapes"
    return picked


def test_grants_extend_inline_and_multiline_caller_lists(reg):
    agents, folder = reg
    before = agents.read_text(encoding="utf-8")
    base = _doc(agents)
    picked = _pick_grants(before, base)
    inline, inline_new = picked["inline"][0], picked["inline"][1][:1]
    multi, multi_new = picked["multi"]
    inline_before = _tool(base, inline)["permitted_callers"]
    multi_before = _tool(base, multi)["permitted_callers"]
    grant_inline = json.dumps([*inline_before, *inline_new])
    assert f'"permitted_callers": {grant_inline}' not in before
    _write(folder, "grants.json", {inline: inline_new, multi: multi_new})
    plan = _apply(agents, folder)
    assert plan.errors == []
    after = _doc(agents)
    text = agents.read_text(encoding="utf-8")
    assert _tool(after, inline)["permitted_callers"] == [*inline_before, *inline_new]
    assert _tool(after, multi)["permitted_callers"] == [*multi_before, *multi_new]
    assert f'"permitted_callers": {grant_inline},' in text  # the inline list stayed on one line
    begin, end = intake._span(text, [*intake._tool_path(text, multi), "permitted_callers"])
    assert "\n" in text[begin:end]  # the multi-line list stayed multi-line
    assert text[begin:end].count('"A') == len(multi_before) + len(multi_new)
    # a refused-caller example that was just granted moves to an agent that is still refused
    for name in (inline, multi):
        assert _tool(after, name)["examples"]["unauthorized_caller"] not in _tool(after, name)["permitted_callers"]
    assert inline_new[0] == _tool(base, inline)["examples"]["unauthorized_caller"]
    assert multi_new[0] == _tool(base, multi)["examples"]["unauthorized_caller"]
    assert _tool(after, multi)["examples"]["unauthorized_caller"] != multi_new[0]
    assert validate_registry(agents) == []
    assert not (folder / "grants.json").exists()
    diff = difflib.unified_diff(before.splitlines(), text.splitlines(), lineterm="", n=0)
    changed = [d for d in diff if d[:1] in "+-" and d[:3] not in ("+++", "---")]
    assert len(changed) <= 12, changed  # a handful of caller lines, nothing else rewritten


def test_grants_may_target_a_tool_added_in_the_same_run(reg):
    agents, folder = reg
    mine = _clone(_doc(agents), "orch_registry_ping", "orch_intake_probe", "A01")
    _write(folder, "a01-orchestrator.tools.json", [mine])
    _write(folder, "grants.json", {"orch_intake_probe": ["A03"]})
    assert _apply(agents, folder).errors == []
    assert _tool(_doc(agents), "orch_intake_probe")["permitted_callers"] == ["A01", "A03"]
    assert validate_registry(agents) == []


@pytest.mark.parametrize(
    "grants, needle",
    [
        ({"no_such_tool": ["A02"]}, "is not registered"),
        ({"orch_plan": ["A99"]}, "unknown agent id(s) A99"),
        ({"orch_plan": ["A01"]}, "twice in permitted_callers"),
        ({"orch_plan": ["A02", "A02"]}, "twice in permitted_callers"),
        ({"orch_plan": []}, "non-empty list of agent ids"),
        ({"orch_plan": "A02"}, "non-empty list of agent ids"),
        (["orch_plan"], "must be a JSON object"),
    ],
)
def test_bad_grants_refuse_and_change_nothing(reg, grants, needle):
    agents, folder = reg
    before = agents.read_bytes()
    _write(folder, "grants.json", grants)
    errors = _apply(agents, folder).errors
    assert any(needle in e for e in errors), errors
    assert agents.read_bytes() == before
    assert (folder / "grants.json").exists()


def test_a_grant_never_removes_the_owner(reg):
    agents, folder = reg
    document = _doc(agents)
    _tool(document, "orch_plan")["permitted_callers"] = ["A02"]
    agents.write_text(json.dumps(document), encoding="utf-8")
    _write(folder, "grants.json", {"orch_plan": ["A03"]})
    assert any("owner A01 must stay a permitted caller" in e for e in intake.plan_merge(agents, folder, ROOT).errors)


def test_granting_every_agent_moves_the_refused_example_to_a_non_agent(reg):
    agents, folder = reg
    document = _doc(agents)
    others = [a["id"] for a in document["agents"] if a["id"] != "A01"]
    _write(folder, "grants.json", {"orch_plan": others})
    assert _apply(agents, folder).errors == []
    tool = _tool(_doc(agents), "orch_plan")
    assert sorted(tool["permitted_callers"]) == sorted(a["id"] for a in document["agents"])
    assert tool["examples"]["unauthorized_caller"] == intake.NO_AGENT_PROBE
    assert validate_registry(agents) == []


# ---- W1 review R3: locking, idempotence, malformed input, line endings -----------------------------------------


def _names(path):
    document = _doc(path)
    return [t["name"] for _c, t in intake.tool_registry.iter_entries(document)]


def test_concurrent_apply_cannot_lose_a_late_fragment(reg, monkeypatch):
    """R3 P1: B applied a late fragment while A sat between plan and write; A then wrote its stale text over B's."""
    agents, folder = reg
    base = _doc(agents)
    _write(folder, "a01-orchestrator.tools.json", [_clone(base, "orch_registry_ping", "orch_first_probe", "A01")])
    late = _clone(base, "orch_registry_ping", "req_late_probe", "A02")
    results: dict = {}
    real_write = intake._atomic_write
    started = []

    def hooked(path, text):
        if not started:
            started.append(True)
            _write(folder, "a02-requirements.tools.json", [late])  # an author finishes a fragment mid-apply

            def run_b():
                results["b"] = _apply(agents, folder)

            thread = threading.Thread(target=run_b)
            thread.start()
            results["thread"] = thread
            time.sleep(0.6)  # without the lock B plans, writes and unlinks inside this window
        real_write(path, text)

    monkeypatch.setattr(intake, "_atomic_write", hooked)
    plan_a = _apply(agents, folder)
    results["thread"].join(timeout=30)
    assert not results["thread"].is_alive()
    assert plan_a.errors == [] and results["b"].errors == []
    names = _names(agents)
    assert "orch_first_probe" in names and "req_late_probe" in names
    assert not list(folder.glob("*.tools.json"))
    assert validate_registry(agents) == []


def test_a_fragment_edited_after_planning_is_kept_and_merged_by_the_next_run(reg, monkeypatch):
    """R3 P1: an edit landing between plan and unlink was deleted unmerged."""
    agents, folder = reg
    base = _doc(agents)
    first = _clone(base, "orch_registry_ping", "orch_first_probe", "A01")
    second = _clone(base, "orch_registry_ping", "orch_second_probe", "A01")
    _write(folder, "a01-orchestrator.tools.json", [first])
    real_write = intake._atomic_write

    def hooked(path, text):
        real_write(path, text)
        _write(folder, "a01-orchestrator.tools.json", [first, second])  # the author appends after planning

    monkeypatch.setattr(intake, "_atomic_write", hooked)
    plan = _apply(agents, folder)
    assert plan.errors == [] and plan.kept == ["a01-orchestrator.tools.json"]
    assert (folder / "a01-orchestrator.tools.json").exists()
    assert "orch_second_probe" not in _names(agents)
    monkeypatch.setattr(intake, "_atomic_write", real_write)
    again = _apply(agents, folder)
    assert again.errors == [] and again.kept == []
    assert "orch_second_probe" in _names(agents) and "orch_first_probe" in _names(agents)
    assert any("orch_first_probe" in line for line in again.already)
    assert not list(folder.glob("*.json"))
    assert validate_registry(agents) == []


def test_a_fragment_removed_by_someone_else_does_not_crash_the_unlink(reg, monkeypatch):
    agents, folder = reg
    _write(folder, "a01-orchestrator.tools.json", [_clone(_doc(agents), "orch_registry_ping", "orch_first_probe", "A01")])
    real_write = intake._atomic_write

    def hooked(path, text):
        real_write(path, text)
        (folder / "a01-orchestrator.tools.json").unlink()

    monkeypatch.setattr(intake, "_atomic_write", hooked)
    assert _apply(agents, folder).errors == []
    assert "orch_first_probe" in _names(agents)


def test_restored_fragments_after_a_crashed_apply_are_reported_applied_and_removed(reg):
    """R3 P2: a crash after the agents.json write left fragments that then refused as duplicates, wedging intake."""
    agents, folder = reg
    base = _doc(agents)
    mine = _clone(base, "orch_registry_ping", "orch_intake_probe", "A01")
    _write(folder, "a01-orchestrator.tools.json", [mine])
    _write(folder, "grants.json", {"orch_plan": ["A02"], "orch_intake_probe": ["A03"]})
    saved = {p.name: p.read_bytes() for p in folder.glob("*.json")}
    assert _apply(agents, folder).errors == []
    merged = agents.read_bytes()
    assert not list(folder.glob("*.json"))

    for name, data in saved.items():  # the crash: agents.json written, fragments still there
        (folder / name).write_bytes(data)
    _write(folder, "a02-requirements.tools.json", [_clone(_doc(agents), "orch_registry_ping", "req_new_probe", "A02")])
    assert intake.main(["--check", "--agents-file", str(agents), "--intake-dir", str(folder)]) == 0
    plan = _apply(agents, folder)
    assert plan.errors == []
    assert len(plan.already) == 3 and len(plan.adds) == 1  # the tool, two grants already applied; the new tool merges
    assert not list(folder.glob("*.json"))
    assert "req_new_probe" in _names(agents)
    assert validate_registry(agents) == []
    assert _tool(_doc(agents), "orch_intake_probe")["permitted_callers"] == ["A01", "A03"]
    assert _tool(_doc(agents), "orch_plan")["permitted_callers"] == ["A01", "A02"]
    assert merged != agents.read_bytes()  # only the new tool changed the file


def test_re_applying_only_already_applied_fragments_changes_nothing_and_removes_them(reg):
    agents, folder = reg
    _write(folder, "grants.json", {"orch_plan": ["A02"]})
    saved = (folder / "grants.json").read_bytes()
    assert _apply(agents, folder).errors == []
    merged = agents.read_bytes()
    (folder / "grants.json").write_bytes(saved)
    plan = _apply(agents, folder)
    assert plan.errors == [] and plan.adds == [] and plan.grants == {} and len(plan.already) == 1
    assert agents.read_bytes() == merged
    assert not (folder / "grants.json").exists()
    assert intake.main(["--apply", "--agents-file", str(agents), "--intake-dir", str(folder)]) == 0


def test_a_tool_with_different_content_is_still_a_collision(reg):
    agents, folder = reg
    mine = _clone(_doc(agents), "orch_registry_ping", "orch_intake_probe", "A01")
    _write(folder, "a01-orchestrator.tools.json", [mine])
    assert _apply(agents, folder).errors == []
    changed = dict(mine, purpose="A different purpose than the registered one.")
    _write(folder, "a01-orchestrator.tools.json", [changed])
    assert any("already exists in the registry or another fragment" in e for e in intake.plan_merge(agents, folder, ROOT).errors)
    assert (folder / "a01-orchestrator.tools.json").exists()


@pytest.mark.parametrize(
    "payload, needle",
    [
        ([{"name": ["x"], "owner": "A05"}], "tool name must be a string"),
        ([{"owner": "A05"}], "tool name must be a string"),
        ([{"name": "orch_bad_probe", "owner": "A01", "purpose": "\ud800"}], "cannot read fragment"),
    ],
)
def test_malformed_entries_are_errors_not_tracebacks(reg, payload, needle):
    agents, folder = reg
    before = agents.read_bytes()
    _write(folder, "a01-orchestrator.tools.json", payload)
    errors = _apply(agents, folder).errors
    assert any(needle in e for e in errors), errors
    assert agents.read_bytes() == before
    assert (folder / "a01-orchestrator.tools.json").exists()
    assert intake.main(["--apply", "--agents-file", str(agents), "--intake-dir", str(folder)]) == 1


def test_crlf_registry_keeps_one_line_ending(reg):
    """R3 P3: a CRLF agents.json came out with a mix of CRLF and bare LF lines."""
    agents, folder = reg
    agents.write_bytes(agents.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    base = _doc(agents)
    _write(folder, "a01-orchestrator.tools.json", [_clone(base, "orch_registry_ping", "orch_intake_probe", "A01")])
    _write(folder, "shared.tools.json", [_clone(base, "shared_verdict", "shared_intake_probe", "shared", callers=["A01", "A02"])])
    _write(folder, "grants.json", {"orch_plan": ["A02"], "be_route_norm": ["A06"]})
    assert _apply(agents, folder).errors == []
    data = agents.read_bytes()
    assert b"\r\n" in data and re.search(rb"(?<!\r)\n", data) is None
    assert validate_registry(agents) == []
    assert "orch_intake_probe" in _names(agents)


def test_the_lock_file_is_not_a_fragment_and_serialises_runs(reg):
    agents, folder = reg
    _write(folder, "a01-orchestrator.tools.json", [_clone(_doc(agents), "orch_registry_ping", "orch_intake_probe", "A01")])
    assert _apply(agents, folder).errors == []
    assert (folder / ".lock").exists()
    assert intake.plan_merge(agents, folder, ROOT).files == []
    done = threading.Event()
    with intake._locked(folder):
        thread = threading.Thread(target=lambda: (_apply(agents, folder), done.set()))
        thread.start()
        assert not done.wait(0.4)  # blocked on the held lock
    assert done.wait(30)
    thread.join()
