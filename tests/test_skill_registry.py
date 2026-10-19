"""Skill source validation (v2.2 DESIGN 4.8): every rule rejects its own fixture and the valid fixture passes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from swarm import skill_registry as sr

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "skills"
OWNER = "a05-backend"


def _tool(name: str, callers: list[str]) -> dict:
    return {"name": name, "owner": "x", "purpose": f"{name} purpose.", "permitted_callers": callers}


LENIENT = {"skills": {"min": 0, "max": 15}}


def _doc(policy: dict | None = LENIENT, **extra) -> dict:
    doc = {
        "agents": [
            {"id": "A05", "slug": "a05-backend", "registered_tools": [
                _tool("be_route_norm", ["A05"]), _tool("be_route_collision", ["A05"]), _tool("be_coverage_delta", ["A05"])]},
            {"id": "A06", "slug": "a06-frontend", "registered_tools": [_tool("fe_component_tree", ["A06"])]},
        ],
        "shared_tools": [_tool("shared_verdict", ["A05", "A06"]), _tool("shared_a05_only", ["A05"])],
    }
    if policy is not None:
        doc["capability_policy"] = policy
    doc.update(extra)
    return doc


def _place(root: Path, fixture: str, owner: str, short: str, text: str | None = None) -> None:
    dest = root / "prompts" / "skills" / owner
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{short}.md").write_text(text if text is not None else (FIXTURES / fixture).read_text(encoding="utf-8"), encoding="utf-8")


def _errors(root: Path, doc: dict | None = None, **kw) -> list[str]:
    return sr.validate_skills(sr.load_skills(root), doc or _doc(), **kw)


def test_valid_fixture_passes_and_parses(tmp_path):
    _place(tmp_path, "valid.md", OWNER, "route-review")
    [skill] = sr.load_skills(tmp_path)
    assert skill.name == "a05-backend--route-review" and skill.short == "route-review"
    assert skill.tools == ("be_route_norm", "be_route_collision")
    assert skill.path == "prompts/skills/a05-backend/route-review.md"
    assert _errors(tmp_path) == []


def test_skills_for_catalog_view(tmp_path):
    _place(tmp_path, "valid.md", OWNER, "route-review")
    [entry] = sr.skills_for(OWNER, root=tmp_path)
    assert {"name", "description", "owner", "tools"} <= set(entry)
    assert entry["owner"] == OWNER and entry["tools"] == ["be_route_norm", "be_route_collision"]
    assert sr.skills_for("shared", root=tmp_path) == []


def test_bad_name_is_rejected_with_the_file(tmp_path):
    _place(tmp_path, "bad_name.md", OWNER, "route-review")
    errors = _errors(tmp_path)
    assert any("route-review.md" in e and "a05-backend--<short>" in e for e in errors), errors


def test_name_must_match_file_name(tmp_path):
    _place(tmp_path, "valid.md", OWNER, "other-file")
    assert any("file name 'other-file.md'" in e for e in _errors(tmp_path))


def test_uppercase_short_is_rejected(tmp_path):
    text = (FIXTURES / "valid.md").read_text().replace("a05-backend--route-review", "a05-backend--Route_Review")
    _place(tmp_path, "", OWNER, "Route_Review", text)
    assert any("must be 'a05-backend--<short>'" in e for e in _errors(tmp_path))


def test_missing_section_is_named(tmp_path):
    _place(tmp_path, "missing_section.md", OWNER, "route-review")
    assert any("missing section '## Failure Handling'" in e for e in _errors(tmp_path))


def test_sections_out_of_order(tmp_path):
    text = (FIXTURES / "valid.md").read_text()
    swapped = text.replace("## Outputs", "## @@").replace("## Handover", "## Outputs").replace("## @@", "## Handover")
    _place(tmp_path, "", OWNER, "route-review", swapped)
    assert any("out of order" in e for e in _errors(tmp_path))


def test_unknown_tool_is_rejected(tmp_path):
    _place(tmp_path, "unknown_tool.md", OWNER, "route-review")
    assert any("'be_does_not_exist' is not in the registry" in e for e in _errors(tmp_path))


def test_owner_must_be_permitted_caller(tmp_path):
    _place(tmp_path, "not_permitted.md", OWNER, "layout-review")
    errors = _errors(tmp_path)
    assert any("not a permitted caller of 'fe_component_tree'" in e for e in errors), errors


def test_shared_skill_tools_must_be_callable_by_every_agent(tmp_path):
    text = (FIXTURES / "valid.md").read_text()
    text = text.replace("a05-backend--route-review", "swarm-route-review").replace("owner: a05-backend", "owner: shared")
    text = text.replace("[be_route_norm, be_route_collision]", "[shared_verdict, shared_a05_only]")
    text = text.replace("`be_route_norm`", "`shared_verdict`").replace("`be_route_collision`", "`shared_a05_only`")
    _place(tmp_path, "", "shared", "route-review", text)
    errors = _errors(tmp_path)
    assert any("'shared_a05_only', which A06 may not call" in e for e in errors), errors
    assert not any("shared_verdict" in e for e in errors)


def test_a_shared_skill_cannot_take_the_generated_driver_name(tmp_path):
    """R3 P2: `swarm-orchestrate` is written by build_agents for A01; a source skill with that name overwrote it."""
    text = (FIXTURES / "valid.md").read_text()
    text = text.replace("a05-backend--route-review", "swarm-orchestrate").replace("owner: a05-backend", "owner: shared")
    text = text.replace("[be_route_norm, be_route_collision]", "[shared_verdict]")
    text = text.replace("`be_route_norm`", "`shared_verdict`").replace("`be_route_collision`", "`shared_verdict`")
    _place(tmp_path, "", "shared", "orchestrate", text)
    errors = _errors(tmp_path)
    assert any("orchestrate.md" in e and "'swarm-orchestrate' is reserved" in e for e in errors), errors


def test_over_size_is_rejected(tmp_path):
    text = (FIXTURES / "valid.md").read_text() + "\n" + "- filler line to push the file past the limit\n" * 160
    _place(tmp_path, "", OWNER, "route-review", text)
    assert any("exceeds the 6144-byte limit" in e for e in _errors(tmp_path))


def test_size_boundary_is_inclusive(tmp_path):
    base = (FIXTURES / "valid.md").read_text()
    pad = sr.MAX_BYTES - len(base.encode())
    _place(tmp_path, "", OWNER, "route-review", base + "x" * pad)
    assert _errors(tmp_path) == []
    _place(tmp_path, "", OWNER, "route-review", base + "x" * (pad + 1))
    assert any("exceeds" in e for e in _errors(tmp_path))


def test_near_duplicate_inside_one_agent(tmp_path):
    _place(tmp_path, "valid.md", OWNER, "route-review")
    _place(tmp_path, "near_duplicate.md", OWNER, "route-audit")
    errors = _errors(tmp_path)
    assert len(errors) == 1 and "near-duplicate of" in errors[0], errors
    assert "route-audit.md" in errors[0] and "route-review.md" in errors[0]


def test_distinct_skills_in_one_agent_are_not_duplicates(tmp_path):
    _place(tmp_path, "valid.md", OWNER, "route-review")
    _place(tmp_path, "distinct.md", OWNER, "coverage-gate")
    assert _errors(tmp_path) == []


def test_duplicate_names_across_owners(tmp_path):
    _place(tmp_path, "valid.md", OWNER, "route-review")
    _place(tmp_path, "valid.md", "a06-frontend", "route-review")
    errors = _errors(tmp_path)
    assert any("already used by" in e for e in errors), errors


def test_unknown_owner_directory(tmp_path):
    _place(tmp_path, "valid.md", "a99-nobody", "route-review")
    assert any("neither an agent slug nor 'shared'" in e for e in _errors(tmp_path))


def test_frontmatter_owner_must_match_directory(tmp_path):
    text = (FIXTURES / "valid.md").read_text().replace("owner: a05-backend", "owner: a06-frontend")
    _place(tmp_path, "", OWNER, "route-review", text)
    assert any("does not match its directory" in e for e in _errors(tmp_path))


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        ("description: Use when", "description: Run when", "must start with 'Use when'"),
        ("description: Use when a backend change adds or edits HTTP routes", "description: Use when " + "x" * 160, "at most 160 characters"),
        ("tools: [be_route_norm, be_route_collision]", "tools: be_route_norm", "list like [a, b]"),
        ("tools: [be_route_norm, be_route_collision]", "tools: [be_route_norm, be_route_norm]", "listed twice"),
        ("owner: a05-backend\n", "", "missing 'owner'"),
        ("owner: a05-backend\n", "owner: a05-backend\nmodel: x\n", "'model' is not allowed"),
    ],
)
def test_frontmatter_rules(tmp_path, old, new, needle):
    text = (FIXTURES / "valid.md").read_text().replace(old, new, 1)
    _place(tmp_path, "", OWNER, "route-review", text)
    errors = _errors(tmp_path)
    assert any(needle in e and "route-review.md" in e for e in errors), errors


def test_missing_frontmatter(tmp_path):
    _place(tmp_path, "", OWNER, "route-review", "# no frontmatter\n")
    assert any("missing frontmatter" in e for e in _errors(tmp_path))


def test_procedure_step_must_name_a_tool_or_script(tmp_path):
    text = (FIXTURES / "valid.md").read_text().replace(
        "3. Record the verdict in `notes.md` using the `write` tool.", "3. Think about the verdict carefully.")
    _place(tmp_path, "", OWNER, "route-review", text)
    assert any("step 3 names no tool or script" in e for e in _errors(tmp_path))


def test_procedure_steps_must_be_sequential(tmp_path):
    text = (FIXTURES / "valid.md").read_text().replace("2. Compare", "5. Compare")
    _place(tmp_path, "", OWNER, "route-review", text)
    assert any("step 5 is out of sequence (expected 2)" in e for e in _errors(tmp_path))


def test_headings_inside_code_fences_are_not_sections(tmp_path):
    text = (FIXTURES / "valid.md").read_text().replace(
        "- `notes.md` with one line per route and the collision list.",
        "```\n## Not a section\n```")
    _place(tmp_path, "", OWNER, "route-review", text)
    assert _errors(tmp_path) == []


def _fill(root: Path, count: int) -> None:
    """`count` valid skills for one agent, each worded differently so the duplicate check stays quiet."""
    for i in range(count):
        words = " ".join(f"w{i}x{j}" for j in range(12))
        _place(root, "", OWNER, f"skill-{i}", f"""---
