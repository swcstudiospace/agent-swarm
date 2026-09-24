"""CORE-04: one absolute Swarm state dir, resolved lazily (D-09..D-11)."""
import json
import os
import subprocess
import sys

from conftest import ROOT

ONE_TASK = {"tasks": [{"id": "one", "capability": "code.backend", "agent": "A05", "title": "one", "depends_on": [], "gates": []}]}


def _env_without_swarm_dir():
    return {k: v for k, v in os.environ.items() if k != "SWARM_DIR"}


def _run(script, *args, cwd, env):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args],
                          capture_output=True, text=True, cwd=cwd, env=env)


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_swarm_dir_same_from_any_cwd(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = _env_without_swarm_dir()
    brief = tmp_path / "brief.md"
    brief.write_text("build it")
    a = _run("orch_plan.py", "--root", str(repo), "--brief", str(brief), "--prefix", "A", "--json", cwd=repo, env=env)
    b = _run("orch_plan.py", "--root", str(repo), "--brief", str(brief), "--prefix", "B", "--json", cwd=elsewhere, env=env)
    assert a.returncode == 0, a.stdout + a.stderr
    assert b.returncode == 0, b.stdout + b.stderr
    sdir = (repo / ".swarm").resolve()
    assert json.loads(a.stdout)["plan_file"].startswith(str(sdir))
    assert json.loads(b.stdout)["plan_file"].startswith(str(sdir))
    assert (sdir / "tasks.db").exists() and not (elsewhere / ".swarm").exists()
    st = _run("orch_status.py", "--root", str(repo), "--history", "A-be", "--json", cwd=elsewhere, env=env)
    assert st.returncode == 0 and json.loads(st.stdout)["history"], st.stdout + st.stderr
    st = _run("orch_status.py", "--root", str(repo), "--history", "B-be", "--json", cwd=repo, env=env)
    assert st.returncode == 0 and json.loads(st.stdout)["history"], st.stdout + st.stderr
    assert (sdir / ".gitignore").read_text() == "*"
    assert not (repo / ".gitignore").exists()


def test_env_relative_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SWARM_DIR", "rel/.swarm")
    from swarm.paths import swarm_dir
    d = swarm_dir()
    assert d.is_absolute() and d == (tmp_path / "rel" / ".swarm").resolve()


def test_no_import_time_resolution(tmp_path, monkeypatch):
    import swarm.runlog as runlog
    assert not hasattr(runlog, "SWARM_DIR") and not hasattr(runlog, "LOG_FILE")
    target = tmp_path / "late"
    monkeypatch.setenv("SWARM_DIR", str(target))
    runlog.emit("x.test", {}, source="t")
    assert (target / "events.jsonl").exists()


def test_no_git_fallback(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    monkeypatch.delenv("SWARM_DIR", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    from swarm.paths import swarm_dir
    assert swarm_dir() == plain.resolve() / ".swarm"


def test_gitignore_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    (tmp_path / ".swarm").mkdir()
    (tmp_path / ".swarm" / ".gitignore").write_text("custom")
    from swarm.paths import swarm_dir
    first, second = swarm_dir(create=True), swarm_dir(create=True)
    assert first == second
    assert (tmp_path / ".swarm" / ".gitignore").read_text() == "custom"
