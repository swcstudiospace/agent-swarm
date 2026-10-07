"""`build_agents.py --install-workspace` wires substrate-mcp (ADR 0001 S4; INST-01..04), run as a subprocess from a
copy of the checkout into a fresh git workspace, the way an operator runs it.

The ADR's two scopes are kept apart: no file the install produces names Greptime as an MCP server, wherever it lands;
no file under the workspace holds a token value; and the one place a token belongs, each agent's env file, is 0600,
outside the workspace and every git checkout, and holds exactly that agent's SUBSTRATE_TOKEN."""
import io
import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from install_helpers import _ENV_KEYS, _snapshot, _write
from swarm import workspace
from swarm.manifest import load_manifest
from swarm.substrate_tee import AGENT_SURFACES
from scripts import _install_substrate as substrate_install

AGENTS = load_manifest()
# What real tokens look like to every check below: long enough for the tracked-file scan, distinct per agent.
TOKENS = {workspace.server_var(AGENT_SURFACES[a["id"]]): f"p14-sentinel-{a['slug']}-7f3a9c0e5d" for a in AGENTS}


@pytest.fixture()
def gws(tmp_path):
    """An empty workspace that is a git checkout of its own, as a real one is."""
    w = tmp_path / "gws"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    return w.resolve()


def _install(tree, ws, home, *args, tokens=TOKENS, env=None):
    base = {k: v for k, v in os.environ.items()
            if k not in _ENV_KEYS and not k.startswith("SUBSTRATE_") and k != "XDG_CONFIG_HOME"}
    run_env = {**base, "HOME": str(home), **tokens, **(env or {})}
    cmd = [sys.executable, str(tree / "scripts" / "build_agents.py"), "--install-workspace", str(ws), *args]
    return subprocess.run(cmd, cwd=tree, capture_output=True, text=True, env=run_env)


def _env_dir(ws, home):
    return workspace.env_dir(ws, {"HOME": str(home)})


def _files(*dirs):
    return {p: p.read_text(encoding="utf-8", errors="replace") for p in _snapshot(*dirs)}


def _git_checkout_above(path: Path) -> Path | None:
    return next((p for p in (path, *path.parents) if (p / ".git").exists()), None)


# ---------------------------------------------------------------- INST-01: the entries, appended and idempotent

def test_a_fresh_workspace_gets_an_entry_per_runtime_file(tree, gws, home):
    r = _install(tree, gws, home)
    assert r.returncode == 0, r.stdout + r.stderr
    claude = json.loads((gws / ".mcp.json").read_text())
    assert claude["mcpServers"] == {"substrate": {"type": "http", "url": "${SUBSTRATE_URL}/mcp",
                                                  "headers": {"Authorization": "Bearer ${SUBSTRATE_TOKEN}"}}}
    grok = tomllib.loads((gws / ".grok" / "config.toml").read_text())
    assert grok["mcp_servers"] == {"substrate": {"url": "${SUBSTRATE_URL}/mcp",
                                                 "headers": {"Authorization": "Bearer ${SUBSTRATE_TOKEN}"}}}
    # a Claude session sees only the tools its agent names, so the installed agents name the server's
    for agent in AGENTS:
        fm = (gws / ".claude" / "agents" / f"{agent['slug']}.md").read_text().split("---")[1]
        assert "mcp__substrate" in next(line for line in fm.splitlines() if line.startswith("tools:"))