name: a05-backend--skill-{i}
description: Use when topic{i} {words} needs handling.
owner: a05-backend
tools: [be_route_norm]
---

## When to Use

- Topic {i}: {words}.

## Procedure

1. Run `be_route_norm` on item {i}.

## Outputs

- Result {i}.

## Failure Handling

- Failure {i}.

## Handover

- Handover {i}.
""")


POLICY = {"skills": {"min": 2, "max": 3}}


def test_count_below_policy_minimum(tmp_path):
    _fill(tmp_path, 1)
    errors = _errors(tmp_path, _doc(POLICY))
    assert any(e.startswith("prompts/skills/a05-backend: 1 skills is below the policy minimum of 2") for e in errors), errors


def test_count_above_policy_maximum(tmp_path):
    _fill(tmp_path, 4)
    errors = _errors(tmp_path, _doc(POLICY))
    assert any("4 skills exceeds the policy maximum of 3" in e for e in errors), errors


def test_count_inside_policy_passes(tmp_path):
    _fill(tmp_path, 2)
    assert [e for e in _errors(tmp_path, _doc(POLICY)) if "a05-backend:" in e] == []


def test_explicit_minimum_flags_agents_without_skills(tmp_path):
    errors = _errors(tmp_path, _doc(POLICY))
    assert any("a06-frontend: 0 skills is below the policy minimum of 2" in e for e in errors)


@pytest.mark.parametrize(
    "policy",
    [None, {"tools": {"min": 10, "max": 15}}, {"skills": {"min": 0, "max": 15}}, {"skills": {"min": 10, "max": 15, "enforce": False}}],
    ids=["no-policy", "no-skills-entry", "min-zero", "enforce-false"],
)
def test_zero_skills_are_not_an_error_until_a_minimum_is_demanded(tmp_path, policy):
    assert _errors(tmp_path, _doc(policy)) == []


def test_default_policy_caps_each_agent_at_fifteen(tmp_path):
    _fill(tmp_path, 16)
    assert any("16 skills exceeds the policy maximum of 15" in e for e in _errors(tmp_path, _doc()))


def test_enforce_false_keeps_the_maximum(tmp_path):
    _fill(tmp_path, 4)
    errors = _errors(tmp_path, _doc({"skills": {"min": 9, "max": 3, "enforce": False}}))
    assert any("exceeds the policy maximum of 3" in e for e in errors) and not any("below" in e for e in errors)


def test_counts_can_be_skipped_for_the_generator(tmp_path):
    _fill(tmp_path, 5)
    assert _errors(tmp_path, _doc(POLICY), counts=False) == []


def test_combined_policy_counts_tools_plus_skills(tmp_path):
    _fill(tmp_path, 1)  # a05 has 3 registered tools + 1 skill = 4
    errors = _errors(tmp_path, _doc({"combined": {"min": 5, "max": 9}}))
    assert any("a05-backend: 4 tools+skills is below the combined minimum of 5" in e for e in errors), errors
    assert not any("combined maximum" in e for e in errors)


def test_shared_baseline_skill_must_exist(tmp_path):
    errors = _errors(tmp_path, _doc(shared_baseline={"tools": [], "skills": ["swarm-handover-intake"]}))
    assert errors == ["agents.json: shared_baseline skill 'swarm-handover-intake' has no source under prompts/skills/shared"]


def _cli(root: Path, doc: dict, *args: str) -> subprocess.CompletedProcess:
    agents = root / "agents.json"
    agents.write_text(json.dumps(doc), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "swarm.skill_registry", "check", "--root", str(root), *args],
        cwd=ROOT, capture_output=True, text=True,
    )


def test_cli_exit_codes_and_messages(tmp_path):
    _place(tmp_path, "valid.md", OWNER, "route-review")
    ok = _cli(tmp_path, _doc())
    assert ok.returncode == 0 and "1 skill(s) ok" in ok.stdout
    _place(tmp_path, "bad_name.md", OWNER, "bad-one")
    bad = _cli(tmp_path, _doc())
    assert bad.returncode == 1
    assert "prompts/skills/a05-backend/bad-one.md:" in bad.stderr


def test_cli_unreadable_registry_is_not_a_pass(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "swarm.skill_registry", "check", "--root", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 2 and "cannot read registry" in r.stderr


def test_real_tree_sources_are_valid_apart_from_counts():
    doc = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    assert sr.validate_skills(sr.load_skills(ROOT), doc, counts=False) == []
