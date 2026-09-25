"""omp agent + skill generation contract (AGENT-01..07, ORCH-01)."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
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


def _fm_lines(text):
    """Frontmatter lines: between the opening `---` line and the next line that is exactly `---`."""
    lines = text.split("\n")
    assert lines[0] == "---"
    return lines[1:lines.index("---", 1)]


def _fm(text):
    """Decode frontmatter; double-quoted values are JSON string literals (valid YAML double-quoted scalars)."""
    fm = {}
    for line in _fm_lines(text):
        k, v = line.split(": ", 1)
        fm[k] = json.loads(v) if v.startswith('"') else v
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
SWARM_NAMES = {"swarm_plan", "swarm_status", "swarm_ingest", "swarm_transition", "swarm_gate"}
OMP_TOOLS = {"read", "grep", "glob", "bash", "write", "edit", "task"} | SWARM_NAMES
GATE_AGENTS = {"A08", "A09", "A10", "A12"}
KEY_ORDER = ["name", "description", "tools", "spawns", "blocking", "autoloadSkills", "output"]


def _agents():
    return list(load_manifest())


def _omp(a):
    return (OMP_AGENTS / f"{a['slug']}.md").read_text(encoding="utf-8")


def _keys(text):
    return [line.split(": ", 1)[0] for line in _fm_lines(text)]


def test_omp_tools_exact():
    for a in _agents():
        tools = _fm(_omp(a))["tools"].split(", ")
        assert tools == [builder.TOOL_MAP[t] for t in a["tools"]] + builder.SWARM_TOOLS.get(a["id"], []), a["id"]
        assert set(tools) <= OMP_TOOLS
    a01 = next(a for a in _agents() if a["id"] == "A01")
    assert _fm(_omp(a01))["tools"] == (
        "read, grep, glob, bash, task, write, swarm_plan, swarm_status, swarm_ingest, swarm_transition"
    )


def test_swarm_tool_grants():
    """D-01: only A01 moves task state and only the four gate agents record verdicts; nobody does both."""
    for a in _agents():
        swarm = [t for t in _fm(_omp(a))["tools"].split(", ") if t.startswith("swarm_")]
        if a["id"] == "A01":
            assert swarm == ["swarm_plan", "swarm_status", "swarm_ingest", "swarm_transition"]
        elif a["id"] in GATE_AGENTS:
            assert swarm == ["swarm_gate"], a["id"]
        else:
            assert swarm == [], a["id"]


@pytest.mark.parametrize(
    ("aid", "grant"),
    [
        ("A01", ["swarm_plan", "swarm_status", "swarm_ingest", "swarm_transition", "swarm_gate"]),
        ("A05", ["swarm_status"]),
        ("A09", ["swarm_gate", "swarm_transition"]),
        ("A08", ["swarm_nope"]),
    ],
)
def test_swarm_tools_guard(monkeypatch, aid, grant):
    monkeypatch.setitem(builder.SWARM_TOOLS, aid, grant)
    with pytest.raises(ValueError, match=aid):
        builder.render_omp(_a(aid), _agents())


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
        if a["slug"] == "a01-orchestrator":
            parts = [x.strip() for x in val.split(",")]
            assert "a01-orchestrator" in parts
            assert "swarm-orchestrate" in parts
        else:
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


def _a01_runtime_lines():
    text = _omp(_a("A01"))
    block = text[text.index("<swarm_runtime>\n") : text.index("</swarm_runtime>")]
    return [line for line in block.splitlines()[1:] if line.strip()]


def _rule(lines, needle):
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert len(hits) == 1, (needle, hits)
    return hits[0]


def test_a01_gate_is_first():
    lines = _a01_runtime_lines()
    assert "Step 0" in lines[0]
    assert _omp(_a("A01")).count("Step 0") == 1
    plan = _rule(lines, "plan mode")
    depth = _rule(lines, '"depth"')
    common = lines.index(builder._OMP_COMMON.splitlines()[0])
    strict = _rule(lines, 'schemaMode: "strict"')
    assert 0 < plan < depth < common < strict


def test_a01_plan_mode_discriminator():
    lines = _a01_runtime_lines()
    plan = lines[_rule(lines, "plan mode")]
    assert "`bash`" in plan and "`_bash`" in plan
    assert "`write`" not in plan and "`_write`" not in plan
    assert "IN_REVIEW" in plan and "summary_md" in plan and "spawn nothing" in plan


def test_a01_depth_rule_blocked_not_in_review():
    lines = _a01_runtime_lines()
    depth = lines[_rule(lines, '"depth"')]
    for token in ("`task`", "`_task`", '"BLOCKED"', "never IN_REVIEW"):
        assert token in depth, token
    assert "without reading, running or writing anything first" in depth


def test_specialist_preamble_unchanged():
    for a in _agents():
        if a["id"] != "A01":
            assert "Step 0" not in _omp(a), a["id"]


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


def _yaml_lists(text):
    """Minimal reader for a YAML mapping of block lists (`key:` then `  - item` lines); stdlib only."""
    out, key = {}, None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line[0].isspace():
            assert line.endswith(":"), line
            key = line[:-1]
            out[key] = []
        else:
            item = line.strip()
            assert key is not None and item.startswith("- "), line
            out[key].append(item[2:].strip())
    return out


def test_omp_wiring():
    """D-11 / PKG-04: the repo loads its own package through `.omp/config.yml` only, never a symlink or copies."""
    config = ROOT / ".omp" / "config.yml"
    assert _yaml_lists(config.read_text(encoding="utf-8")) == {"extensions": ["omp"]}
    for name in ("agents", "skills", "extensions"):
        assert not os.path.lexists(ROOT / ".omp" / name), name
    ignored = subprocess.run(["git", "check-ignore", "-q", ".omp/config.yml"], cwd=ROOT)
    assert ignored.returncode == 1  # 1 = not ignored (0 would mean .gitignore hides the wiring)
    pkg = json.loads((ROOT / "omp" / "package.json").read_text(encoding="utf-8"))
    assert pkg["omp"]["extensions"] == ["./src/index.ts"]


_TREE = ("scripts", "swarm", "prompts", "agents.json", ".claude", ".grok", "omp")


@pytest.fixture()
def omp_tree(tmp_path):
    """Copy of the generator inputs/outputs; build_agents.py resolves ROOT from its own path."""
    ignore = shutil.ignore_patterns("__pycache__")
    for name in _TREE:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, tmp_path / name, ignore=ignore)
        else:
            shutil.copy2(src, tmp_path / name)
    return tmp_path


def _check(tree):
    env = {k: v for k, v in os.environ.items() if k != "SWARM_AGENTS_FILE"}
    cmd = [sys.executable, str(tree / "scripts" / "build_agents.py"), "--check"]
    return subprocess.run(cmd, cwd=tree, capture_output=True, text=True, env=env)


def test_omp_check_clean_on_copy(omp_tree):
    r = _check(omp_tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("up-to-date")


@pytest.mark.parametrize("rel", ["omp/agents/a05-backend.md", "omp/skills/a05-backend/SKILL.md"], ids=["agent", "skill"])
def test_omp_check_detects_drift(omp_tree, rel):
    target = omp_tree / rel
    edited = target.read_text(encoding="utf-8") + "\nhand edit: tools: task\n"
    target.write_text(edited, encoding="utf-8")
    r = _check(omp_tree)
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.startswith("stale:") and rel in r.stdout
    assert target.read_text(encoding="utf-8") == edited


@pytest.mark.parametrize("rel", ["omp/agents/zz-orphan.md", "omp/skills/zz-orphan/SKILL.md"], ids=["agent", "skill"])
def test_omp_check_detects_orphan(omp_tree, rel):
    orphan = omp_tree / rel
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("---\nname: zz-orphan\n---\n", encoding="utf-8")
    r = _check(omp_tree)
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.startswith("stale:") and rel in r.stdout
    assert orphan.exists()
    env = {k: v for k, v in os.environ.items() if k != "SWARM_AGENTS_FILE"}
    subprocess.run([sys.executable, str(omp_tree / "scripts" / "build_agents.py")], cwd=omp_tree, check=True, env=env, capture_output=True)
    assert not orphan.exists()
    assert _check(omp_tree).returncode == 0


TRICKY = 'path C:\\new dir "quoted": a\n---\nb — end'


@pytest.mark.parametrize("render", ["agent", "skill"])
def test_omp_description_round_trips(render):
    agents = _agents()
    a = dict(agents[4], description=TRICKY)
    text = builder.render_omp(a, agents) if render == "agent" else skills_mod.omp_skill(a)
    desc = _fm(text)["description"]
    assert TRICKY in desc
    assert desc.startswith(f"{a['id']} {a['code']} ")
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load("\n".join(_fm_lines(text)))["description"] == desc


def test_omp_orphan_symlink_never_followed(omp_tree, tmp_path_factory):
    victim = tmp_path_factory.mktemp("victim")
    (victim / "SKILL.md").write_text("keep", encoding="utf-8")
    link = omp_tree / "omp" / "skills" / "zz-link"
    link.symlink_to(victim, target_is_directory=True)
    r = _check(omp_tree)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "omp/skills/zz-link" in r.stdout
    env = {k: v for k, v in os.environ.items() if k != "SWARM_AGENTS_FILE"}
    w = subprocess.run([sys.executable, str(omp_tree / "scripts" / "build_agents.py")], cwd=omp_tree, env=env, capture_output=True, text=True)
    assert w.returncode == 1, w.stdout + w.stderr
    assert "refused" in w.stderr and "Traceback" not in w.stderr
    assert (victim / "SKILL.md").read_text(encoding="utf-8") == "keep"
    assert link.is_symlink()
