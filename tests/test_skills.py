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
