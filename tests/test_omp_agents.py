"""omp agent + skill generation contract (AGENT-01..06, ORCH-01)."""
import importlib.util
import json
from pathlib import Path

import pytest

from swarm.manifest import load_manifest

ROOT = Path(__file__).resolve().parent.parent
OMP_AGENTS = ROOT / "omp" / "agents"
OMP_SKILLS = ROOT / "omp" / "skills"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


skills_mod = _load("_write_skills")


def _fm(text):
    fm = {}
    for line in text.split("---", 2)[1].strip().splitlines():
        k, v = line.split(": ", 1)
        fm[k] = v
    return fm


def test_omp_agent_files_match_manifest():
    slugs = {a["slug"] for a in load_manifest()}
    assert {p.stem for p in OMP_AGENTS.glob("*.md")} == slugs
    for slug in slugs:
        assert _fm((OMP_AGENTS / f"{slug}.md").read_text(encoding="utf-8"))["name"] == slug


def test_omp_skill_files_exist():
    for a in load_manifest():
        p = OMP_SKILLS / a["slug"] / "SKILL.md"
        fm = _fm(p.read_text(encoding="utf-8"))
        assert fm["name"] == a["slug"]
        assert fm["description"].strip('"').strip()


def test_omp_skill_matches_renderer():
    for a in load_manifest():
        p = OMP_SKILLS / a["slug"] / "SKILL.md"
        assert skills_mod.omp_skill(a) == p.read_text(encoding="utf-8"), a["slug"]


builder = _load("build_agents")
SCHEMA = ROOT / "swarm" / "schemas" / "task.result.v1.json"
OMP_TOOLS = {"read", "grep", "glob", "bash", "write", "edit", "task"}
KEY_ORDER = ["name", "description", "tools", "spawns", "blocking", "autoloadSkills", "output"]


def _agents():
    return list(load_manifest())


def _omp(a):
    return (OMP_AGENTS / f"{a['slug']}.md").read_text(encoding="utf-8")


def _keys(text):
    return [line.split(": ", 1)[0] for line in text.split("---", 2)[1].strip().splitlines()]


def test_omp_tools_exact():
    for a in _agents():
        tools = _fm(_omp(a))["tools"].split(", ")
        assert tools == [builder.TOOL_MAP[t] for t in a["tools"]], a["id"]
        assert set(tools) <= OMP_TOOLS
    a01 = next(a for a in _agents() if a["id"] == "A01")
    assert _fm(_omp(a01))["tools"] == "read, grep, glob, bash, task, write"


def test_omp_no_model_key():
    for a in _agents():
        text = _omp(a)
        assert "model" not in _keys(text)
        head = text.split("---", 2)[1]
        for bad in ("opus", "sonnet", "haiku", "inherit"):
            assert bad not in head.split("output:")[0].lower(), (a["id"], bad)


def _a(aid):
    return next(a for a in _agents() if a["id"] == aid)


def test_render_omp_unmapped_tool_raises():
    with pytest.raises(ValueError, match="A05"):
        builder.render_omp({**_a("A05"), "tools": ["Read", "WebFetch"]}, _agents())


def test_render_omp_empty_tools_raises():
    with pytest.raises(ValueError, match="A05"):
        builder.render_omp({**_a("A05"), "tools": []}, _agents())


def test_render_omp_specialist_task_raises():
    with pytest.raises(ValueError, match="A09"):
        builder.render_omp({**_a("A09"), "tools": ["Read", "Agent"]}, _agents())


def test_omp_spawn_topology():
    agents = _agents()
    for a in agents:
        text = _omp(a)
        fm = _fm(text)
        if a["id"] == "A01":
            assert fm["spawns"].split(", ") == [x["slug"] for x in agents if x["id"] != "A01"]
            assert a["slug"] not in fm["spawns"]
            assert "task" in fm["tools"].split(", ")
        else:
            assert "\nspawns: \"\"\n" in text, a["id"]
            assert "task" not in fm["tools"].split(", ")


def test_omp_spawns_full_under_only():
    from conftest import run_script
    r = run_script("build_agents.py", "--check", "--only", "a01-orchestrator")
    assert r.returncode == 0, r.stdout + r.stderr


def test_omp_output_equals_schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for a in _agents():
        text = _omp(a)
        fm = _fm(text)
        assert json.loads(fm["output"]) == schema
        assert "schemaMode" not in _keys(text)


def test_omp_autoload_own_slug():
    for a in _agents():
        val = _fm(_omp(a))["autoloadSkills"]
        assert val == a["slug"] and "," not in val


def test_omp_blocking_set():
    blocking = set()
    for a in _agents():
        fm = _fm(_omp(a))
        if "blocking" in fm:
            assert fm["blocking"] == "true", a["id"]
            blocking.add(a["id"])
    assert blocking == {"A01", "A08", "A09", "A10", "A12"}


def test_omp_frontmatter_key_order():
    for a in _agents():
        keys = _keys(_omp(a))
        assert keys == [k for k in KEY_ORDER if k in keys] and len(keys) == len(set(keys))
        assert set(KEY_ORDER) - {"blocking"} <= set(keys)


def test_omp_no_bundled_name_collision():
    slugs = {a["slug"] for a in _agents()}
    assert not slugs & {"scout", "reviewer", "security-reviewer", "task", "sonic", "main", "sub"}


GATES = ("a08-qa", "a09-reviewer", "a10-security", "a12-release")


def test_a01_strict_dispatch():
    text = builder.OMP_ORCH_PREAMBLE
    assert 'schemaMode: "strict"' in text
    rule = next(line for line in text.splitlines() if 'schemaMode: "strict"' in line)
    for gate in GATES:
        assert gate in rule, gate
    assert _omp(_a("A01")).count(rule) == 1


def test_a01_plan_mode_before_depth():
    text = builder.OMP_ORCH_PREAMBLE
    plan = next(line for line in text.splitlines() if "plan mode" in line)
    depth = next(line for line in text.splitlines() if '"depth"' in line)
    assert "IN_REVIEW" in plan and "summary_md" in plan and "spawn nothing" in plan
    assert "BLOCKED" in depth and "needs" in depth
    assert text.index(plan) < text.index(depth)


def test_omp_yield_delivery():
    for a in _agents():
        text = _omp(a)
        assert "`yield` tool with your `task.result`" in text, a["id"]
        assert "replaces any fenced-json finish instruction" in text, a["id"]


def test_specialist_preamble_no_spawn():
    assert "`task`" not in builder.OMP_PREAMBLE
    assert "spawn" not in builder.OMP_PREAMBLE.lower()


def test_omp_preamble_keeps_autonomy_ceiling():
    for a in _agents():
        assert 'L3/L4, stop and report `"state": "BLOCKED", "needs": "human-approval: …"`' in _omp(a), a["id"]


def test_omp_size_bound():
    sizes = {p.stem: p.stat().st_size for p in OMP_AGENTS.glob("*.md")}
    assert max(sizes.values()) <= 24 * 1024
    assert max(sizes, key=sizes.get) == "a01-orchestrator"


def test_omp_no_host_paths():
    for p in (ROOT / "omp").rglob("*"):
        if p.is_file():
            assert "/root/" not in p.read_text(encoding="utf-8"), p
