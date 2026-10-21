"""v2.2 capability acceptance proofs over the committed registry, skill sources and generated omp files.

Every failure names the agent (or tool/skill) and the missing item.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarm import skill_registry as sr
from swarm.tool_registry import tool_tier

ROOT = Path(__file__).resolve().parent.parent
OMP_AGENT_MAX_BYTES = 64 * 1024
MAX_VISIBLE_OMP_SKILLS = 16
EXPECTED_SHARED_WRITE = frozenset({
    "shared_handover_write",
    "shared_handover_receipt",
    "shared_status_report",
    "shared_scratchpad_put",
    "shared_escalate",
    "shared_log_emit",
})
# orch_reconcile mutates the Task Store (reopens tasks, creates gate reruns, moves tasks to DONE) and appends run-log events.
EXPECTED_LANE_WRITE: frozenset[str] = frozenset({"orch_reconcile"})
REQUIRED_AUTOLOAD = ("swarm-handover-intake", "swarm-handover-emit")
REQUIRED_OMP_TOOLS = ("swarm_catalog", "swarm_tool_call")

DOC = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
AGENTS = DOC["agents"]
SKILLS = sr.load_skills(ROOT)
AGENT_IDS = [a["slug"] for a in AGENTS]


def _frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "---", f"{path.relative_to(ROOT)}: no frontmatter"
    fm: dict[str, str] = {}
    for line in lines[1:lines.index("---", 1)]:
        key, _, value = line.partition(": ")
        fm[key] = value
    return fm


def _all_tools() -> list[tuple[str, dict]]:
    out = [("shared", t) for t in DOC["shared_tools"]]
    out += [(a["slug"], t) for a in AGENTS for t in a["registered_tools"]]
    return out


def test_there_are_fifteen_agents():
    assert len(AGENTS) == 15, f"expected 15 agents, found {len(AGENTS)}"


@pytest.mark.parametrize("slug", AGENT_IDS)
def test_agent_tool_and_skill_counts_are_inside_the_policy(slug):
    policy = DOC["capability_policy"]
    agent = next(a for a in AGENTS if a["slug"] == slug)
    tools = len(agent["registered_tools"])
    skills = len(sr.skills_for_owner(SKILLS, slug))
    lo, hi = policy["tools"]["min"], policy["tools"]["max"]
    assert lo <= tools <= hi, f"{slug}: {tools} registered lane tools, policy requires {lo}..{hi}"
    lo, hi = policy["skills"]["min"], policy["skills"]["max"]
    assert lo <= skills <= hi, f"{slug}: {skills} skills, policy requires {lo}..{hi}"


def test_tool_names_are_unique_across_agents_and_shared():
    seen: dict[str, str] = {}
    for owner, tool in _all_tools():
        name = tool["name"]
        assert name not in seen, f"tool {name!r} registered by both {seen[name]} and {owner}"
        seen[name] = owner


def test_skill_names_are_unique_across_agents_and_shared():
    seen: dict[str, str] = {}
    for s in SKILLS:
        assert s.name not in seen, f"skill {s.name!r} defined by both {seen[s.name]} and {s.path}"
        seen[s.name] = s.path


def test_every_agent_is_a_permitted_caller_of_every_baseline_tool():
    by_name = {t["name"]: t for _owner, t in _all_tools()}
    baseline = DOC["shared_baseline"]["tools"]
    assert baseline, "shared_baseline.tools is empty"
    for agent in AGENTS:
        for name in baseline:
            assert name in by_name, f"shared_baseline tool {name!r} is not registered"
            assert agent["id"] in by_name[name]["permitted_callers"], (
                f"{agent['slug']} ({agent['id']}) is not a permitted caller of baseline tool {name!r}")


def test_every_baseline_skill_exists_and_is_hidden():
    names = {s.name for s in SKILLS}
    baseline = DOC["shared_baseline"]["skills"]
    assert baseline, "shared_baseline.skills is empty"
    for name in baseline:
        assert name in names, f"baseline skill {name!r} has no source under prompts/skills/shared"
        omp = ROOT / "omp" / "skills" / name / "SKILL.md"
        assert omp.is_file(), f"baseline skill {name!r}: omp/skills/{name}/SKILL.md is not generated"
        assert _frontmatter(omp).get("disable-model-invocation") == "true", f"baseline skill {name!r} is not hidden"


@pytest.mark.parametrize("slug", AGENT_IDS)
def test_omp_agent_autoloads_its_index_and_the_handover_skills(slug):
    fm = _frontmatter(ROOT / "omp" / "agents" / f"{slug}.md")
    autoload = fm["autoloadSkills"].split(",")
    assert autoload[0] == slug, f"{slug}: autoloadSkills must start with {slug!r}, got {autoload}"
    for required in REQUIRED_AUTOLOAD:
        assert required in autoload, f"{slug}: autoloadSkills is missing {required!r} (has {autoload})"


@pytest.mark.parametrize("slug", AGENT_IDS)
def test_omp_agent_lists_the_registry_bridge_tools(slug):
    tools = [t.strip() for t in _frontmatter(ROOT / "omp" / "agents" / f"{slug}.md")["tools"].split(",")]
    for required in REQUIRED_OMP_TOOLS:
        assert required in tools, f"{slug}: omp frontmatter tools is missing {required!r} (has {tools})"


def test_every_tool_has_a_read_or_write_tier_only():
    """An absent `side_effect_tier` means READ (swarm.tool_registry.tool_tier); DESTRUCTIVE and EXTERNAL never appear."""
    for owner, tool in _all_tools():
        tier = tool_tier(tool)
        assert tier in ("READ", "WRITE"), f"{owner}: tool {tool['name']!r} has side_effect_tier {tier!r}, expected READ or WRITE"


def test_write_tools_are_exactly_the_expected_set():
    shared = {t["name"] for t in DOC["shared_tools"] if tool_tier(t) == "WRITE"}
    assert shared == EXPECTED_SHARED_WRITE, (
        f"shared WRITE tools drifted: unexpected {sorted(shared - EXPECTED_SHARED_WRITE)}, missing {sorted(EXPECTED_SHARED_WRITE - shared)}")
    lane = {t["name"] for a in AGENTS for t in a["registered_tools"] if tool_tier(t) == "WRITE"}
    assert lane == EXPECTED_LANE_WRITE, (
        f"lane WRITE tools drifted: unexpected {sorted(lane - EXPECTED_LANE_WRITE)}, missing {sorted(EXPECTED_LANE_WRITE - lane)}")


def test_visible_omp_skills_stay_within_the_budget():
    visible = sorted(
        p.parent.name for p in (ROOT / "omp" / "skills").glob("*/SKILL.md")
        if _frontmatter(p).get("disable-model-invocation") != "true"
    )
    assert len(visible) <= MAX_VISIBLE_OMP_SKILLS, (
        f"{len(visible)} visible omp skills exceed {MAX_VISIBLE_OMP_SKILLS}; un-hidden: {visible}")


def test_omp_agent_files_fit_and_the_orchestrator_is_largest():
    sizes = {p.stem: p.stat().st_size for p in (ROOT / "omp" / "agents").glob("*.md")}
    assert set(sizes) == set(AGENT_IDS), f"omp/agents differs from the registry: {sorted(set(sizes) ^ set(AGENT_IDS))}"
    for slug, size in sizes.items():
        assert size <= OMP_AGENT_MAX_BYTES, f"{slug}: omp agent file is {size} bytes, limit {OMP_AGENT_MAX_BYTES}"
    largest = max(sizes, key=sizes.get)
    assert largest == "a01-orchestrator", f"largest omp agent is {largest} ({sizes[largest]} bytes), expected a01-orchestrator"
