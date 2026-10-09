"""Ultrathink graph summary → orch_plan custom plan.

Parity with the TypeScript twin lives here, not in tests/test_ts_scripts.py, so a
parallel unit can edit that file without a merge conflict. The twin discovery test
still runs scripts/ts/orch_from_graph.ts --dry-run --json on its own.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "graph"
SUMMARY = FIXTURES / "summary.json"
SCRIPT = ROOT / "scripts" / "orch_from_graph.py"
TWIN = ROOT / "scripts" / "ts" / "orch_from_graph.ts"
GRAPH_ID = "ut-mv00tcgl-d8215d3e"
BUN = shutil.which("bun") or str(Path.home() / ".bun" / "bin" / "bun")
HAS_BUN = Path(BUN).exists()

# Fixture node order. A split is explicit: critique is always review plus security,
# and generate/refine/decompose split only when steps name files of different owners.
EXPECTED = [
    ("n1", ["A02"]),
    ("n2", ["A05"]),
    ("n4", ["A05"]),
    ("n5", ["A05", "A11"]),
    ("n6", ["A11", "A15"]),
    ("n7", ["A09", "A10"]),
    ("n3", ["A05"]),
    ("n8", ["A01"]),
]
CAPABILITY = {
    "A01": "plan.decompose",
    "A02": "req.spec",
    "A03": "design.blueprint",
    "A05": "code.backend",
    "A06": "code.frontend",
    "A07": "data.migration",
    "A09": "review.code",
    "A10": "sec.threatmodel",
    "A11": "iac.change",
    "A15": "docs.bundle",
}
MEDIUM = {"A05", "A06", "A07", "A11"}


def _env(tmp_path: Path) -> dict:
    env = os.environ.copy()
    env["SWARM_DIR"] = str(tmp_path / "events")
    for key in list(env):
        if key.startswith("SUBSTRATE"):
            env.pop(key)
    return env


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--json"],
        cwd=ROOT, capture_output=True, text=True, env=_env(tmp_path),
    )


def _plan(tmp_path: Path, summary: Path | None = None, **extra) -> dict:
    out = tmp_path / "plan.json"
    args = ["--summary", str(summary or SUMMARY), "--out", str(out)]
    proc = _run(tmp_path, *args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    written = json.loads(out.read_text(encoding="utf-8"))
    reported = json.loads(proc.stdout)
    assert reported["status"] == "ok"
    assert reported["agent"] == "A01"
    assert reported["script"] == "orch_from_graph"
    assert reported["plan"] == written
    return written


def _node(node_id: str, kind: str, steps: list[str], depends_on: list[str] | None = None, title: str = "t") -> dict:
    return {"id": node_id, "kind": kind, "title": title, "depends_on": depends_on or [], "steps": steps}


def _write_summary(tmp_path: Path, nodes: list[dict], **extra) -> Path:
    body = {"graphId": GRAPH_ID, "nodes": nodes, **extra}
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _task_ids(node_id: str, agents: list[str]) -> list[str]:
    if len(agents) == 1:
        return [node_id]
    return [f"{node_id}-{agent.lower()}" for agent in agents]


def test_fixture_is_the_pinned_summary():
    data = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert data["graphId"] == GRAPH_ID
    assert data["totalSteps"] == 58
    assert sum(len(node["steps"]) for node in data["nodes"]) == 58
    assert [node["id"] for node in data["nodes"]] == [node_id for node_id, _ in EXPECTED]


def test_fixture_converts_to_one_task_per_node_plus_explicit_splits(tmp_path):
    plan = _plan(tmp_path)
    raw = SUMMARY.read_bytes()
    assert plan["graph_id"] == GRAPH_ID
    assert plan["summary_sha256"] == hashlib.sha256(raw).hexdigest()
    assert plan["summary_sha256"] == "53bac5fdccefe4413c9fc073ab44c29cf3cdacf8411581bbe2968ae2e50bab5f"
    tasks = plan["tasks"]
    assert len(tasks) == 11
    assert len(tasks) < 58

    expected_ids = [tid for node_id, agents in EXPECTED for tid in _task_ids(node_id, agents)]
    assert [task["id"] for task in tasks] == expected_ids

    manifest = {agent["id"]: agent for agent in json.loads((ROOT / "agents.json").read_text())["agents"]}
    assert len(manifest) == 15
    by_node: dict[str, list[dict]] = {}
    for task in tasks:
        by_node.setdefault(task["node_id"], []).append(task)
        assert task["agent"] in manifest
        assert task["capability"] in manifest[task["agent"]]["capabilities"]
        assert task["capability"] == CAPABILITY[task["agent"]]
        risk = "medium" if task["agent"] in MEDIUM else "low"
        assert task["risk_class"] == risk
        assert task["gates"] == (["review", "quality"] if risk == "medium" else ["review"])
        assert task["acceptance"]
    assert list(by_node) == [node_id for node_id, _ in EXPECTED]
    for node_id, agents in EXPECTED:
        assert [task["agent"] for task in by_node[node_id]] == agents

    # understand and synthesize keep a single task even when a step names a file.
    n1 = by_node["n1"][0]
    assert n1["agent"] == "A02" and any("herdr.ts" in step for step in n1["acceptance"])
    assert len(n1["acceptance"]) == 7
    n8 = by_node["n8"][0]
    assert n8["agent"] == "A01" and any("core.py" in step for step in n8["acceptance"])
    assert len(n8["acceptance"]) == 7

    n5 = {task["agent"]: task for task in by_node["n5"]}
    assert any("doctor-units.ts" in step for step in n5["A05"]["acceptance"])
    assert any("deploy-release.sh" in step for step in n5["A11"]["acceptance"])
    assert not any("deploy-release.sh" in step for step in n5["A05"]["acceptance"])

    assert by_node["n3"][0]["depends_on"] == ["n2"]
    assert by_node["n8"][0]["depends_on"] == [
        "n3", "n4", "n5-a05", "n5-a11", "n6-a11", "n6-a15", "n7-a09", "n7-a10",
    ]
    for task in by_node["n5"] + by_node["n7"]:
        assert task["depends_on"] == ["n1"]


def test_separate_owned_files_split_and_prose_stays_with_the_architect(tmp_path):
    split = _write_summary(tmp_path, [
        _node("n1", "generate", ["Edit src/api.py", "Edit docs/GUIDE.md"], title="Two owners"),
    ])
    plan = _plan(tmp_path, split)
    assert [(t["id"], t["agent"], t["capability"]) for t in plan["tasks"]] == [
        ("n1-a05", "A05", "code.backend"),
        ("n1-a15", "A15", "docs.bundle"),
    ]
    by_agent = {t["agent"]: t for t in plan["tasks"]}
    assert by_agent["A05"]["acceptance"] == ["Edit src/api.py"]
    assert by_agent["A15"]["acceptance"] == ["Edit docs/GUIDE.md"]

    prose = _write_summary(tmp_path, [_node("d1", "decompose", ["Describe the boundary"])])
    assert [(t["id"], t["agent"]) for t in _plan(tmp_path, prose)["tasks"]] == [("d1", "A03")]

    one = _write_summary(tmp_path, [_node("r1", "refine", ["Change src/only.py", "Run the unit tests"])])
    tasks = _plan(tmp_path, one)["tasks"]
    assert [(t["id"], t["agent"]) for t in tasks] == [("r1", "A05")]
    assert tasks[0]["acceptance"] == ["Change src/only.py", "Run the unit tests"]

    understand = _write_summary(tmp_path, [_node("u1", "understand", ["Read src/api.py and write the spec"])])
    assert [(t["id"], t["agent"]) for t in _plan(tmp_path, understand)["tasks"]] == [("u1", "A02")]

    synth = _write_summary(tmp_path, [
        _node("u1", "understand", ["Pin the contract"]),
        _node("s1", "synthesize", ["Ship src/api.py"], depends_on=["u1"]),
    ])
    tasks = _plan(tmp_path, synth)["tasks"]
    assert [(t["id"], t["agent"]) for t in tasks] == [("u1", "A02"), ("s1", "A01")]
    assert tasks[1]["depends_on"] == ["u1"]


def test_downstream_depends_on_every_split(tmp_path):
    path = _write_summary(tmp_path, [
        _node("n1", "generate", ["Edit src/api.py", "Edit docs/GUIDE.md"]),
        _node("n2", "synthesize", ["Order the work"], depends_on=["n1"]),
    ])
    tasks = {t["id"]: t for t in _plan(tmp_path, path)["tasks"]}
    assert tasks["n2"]["depends_on"] == ["n1-a05", "n1-a15"]
    assert tasks["n1-a05"]["depends_on"] == []
    assert tasks["n1-a15"]["depends_on"] == []


def test_orch_plan_accepts_the_fixture_plan(tmp_path):
    out = tmp_path / "plan.json"
    proc = _run(tmp_path, "--summary", str(SUMMARY), "--out", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    env = _env(tmp_path)
    env["SWARM_DIR"] = str(repo / ".swarm")
    planned = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "orch_plan.py"),
         "--repo", str(repo), "--pattern", "custom", "--plan", str(out),
         "--graph-id", GRAPH_ID, "--prefix", "G", "--json"],
        cwd=repo, capture_output=True, text=True, env=env,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    body = json.loads(planned.stdout)
    assert body["status"] == "ok"
    assert body["pattern"] == "custom"
    assert len(body["tasks"]) == 11
    agents = {task["task_id"]: task["agent_id"] for task in body["tasks"]}
    assert agents["G-n1"] == "A02"
    assert agents["G-n8"] == "A01"
    assert agents["G-n7-a09"] == "A09"
    assert agents["G-n7-a10"] == "A10"
    assert agents["G-n5-a11"] == "A11"
    stored = json.loads(out.read_text(encoding="utf-8"))
    assert stored["summary_sha256"] == hashlib.sha256(SUMMARY.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", ["cycle.json", "unknown_kind.json", "bad_id.json"])
def test_malformed_fixtures_are_refused(tmp_path, name):
    out = tmp_path / "plan.json"
    proc = _run(tmp_path, "--summary", str(FIXTURES / name), "--out", str(out))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    body = json.loads(proc.stdout)
    assert body["status"] == "error"
    assert body["error"]["code"] == "E-INPUT"
    assert not out.exists()


def test_unknown_dependency_total_steps_and_wave_order_are_refused(tmp_path):
    missing = _write_summary(tmp_path, [_node("n1", "understand", ["Pin it"], depends_on=["n9"])])
    proc = _run(tmp_path, "--summary", str(missing), "--out", str(tmp_path / "a.json"))
    assert proc.returncode == 2 and json.loads(proc.stdout)["error"]["code"] == "E-INPUT"

    mismatch = _write_summary(tmp_path, [_node("n1", "understand", ["Pin it"])], totalSteps=3)
    proc = _run(tmp_path, "--summary", str(mismatch), "--out", str(tmp_path / "b.json"))
    assert proc.returncode == 2 and "totalSteps" in json.loads(proc.stdout)["error"]["message"]

    waves = [{"wave": 1, "parallel": False, "ids": ["n2"]}, {"wave": 2, "parallel": False, "ids": ["n1"]}]
    inverted = _write_summary(tmp_path, [
        _node("n1", "understand", ["Pin it"]),
        _node("n2", "generate", ["Edit src/api.py"], depends_on=["n1"]),
    ], waves=waves)
    proc = _run(tmp_path, "--summary", str(inverted), "--out", str(tmp_path / "c.json"))
    assert proc.returncode == 2 and json.loads(proc.stdout)["error"]["code"] == "E-INPUT"
    assert not (tmp_path / "c.json").exists()


def test_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "plan.json"
    bare = _run(tmp_path, "--dry-run")
    assert bare.returncode == 0, bare.stdout + bare.stderr
    body = json.loads(bare.stdout)
    assert body["status"] == "ok"
    assert body["agent"] == "A01" and body["script"] == "orch_from_graph"

    preview = _run(tmp_path, "--dry-run", "--summary", str(SUMMARY), "--out", str(out))
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert json.loads(preview.stdout)["plan"]["graph_id"] == GRAPH_ID
    assert not out.exists()


def test_out_of_order_dependencies_load_in_orch_plan(tmp_path):
    path = _write_summary(tmp_path, [
        _node("n2", "generate", ["Edit src/api.py"], depends_on=["n1"], title="After"),
        _node("n1", "understand", ["Pin the contract"], title="Before"),
    ])
    out = tmp_path / "plan.json"
    proc = _run(tmp_path, "--summary", str(path), "--out", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tasks = json.loads(out.read_text(encoding="utf-8"))["tasks"]
    assert [task["id"] for task in tasks] == ["n1", "n2"]
    assert tasks[1]["depends_on"] == ["n1"]
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    env = _env(tmp_path)
    env["SWARM_DIR"] = str(repo / ".swarm")
    planned = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "orch_plan.py"),
         "--repo", str(repo), "--pattern", "custom", "--plan", str(out),
         "--graph-id", GRAPH_ID, "--prefix", "G", "--json"],
        cwd=repo, capture_output=True, text=True, env=env,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    assert len(json.loads(planned.stdout)["tasks"]) == 2


def test_mixed_owner_step_names_only_that_agents_files(tmp_path):
    path = _write_summary(tmp_path, [
        _node("n1", "generate", ["Edit src/api.py and docs/GUIDE.md"]),
    ])
    tasks = {task["agent"]: task for task in _plan(tmp_path, path)["tasks"]}
    assert tasks["A05"]["acceptance"] == ["Edit src/api.py"]
    assert tasks["A15"]["acceptance"] == ["Edit docs/GUIDE.md"]


def test_node_id_with_parent_segment_is_refused(tmp_path):
    path = _write_summary(tmp_path, [_node("a..b", "understand", ["Pin it"])])
    out = tmp_path / "plan.json"
    proc = _run(tmp_path, "--summary", str(path), "--out", str(out))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["error"]["code"] == "E-INPUT"
    assert ".." in json.loads(proc.stdout)["error"]["message"]
    assert not out.exists()


def test_deep_chain_and_cycle_stay_within_e_input(tmp_path):
    chain = [
        _node(f"n{i}", "understand", ["Pin it"], depends_on=[] if i == 0 else [f"n{i - 1}"])
        for i in range(1199, -1, -1)
    ]
    plan = _plan(tmp_path, _write_summary(tmp_path, chain))
    assert [task["id"] for task in plan["tasks"]] == [f"n{i}" for i in range(1200)]

    cycle = [
        _node(f"c{i}", "understand", ["Pin it"], depends_on=[f"c{(i + 1) % 1200}"])
        for i in range(1200)
    ]
    proc = _run(tmp_path, "--summary", str(_write_summary(tmp_path, cycle)), "--out", str(tmp_path / "cycle.json"))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    body = json.loads(proc.stdout)
    assert body["error"]["code"] == "E-INPUT"
    assert "RecursionError" not in proc.stderr
    assert not (tmp_path / "cycle.json").exists()


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_twins_agree_on_relative_paths_outside_the_checkout(tmp_path):
    (tmp_path / "summary.json").write_bytes(SUMMARY.read_bytes())
    env = _env(tmp_path)
    py = subprocess.run(
        [sys.executable, str(SCRIPT), "--summary", "summary.json", "--out", "py.json", "--json"],
        cwd=tmp_path, capture_output=True, text=True, env=env,
    )
    ts = subprocess.run(
        [BUN, str(TWIN), "--summary", "summary.json", "--out", "ts.json", "--json"],
        cwd=tmp_path, capture_output=True, text=True, env=env,
    )
    assert py.returncode == 0, py.stdout + py.stderr
    assert ts.returncode == 0, ts.stdout + ts.stderr
    assert (tmp_path / "py.json").read_bytes() == (tmp_path / "ts.json").read_bytes()
    assert not (ROOT / "py.json").exists()
    assert not (ROOT / "ts.json").exists()


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_ts_twin_plan_is_byte_identical(tmp_path):
    py_out = tmp_path / "py.json"
    ts_out = tmp_path / "ts.json"
    env = _env(tmp_path)
    py = subprocess.run(
        [sys.executable, str(SCRIPT), "--summary", str(SUMMARY), "--out", str(py_out), "--json"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    ts = subprocess.run(
        [BUN, str(TWIN), "--summary", str(SUMMARY), "--out", str(ts_out), "--json"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert py.returncode == 0, py.stdout + py.stderr
    assert ts.returncode == 0, ts.stdout + ts.stderr
    assert py_out.read_bytes() == ts_out.read_bytes()