def test_existing_entries_are_kept_and_a_second_run_changes_nothing(tree, gws, home):
    old_json = json.dumps({"mcpServers": {"linear": {"type": "http", "url": "https://mcp.linear.app/mcp"}},
                           "keep": [1, 2]}, indent=2) + "\n"
    old_toml = 'model = "grok-4"\n\n[mcp_servers.notion]\nurl = "https://mcp.notion.com/mcp"\n'
    _write(gws / ".mcp.json", old_json)
    _write(gws / ".grok" / "config.toml", old_toml)

    assert _install(tree, gws, home).returncode == 0
    claude = json.loads((gws / ".mcp.json").read_text())
    assert list(claude["mcpServers"]) == ["linear", "substrate"] and claude["keep"] == [1, 2]
    toml_text = (gws / ".grok" / "config.toml").read_text()
    assert toml_text.startswith(old_toml)
    assert list(tomllib.loads(toml_text)["mcp_servers"]) == ["notion", "substrate"]
    assert (gws / ".mcp.json.substrate-backup").read_text() == old_json
    assert (gws / ".grok" / "config.toml.substrate-backup").read_text() == old_toml

    first = _snapshot(gws, _env_dir(gws, home))
    r = _install(tree, gws, home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot(gws, _env_dir(gws, home)) == first
    assert "already has substrate (claude, omp)" in r.stdout and "15 unchanged" in r.stdout


# ---------------------------------------------------------------- INST-02: what the generated files may not hold

def test_no_greptime_server_anywhere_and_no_token_under_the_workspace(tree, gws, home):
    assert _install(tree, gws, home).returncode == 0
    under_ws, env_files = _files(gws), _files(_env_dir(gws, home))
    assert env_files
    for path, text in {**under_ws, **env_files}.items():
        # store-lock rule 2, checked where the configs land
        if "mcpServers" in text or "mcp_servers" in text:
            assert "greptime" not in text.lower(), path
    for path, text in under_ws.items():
        leaked = [var for var, token in TOKENS.items() if token in text]
        assert leaked == [], f"{path} holds the token of {leaked}"


# ---------------------------------------------------------------- INST-03: one token per agent, outside everything

def test_each_agent_gets_a_0600_env_file_outside_the_workspace_and_every_checkout(tree, gws, home):
    assert _install(tree, gws, home).returncode == 0
    directory = _env_dir(gws, home)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert not directory.resolve().is_relative_to(gws) and not directory.resolve().is_relative_to(tree)
    assert _git_checkout_above(directory.resolve()) is None
    for agent in AGENTS:
        path = directory / f"{agent['slug']}.env"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path
        var = workspace.server_var(AGENT_SURFACES[agent["id"]])
        assert path.read_text() == f"SUBSTRATE_TOKEN={TOKENS[var]}\n"  # that agent's, and nothing else
    assert sorted(p.name for p in directory.iterdir()) == sorted(f"{a['slug']}.env" for a in AGENTS)


def test_a_missing_token_says_what_to_create_and_writes_nothing(tree, gws, home):
    tokens = {k: v for k, v in TOKENS.items() if k != "SUBSTRATE_TOKEN_SWARM_A05_BE"}
    before = _snapshot(tree, gws, home)
    r = _install(tree, gws, home, tokens=tokens)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "SUBSTRATE_TOKEN_SWARM_A05_BE" in r.stderr
    assert str(_env_dir(gws, home) / "a05-backend.env") in r.stderr
    assert "0600" in r.stderr and "SUBSTRATE_TOKEN=<that token>" in r.stderr
    assert "SUBSTRATE_TOKEN_SWARM_A06_FE" not in r.stderr  # only what is actually missing
    assert _snapshot(tree, gws, home) == before
    assert not any(token in r.stdout + r.stderr for token in TOKENS.values())


def test_a_file_the_operator_created_is_kept_and_a_world_readable_one_is_refused(tree, gws, home):
    tokens = {k: v for k, v in TOKENS.items() if k != "SUBSTRATE_TOKEN_SWARM_A05_BE"}
    path = _env_dir(gws, home) / "a05-backend.env"
    workspace.write_token(path, "operator-made-token-0123456789")
    r = _install(tree, gws, home, tokens=tokens)
    assert r.returncode == 0, r.stdout + r.stderr
    assert path.read_text() == "SUBSTRATE_TOKEN=operator-made-token-0123456789\n"

    path.chmod(0o644)
    r = _install(tree, gws, home, tokens=tokens)
    assert r.returncode == 2 and "must be 0600" in r.stderr


@pytest.mark.parametrize("where", ["git-checkout", "workspace"])
def test_env_files_inside_a_checkout_or_the_workspace_are_refused(tree, gws, home, tmp_path, where):
    if where == "git-checkout":
        config = tmp_path / "dotfiles" / ".config"
        config.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(tmp_path / "dotfiles")], check=True)
    else:
        config = gws / ".config"
    before = _snapshot(tree, gws, home, tmp_path / "dotfiles") if where == "git-checkout" else _snapshot(tree, gws, home)
    r = _install(tree, gws, home, env={"XDG_CONFIG_HOME": str(config)})
    assert r.returncode == 2, r.stdout + r.stderr
    assert ("inside the git checkout" if where == "git-checkout" else "inside the workspace") in r.stderr
    assert "XDG_CONFIG_HOME" in r.stderr
    after = _snapshot(tree, gws, home, tmp_path / "dotfiles") if where == "git-checkout" else _snapshot(tree, gws, home)
    assert after == before


# ---------------------------------------------------------------- INST-04: refusals and the dry run

def test_a_runtime_that_cannot_call_mcp_is_refused_as_an_executor(tree, gws, home):
    before = _snapshot(tree, gws, home)
    r = _install(tree, gws, home, "--runtimes", "claude,trae")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "refusing to wire trae as an executing agent" in r.stderr
    assert _snapshot(tree, gws, home) == before


