"""INST-03 on the runner's side (ADR 0001 S4 criteria 4-5): each agent session starts with its own SUBSTRATE_TOKEN and no
other, and each runtime's argv reaches the `substrate` MCP entry the install wrote into the workspace."""
import importlib.util
import json
import os
import shlex
from types import SimpleNamespace

import pytest

from conftest import ROOT, omp_calls, run_script, stub_omp
from swarm import workspace

A05_TOKEN = "p14-a05-own-token-0123456789"
RUNNER_TOKENS = {"SUBSTRATE_TOKEN": "p14-runner-a01-token-0123456789",
                 "SUBSTRATE_TOKEN_SWARM_A05_BE": "p14-runner-copy-of-a05-0123456789",
                 "SUBSTRATE_TOKEN_SWARM_A09_REV": "p14-runner-a09-token-0123456789",
                 "SUBSTRATE_OPERATOR_TOKENS": "p14-operator-token-0123456789"}
ONE_TASK = {"tasks": [{"id": "one", "capability": "code.backend", "agent": "A05", "title": "one", "depends_on": [], "gates": []}]}
RESULT = {"task_id": "T-one", "state": "IN_REVIEW", "outputs": [{"kind": "code.backend", "uri": "file://x", "version": "1",
                                                                 "digest": ""}], "metrics": {}, "summary_md": "ok"}
A05 = {"slug": "a05-backend", "id": "A05"}


@pytest.fixture()
def sr(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    spec = importlib.util.spec_from_file_location("swarm_run_tokens_under_test", ROOT / "scripts" / "swarm_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    """A workspace whose A05 env file holds A05's token, run by a runner that holds several other tokens."""
    monkeypatch.delenv("SUBSTRATE_URL", raising=False)  # off: these tests are about the session, not the lease
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for k, v in RUNNER_TOKENS.items():
        monkeypatch.setenv(k, v)
    w = tmp_path / "work"
    w.mkdir()
    workspace.write_token(workspace.env_file(w, "a05-backend"), A05_TOKEN)
    return w


def _wire(ws):
    """What the install leaves in the workspace for the runtimes to read."""
    (ws / ".mcp.json").write_text(json.dumps({"mcpServers": {"substrate": workspace.SPEC["json"]}}))
    (ws / ".grok").mkdir()
    (ws / ".grok" / "config.toml").write_text(workspace.SPEC["toml"]["block"])


def _args(**kw):
    return SimpleNamespace(**{"claude_bin": "claude", "grok_bin": "grok", "omp_bin": "omp", "model": "",
                              "permission_mode": "acceptEdits", "max_turns": 5, "task_timeout": 90,
                              "allowed_tools": "Read,Grep", **kw})


def _tokens(env):
    return {k: v for k, v in env.items() if workspace.carries_token(k)}


@pytest.mark.parametrize("runtime", ["claude", "grok", "omp"])
def test_a_session_holds_its_own_token_and_no_other(sr, ws, tmp_path, runtime):
    _, env, _ = sr.headless_command(runtime, A05, ws, tmp_path / ".swarm", _args())
    assert _tokens(env) == {"SUBSTRATE_TOKEN": A05_TOKEN}


def test_without_an_env_file_or_its_variable_a_session_gets_no_token_at_all(sr, ws, tmp_path):
    # A10 has neither: not the runner's SUBSTRATE_TOKEN, since a borrowed token would let the agent act, and be
    # recorded, as another surface
    _, env, _ = sr.headless_command("claude", {"slug": "a10-security", "id": "A10"}, ws, tmp_path / ".swarm", _args())
    assert _tokens(env) == {}


def test_without_an_env_file_a_session_gets_its_own_server_side_variable(sr, ws, tmp_path):
    # A09 has no env file but the runner holds SUBSTRATE_TOKEN_SWARM_A09_REV: the same secret, so the session gets it
    # as SUBSTRATE_TOKEN, the lookup the lease and the tee use too
    _, env, _ = sr.headless_command("claude", {"slug": "a09-reviewer", "id": "A09"}, ws, tmp_path / ".swarm", _args())
    assert _tokens(env) == {"SUBSTRATE_TOKEN": RUNNER_TOKENS["SUBSTRATE_TOKEN_SWARM_A09_REV"]}


def test_claude_gets_the_workspace_mcp_config_and_the_substrate_tools(sr, ws, tmp_path):
    _wire(ws)
    argv, _, cwd = sr.headless_command("claude", A05, ws, tmp_path / ".swarm", _args())
    assert cwd == ROOT  # where .claude/agents resolves, and where a workspace .mcp.json is never read
    assert argv[argv.index("--mcp-config") + 1] == str(ws / ".mcp.json") and "--strict-mcp-config" in argv
    allowed = argv[argv.index("--allowedTools") + 1: argv.index("--add-dir", argv.index("--allowedTools"))]
    assert allowed == ["Read", "Grep", "mcp__substrate"]
    assert argv[-2:] == ["--add-dir", str(ws)]


def test_an_unwired_workspace_gets_no_mcp_flags(sr, ws, tmp_path):
    argv, _, _ = sr.headless_command("claude", A05, ws, tmp_path / ".swarm", _args())
    assert "--mcp-config" not in argv and "mcp__substrate" not in argv
    argv, _, _ = sr.headless_command("grok", A05, ws, tmp_path / ".swarm", _args())
    assert "--trust" not in argv


def test_grok_clears_folder_trust_for_the_workspace_it_runs_in(sr, ws, tmp_path):
    _wire(ws)
    argv, _, cwd = sr.headless_command("grok", A05, ws, tmp_path / ".swarm", _args())
    assert "--trust" in argv and argv[argv.index("--cwd") + 1] == str(ws) and cwd == ws


def test_omp_runs_where_the_entry_is_and_its_tools_list_names_only_builtins(sr, ws, tmp_path):
    # omp reads <cwd>/.mcp.json; MCP tools are not filtered by --tools (they mount as xd:// devices), so nothing in
    # the argv may restrict them either
    _wire(ws)
    argv, _, cwd = sr.headless_command("omp", A05, ws, tmp_path / ".swarm", _args())
    assert argv[argv.index("--cwd") + 1] == str(ws) and cwd == ws
    assert "--no-tools" not in argv and all(not t.startswith("mcp") for t in argv[argv.index("--tools") + 1].split(","))


@pytest.mark.parametrize("entry", ["projected", "absent", "operator-token", "invalid-json"])
def test_omp_guard_scope_requires_the_projected_own_token_entry(sr, ws, tmp_path, monkeypatch, entry):
    monkeypatch.setenv("SWARM_SUBSTRATE_AGENT", "a09-reviewer")
    if entry != "absent":
        _wire(ws)
    if entry == "operator-token":
        config = json.loads((ws / ".mcp.json").read_text())
        config["mcpServers"]["substrate"]["headers"]["Authorization"] = "Bearer operator-token"
        (ws / ".mcp.json").write_text(json.dumps(config))
    elif entry == "invalid-json":
        (ws / ".mcp.json").write_text("{")
    _, env, _ = sr.headless_command("omp", A05, ws, tmp_path / ".swarm", _args())
    assert env.get("SWARM_SUBSTRATE_AGENT") == ("a05-backend" if entry == "projected" else None)
    # Even a projected entry never unlocks a session lacking that agent's own credential.
    _, env, _ = sr.headless_command("omp", {"slug": "a10-security", "id": "A10"}, ws, tmp_path / ".swarm", _args())
    assert "SWARM_SUBSTRATE_AGENT" not in env


def _planned(tmp_path, env):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(ONE_TASK))
    r = run_script("orch_plan.py", "--plan", str(plan), "--prefix", "T", "--repo", str(tmp_path / "work"), "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr


def _runner_env(tmp_path):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("SWARM_", "SUBSTRATE_")) and k != "ANTHROPIC_API_KEY"}
    return {**base, **RUNNER_TOKENS, "SWARM_DIR": str(tmp_path / ".swarm"), "XDG_CONFIG_HOME": str(tmp_path / "config")}


