"""Load-time schema validation of the agent roster (swarm/manifest.py)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm.manifest import REQUIRED, by_capability, get_agent, load_manifest, validate_agents

ROOT = Path(__file__).resolve().parent.parent
REAL = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))["agents"]


def _agent(**overrides: object) -> dict:
    agent = copy.deepcopy(REAL[4])  # A05 backend: has every REQUIRED key
    agent.update(overrides)
    return agent


def _other(ident: str, slug: str, code: str) -> dict:
    return _agent(id=ident, slug=slug, code=code)


def _write(tmp_path: Path, agents: object, wrap: bool = True) -> Path:
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"agents": agents} if wrap else agents), encoding="utf-8")
    return path


def _problems_for(**overrides: object) -> list[str]:
    return validate_agents([_agent(**overrides)])


# --- the real roster -------------------------------------------------------------------------


def test_real_manifest_is_valid_with_thirty_unique_agents():
    assert validate_agents(REAL) == []
    agents = load_manifest()
    assert len(agents) == 30
    for field in ("id", "slug", "code"):
        values = [a[field].lower() for a in agents]
        assert len(set(values)) == 30, field
    assert [a["id"] for a in agents] == [f"A{n:02d}" for n in range(1, 31)]


def test_get_agent_resolves_by_id_code_and_slug_case_insensitively():
    by_id, by_code, by_slug = get_agent("A05"), get_agent("be"), get_agent("A05-BACKEND")
    assert by_id == by_code == by_slug
    assert by_id["slug"] == "a05-backend"
    with pytest.raises(KeyError, match="no agent matches 'A99'"):
        get_agent("A99")


def test_by_capability_still_filters_the_loaded_roster():
    assert [a["id"] for a in by_capability("plan.decompose")] == ["A01"]
    assert by_capability("no.such.capability") == []


# --- individual rules ------------------------------------------------------------------------


def test_valid_single_agent_has_no_problems():
    assert validate_agents([_agent()]) == []


@pytest.mark.parametrize("doc", [None, {}, "agents", 7, []])
def test_document_must_be_non_empty_list(doc):
    problems = validate_agents(doc)
    assert len(problems) == 1
    assert "non-empty list" in problems[0]


def test_entry_must_be_an_object():
    problems = validate_agents([_agent(), "A02"])
    assert problems == ["agents[1]: entry must be an object, got str"]


def test_every_missing_key_is_reported_with_the_agent_id_in_one_message():
    agent = _agent()
    del agent["lane"], agent["scripts"]
    (problem,) = validate_agents([agent])
    assert problem == "agent A05 manifest missing ['lane', 'scripts']"


def test_all_required_keys_missing_names_the_entry_by_index_when_id_is_absent():
    (problem,) = validate_agents([{}])
    assert problem.startswith("agents[0] manifest missing [")
    for key in REQUIRED:
        assert repr(key) in problem


@pytest.mark.parametrize("bad", ["a05", "A5", "A005", "AX5", "A05 ", "", 5, None])
def test_id_pattern(bad):
    problems = _problems_for(id=bad)
    assert any(f"id {bad!r} must match ^A\\d{{2}}$" in p for p in problems), problems


@pytest.mark.parametrize("bad", ["A05-backend", "a05_backend", "a05", "a05-", "a05-Backend", "a05--x", "backend", 5])
def test_slug_pattern(bad):
    problems = _problems_for(slug=bad)
    assert any(f"slug {bad!r} must match" in p for p in problems), problems


def test_slug_digits_must_equal_id_digits():
    (problem,) = _problems_for(slug="a06-backend")
    assert "agent A05" in problem
    assert "slug 'a06-backend' number 06 does not match id 'A05'" in problem


@pytest.mark.parametrize("bad", ["be", "B-E", "B E", "", None, 3])
def test_code_pattern(bad):
    problems = _problems_for(code=bad)
    assert any(f"code {bad!r} must be a non-empty string of [A-Z0-9]" in p for p in problems), problems


@pytest.mark.parametrize("field", ["name", "class", "lane", "description"])
@pytest.mark.parametrize("bad", ["", "   ", None, 4, ["x"]])
def test_text_fields_must_be_non_empty_strings(field, bad):
    (problem,) = _problems_for(**{field: bad})
    assert f"agent A05: {field} must be a non-empty string" in problem


@pytest.mark.parametrize("bad", [0, -1, True, False, 1.0, "3", None])
def test_max_parallel_must_be_an_int_of_at_least_one(bad):
    (problem,) = _problems_for(max_parallel=bad)
    assert f"agent A05: max_parallel must be an integer >= 1, got {bad!r}" == problem


def test_max_parallel_boundary_one_is_valid():
    assert _problems_for(max_parallel=1) == []


@pytest.mark.parametrize("field", ["capabilities", "consumes", "produces", "scripts"])
def test_list_fields_must_be_lists(field):
    (problem,) = _problems_for(**{field: "scripts/x.py"})
    assert problem == f"agent A05: {field} must be a list, got str"


@pytest.mark.parametrize("field", ["capabilities", "consumes", "produces", "scripts"])
def test_list_fields_reject_empty_and_non_string_members(field):
    members = ["scripts/x.py", ""] if field == "scripts" else ["x.y", ""]
    (problem,) = _problems_for(**{field: members})
    assert f"agent A05: {field} must contain only non-empty strings" in problem
    assert "''" in problem
    (problem,) = _problems_for(**{field: [3]})
    assert f"agent A05: {field} must contain only non-empty strings, got [3]" == problem


@pytest.mark.parametrize("field", ["capabilities", "consumes", "produces", "scripts"])
def test_list_fields_reject_duplicate_entries(field):
    entry = "scripts/x.py" if field == "scripts" else "x.y"
    (problem,) = _problems_for(**{field: [entry, "scripts/y.py" if field == "scripts" else "y.z", entry]})
    assert problem == f"agent A05: {field} has duplicate entries {[entry]!r}"


@pytest.mark.parametrize("field", ["capabilities", "produces"])
def test_capabilities_and_produces_must_not_be_empty(field):
    (problem,) = _problems_for(**{field: []})
    assert problem == f"agent A05: {field} must not be empty"


@pytest.mark.parametrize("field", ["consumes", "scripts"])
def test_consumes_and_scripts_may_be_empty(field):
    assert _problems_for(**{field: []}) == []


def test_same_capability_may_be_shared_between_agents():
    # code.patch is legitimately owned by several implementers; uniqueness is not a manifest rule.
    agents = [_other("A05", "a05-backend", "BE"), _other("A06", "a06-frontend", "FE")]
    assert agents[0]["capabilities"] == agents[1]["capabilities"]
    assert validate_agents(agents) == []


@pytest.mark.parametrize("level", ["L0", "L5", "l2", "", None, 2, ["L2"]])
def test_autonomy_levels_are_limited_to_l1_to_l4(level):
    (problem,) = _problems_for(autonomy_ceiling={"schedule": "L2", "deploy": level})
    assert problem.startswith("agent A05: autonomy_ceiling['deploy'] must be one of ['L1', 'L2', 'L3', 'L4']")
    assert f"got {level!r}" in problem


@pytest.mark.parametrize("level", ["L1", "L2", "L3", "L4"])
def test_each_valid_autonomy_level_is_accepted(level):
    assert _problems_for(autonomy_ceiling={"a": level}) == []


def test_autonomy_ceiling_must_be_an_object():
    (problem,) = _problems_for(autonomy_ceiling=["L1"])
    assert problem == "agent A05: autonomy_ceiling must be an object, got list"


@pytest.mark.parametrize(
    "bad",
    ["/etc/passwd.py", "/abs/scripts/x.py", "scripts/../x.py", "../scripts/x.py", "scripts/x.sh",
     "tools/x.py", "scripts/sub/x.py", "scripts/.py", "scripts/x.py/..", "x.py"],
)
def test_scripts_must_be_repo_relative_scripts_py_paths(bad):
    problems = _problems_for(scripts=["scripts/ok.py", bad])
    assert problems == [f"agent A05: scripts entry {bad!r} must be a repo-relative scripts/<name>.py path"]


# --- uniqueness across agents ----------------------------------------------------------------


def test_duplicate_id_is_rejected_and_names_both_entries():
    problems = validate_agents([_other("A05", "a05-backend", "BE"), _other("A05", "a05-other", "OTHER")])
    assert problems == ["agent A05: duplicate id 'A05' (already used by agents[0])"]


def test_duplicate_slug_is_rejected_case_insensitively():
    # An uppercase twin is itself malformed, and is additionally reported as a duplicate.
    # Same digits force the same id, so the id duplicate is reported as well.
    problems = validate_agents([_other("A05", "a05-backend", "BE"), _other("A05", "a05-Backend", "FE")])
    assert "agent A05: duplicate slug 'a05-Backend' (already used by agents[0])" in problems
    assert "agent A05: duplicate id 'A05' (already used by agents[0])" in problems


def test_duplicate_slug_exact_is_rejected():
    problems = validate_agents([_other("A05", "a05-backend", "BE"), _other("A05", "a05-backend", "FE")])
    assert "agent A05: duplicate slug 'a05-backend' (already used by agents[0])" in problems


def test_duplicate_code_is_rejected_case_insensitively():
    problems = validate_agents([_other("A05", "a05-backend", "BE"), _other("A06", "a06-frontend", "be")])
    assert any("duplicate code 'be' (already used by agents[0])" in p for p in problems), problems
    problems = validate_agents([_other("A05", "a05-backend", "BE"), _other("A06", "a06-frontend", "BE")])
    assert problems == ["agent A06: duplicate code 'BE' (already used by agents[0])"]


# --- aggregation and load_manifest -------------------------------------------------------------


def test_independent_defects_are_all_reported_together():
    bad_a = _other("A05", "a05-backend", "BE")
    bad_a["max_parallel"] = 0
    bad_b = _other("A06", "a06-frontend", "FE")
    bad_b["lane"] = ""
    problems = validate_agents([bad_a, bad_b])
    assert len(problems) == 2
    assert "agent A05: max_parallel" in problems[0]
    assert "agent A06: lane" in problems[1]


def test_multiple_defects_in_one_agent_each_get_a_line():
    problems = _problems_for(max_parallel=0, lane="", capabilities=[])
    assert len(problems) == 3
    assert {p.split(":")[1].split()[0] for p in problems} == {"max_parallel", "lane", "capabilities"}


def test_load_manifest_returns_the_list_for_a_good_file(tmp_path):
    agents = [_other("A05", "a05-backend", "BE"), _other("A06", "a06-frontend", "FE")]
    assert load_manifest(_write(tmp_path, agents)) == agents
    assert load_manifest(_write(tmp_path, agents, wrap=False)) == agents


def test_load_manifest_raises_one_line_per_problem_prefixed_with_the_file_name(tmp_path):
    bad_a = _other("A05", "a05-backend", "BE")
    bad_a["max_parallel"] = True
    bad_b = _other("A06", "a06-frontend", "BE")
    path = _write(tmp_path, [bad_a, bad_b])
    with pytest.raises(ValueError) as exc:
        load_manifest(path)
    lines = str(exc.value).splitlines()
    assert len(lines) == 2
    assert all(line.startswith("agents.json: ") for line in lines)
    assert "agent A05: max_parallel must be an integer >= 1, got True" in lines[0]
    assert "agent A06: duplicate code 'BE'" in lines[1]


def test_load_manifest_reports_missing_keys_for_every_agent(tmp_path):
    first, second = _other("A05", "a05-backend", "BE"), _other("A06", "a06-frontend", "FE")
    del first["name"]
    del second["consumes"]
    with pytest.raises(ValueError) as exc:
        load_manifest(_write(tmp_path, [first, second]))
    assert str(exc.value).splitlines() == [
        "agents.json: agent A05 manifest missing ['name']",
        "agents.json: agent A06 manifest missing ['consumes']",
    ]


def test_load_manifest_rejects_invalid_json_and_missing_agents_key(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match=r"^broken\.json: invalid JSON"):
        load_manifest(path)
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"^broken\.json: document has no 'agents' key$"):
        load_manifest(path)


def test_load_manifest_rejects_an_empty_roster(tmp_path):
    with pytest.raises(ValueError, match="non-empty list"):
        load_manifest(_write(tmp_path, []))


def test_get_agent_and_by_capability_fail_fast_on_a_bad_file(tmp_path):
    path = _write(tmp_path, [_agent(max_parallel=0)])
    with pytest.raises(ValueError, match="max_parallel"):
        get_agent("A05", path)
    with pytest.raises(ValueError, match="max_parallel"):
        by_capability("code.patch", path)


def test_get_agent_resolves_from_a_custom_file(tmp_path):
    path = _write(tmp_path, [_other("A05", "a05-backend", "BE"), _other("A06", "a06-frontend", "FE")])
    assert get_agent("fe", path)["id"] == "A06"
    assert get_agent("A05-Backend", path)["code"] == "BE"
