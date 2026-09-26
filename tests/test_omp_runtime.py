"""RUN-01..03 (Phase 6): `swarm_run.py --runtime omp` — the omp argv, the JSONL-stream normaliser, per-runtime
preflight, the --dry-run invocation print, and parity with the in-session ingest path."""
import importlib.util
import json
import os
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import ROOT, omp_calls, omp_grandchild, run_script, stub_claude, stub_omp

FIXTURE = ROOT / "tests" / "fixtures" / "omp_agent_end.jsonl"
KEY_VARS = ("SWARM_SIGNING_KEY", "SWARM_ED25519_KEY", "SWARM_REQUIRE_KEY")
_ENV_NOISE = (*KEY_VARS, "SWARM_AGENT_SESSION", "SWARM_CHILD", "SWARM_AGENT", "SWARM_TASK_ID", "SWARM_CORRELATION_ID",
              "SWARM_DRYRUN_FAIL", "SWARM_RUNTIME", "ANTHROPIC_API_KEY")
RESULT = {"task_id": "T-one", "state": "IN_REVIEW", "outputs": [{"kind": "code.backend", "uri": "file://x", "version": "1", "digest": ""}],
          "metrics": {"tests": 3}, "summary_md": "ok"}
ONE_TASK = {"tasks": [{"id": "one", "capability": "code.backend", "agent": "A05", "title": "one", "depends_on": [], "gates": []}]}