@pytest.mark.parametrize("case", ["bad-json", "differing-entry", "symlinked-config", "home", "omp-dir", "checkout"])
def test_refusals_hold_with_every_token_given_and_write_nothing(tree, gws, home, tmp_path, case):
    target = gws
    if case == "bad-json":
        _write(gws / ".mcp.json", "{ not json")
    elif case == "differing-entry":
        _write(gws / ".grok" / "config.toml", '[mcp_servers.substrate]\nurl = "http://127.0.0.1:7410/mcp"\n')
    elif case == "symlinked-config":
        victim = _write(tmp_path / "outside" / "victim.json", "{}\n")
        (gws / ".mcp.json").symlink_to(victim)
    elif case == "home":
        target = home
    elif case == "omp-dir":
        target = home / ".omp" / "agent"
        target.mkdir(parents=True)
    else:
        target = tree / "omp"
    before = _snapshot(tree, gws, home, tmp_path / "outside")
    r = _install(tree, target, home)
    assert r.returncode == 2, r.stdout + r.stderr
    assert _snapshot(tree, gws, home, tmp_path / "outside") == before  # no env file either


def test_dry_run_prints_the_additions_and_the_env_files_and_writes_nothing(tree, gws, home):
    before = _snapshot(tree, gws, home)
    r = _install(tree, gws, home, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot(tree, gws, home) == before
    assert "+ create .mcp.json (claude, omp):" in r.stdout and "+ create .grok/config.toml (grok):" in r.stdout
    assert '"Authorization": "Bearer ${SUBSTRATE_TOKEN}"' in r.stdout
    assert f"agent env files in {_env_dir(gws, home)}" in r.stdout
    for agent in AGENTS:
        var = workspace.server_var(AGENT_SURFACES[agent["id"]])
        assert f"{agent['slug']}.env" in r.stdout and f"${var}" in r.stdout
    assert not any(token in r.stdout + r.stderr for token in TOKENS.values())


def test_dry_run_with_a_missing_token_still_prints_the_plan_and_exits_2(tree, gws, home):
    tokens = {k: v for k, v in TOKENS.items() if k != "SUBSTRATE_TOKEN_SWARM_A09_REV"}
    r = _install(tree, gws, home, "--dry-run", tokens=tokens)
    assert r.returncode == 2
    assert "+ create .mcp.json (claude, omp):" in r.stdout
    assert "a09-reviewer.env" in r.stdout and "MISSING: export SUBSTRATE_TOKEN_SWARM_A09_REV" in r.stdout
    assert list(gws.iterdir()) == [gws / ".git"]


def test_no_substrate_installs_the_rest_without_tokens(tree, gws, home):
    r = _install(tree, gws, home, "--no-substrate", tokens={})
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (gws / ".mcp.json").exists() and not (gws / ".grok" / "config.toml").exists()
    assert not _env_dir(gws, home).exists()
    assert (gws / ".claude" / "agents" / "a05-backend.md").exists()


def test_atomic_config_write_ignores_predictable_and_exclusive_staging_symlink_collisions(tmp_path, monkeypatch):
    target = tmp_path / ".mcp.json"
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    old_tmp = target.with_name(f".{target.name}.substrate-tmp-{os.getpid()}")
    old_tmp.symlink_to(victim)
    collision = tmp_path / ".substrate-collision.tmp"
    collision.symlink_to(victim)
    names = iter(("collision", "safe"))
    monkeypatch.setattr(workspace.secrets, "token_hex", lambda _: next(names))

    substrate_install._write_atomic(target, "replacement")

    assert target.read_text() == "replacement"
    assert victim.read_text() == "untouched"
    assert old_tmp.is_symlink() and collision.is_symlink()
    assert set(tmp_path.iterdir()) == {target, victim, old_tmp, collision}


@pytest.mark.parametrize("swapped", ["destination", "ancestor"])
def test_atomic_config_write_rechecks_links_after_staging(tmp_path, monkeypatch, swapped):
    directory = tmp_path / "configs"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = directory / "config"
    victim = outside / "config"
    victim.write_text("untouched")
    stage = workspace._stage_file

    def stage_and_swap(*args):
        result = stage(*args)
        if swapped == "destination":
            target.symlink_to(victim)
        else:
            directory.rename(tmp_path / "original")
            directory.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(workspace, "_stage_file", stage_and_swap)
    with pytest.raises((OSError, workspace.EnvFileProblem)):
        substrate_install._write_atomic(target, "replacement")
    assert victim.read_text() == "untouched"
    assert not list(tmp_path.rglob(".substrate-*.tmp"))
    if swapped == "destination":
        assert target.is_symlink()
    else:
        assert directory.is_symlink()
        assert not list((tmp_path / "original").iterdir())


@pytest.mark.parametrize("unsafe", ["permissions", "owner", "symlink"])
@pytest.mark.parametrize("held", [False, True])
def test_private_directory_refusal_precedes_tokens_preflight_and_dry_run(tmp_path, monkeypatch, unsafe, held):
    ws = tmp_path / "work"
    ws.mkdir()
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config"), **TOKENS}
    directory = workspace.env_dir(ws, env)
    if held:
        for agent in AGENTS:
            workspace.write_token(directory / f"{agent['slug']}.env", TOKENS[workspace.server_var(AGENT_SURFACES[agent["id"]])])
    else:
        directory.mkdir(mode=0o700, parents=True)
    if unsafe == "permissions":
        directory.chmod(0o777)
    elif unsafe == "owner":
        uid = os.getuid()
        monkeypatch.setattr(workspace.os, "getuid", lambda: uid + 1)
    else:
        original = directory.with_name("original")
        directory.rename(original)
        directory.symlink_to(original, target_is_directory=True)
    before = _snapshot(tmp_path)

    for tokens in (True, False):
        problem = substrate_install.preflight(ws, ["claude", "grok"], env, tokens=tokens)
        assert problem and "0700" in problem and "XDG_CONFIG_HOME" in problem
    for dry_run in (True, False):
        out = io.StringIO()
        assert substrate_install.install_substrate(ws, ["claude", "grok"], dry_run, out, env) == 2
        assert "0700" in out.getvalue()
        assert not any(token in out.getvalue() for token in TOKENS.values())
    with pytest.raises((workspace.EnvFileProblem, OSError)):
        workspace.write_token(directory / "a05-backend.env", "replacement")
    assert _snapshot(tmp_path) == before
    if unsafe == "permissions":
        assert stat.S_IMODE(directory.stat().st_mode) == 0o777


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("fail_at", ["a06-frontend.env", "config.toml"])
def test_later_publish_failure_rolls_back_tokens_configs_and_backups(tmp_path, monkeypatch, existing, fail_at):
    ws = tmp_path / "work"
    ws.mkdir()
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config"), **TOKENS}
    directory = workspace.env_dir(ws, env)
    if existing:
        _write(ws / ".mcp.json", '{"keep": true}\n').chmod(0o640)
        _write(ws / ".grok" / "config.toml", 'model = "grok-4"\n')
        _write(ws / ".mcp.json.substrate-backup", "older backup\n")
        for agent in AGENTS:
            workspace.write_token(directory / f"{agent['slug']}.env", f"old-{agent['slug']}")
    before = _snapshot(tmp_path)
    modes = {p: stat.S_IMODE(p.stat().st_mode) for p in before}
    replace = os.replace
    published = []

    def fail_later(src, dst, **kwargs):
        if Path(dst).name == fail_at:
            raise OSError("injected later publish failure")
        published.append(Path(dst).name)
        return replace(src, dst, **kwargs)

    monkeypatch.setattr(workspace.os, "replace", fail_later)
    out = io.StringIO()
    assert substrate_install.install_substrate(ws, ["claude", "grok"], False, out, env) == 2
    assert "a01-orchestrator.env" in published
    assert _snapshot(tmp_path) == before
    assert {p: stat.S_IMODE(p.stat().st_mode) for p in before} == modes
    assert not list(tmp_path.rglob(".substrate-*.tmp"))
    if not existing:
        assert set(tmp_path.iterdir()) == {ws}
        assert not list(ws.iterdir())
    assert not any(token in out.getvalue() for token in TOKENS.values())


@pytest.mark.parametrize("edit_at", ["staging", "publishing"])
def test_failed_install_preserves_intervening_operator_config_edits(tmp_path, monkeypatch, edit_at):
    ws = tmp_path / "work"
    ws.mkdir()
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config"), **TOKENS}
    target = _write(ws / ".mcp.json", '{"before": true}\n')
    before = _snapshot(tmp_path)
    external = '{"operator": "changed during install"}\n'
    if edit_at == "staging":
        plan = substrate_install.plan

        def plan_and_edit(*args):
            result = plan(*args)
            target.write_text(external)
            return result

        monkeypatch.setattr(substrate_install, "plan", plan_and_edit)
    else:
        replace = os.replace

        def edit_and_fail(src, dst, **kwargs):
            if Path(dst).name == "config.toml":
                target.write_text(external)
                raise OSError("injected later publish failure")
            return replace(src, dst, **kwargs)

        monkeypatch.setattr(workspace.os, "replace", edit_and_fail)
    out = io.StringIO()
    assert substrate_install.install_substrate(ws, ["claude", "grok"], False, out, env) == 2
    assert _snapshot(tmp_path) == {**before, target: external.encode()}
    assert not list(tmp_path.rglob(".substrate-*.tmp"))
    assert not workspace.env_dir(ws, env).exists()