def test_the_live_child_gets_only_its_own_token(ws, tmp_path):
    env = _runner_env(tmp_path)
    _planned(tmp_path, env)
    stub = stub_omp(tmp_path, RESULT)
    r = run_script("swarm_run.py", "--runtime", "omp", "--omp-bin", str(stub), "--once", "--repo", str(ws), "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    (call,) = omp_calls(stub)
    assert _tokens(call["env"]) == {"SUBSTRATE_TOKEN": A05_TOKEN}


def test_dry_run_names_what_is_stripped_and_where_the_token_comes_from_never_its_value(ws, tmp_path):
    env = _runner_env(tmp_path)
    _planned(tmp_path, env)
    r = run_script("swarm_run.py", "--runtime", "claude", "--dry-run", "--once", "--repo", str(ws), "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    line = next(ln for ln in r.stderr.splitlines() if ln.startswith("dry-run T-one [A05]: "))
    tokens = shlex.split(line.removeprefix("dry-run T-one [A05]: "))
    unset = {tokens[i + 1] for i, t in enumerate(tokens) if t == "-u"}
    assert set(RUNNER_TOKENS) <= unset
    assert f"dry-run T-one [A05] SUBSTRATE_TOKEN: from {workspace.env_file(ws, 'a05-backend')}" in r.stderr.splitlines()
    assert not any(v in r.stdout + r.stderr for v in (A05_TOKEN, *RUNNER_TOKENS.values()))
