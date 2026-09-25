"""Workspace install of the omp package (PKG-01..03): link merge, carry-over, shadow scan, copy mode, dry run."""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("_install_omp", ROOT / "scripts" / "_install_omp.py")
inst = importlib.util.module_from_spec(_spec)
sys.modules["_install_omp"] = inst  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(inst)
PKG = str(inst.PKG)
_ENV_KEYS = ("OMP_PROFILE", "PI_CODING_AGENT_DIR", "SWARM_AGENTS_FILE")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return h


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w.resolve()


def _cfg(ws):
    return ws / ".omp" / "config.yml"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(ws, mode="link", dry_run=False):
    buf = io.StringIO()
    rc = inst.install_omp(ws, mode, dry_run, buf)
    return rc, buf.getvalue()


def _warnings(out):
    return [line for line in out.splitlines() if line.startswith("WARNING")]


def _shadows(ws, home, before=()):
    return {(s.kind, s.name, s.level, s.path) for s in inst.shadow_scan(ws, home, before, env={})}


# ---------------------------------------------------------------- link


def test_link_empty_workspace(ws, home):
    rc, out = _run(ws)
    assert rc == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n  - {PKG}\n"
    assert _warnings(out) == []


def test_link_second_run_is_byte_identical_noop(ws, home):
    _run(ws)
    before = _cfg(ws).read_bytes()
    rc, out = _run(ws)
    assert rc == 0
    assert _cfg(ws).read_bytes() == before
    assert "installed" not in out and _warnings(out) == []


