"""Unit + integration tests for the AgentSwarm runtime toolkit and orchestration scripts."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def swarm_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    # reload modules that cache SWARM_DIR at import time
    for m in [m for m in list(sys.modules) if m.startswith("swarm")]:
        del sys.modules[m]
    return tmp_path / ".swarm"


def run_script(name, *args, env=None):
    cmd = [sys.executable, str(ROOT / "scripts" / name), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **(env or {})}, cwd=ROOT)


# ---------------------------------------------------------------- envelope
def test_envelope_roundtrip(swarm_dir):
    from swarm.envelope import build_envelope, sign_envelope, verify_envelope, validate_envelope
    from swarm.errors import SwarmError
    env = build_envelope(source="A01@1", target="A05", msg_type="task.assign", payload={"task_id": "T-1"})
    with pytest.raises(SwarmError):
        validate_envelope(env)  # task.assign requires signature
    sign_envelope(env)
    validate_envelope(env)
    assert verify_envelope(env)
    env["payload"]["task_id"] = "T-2"
    assert not verify_envelope(env)  # tampering detected


def test_envelope_rejects_bad_fields(swarm_dir):
    from swarm.envelope import build_envelope
    from swarm.errors import SwarmError
    with pytest.raises(SwarmError):
        build_envelope(source="x", target="y", msg_type="noname", payload={})
    with pytest.raises(SwarmError):
        build_envelope(source="x", target="y", msg_type="a.b", payload={}, priority="P9")


# ---------------------------------------------------------------- task store
def test_state_machine_and_fail_closed_gates(swarm_dir):
    from swarm.taskstore import TaskStore
    from swarm.errors import SwarmError
    ts = TaskStore()
    ts.create(task_id="T-1", correlation_id="c", capability="code.backend", risk_class="high")
    with pytest.raises(SwarmError):
        ts.transition("T-1", "DONE")  # illegal
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        ts.transition("T-1", s)
    assert ts.required_gates("T-1") == ["review", "quality", "security", "release"]
    with pytest.raises(SwarmError):
        ts.transition("T-1", "APPROVED")
    for g in ("review", "quality", "security", "release"):
        ts.record_verdict("T-1", g, "pass", "A0x")
    assert ts.transition("T-1", "APPROVED")["state"] == "APPROVED"
    assert len(ts.history("T-1")) == 7  # CREATED + 5 + APPROVED


def test_rework_cap_escalates(swarm_dir):
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    ts.create(task_id="T-2", correlation_id="c", capability="code.backend", notes={"gates": ["review"]})
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        ts.transition("T-2", s)
    ts.record_verdict("T-2", "review", "fail", "A09")
    assert ts.transition("T-2", "CHANGES_REQUESTED")["state"] == "CHANGES_REQUESTED"
    assert ts.latest_verdicts("T-2") == {}  # stale after rework
    ts.transition("T-2", "IN_PROGRESS")
    ts.transition("T-2", "IN_REVIEW")
    ts.transition("T-2", "CHANGES_REQUESTED")
    ts.transition("T-2", "IN_PROGRESS")
    ts.transition("T-2", "IN_REVIEW")
    assert ts.transition("T-2", "CHANGES_REQUESTED")["state"] == "ESCALATED"


def test_gate_override_and_ready(swarm_dir):
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    ts.create(task_id="A", correlation_id="c", capability="req.spec", notes={"gates": []})
    ts.create(task_id="B", correlation_id="c", capability="code.backend", depends_on=["A"])
    assert [t["task_id"] for t in ts.ready("c")] == ["A"]
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        ts.transition("A", s)
    assert [t["task_id"] for t in ts.ready("c")] == ["B"]  # IN_REVIEW satisfies dependants


# ---------------------------------------------------------------- gates
def test_make_verdict_signed_and_derived(swarm_dir):
    from swarm.gates import make_verdict, make_finding, validate_verdict, conjunction
    from swarm.errors import SwarmError
    v = make_verdict(gate="security", task_id="T-1", agent_id="A10@1",
                     findings=[make_finding("SF-1", "critical", "secret", "AWS key")])
    assert validate_verdict(v)["verdict"] == "fail"
    v2 = make_verdict(gate="review", task_id="T-1", agent_id="A09@1", findings=[make_finding("RF-1", "minor", "style", "nit")])
    assert v2["payload"]["verdict"] == "pass"
    with pytest.raises(SwarmError):
        make_verdict(gate="quality", task_id="T-1", agent_id="A08", verdict="waive")
    assert conjunction({"review": "pass"}, ["review", "quality"]) == ("fail", ["quality:missing"])


# ---------------------------------------------------------------- manifest + generated agents
def test_manifest_consistency():
    from swarm.manifest import load_manifest
    agents = load_manifest()
    assert len(agents) == 15
    ids = [a["id"] for a in agents]
    assert ids == [f"A{i:02d}" for i in range(1, 16)]
    for a in agents:
        assert (ROOT / a["spec"]).exists(), a["spec"]
        assert (ROOT / a["prompt"]).exists(), a["prompt"]
        for s in a["scripts"]:
            assert (ROOT / s).exists(), s
        claude_agent = ROOT / ".claude" / "agents" / f"{a['slug']}.md"
        grok_agent = ROOT / ".grok" / "agents" / f"{a['slug']}.md"
        assert claude_agent.exists()
        assert grok_agent.exists()
        claude_fm = claude_agent.read_text().split("---", 2)[1]
        grok_fm = grok_agent.read_text().split("---", 2)[1]
        assert "model: inherit" in claude_fm
        assert "model: opus" not in claude_fm and "model: sonnet" not in claude_fm and "model: haiku" not in claude_fm
        assert "prompt_mode: full" in grok_fm
        assert "agents_md: true" in grok_fm
        assert "permission_mode: default" in grok_fm
    # every produced artifact has a consumer somewhere (no orphan producers)
    consumed = {c for a in agents for c in a["consumes"]}
    consumed |= {"gate.verdict", "task.result"}
    for a in agents:
        assert any(p in consumed or p.startswith(("gate.", "task.", "swarm.", "plan.", "conflict.", "escalation.")) for p in a["produces"]), a["id"]


def test_prompts_are_xml_tagged():
    import re
    for p in sorted((ROOT / "prompts").glob("A*.md")):
        text = p.read_text()
        assert re.search(r'<agent id="A\d\d"', text), p.name
        for tag in ("role", "inputs", "outputs", "output_format", "tools", "decision_logic", "autonomy",
                    "error_handling", "metrics", "security", "constraints",
                    "system_role", "scope", "out_of_scope", "workflow", "acceptance_criteria",
                    "states", "graph_of_thought", "graceful_degradation", "security_and_validation"):
            assert f"<{tag}>" in text and f"</{tag}>" in text, f"{p.name} missing <{tag}>"
        assert "<script path=" in text, p.name
        assert "python3 scripts/" in text, p.name
        assert "scripts/ts/" in text, p.name
        assert text.rstrip().endswith("</agent>"), p.name


def test_generated_agents_up_to_date():
    r = run_script("build_agents.py", "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_all_scripts_dry_run(swarm_dir):
    skip = {"build_agents.py", "swarm_run.py", "orch_status.py"}
    for script in sorted((ROOT / "scripts").glob("*.py")):
        if script.name in skip or script.name.startswith("_"):
            continue
        r = run_script(script.name, "--dry-run", "--json", env={"SWARM_DIR": str(swarm_dir)})
        assert r.returncode in (0, 1), f"{script.name}: rc={r.returncode}\n{r.stderr[-800:]}"
        assert "Traceback" not in r.stderr, script.name
        data = json.loads(r.stdout)
        assert data["status"] in ("ok", "fail"), script.name


# ---------------------------------------------------------------- orchestration end-to-end (dry run)
@pytest.mark.parametrize("pattern,n", [("feature", 13), ("hotfix", 8), ("dependency", 8)])
def test_plan_and_run_dry(swarm_dir, pattern, n):
    env = {"SWARM_DIR": str(swarm_dir)}
    r = run_script("orch_plan.py", "--brief-text", "demo", "--pattern", pattern, "--prefix", "X", "--json", env=env)
    assert r.returncode == 0, r.stderr
    assert len(json.loads(r.stdout)["tasks"]) == n
    r = run_script("swarm_run.py", "--dry-run", "--max-parallel", "4", "--json", env=env)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-800:]
    out = json.loads(r.stdout)
    assert out["complete"] and out["counts"] == {"DONE": n}
    assert (swarm_dir / "events.jsonl").exists()
    assert list((swarm_dir / "assignments").glob("X-*.md"))


def test_run_rework_ladder_escalates(swarm_dir):
    env = {"SWARM_DIR": str(swarm_dir), "SWARM_DRYRUN_FAIL": "H-patch:quality"}
    assert run_script("orch_plan.py", "--brief-text", "x", "--pattern", "hotfix", "--prefix", "H", env=env).returncode == 0
    r = run_script("swarm_run.py", "--dry-run", "--json", env=env)
    out = json.loads(r.stdout)
    assert out["escalated"] == ["H-patch"] and not out["complete"]
    st = json.loads(run_script("orch_status.py", "--history", "H-patch", "--json", env=env).stdout)
    states = [h["to_state"] for h in st["history"]]
    assert states.count("CHANGES_REQUESTED") == 3 and states[-1] == "ESCALATED"
    esc = [json.loads(ln) for ln in (swarm_dir / "events.jsonl").read_text().splitlines() if '"escalation.request"' in ln]
    assert esc and esc[0]["task_id"] == "H-patch"


def test_status_rejects_illegal_agent_report(swarm_dir, tmp_path):
    env = {"SWARM_DIR": str(swarm_dir)}
    run_script("orch_plan.py", "--brief-text", "x", "--pattern", "hotfix", "--prefix", "H", env=env)
    bad = tmp_path / "s.json"
    bad.write_text(json.dumps({"task_id": "H-rca", "state": "DONE"}))
    r = run_script("orch_status.py", "--ingest", str(bad), "--json", env=env)
    assert r.returncode == 2 and "E-CONTRACT" in r.stdout
