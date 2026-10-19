from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent


def test_all_agent_skills_exist():
    agents = json.loads((ROOT / "agents.json").read_text())["agents"]
    for a in agents:
        p = ROOT / "skills" / a["slug"] / "SKILL.md"
        assert p.exists(), p
        text = p.read_text()
        assert text.startswith("---\n")
        assert f"name: {a['slug']}" in text.split("---", 2)[1]
        assert "disable-model-invocation: false" in text
        assert "## When to Use" in text
        assert "## Procedure" in text
        assert "python3 scripts/" in text
        assert "scripts/ts/" in text


def test_orchestrate_skill():
    p = ROOT / "skills" / "orchestrate" / "SKILL.md"
    text = p.read_text()
    fm = text.split("---", 2)[1]
    assert "name: agent-swarm-orchestrate" in fm
    assert "disable-model-invocation: false" in fm
    assert "a01-orchestrator" in text
    assert "spawn_subagent" in text
    assert "UserPromptSubmit" in text or "hook" in text.lower()
    for skill in (ROOT / "skills").rglob("*"):
        if skill.is_file():
            assert "/root/" not in skill.read_text(), skill


def test_source_skills_are_generated_hidden_and_footed():
    """Each prompts/skills source renders to a hidden omp skill carrying its generated-from footer."""
    from swarm.skill_registry import load_skills

    for s in load_skills(ROOT):
        text = (ROOT / "omp" / "skills" / s.name / "SKILL.md").read_text(encoding="utf-8")
        head = text.split("---", 2)[1]
        assert f'name: "{s.name}"' in head and "disable-model-invocation: true" in head, s.name
        assert text.rstrip().endswith(f"<!-- generated from {s.path}; edit the source and run scripts/build_agents.py -->"), s.name
        assert "/root/" not in text, s.name


def test_index_skills_stay_visible():
    """The per-agent routing skills keep their frontmatter: no hide flag, so omp still lists them."""
    agents = json.loads((ROOT / "agents.json").read_text())["agents"]
    for a in agents:
        head = (ROOT / "omp" / "skills" / a["slug"] / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        assert "disable-model-invocation" not in head and f'name: "{a["slug"]}"' in head, a["slug"]
