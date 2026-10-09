"""Parallel blast-radius lanes: same agent, disjoint worktrees, one Greptile review after the join."""
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from conftest import ROOT, run_script

from swarm.blast_radius import merge_branches, normalize_slices, parallel_rows, prepare_slice_worktree
from swarm.errors import SwarmError
from swarm.taskstore import TaskStore


def _runner():
    spec = importlib.util.spec_from_file_location("swarm_run_blast", ROOT / "scripts" / "swarm_run.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/swarm_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "swarm@localhost")
    _git(repo, "config", "user.name", "agent-swarm")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    return repo


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", name)


BRIEF = """
Ship the API and the CLI as two lanes.

```blast-radii
[
  {"id": "api", "paths": ["swarm/"], "agent": "A05", "title": "Task store lane"},
  {"id": "cli", "paths": ["scripts/"], "agent": "A05", "title": "CLI lane"}
]
```
"""


def test_parallel_plan_repeats_one_agent_and_reviews_once_after_join(tmp_path):
    env = {"SWARM_DIR": str(tmp_path / ".swarm")}
    first = run_script("orch_plan.py", "--brief-text", BRIEF, "--pattern", "feature", "--prefix", "X",
                       "--risk-class", "medium", "--json", env=env)
    assert first.returncode == 0, first.stderr + first.stdout
    plan = json.loads(first.stdout)
    assert plan["pattern"] == "parallel"
    by_id = {task["task_id"]: task for task in plan["tasks"]}
    assert set(by_id) == {"X-api", "X-cli", "X-join", "X-rev", "X-qa"}
    for lane in ("X-api", "X-cli"):
        notes = by_id[lane]["notes"]
        assert by_id[lane]["agent_id"] == "A05" and by_id[lane]["depends_on"] == []
        assert notes["isolate"] == "worktree" and notes["blast_radius"]
        assert notes["branch"] == f"swarm/X/{lane.removeprefix('X-')}"
        assert "gates" not in notes  # the slice keeps the risk-class gates, satisfied by the post-merge review
    assert by_id["X-join"]["agent_id"] == "A11"
    assert by_id["X-join"]["depends_on"] == ["X-api", "X-cli"]
    assert by_id["X-join"]["notes"]["merge_branches"] == ["swarm/X/api", "swarm/X/cli"]
    for gate in ("X-rev", "X-qa"):
        assert by_id[gate]["depends_on"] == ["X-join"]
        assert by_id[gate]["notes"]["gate_for"] == ["X-api", "X-cli"]
    assert by_id["X-rev"]["notes"]["review_source"] == "greptile"
    assert "X-sec" not in by_id and "X-rel" not in by_id

    again = run_script("orch_plan.py", "--brief-text", BRIEF, "--pattern", "feature", "--prefix", "X",
                       "--risk-class", "medium", "--json", env=env)
    assert json.loads(again.stdout)["reused"] is True

    dry = run_script("swarm_run.py", "--dry-run", "--max-parallel", "3", "--json", env=env)
    assert dry.returncode == 0, dry.stdout[-1500:] + dry.stderr[-800:]
    out = json.loads(dry.stdout)
    assert out["complete"] and out["counts"] == {"DONE": 5}
    assignments = list((tmp_path / ".swarm" / "assignments").glob("X-*.md"))
    text = {path.name.split(".a")[0]: path.read_text(encoding="utf-8") for path in assignments}
    assert "You are one replica of your agent" in text["X-api"]
    assert "swarm/X/api" in text["X-api"] and "swarm/" in text["X-api"]
    assert "Greptile review" in text["X-rev"]
    assert "swarm/X/cli" in text["X-join"]
    assert not (tmp_path / ".swarm" / "worktrees").exists()


def test_high_risk_adds_security_and_release_once():
    rows = parallel_rows(normalize_slices([
        {"id": "api", "paths": ["swarm"], "agent": "A05"},
        {"id": "ui", "paths": ["omp"], "agent": "A06"},
    ]), "high")
    suffixes = [row[0] for row in rows]
    assert suffixes == ["api", "ui", "join", "rev", "qa", "sec", "rel"]
    release = rows[-1]
    assert release[4] == ["rev", "qa", "sec"]
    assert parallel_rows(normalize_slices([
        {"id": "api", "paths": ["swarm"], "agent": "a05-backend"},
        {"id": "docs", "paths": ["docs"], "agent": "A15"},
    ]), "low")[3][0] == "rev"


def test_overlapping_and_single_radii_are_refused(tmp_path):
    overlap = '[{"id":"a","paths":["swarm"],"agent":"A05"},{"id":"b","paths":["swarm/taskstore.py"],"agent":"A05"}]'
    refused = run_script("orch_plan.py", "--brief-text", "x", "--slices-json", overlap, "--prefix", "Z", "--json",
                         env={"SWARM_DIR": str(tmp_path / "a")})
    assert refused.returncode == 2 and "overlap" in refused.stdout
    one = run_script("orch_plan.py", "--brief-text", "x", "--slices-json",
                     '[{"id":"only","paths":["swarm"],"agent":"A05"}]', "--prefix", "Z", "--json",
                     env={"SWARM_DIR": str(tmp_path / "b")})
    assert one.returncode == 2 and "at least two" in one.stdout


def test_feature_without_radii_stays_thirteen_tasks(tmp_path):
    result = run_script("orch_plan.py", "--brief-text", "demo", "--pattern", "feature", "--prefix", "F", "--json",
                        env={"SWARM_DIR": str(tmp_path / ".swarm")})
    body = json.loads(result.stdout)
    assert result.returncode == 0 and body["pattern"] == "feature" and len(body["tasks"]) == 13


def test_same_agent_fills_several_slots_until_its_ceiling(tmp_path):
    runner = _runner()
    store = TaskStore(tmp_path / "tasks.db")
    ready = [{"task_id": f"T-{i}", "agent_id": "A05", "capability": "code.backend"} for i in range(3)]
    batch, waiting = runner.select_batch(store, ready, 3, None)
    assert [task["task_id"] for task, _ in batch] == ["T-0", "T-1", "T-2"] and waiting == []

    mixed = [{"task_id": f"R-{i}", "agent_id": "A12", "capability": "release.plan"} for i in range(3)]
    mixed.append({"task_id": "R-be", "agent_id": "A05", "capability": "code.backend"})
    batch, waiting = runner.select_batch(store, mixed, 5, None)
    assert [task["task_id"] for task, _ in batch] == ["R-0", "R-1", "R-be"] and waiting == []


def test_lanes_get_worktrees_and_the_join_merges_them(tmp_path):
    repo = _repo(tmp_path)
    api = prepare_slice_worktree(repo, tmp_path / "wt-api", "swarm/X/api")
    cli = prepare_slice_worktree(repo, tmp_path / "wt-cli", "swarm/X/cli")
    assert api != cli
    _commit(api, "api.txt", "api\n")
    _commit(cli, "cli.txt", "cli\n")
    merge_branches(repo, ["swarm/X/api", "swarm/X/cli"])
    assert (repo / "api.txt").read_text(encoding="utf-8") == "api\n"
    assert (repo / "cli.txt").read_text(encoding="utf-8") == "cli\n"

    clash = _repo(tmp_path / "clash")
    left = prepare_slice_worktree(clash, tmp_path / "clash-a", "swarm/X/a")
    right = prepare_slice_worktree(clash, tmp_path / "clash-b", "swarm/X/b")
    _commit(left, "same.txt", "left\n")
    _commit(right, "same.txt", "right\n")
    merge_branches(clash, ["swarm/X/a"])
    try:
        merge_branches(clash, ["swarm/X/b"])
    except SwarmError as exc:
        assert "cannot merge" in str(exc)
    else:
        raise AssertionError("conflicting lane merged")
    assert not (clash / ".git" / "MERGE_HEAD").exists()
    assert (clash / "same.txt").read_text(encoding="utf-8") == "left\n"


def test_runner_join_merges_without_a_model(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _commit(repo, "keep.txt", "keep\n")
    api = prepare_slice_worktree(repo, tmp_path / "lane-api", "swarm/X/api")
    cli = prepare_slice_worktree(repo, tmp_path / "lane-cli", "swarm/X/cli")
    _commit(api, "api.txt", "api\n")
    _commit(cli, "cli.txt", "cli\n")
    store = TaskStore(tmp_path / "tasks.db")
    store.create(task_id="X-join", correlation_id="corr", capability="ci.pipeline", title="join", agent_id="A11",
                 notes={"role": "join", "merge_branches": ["swarm/X/api", "swarm/X/cli"], "gates": []})
    store.transition("X-join", "VALIDATED")
    store.transition("X-join", "PLANNED")
    runner = _runner()
    monkeypatch.setattr(runner, "run_agent_headless", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model ran")))
    args = SimpleNamespace(dry_run=False, task_timeout=30, runtime="claude", claude_bin="claude", grok_bin="grok",
                           omp_bin="omp", permission_mode="acceptEdits", max_turns=1, model="", allowed_tools="")
    events = []
    outcome = runner.execute_one(store.path, store.get("X-join"), {"id": "A11", "slug": "a11-devops"}, args,
                                 SimpleNamespace(emit=lambda *a, **k: events.append(a)), repo, None)
    assert outcome[2] == "IN_REVIEW"
    assert (repo / "api.txt").exists() and (repo / "cli.txt").exists() and (repo / "keep.txt").exists()