def test_link_keeps_other_keys_and_comments(ws, home):
    text = "# local wiring\ntheme: dark  # keep me\ntask:\n  maxRecursionDepth: 3\n"
    _write(_cfg(ws), text)
    assert _run(ws)[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == text + f"extensions:\n  - {PKG}\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("extensions:\n  - /opt/other  # mine\ntheme: dark\n", "extensions:\n  - /opt/other  # mine\n  - {pkg}\ntheme: dark\n"),
        ("extensions:\n- /opt/other\n# tail\n", "extensions:\n- /opt/other\n- {pkg}\n# tail\n"),
        ("extensions:\n  - /opt/other", "extensions:\n  - /opt/other\n  - {pkg}\n"),
    ],
    ids=["indented", "column-0", "no-final-newline"],
)
def test_link_appends_to_block_list(ws, home, text, expected):
    _write(_cfg(ws), text)
    assert _run(ws)[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == expected.format(pkg=PKG)


@pytest.mark.parametrize("spelling", ["relative", "tilde", "symlink"])
def test_link_entry_resolving_to_package_is_noop(ws, home, tmp_path, spelling):
    if spelling == "relative":
        entry = os.path.relpath(PKG, ws)
    elif spelling == "tilde":
        (home / "swarm").symlink_to(ROOT)
        entry = "~/swarm/omp"
    else:
        (tmp_path / "alias").symlink_to(PKG)
        entry = str(tmp_path / "alias")
    text = f"extensions:\n  - {entry}\n"
    _write(_cfg(ws), text)
    rc, out = _run(ws)
    assert rc == 0
    assert _cfg(ws).read_text(encoding="utf-8") == text
    assert "installed" not in out


@pytest.mark.parametrize("value", ["extensions: [/opt/x]\n", "extensions: []\n", "extensions: /opt/x\n", "extensions:\ntheme: x\n"])
def test_link_unsupported_value_exits_2_and_writes_nothing(ws, home, value):
    text = "theme: dark\n" + value
    _write(_cfg(ws), text)
    rc, out = _run(ws)
    assert rc == 2
    assert _cfg(ws).read_text(encoding="utf-8") == text
    assert PKG in out  # the manual instruction names the entry to add


def test_link_dry_run_writes_nothing(ws, home):
    rc, out = _run(ws, dry_run=True)
    assert rc == 0
    assert not (ws / ".omp").exists()
    assert f"+  - {PKG}" in out.splitlines()


# ---------------------------------------------------------------- carry-over (project array replaces the user array)


def test_carry_over_user_list(ws, home):
    _write(home / ".omp" / "agent" / "config.yml", "theme: dark\nextensions: [/x]\n")
    rc, out = _run(ws)
    assert rc == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n  - /x\n  - {PKG}\n"
    assert any("/x" in line for line in _warnings(out))


def test_carry_over_prefers_workspace_settings_json(ws, home):
    _write(home / ".omp" / "agent" / "config.yml", "extensions:\n  - /x\n")
    _write(ws / ".omp" / "settings.json", json.dumps({"extensions": ["/y"]}))
    assert _run(ws)[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n  - /y\n  - {PKG}\n"


def test_no_carry_over_when_workspace_has_key(ws, home):
    _write(home / ".omp" / "agent" / "config.yml", "extensions:\n  - /x\n")
    _write(_cfg(ws), "extensions:\n  - /opt/a\n")
    rc, out = _run(ws)
    assert rc == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n  - /opt/a\n  - {PKG}\n"
    assert _warnings(out) == []


# ---------------------------------------------------------------- shadow scan


def test_project_agent_matches_on_frontmatter_name(ws, home):
    other = _write(ws / ".omp" / "agents" / "zz-other.md", "---\nname: a01-orchestrator\n---\nbody\n")
    _write(ws / ".omp" / "agents" / "a03-architect.md", "---\nname: my-architect\n---\n")
    assert _shadows(ws, home) == {("agent", "a01-orchestrator", "project", other)}


def test_user_agent_warns(ws, home):
    user = _write(home / ".omp" / "agent" / "agents" / "a02-requirements.md", "---\nname: a02-requirements\n---\n")
    assert _shadows(ws, home) == {("agent", "a02-requirements", "user", user)}


def test_project_and_user_skills_warn(ws, home):
    project = _write(ws / ".omp" / "skills" / "a03-architect" / "SKILL.md", "no frontmatter: the dir name counts\n")
    user = _write(home / ".omp" / "agent" / "skills" / "ux" / "SKILL.md", "---\nname: a04-ux-designer\n---\n")
    assert _shadows(ws, home) == {("skill", "a03-architect", "project", project), ("skill", "a04-ux-designer", "user", user)}


def test_custom_skill_directory_warns(ws, home):
    _write(_cfg(ws), "skills:\n  customDirectories:\n    - custom\n")
    skill = _write(ws / "custom" / "a06-frontend" / "SKILL.md", "---\nname: a06-frontend\n---\n")
    assert _shadows(ws, home) == {("skill", "a06-frontend", "project", skill)}


@pytest.mark.parametrize("first", [True, False], ids=["before-package", "after-package"])
def test_extension_entry_warns_only_before_package(ws, home, tmp_path, first):
    ext = tmp_path / "ext"
    agent = _write(ext / "agents" / "x.md", "---\nname: a04-ux-designer\n---\n")
    entries = [str(ext), PKG] if first else [PKG, str(ext)]
    _write(_cfg(ws), "extensions:\n" + "".join(f"  - {e}\n" for e in entries))
    rc, out = _run(ws)
    assert rc == 0
    shadows = [line for line in _warnings(out) if str(agent) in line]
    assert len(shadows) == (1 if first else 0)
    if first:
        assert "a04-ux-designer" in shadows[0] and "(extension)" in shadows[0]


def test_lower_priority_providers_do_not_warn(ws, home):
    _write(ws / ".claude" / "skills" / "a05-backend" / "SKILL.md", "---\nname: a05-backend\n---\n")
    _write(ws / ".agents" / "skills" / "a06-frontend" / "SKILL.md", "---\nname: a06-frontend\n---\n")
    _write(ws / ".claude" / "agents" / "a05-backend.md", "---\nname: a05-backend\n---\n")
    assert _shadows(ws, home) == set()
    assert _warnings(_run(ws)[1]) == []


# ---------------------------------------------------------------- copy mode


def _package_files():
    agents = {Path("agents") / p.name: p for p in (inst.PKG / "agents").glob("*.md")}
    skills = {Path("skills") / p.parent.name / "SKILL.md": p for p in (inst.PKG / "skills").glob("*/SKILL.md")}
    return {**agents, **skills}


def test_copy_mode_copies_exactly_agents_and_skills(ws, home):
    rc, out = _run(ws, "copy")
    assert rc == 0
    omp = ws / ".omp"
    copied = {p.relative_to(omp) for p in omp.rglob("*") if p.is_file()}
    expected = _package_files()
    assert copied == set(expected)
    assert all((omp / rel).read_bytes() == src.read_bytes() for rel, src in expected.items())
    assert not _cfg(ws).exists()
    assert "no tools" in out and "no guard" in out


def test_copy_mode_leaves_config_untouched(ws, home):
    text = "extensions:\n  - /opt/other\n"
    _write(_cfg(ws), text)
    assert _run(ws, "copy")[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == text


def test_copy_mode_reports_existing_link(ws, home):
    _run(ws)
    linked = _cfg(ws).read_bytes()
    rc, out = _run(ws, "copy")
    assert rc == 0
    assert _cfg(ws).read_bytes() == linked
    assert any(str(_cfg(ws)) in line for line in _warnings(out))


def test_copy_mode_dry_run_writes_nothing(ws, home):
    rc, out = _run(ws, "copy", dry_run=True)
    assert rc == 0
    assert not (ws / ".omp").exists()
    assert "no tools" in out and "no guard" in out


def test_link_after_copy_reports_every_leftover(ws, home):
    _run(ws, "copy")
    rc, out = _run(ws)
    assert rc == 0
    warned = {line for line in _warnings(out) if "(project)" in line}
    assert len(warned) == len(_package_files())


# ---------------------------------------------------------------- the one command (build_agents.py --install-workspace)

_TREE = ("scripts", "swarm", "prompts", "agents.json", ".claude", ".grok", "omp", "skills", "hooks")


@pytest.fixture()
def tree(tmp_path):
    """A copy of the checkout the installer runs from; build_agents.py resolves ROOT from its own path."""
    t = tmp_path / "checkout"
    ignore = shutil.ignore_patterns("__pycache__", "node_modules")
    for name in _TREE:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, t / name, ignore=ignore, symlinks=True)
        else:
            t.mkdir(exist_ok=True)
            shutil.copy2(src, t / name)
    return t.resolve()


def _cli(tree, ws, home, *args):
    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    env["HOME"] = str(home)
    cmd = [sys.executable, str(tree / "scripts" / "build_agents.py"), "--install-workspace", str(ws), *args]
    return subprocess.run(cmd, cwd=tree, capture_output=True, text=True, env=env)


def _snapshot(*dirs):
    return {
        p: p.read_bytes()
        for d in dirs
        for p in sorted(Path(d).rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_install_workspace_has_no_repo_side_effect(tree, ws, home):
    hook = tree / ".grok" / "hooks" / "agent-swarm.json"
    hook_before = hook.read_bytes()
    before = _snapshot(tree)
    r = _cli(tree, ws, home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert hook.read_bytes() == hook_before
    assert _snapshot(tree) == before
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n  - {tree / 'omp'}\n"
    ws_hook = json.loads((ws / ".grok" / "hooks" / "agent-swarm.json").read_text(encoding="utf-8"))
    assert ws_hook["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == f"python3 {tree / 'hooks' / 'user_prompt_submit.py'}"
    orch = (ws / ".claude" / "skills" / "agent-swarm" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    assert "$SWARM_ROOT" not in orch and f"{tree}/scripts/orch_plan.py" in orch


@pytest.mark.parametrize("mode", ["link", "copy"])
def test_install_workspace_dry_run_writes_nothing(tree, ws, home, mode):
    before = _snapshot(tree, ws, home)
    r = _cli(tree, ws, home, "--omp-mode", mode, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot(tree, ws, home) == before
    assert list(ws.iterdir()) == []