@pytest.fixture()
def sr(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    spec = importlib.util.spec_from_file_location("swarm_run_omp_under_test", ROOT / "scripts" / "swarm_run.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env(swarm, **extra) -> dict:
    return {**{k: v for k, v in os.environ.items() if k not in _ENV_NOISE}, "SWARM_DIR": str(swarm), **extra}


def _plan(tmp_path, env, plan=ONE_TASK, *extra) -> Path:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    f = tmp_path / "plan.json"
    f.write_text(json.dumps(plan))
    r = run_script("orch_plan.py", "--plan", str(f), "--prefix", "T", "--repo", str(work), "--json", *extra, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    return work


def _history(env, tid):
    return [h["to_state"] for h in json.loads(run_script("orch_status.py", "--history", tid, "--json", env=env).stdout)["history"]]


def _states(swarm) -> dict:
    con = sqlite3.connect(swarm / "tasks.db")
    states = dict(con.execute("SELECT task_id, state FROM tasks"))
    con.close()
    return states


def _stream(*events) -> str:
    return "".join((e if isinstance(e, str) else json.dumps(e)) + "\n" for e in events)


def _asst(text, stop="stop", cost=0.0):
    return {"role": "assistant", "content": [{"type": "text", "text": text}], "stopReason": stop,
            "usage": {"cost": {"total": cost}}}


def _yield_result(data=None, *, status="success", is_error=False, error=None):
    details = {"status": status, **({"data": data} if data is not None else {}), **({"error": error} if error else {})}
    return {"role": "toolResult", "toolName": "yield", "isError": is_error, "details": details}


def _end(*messages, terminal=True):
    return {"type": "agent_end", "messages": [{"role": "user", "content": "go"}, *messages], "isTerminal": terminal}


def _block(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


# ---------------------------------------------------------------- (a) omp_stream_text
def test_fixture_stream_yields_payload(sr):
    from swarm.results import parse_result
    text, meta = sr.omp_stream_text(FIXTURE.read_text())
    assert parse_result(text) == {"task_id": "T-p6-1", "state": "IN_REVIEW", "summary_md": "probe", "outputs": [], "findings": []}
    assert meta == {"session_id": "sess-p7", "total_cost_usd": pytest.approx(0.00356), "num_turns": 1, "is_error": False}


def test_yield_payload_wins_over_text_json(sr):
    from swarm.results import parse_result
    text, meta = sr.omp_stream_text(_stream(_end(_asst("summary\n" + _block({"task_id": "T-1", "state": "FAILED"}), "toolUse"),
                                                 _yield_result({"task_id": "T-1", "state": "IN_REVIEW"}))))
    assert parse_result(text) == {"task_id": "T-1", "state": "IN_REVIEW"}
    assert meta["is_error"] is False and "yield_error" not in meta


def test_text_json_fallback_without_yield(sr):
    from swarm.results import parse_result
    text, meta = sr.omp_stream_text(_stream(_end(_asst("done\n" + _block({"task_id": "T-1", "state": "IN_REVIEW"})))))
    assert parse_result(text) == {"task_id": "T-1", "state": "IN_REVIEW"}
    assert meta["is_error"] is False and "yield_error" not in meta


def test_last_terminal_agent_end_wins(sr):
    from swarm.results import parse_result
    text, meta = sr.omp_stream_text(_stream(
        {"type": "turn_end"}, _end(_asst("x"), _yield_result({"task_id": "T-1", "state": "FAILED"}), terminal=False),
        {"type": "turn_end"}, _end(_asst("y", cost=0.5), _yield_result({"task_id": "T-1", "state": "IN_REVIEW"}))))
    assert parse_result(text) == {"task_id": "T-1", "state": "IN_REVIEW"}
    assert meta["num_turns"] == 2 and meta["total_cost_usd"] == 0.5


def test_only_non_terminal_agent_end_is_error(sr):
    text, meta = sr.omp_stream_text(_stream(_end(_asst("x"), _yield_result({"task_id": "T-1", "state": "IN_REVIEW"}), terminal=False)))
    assert text == ""
    assert meta["is_error"] is True


@pytest.mark.parametrize("stop", ["error", "aborted"])
def test_error_stop_reason_is_error(sr, stop):
    _, meta = sr.omp_stream_text(_stream(_end(_asst("provider failed", stop))))
    assert meta["is_error"] is True


def test_non_json_noise_is_skipped(sr):
    from swarm.results import parse_result
    text, meta = sr.omp_stream_text(_stream("warning: something", {"type": "session", "id": "s1"}, "[1, 2]", "{not json",
                                            _end(_asst("ok"), _yield_result({"task_id": "T-1", "state": "IN_REVIEW"})),
                                            "trailing noise"))
    assert parse_result(text) == {"task_id": "T-1", "state": "IN_REVIEW"}
    assert meta["session_id"] == "s1" and meta["is_error"] is False


def test_claude_shaped_stdout_is_error(sr):
    text, meta = sr.omp_stream_text('{"result":"ok"}')
    assert text == "" and meta["is_error"] is True


def test_yield_error_adds_no_block(sr):
    from swarm.results import parse_result
    text, meta = sr.omp_stream_text(_stream(_end(_asst("gave up", "toolUse"),
                                                 _yield_result(status="aborted", error="cannot reach the repo"))))
    assert parse_result(text) is None  # → E-CONTRACT reject, task FAILED
    assert meta["yield_error"] == "cannot reach the repo"


# ---------------------------------------------------------------- headless_command (omp)
def test_omp_command_shape(sr, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SIGNING_KEY", "secret")
    args = type("A", (), {"omp_bin": "/opt/omp", "model": "@smol", "task_timeout": 90})()
    sdir = tmp_path / ".swarm"
    argv, env, cwd = sr.headless_command("omp", {"slug": "a12-release", "id": "A12"}, tmp_path, sdir, args)
    assert argv[:7] == ["/opt/omp", "-p", "--mode", "json", "--no-session", "--no-title", "--no-extensions"]
    opt = dict(zip(argv[7::2], argv[8::2]))
    assert opt == {"-e": str(ROOT / "omp"), "--cwd": str(tmp_path), "--approval-mode": "yolo",
                   "--tools": "read,grep,glob,bash,write,swarm_gate,yield", "--append-system-prompt": str(sdir / "agents" / "a12-release.md"),
                   "--max-time": "81s", "--model": "@smol"}
    body = (sdir / "agents" / "a12-release.md").read_text()
    assert not body.startswith("---") and "<swarm_runtime>" in body
    assert cwd == tmp_path and "SWARM_SIGNING_KEY" not in env and env["SWARM_AGENT"] == "a12-release"


def test_omp_command_missing_body_is_e_dep(sr, tmp_path):
    from swarm.errors import SwarmError
    args = type("A", (), {"model": "", "task_timeout": 1800})()
    with pytest.raises(SwarmError) as e:
        sr.headless_command("omp", {"slug": "a99-nobody", "id": "A99"}, tmp_path, tmp_path / ".swarm", args)
    assert e.value.code.value == "E-DEP"


@pytest.mark.parametrize("timeout", [2, 5, 6, 30, 59, 60, 61, 90, 119, 120, 121, 600, 1800, 7200])
def test_omp_max_time_below_task_timeout(sr, tmp_path, timeout):
    """WR-03: omp's own --max-time ends the session before the runner's kill, with a margin of at most 60 s."""
    args = type("A", (), {"omp_bin": "omp", "model": "", "task_timeout": timeout})()
    argv, _, _ = sr.headless_command("omp", {"slug": "a05-backend", "id": "A05"}, tmp_path, tmp_path / ".swarm", args)
    max_time = argv[argv.index("--max-time") + 1]
    assert max_time.endswith("s")
    seconds = int(max_time[:-1])
    assert 1 <= seconds < timeout
    assert timeout - seconds <= 60


@pytest.mark.parametrize(("explicit", "env", "want"), [("omp", None, "omp"), ("auto", "omp", "omp"), ("auto", "grok", "grok"),
                                                       ("claude", "omp", "claude")])
def test_resolve_runtime(sr, monkeypatch, explicit, env, want):
    if env:
        monkeypatch.setenv("SWARM_RUNTIME", env)
    else:
        monkeypatch.delenv("SWARM_RUNTIME", raising=False)
    assert sr.resolve_runtime(explicit) == want


def test_auto_never_picks_omp_from_path(sr, tmp_path, monkeypatch):
    stub = stub_omp(tmp_path, RESULT)
    monkeypatch.delenv("SWARM_RUNTIME", raising=False)
    monkeypatch.setenv("PATH", str(stub.parent))
    assert sr.resolve_runtime("auto") == "claude"


# ---------------------------------------------------------------- (b) --dry-run prints the invocation
def test_dry_run_prints_omp_invocation(tmp_path):
    swarm = tmp_path / ".swarm"
    env = _env(swarm, SWARM_SIGNING_KEY="runner-secret-value")
    work = _plan(tmp_path, env)
    r = run_script("swarm_run.py", "--runtime", "omp", "--dry-run", "--repo", str(work), "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["complete"] is True
    assert "runner-secret-value" not in r.stderr
    lines = [ln for ln in r.stderr.splitlines() if ln.startswith("dry-run T-one [A05]: ")]
    assert len(lines) == 1
    tokens = shlex.split(lines[0].removeprefix("dry-run T-one [A05]: "))
    # WR-06: a replay from the runner's shell drops the key vars, as the live child env does
    assert tokens[:7] == ["env", "-u", "SWARM_SIGNING_KEY", "-u", "SWARM_ED25519_KEY", "-u", "SWARM_REQUIRE_KEY"]
    assert dict(t.split("=", 1) for t in tokens[7:13]) == {
        "SWARM_DIR": str(swarm), "SWARM_CHILD": "1", "SWARM_AGENT_SESSION": "1", "SWARM_AGENT": "a05-backend",
        "AIO_UPLIFT": "0", "AIO_SWARM": "0"}
    argv, (redirect, stdin) = tokens[13:-2], tokens[-2:]
    assert argv[0] == "omp" and "--no-extensions" in argv
    opt = lambda k: argv[argv.index(k) + 1]  # noqa: E731
    assert opt("-e") == str(ROOT / "omp")
    assert opt("--tools").endswith(",yield")
    body = Path(opt("--append-system-prompt"))
    assert body.is_absolute() and body.is_file()
    assert opt("--cwd") == str(work)
    assert redirect == "<" and Path(stdin) == swarm / "assignments" / "T-one.a1.md" and Path(stdin).is_file()
    raw = [json.loads(ln) for ln in (swarm / "events.jsonl").read_text().splitlines() if '"task.result.raw"' in ln]
    meta = raw[-1]["payload"]["meta"]
    assert meta["runtime"] == "omp" and meta["argv"] == argv


def test_dry_run_line_replays_without_keys(tmp_path):
    """WR-06: the printed line, run by a shell that exports the runner's keys, starts a key-less session."""
    swarm = tmp_path / ".swarm"
    keys = {"SWARM_SIGNING_KEY": "runner-secret", "SWARM_ED25519_KEY": "11" * 32, "SWARM_REQUIRE_KEY": "1"}
    env = _env(swarm, **keys)
    work = _plan(tmp_path, env)
    stub = stub_omp(tmp_path, RESULT)
    r = run_script("swarm_run.py", "--runtime", "omp", "--omp-bin", str(stub), "--dry-run", "--repo", str(work),
                   "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    line = next(ln for ln in r.stderr.splitlines() if ln.startswith("dry-run T-one [A05]: "))
    p = subprocess.run(["/bin/sh", "-c", line.removeprefix("dry-run T-one [A05]: ")], capture_output=True, text=True,
                       env=env, cwd=work, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    (call,) = omp_calls(stub)
    assert not set(KEY_VARS) & set(call["env"])
    assert call["env"]["SWARM_AGENT"] == "a05-backend" and call["env"]["SWARM_AGENT_SESSION"] == "1"


# ---------------------------------------------------------------- (c) preflight checks only the selected runtime
def test_omp_preflight_without_claude(tmp_path):
    swarm = tmp_path / ".swarm"
    env = _env(swarm)
    work = _plan(tmp_path, env)
    stub = stub_omp(tmp_path, RESULT, name="omp-alt")
    assert shutil.which("claude", path=str(stub.parent)) is None
    r = run_script("swarm_run.py", "--runtime", "omp", "--omp-bin", "omp-alt", "--repo", str(work), "--json",
                   env={**env, "PATH": str(stub.parent)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["complete"] is True
    assert [Path(c["argv"][0]).name for c in omp_calls(stub)] == ["omp-alt"]


def test_grok_preflight_without_claude(tmp_path):
    swarm = tmp_path / ".swarm"
    env = _env(swarm)
    work = _plan(tmp_path, env)
    bin_dir = tmp_path / "grok-bin"
    bin_dir.mkdir()
    shutil.copy(stub_claude(tmp_path, RESULT), bin_dir / "grok")
    r = run_script("swarm_run.py", "--runtime", "grok", "--repo", str(work), "--json", env={**env, "PATH": str(bin_dir)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["complete"] is True


def test_missing_omp_is_e_dep(tmp_path):
    swarm = tmp_path / ".swarm"
    env = _env(swarm)
    work = _plan(tmp_path, env)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    r = run_script("swarm_run.py", "--runtime", "omp", "--repo", str(work), "--json", env={**env, "PATH": str(empty)})
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)["error"]
    assert err["code"] == "E-DEP"
    assert "omp not on PATH (--runtime omp" in err["message"]
    assert _states(swarm) == {"T-one": "PLANNED"}


def _failed_reasons(swarm, tid) -> list[str]:
    con = sqlite3.connect(swarm / "tasks.db")
    rows = [r for (r,) in con.execute("SELECT reason FROM transitions WHERE task_id = ? AND to_state = 'FAILED' ORDER BY id",
                                      (tid,))]
    con.close()
    return rows


def test_relative_omp_bin_resolved_at_preflight(tmp_path):
    """WR-05: a relative --omp-bin found from the runner's cwd still spawns, although the session's cwd is the repo."""
    swarm = tmp_path / ".swarm"
    env = _env(swarm)
    work = _plan(tmp_path, env)
    stub = stub_omp(tmp_path, RESULT)
    # the runner's cwd must hold the relative path; from the repo (work/) `omp-bin/omp` does not exist
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "swarm_run.py"), "--runtime", "omp", "--omp-bin", "omp-bin/omp",
                        "--repo", str(work), "--once", "--json"], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _states(swarm) == {"T-one": "DONE"}
    (call,) = omp_calls(stub)
    assert os.path.isabs(call["argv"][0]) and os.path.samefile(call["argv"][0], stub)


def test_spawn_failure_fails_the_task(tmp_path):
    """WR-05: a binary that passes preflight but cannot be executed fails its task (E-DEP) instead of aborting the run
    and stranding the task IN_PROGRESS."""
    swarm = tmp_path / ".swarm"
    env = _env(swarm)
    work = _plan(tmp_path, env)
    bad = tmp_path / "bad-bin" / "omp"
    bad.parent.mkdir()
    bad.write_text("#!/nonexistent/interpreter\n")
    bad.chmod(0o755)
    r = run_script("swarm_run.py", "--runtime", "omp", "--omp-bin", str(bad), "--repo", str(work), "--once", "--json", env=env)
    assert r.returncode in (0, 1), r.stdout + r.stderr
    assert _states(swarm) == {"T-one": "FAILED"}
    assert any("E-DEP" in reason for reason in _failed_reasons(swarm, "T-one"))


def test_yield_error_fails_gate_session_and_records_no_gate(tmp_path):
    """WR-02: a session whose yield failed is E-CONTRACT → FAILED even when its final text carries an IN_REVIEW json
    block, and the runner records no gate for it (CR-04)."""
    swarm = tmp_path / ".swarm"
    env = _env(swarm, SWARM_SIGNING_KEY="runner-secret")
    plan = {"tasks": [{"id": "be", "capability": "code.backend", "agent": "A05", "gates": ["review"]},
                      {"id": "rev", "capability": "gate.review", "agent": "A09", "depends_on": ["be"],
                       "gates": {"gate": "review", "for": ["be"]}}]}
    work = _plan(tmp_path, env, plan, "--risk-class", "low")
    stub = stub_omp(tmp_path, {"state": "IN_REVIEW", "outputs": [], "summary_md": "stub omp session"},
                    yield_error="could not run the gate script", yield_error_on='"gate": "review"')
    r = run_script("swarm_run.py", "--runtime", "omp", "--omp-bin", str(stub), "--repo", str(work), "--json", env=env)
    assert r.returncode in (0, 1), r.stdout + r.stderr
    con = sqlite3.connect(swarm / "tasks.db")
    rows = list(con.execute("SELECT gate FROM verdicts WHERE task_id = 'T-be'"))
    con.close()
    assert rows == []
    states = _states(swarm)
    assert states["T-be"] == "IN_REVIEW" and states["T-rev"] == "ESCALATED"
    reasons = _failed_reasons(swarm, "T-rev")
    assert reasons and all("E-CONTRACT" in x and "could not run the gate script" in x for x in reasons)


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def test_timeout_kills_the_session_process_group(tmp_path):
    """WR-04: on --task-timeout the session's whole process group goes, its tool processes included, and the partial
    stream is kept in the task.result.raw meta."""
    swarm = tmp_path / ".swarm"
    env = _env(swarm)
    work = _plan(tmp_path, env)
    stub = stub_omp(tmp_path, RESULT, hang=True)
    try:
        r = run_script("swarm_run.py", "--runtime", "omp", "--omp-bin", str(stub), "--task-timeout", "3", "--repo", str(work),
                       "--once", "--json", env=env)
        assert r.returncode in (0, 1), r.stdout + r.stderr
        pid = omp_grandchild(stub)
        assert pid is not None
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(pid), "the session's tool process outlived the timeout"
    finally:
        pid = omp_grandchild(stub)
        if pid is not None and _alive(pid):
            os.kill(pid, 9)
    assert _states(swarm) == {"T-one": "FAILED"}
    assert _failed_reasons(swarm, "T-one") == ["E-TIMEOUT: task_timeout exceeded"]
    raw = [json.loads(ln) for ln in (swarm / "events.jsonl").read_text().splitlines() if '"task.result.raw"' in ln]
    meta = raw[-1]["payload"]["meta"]
    assert meta["timed_out"] is True and meta["session_id"] == "stub-session" and meta["is_error"] is True


def test_extension_load_failure_fails_gate_session_and_records_no_gate(tmp_path):
    """T-06-06: omp only warns on stderr when the -e guard package fails to load, and runs the session unguarded;
    the runner fails that task closed (E-DEP), applies none of its result and records no gate."""
    swarm = tmp_path / ".swarm"
    env = _env(swarm, SWARM_SIGNING_KEY="runner-secret")
    plan = {"tasks": [{"id": "be", "capability": "code.backend", "agent": "A05", "gates": ["review"]},
                      {"id": "rev", "capability": "gate.review", "agent": "A09", "depends_on": ["be"],
                       "gates": {"gate": "review", "for": ["be"]}}]}
    work = _plan(tmp_path, env, plan, "--risk-class", "low")
    stub = stub_omp(tmp_path, {"state": "IN_REVIEW", "outputs": [], "summary_md": "stub omp session"},
                    load_error_on='"gate": "review"')
    r = run_script("swarm_run.py", "--runtime", "omp", "--omp-bin", str(stub), "--repo", str(work), "--json", env=env)
    assert r.returncode in (0, 1), r.stdout + r.stderr
    con = sqlite3.connect(swarm / "tasks.db")
    rows = list(con.execute("SELECT gate FROM verdicts WHERE task_id = 'T-be'"))
    con.close()
    assert rows == []
    states = _states(swarm)
    assert states["T-be"] == "IN_REVIEW" and states["T-rev"] == "ESCALATED"
    reasons = _failed_reasons(swarm, "T-rev")
    assert reasons and all(x.startswith("E-DEP") and "Failed to load extension" in x for x in reasons)


def _grandchild_when_started(stub: Path, timeout: float = 30) -> int:
    f = stub.parent / "grandchild.pid"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if f.exists() and f.read_text().strip():
            return int(f.read_text())
        time.sleep(0.1)
    raise AssertionError("the stub session never started its tool process")


def _ppid(pid: int) -> int:
    with open(f"/proc/{pid}/stat") as fh:
        return int(fh.read().rsplit(")", 1)[1].split()[1])


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_signal_to_runner_group_ends_sessions(tmp_path, sig):
    """WR-09 / T-06-12: sessions sit in their own OS session, so a SIGTERM/SIGHUP sent to the runner's process group
    (GNU timeout, a terminal hangup) misses them; the runner must end them itself and leave the task retryable."""
    swarm = tmp_path / ".swarm"
    env = _env(swarm)
    work = _plan(tmp_path, env)
    stub = stub_omp(tmp_path, RESULT, hang=True)
    runner = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "swarm_run.py"), "--runtime", "omp", "--omp-bin",
                               str(stub), "--task-timeout", "100", "--repo", str(work), "--once", "--json"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=ROOT,
                              start_new_session=True)
    pids: list[int] = []
    try:
        grandchild = _grandchild_when_started(stub)
        pids = [_ppid(grandchild), grandchild]  # the stub omp session, its tool process
        os.killpg(runner.pid, sig)
        out, err = runner.communicate(timeout=30)
        deadline = time.monotonic() + 5
        while any(_alive(p) for p in pids) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert [_alive(p) for p in pids] == [False, False], "the session outlived the runner\n" + out + err
    finally:
        for p in [*pids, runner.pid]:
            if _alive(p):
                os.kill(p, signal.SIGKILL)
        runner.wait()
    assert runner.returncode == 128 + sig
    con = sqlite3.connect(swarm / "tasks.db")
    ((state, notes),) = con.execute("SELECT state, notes FROM tasks WHERE task_id = 'T-one'")
    con.close()
    assert state == "FAILED" and json.loads(notes).get("running") is None


# ---------------------------------------------------------------- (d) headless omp vs in-session ingest
def test_omp_headless_and_ingest_same_transitions(tmp_path):
    d1, d2 = tmp_path / "a", tmp_path / "b"
    d1.mkdir()
    d2.mkdir()
    env1 = _env(d1 / ".swarm")
    work = _plan(d1, env1)
    stub = stub_omp(tmp_path, RESULT)
    r = run_script("swarm_run.py", "--runtime", "omp", "--omp-bin", str(stub), "--repo", str(work), "--once", "--json", env=env1)
    assert r.returncode == 0, r.stdout + r.stderr
    env2 = _env(d2 / ".swarm")
    _plan(d2, env2)
    f = tmp_path / "r.json"
    f.write_text(json.dumps(RESULT))
    r = run_script("orch_status.py", "--ingest", str(f), "--json", env=env2)
    assert r.returncode == 0, r.stdout + r.stderr
    h1, h2 = _history(env1, "T-one"), _history(env2, "T-one")
    assert h1 == h2
    assert h1[-6:] == ["PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW", "APPROVED", "DONE"]


# ---------------------------------------------------------------- (e) a low-risk plan reaches DONE on omp
def test_low_risk_plan_done_on_omp(tmp_path):
    swarm = tmp_path / ".swarm"
    env = _env(swarm, SWARM_SIGNING_KEY="runner-secret", SWARM_ED25519_KEY="11" * 32)
    plan = {"tasks": [{"id": "be", "capability": "code.backend", "agent": "A05", "gates": []},
                      {"id": "doc", "capability": "docs.bundle", "agent": "A15", "depends_on": ["be"], "gates": []}]}
    work = _plan(tmp_path, env, plan, "--risk-class", "low")
    stub = stub_omp(tmp_path, {"state": "IN_REVIEW", "outputs": [], "summary_md": "stub omp session"})
    r = run_script("swarm_run.py", "--runtime", "omp", "--omp-bin", str(stub), "--repo", str(work), "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["complete"] is True
    assert _states(swarm) == {"T-be": "DONE", "T-doc": "DONE"}
    calls = omp_calls(stub)
    assert sorted(c["env"]["SWARM_AGENT"] for c in calls) == ["a05-backend", "a15-docs"]
    for c in calls:
        e = c["env"]
        assert e["SWARM_CHILD"] == "1" and e["SWARM_AGENT_SESSION"] == "1"
        assert e["AIO_UPLIFT"] == "0" and e["AIO_SWARM"] == "0"
        assert Path(e["SWARM_DIR"]).is_absolute() and Path(e["SWARM_DIR"]) == swarm
        assert not set(KEY_VARS) & set(e)
        assert Path(c["cwd"]) == work
        assert c["argv"][1:4] == ["-p", "--mode", "json"]

