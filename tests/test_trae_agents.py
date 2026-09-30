"""The Trae bundle is a reproducible UI registration kit, not a hidden API."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("build_trae_agents", ROOT / "scripts/build_trae_agents.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_all_roles_are_valid_compact_xml():
    agents = builder.load_agents()
    assert [a["id"] for a in agents] == [f"A{i:02}" for i in range(1, 16)]
    for agent in agents:
        text = builder.render_agent(agent, agents)
        assert len(text) < 10_000
        root = ET.fromstring(text)
        assert root.tag == "agent"
        assert root.attrib["identifier"] == agent["slug"]
        for tag in ("role", "runtime", "inputs", "outputs", "workflow", "safety", "output_format"):
            assert root.findtext(tag), (agent["id"], tag)
        assert "SOLO" in root.findtext("runtime")
        assert "Do not invoke other agents" in root.findtext("runtime")
        assert "human approval" in root.findtext("safety")
        assert "swarm_run.py" not in root.findtext("tools")


def test_coordinator_covers_all_roles_and_returns_dispatch():
    agents = builder.load_agents()
    root = ET.fromstring(builder.render_agent(agents[0], agents))
    roster = root.find("roster")
    assert [a.attrib["identifier"] for a in roster] == [a["slug"] for a in agents[1:]]
    output = json.loads(root.findtext("output_format").split("```json\n")[1].split("\n```")[0])
    assert output["type"] == "swarm.dispatch"
    assert {"assignments", "tasks", "role_coverage", "blocked", "complete"} <= output.keys()
    flow = root.findtext("workflow")
    assert "max two rework cycles" in flow
    assert "revision" in flow
    assert "disjoint" in flow
    assert "not_applicable" in flow
    assert "A12" in flow and "A13" in flow and "A14" in flow


def test_specialist_result_has_identity_evidence_and_gate_fields():
    agents = builder.load_agents()
    for agent in agents[1:]:
        root = ET.fromstring(builder.render_agent(agent, agents))
        text = root.findtext("output_format")
        result = json.loads(text.split("```json\n")[1].split("\n```")[0])
        assert result["agent_id"] == agent["id"]
        assert result["type"] == "task.result"
        assert {"task_id", "correlation_id", "revision", "artifacts", "checks", "needs", "gate_verdicts"} <= result.keys()
        assert result["state"] == "BLOCKED"
        assert result["gate_verdicts"] == []
        assert "Never approve your own work" in root.findtext("safety")


def test_source_text_is_xml_escaped(monkeypatch):
    agents = builder.load_agents()
    original = builder.section
    monkeypatch.setattr(builder, "section", lambda text, tag: "A & B < C" if tag == "role" else original(text, tag))
    root = ET.fromstring(builder.render_agent(agents[1], agents))
    assert root.findtext("role").strip() == "A & B < C"


def test_oversize_prompt_is_rejected_not_truncated(monkeypatch):
    agents = builder.load_agents()
    monkeypatch.setattr(builder, "section", lambda text, tag: "x" * 10_000)
    with pytest.raises(ValueError, match="10,000"):
        builder.render_agent(agents[1], agents)


def test_missing_source_section_fails():
    with pytest.raises(ValueError, match="role"):
        builder.section("<agent/>", "role")


def test_bundle_check_is_read_only_and_detects_drift(tmp_path):
    output = tmp_path / "new"
    assert builder.write_bundle(output, check=True) == 1
    assert not output.exists()
    assert builder.write_bundle(output, check=False) == 0
    assert builder.write_bundle(output, check=True) == 0
    prompt = output / "agents/a05-backend.md"
    prompt.write_text("user edit", encoding="utf-8")
    assert builder.write_bundle(output, check=True) == 1
    assert prompt.read_text() == "user edit"
    registry = json.loads((output / "registration.json").read_text())
    assert registry["registration"] == "manual-ui"
    assert len(registry["agents"]) == 15
    for agent in registry["agents"]:
        assert agent["callable_by_other_agents"] is True
        assert (output / agent["prompt_file"]).exists()
    command = (output / "commands/swarm.md").read_text()
    assert len(command) < 10_000
    assert "a01-orchestrator" in command
    assert "Do not impersonate" in command
    assert "L1" in command and "Plan/Spec" in command
    assert "registration.json" in command


def test_dry_run_validates_without_writes(tmp_path, capsys):
    output = tmp_path / "not-created"
    assert builder.write_bundle(output, check=False, dry_run=True, json_output=True) == 0
    assert not output.exists()
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["status"] == "ok"
    assert len(report["changed"]) == 18
    assert report["agent"] == "A01"
    assert report["script"] == "build_trae_agents"


def test_committed_bundle_matches_generator():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_trae_agents.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
