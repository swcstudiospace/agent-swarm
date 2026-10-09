"""Workspace install of the omp package (PKG-01..03): link merge, carry-over, shadow scan, copy mode, dry run."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from install_helpers import _ENV_KEYS, PKG, ROOT, _cfg, _snapshot, _write, _yaml, inst


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


@pytest.mark.parametrize("prefix", ["@", "file://"])
def test_link_appends_package_after_shorthand_spelling_omp_gives_no_root(ws, home, yaml_mode, prefix):
    # omp's extension-root discovery (`resolveAgainst`) expands only `~`, so `@<pkg>`/`file://<pkg>` load no agents or skills (WR-06)
    items = f"  - /opt/other\n  - {json.dumps(prefix + PKG)}\n"
    _write(_cfg(ws), f"extensions:\n{items}theme: dark\n")
    assert _run(ws)[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n{items}  - {PKG}\ntheme: dark\n"


@pytest.mark.parametrize("spelling", ["tilde", "absolute"])
def test_link_tilde_or_absolute_package_entry_is_noop(ws, home, yaml_mode, spelling):
    if spelling == "tilde":
        (home / "swarm").symlink_to(ROOT)
        entry = "~/swarm/omp"
    else:
        entry = PKG
    text = f"extensions:\n  - /opt/other\n  - {entry}\ntheme: dark\n"
    _write(_cfg(ws), text)
    assert _run(ws)[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == text


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


@pytest.mark.parametrize("key", ['"extensions"', "'extensions'", "\ufeffextensions"], ids=["double-quoted", "single-quoted", "bom"])
def test_link_recognises_quoted_or_bom_prefixed_key(ws, home, yaml_mode, key):
    text = f"{key}:\n  - /opt/a\ntheme: dark\n"
    _write(_cfg(ws), text)
    assert _run(ws)[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"{key}:\n  - /opt/a\n  - {PKG}\ntheme: dark\n"


def test_link_refuses_a_key_it_cannot_edit(ws, home, yaml_mode):
    text = "{extensions: [/opt/a], theme: dark}\n"
    _write(_cfg(ws), text)
    rc, out = _run(ws)
    assert rc == 2
    assert _cfg(ws).read_text(encoding="utf-8") == text


@pytest.mark.parametrize("entry", ["@/opt/x", "@scope/pkg", "yes", "On", "1.5", "0x1F", "2001-12-14", "NULL"])
def test_carry_over_keeps_entries_yaml_would_misread_as_strings(ws, home, yaml_mode, entry):
    _write(home / ".omp" / "agent" / "config.yml", f"extensions:\n  - {json.dumps(entry)}\n")
    assert _run(ws)[0] == 0
    text = _cfg(ws).read_text(encoding="utf-8")
    if _yaml is not None:
        assert _yaml.safe_load(text) == {"extensions": [entry, PKG]}
    else:
        assert text == f"extensions:\n  - {json.dumps(entry)}\n  - {PKG}\n"


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


def test_carry_over_user_yaml_without_key_suppresses_legacy_settings(ws, home):
    _write(home / ".omp" / "agent" / "config.yml", "theme: dark\n")
    _write(home / ".omp" / "agent" / "settings.json", json.dumps({"extensions": ["/opt/legacy"]}))
    rc, out = _run(ws)
    assert rc == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n  - {PKG}\n"
    assert _warnings(out) == []


def test_carry_over_reads_user_config_yaml(ws, home):
    _write(home / ".omp" / "agent" / "config.yaml", "extensions:\n  - /opt/fromyaml\n")
    assert _run(ws)[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n  - /opt/fromyaml\n  - {PKG}\n"


def test_carry_over_reads_pi_coding_agent_dir(ws, home, tmp_path, monkeypatch):
    agent_dir = tmp_path / "agentdir"
    _write(agent_dir / "config.yml", "extensions:\n  - /opt/fromagentdir\n")
    _write(home / ".omp" / "agent" / "config.yml", "extensions:\n  - /opt/base\n")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    assert _run(ws)[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n  - /opt/fromagentdir\n  - {PKG}\n"


@pytest.mark.parametrize("var", ["OMP_PROFILE", "PI_PROFILE"])
def test_carry_over_reads_the_profile_config(ws, home, monkeypatch, var):
    _write(home / ".omp" / "profiles" / "work" / "agent" / "config.yml", "extensions:\n  - /opt/fromprofile\n")
    _write(home / ".omp" / "agent" / "config.yml", "extensions:\n  - /opt/base\n")
    monkeypatch.setenv(var, "work")
    assert _run(ws)[0] == 0
    assert _cfg(ws).read_text(encoding="utf-8") == f"extensions:\n  - /opt/fromprofile\n  - {PKG}\n"


# ---------------------------------------------------------------- shadow scan


def test_project_agent_matches_on_frontmatter_name(ws, home):
    other = _write(ws / ".omp" / "agents" / "zz-other.md", "---\nname: a01-orchestrator\ndescription: mine\n---\nbody\n")
    _write(ws / ".omp" / "agents" / "a03-architect.md", "---\nname: my-architect\ndescription: mine\n---\n")
    assert _shadows(ws, home) == {("agent", "a01-orchestrator", "project", other)}


def test_user_agent_warns(ws, home):
    user = _write(home / ".omp" / "agent" / "agents" / "a02-requirements.md", "---\nname: a02-requirements\ndescription: mine\n---\n")
    assert _shadows(ws, home) == {("agent", "a02-requirements", "user", user)}


def test_project_and_user_skills_warn(ws, home):
    project = _write(ws / ".omp" / "skills" / "a03-architect" / "SKILL.md", "---\ndescription: the dir name counts\n---\n")
    user = _write(home / ".omp" / "agent" / "skills" / "ux" / "SKILL.md", "---\nname: a04-ux-designer\ndescription: mine\n---\n")
    assert _shadows(ws, home) == {("skill", "a03-architect", "project", project), ("skill", "a04-ux-designer", "user", user)}


@pytest.mark.parametrize(
    "text",
    [
        "---\nname: a02-requirements\n---\n",
        "---\ndescription: the stem does not count\n---\n",
        "no frontmatter\n",
        "---\nname: a02-requirements\ndescription: 1\n---\n",
    ],
    ids=["no-description", "no-name", "no-frontmatter", "non-string-description"],
)
def test_agent_omp_does_not_load_never_warns(ws, home, text):
    _write(ws / ".omp" / "agents" / "a02-requirements.md", text)
    _write(home / ".omp" / "agent" / "agents" / "a02-requirements.md", text)
    assert _shadows(ws, home) == set()


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter\n",
        "---\nname: a03-architect\n---\n",
        '---\nname: a03-architect\ndescription: ""\n---\n',
        "---\nname: a03-architect\ndescription: mine\nenabled: false\n---\n",
    ],
    ids=["no-frontmatter", "no-description", "empty-description", "disabled"],
)
def test_skill_omp_does_not_load_never_warns(ws, home, text):
    _write(ws / ".omp" / "skills" / "a03-architect" / "SKILL.md", text)
    _write(home / ".omp" / "agent" / "skills" / "a03-architect" / "SKILL.md", text)
    assert _shadows(ws, home) == set()


def test_custom_skill_directory_warns(ws, home):
    _write(_cfg(ws), "skills:\n  customDirectories:\n    - custom\n")
    skill = _write(ws / "custom" / "a06-frontend" / "SKILL.md", "---\nname: a06-frontend\ndescription: mine\n---\n")
    assert _shadows(ws, home) == {("skill", "a06-frontend", "project", skill)}


@pytest.mark.parametrize("first", [True, False], ids=["before-package", "after-package"])
def test_extension_entry_warns_only_before_package(ws, home, tmp_path, first):
    ext = tmp_path / "ext"
    agent = _write(ext / "agents" / "x.md", "---\nname: a04-ux-designer\ndescription: mine\n---\n")
    entries = [str(ext), PKG] if first else [PKG, str(ext)]
    _write(_cfg(ws), "extensions:\n" + "".join(f"  - {e}\n" for e in entries))
    rc, out = _run(ws)
    assert rc == 0
    shadows = [line for line in _warnings(out) if str(agent) in line]
    assert len(shadows) == (1 if first else 0)
    if first:
        assert "a04-ux-designer" in shadows[0] and "(extension)" in shadows[0]


def test_lower_priority_providers_do_not_warn(ws, home):
    _write(ws / ".claude" / "skills" / "a05-backend" / "SKILL.md", "---\nname: a05-backend\ndescription: mine\n---\n")
    _write(ws / ".agents" / "skills" / "a06-frontend" / "SKILL.md", "---\nname: a06-frontend\ndescription: mine\n---\n")
    _write(ws / ".claude" / "agents" / "a05-backend.md", "---\nname: a05-backend\ndescription: mine\n---\n")
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


# ---------------------------------------------------------------- never through a symlink, never outside a workspace (CR-01, WR-01)


@pytest.mark.parametrize("link", ["file", "parent"])
def test_copy_mode_refuses_a_symlinked_destination(ws, home, tmp_path, link):
    outside = tmp_path / "outside"
    victim = _write(outside / "victim.txt", "keep\n")
    if link == "file":
        (ws / ".omp" / "agents").mkdir(parents=True)
        (ws / ".omp" / "agents" / "a05-backend.md").symlink_to(victim)
    else:
        (ws / ".omp").symlink_to(outside)
    before = _snapshot(outside, ws)
    rc, _ = _run(ws, "copy")
    assert rc == 2
    assert _snapshot(outside, ws) == before
    assert sorted(p.name for p in outside.iterdir()) == ["victim.txt"]


def test_link_refuses_a_symlinked_config(ws, home, tmp_path):
    target = _write(tmp_path / "outside" / "config.yml", "theme: dark\n")
    (ws / ".omp").mkdir()
    _cfg(ws).symlink_to(target)
    rc, _ = _run(ws)
    assert rc == 2
    assert target.read_text(encoding="utf-8") == "theme: dark\n"
    assert _cfg(ws).is_symlink()


# ---------------------------------------------------------------- write-time re-validation (T-07-05 TOCTOU)


def _first_copy_dest(ws):
    agents, _ = inst.package_names()
    return ws / ".omp" / "agents" / f"{sorted(agents)[0]}.md"


def _real_files(ws):
    return [p for p in ws.rglob("*") if p.is_file() and not p.is_symlink()]


def test_copy_mode_refuses_hardlink_at_destination(ws, home, tmp_path):
    outside = tmp_path / "outside"
    victim = _write(outside / "victim.txt", "keep\n")
    dest = _first_copy_dest(ws)
    dest.parent.mkdir(parents=True)
    os.link(victim, dest)
    rc, out = _run(ws, "copy")
    assert rc == 2
    assert "hardlink" in out
    assert victim.read_bytes() == b"keep\n"
    assert _real_files(ws) == [dest]
    assert sorted(p.name for p in outside.iterdir()) == ["victim.txt"]


def test_copy_mode_write_time_refuses_symlink_at_destination(ws, home, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = _write(outside / "victim.txt", "keep\n")
    dest = _first_copy_dest(ws)
    real = inst._revalidate_destinations

    def planting(w, ds, state):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(victim)
        return real(w, ds, state)

    monkeypatch.setattr(inst, "_revalidate_destinations", planting)
    rc, out = _run(ws, "copy")
    assert rc == 2
    assert "symlink" in out
    assert victim.read_bytes() == b"keep\n"
    assert _real_files(ws) == []


def test_copy_mode_write_time_refuses_symlink_at_path_component(ws, home, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    victim = _write(outside / "victim.txt", "keep\n")
    real = inst._revalidate_destinations

    def planting(w, ds, state):
        (ws / ".omp").symlink_to(outside)
        return real(w, ds, state)

    monkeypatch.setattr(inst, "_revalidate_destinations", planting)
    rc, out = _run(ws, "copy")
    assert rc == 2
    assert "symlink" in out
    assert victim.read_bytes() == b"keep\n"
    assert sorted(p.name for p in outside.iterdir()) == ["victim.txt"]
    assert _real_files(ws) == []


def test_copy_mode_write_time_refuses_hardlink_at_destination(ws, home, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = _write(outside / "victim.txt", "keep\n")
    dest = _first_copy_dest(ws)
    real = inst._revalidate_destinations

    def planting(w, ds, state):
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.link(victim, dest)
        return real(w, ds, state)

    monkeypatch.setattr(inst, "_revalidate_destinations", planting)
    rc, out = _run(ws, "copy")
    assert rc == 2
    assert "hardlink" in out
    assert victim.read_bytes() == b"keep\n"
    assert _real_files(ws) == [dest]


def test_link_write_time_refuses_planted_config(ws, home, tmp_path, monkeypatch):
    real = inst._revalidate_destinations

    def planting(w, ds, state):
        _write(_cfg(ws), "theme: planted\n")
        return real(w, ds, state)

    monkeypatch.setattr(inst, "_revalidate_destinations", planting)
    rc, out = _run(ws)
    assert rc == 2
    assert "after the plan check" in out
    assert _cfg(ws).read_text(encoding="utf-8") == "theme: planted\n"


@pytest.mark.parametrize("where", ["home", "omp-dir", "checkout"])
def test_install_omp_refuses_home_omp_dir_and_checkout(home, tmp_path, monkeypatch, where):
    fake = tmp_path / "checkout"  # stands in for the checkout, so no test ever targets the real one
    target = {"home": home, "omp-dir": home / ".omp" / "agent", "checkout": fake / "omp"}[where]
    target.mkdir(parents=True, exist_ok=True)
    if where == "checkout":
        monkeypatch.setattr(inst, "ROOT", fake.resolve())
    before = _snapshot(tmp_path)
    for mode in ("link", "copy"):
        assert _run(target, mode)[0] == 2
    assert _snapshot(tmp_path) == before
    assert inst.preflight(target, "link") is not None


# ---------------------------------------------------------------- the one command (build_agents.py --install-workspace)


def _cli(tree, ws, home, *args):
    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    env["HOME"] = str(home)
    cmd = [sys.executable, str(tree / "scripts" / "build_agents.py"), "--install-workspace", str(ws), *args]
    return subprocess.run(cmd, cwd=tree, capture_output=True, text=True, env=env)


def test_install_workspace_has_no_repo_side_effect(tree, ws, home):
    hook = tree / ".grok" / "hooks" / "agent-swarm.json"
    hook_before = hook.read_bytes()
    before = _snapshot(tree)
    r = _cli(tree, ws, home, "--no-substrate")  # the substrate step has its own tests (test_install_substrate.py)
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
    r = _cli(tree, ws, home, "--omp-mode", mode, "--dry-run", "--no-substrate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot(tree, ws, home) == before
    assert list(ws.iterdir()) == []


@pytest.mark.parametrize("link", ["claude-skill-dir", "claude-settings", "grok-dir"])
def test_install_workspace_refuses_symlinked_claude_and_grok_destinations(tree, ws, home, tmp_path, link):
    outside = tmp_path / "outside"
    victim = _write(outside / "victim.json", "{}\n")
    if link == "claude-skill-dir":  # the review's probe: the copy would rewrite the checkout's own skill
        dest = ws / ".claude" / "skills" / "agent-swarm" / "orchestrate"
        dest.parent.mkdir(parents=True)
        dest.symlink_to(tree / "skills" / "orchestrate")
    elif link == "claude-settings":
        (ws / ".claude").mkdir()
        (ws / ".claude" / "settings.json").symlink_to(victim)
    else:
        (ws / ".grok").symlink_to(outside)
    before = _snapshot(tree, outside, ws)
    r = _cli(tree, ws, home)
    assert r.returncode == 2, r.stdout + r.stderr
    assert _snapshot(tree, outside, ws) == before
    assert sorted(p.name for p in outside.iterdir()) == ["victim.json"]
    assert not (ws / ".omp").exists()


def test_hook_command_quotes_a_checkout_path_with_spaces():
    import importlib.util
    import shlex

    spec = importlib.util.spec_from_file_location("build_agents_under_test", ROOT / "scripts" / "build_agents.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    script = Path("/tmp/my swarm/hooks/user_prompt_submit.py")
    cmd = mod.hook_command(script)
    assert shlex.split(cmd) == ["python3", str(script)]
    assert "python3 /tmp/my" not in cmd


@pytest.mark.parametrize("rel", [".claude/settings.json", ".grok/hooks/agent-swarm.json"])
def test_invalid_hook_config_is_left_in_place(tree, ws, home, rel):
    path = _write(ws / rel, "{ not json\n")
    before = path.read_bytes()
    snap = _snapshot(tree, ws)
    r = _cli(tree, ws, home, "--no-substrate")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "not valid JSON" in r.stderr
    assert path.read_bytes() == before
    assert _snapshot(tree, ws) == snap


def test_device_and_fifo_configs_are_not_read(tmp_path):
    """T-07-09: /dev/zero and a FIFO must not block, and must not look like an empty config.

    Both reads run in a child. A regression that drains `/dev/zero` or blocks on the FIFO fails on the
    timeout instead of hanging the suite; a non-None result fails too."""
    fifo = tmp_path / "config.yml"
    os.mkfifo(fifo)
    code = (
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "spec = importlib.util.spec_from_file_location('_install_omp', root / 'scripts' / '_install_omp.py')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['_install_omp'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "for raw in ('/dev/zero', sys.argv[2]):\n"
        "    if mod._read(Path(raw)) is not None:\n"
        "        raise SystemExit(1)\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code, str(ROOT), str(fifo)],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("reading /dev/zero or a FIFO did not finish within 2s")
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_read_reassembles_short_reads_under_the_cap(tmp_path, monkeypatch):
    """A regular file under the cap comes back whole even when each os.read returns one byte."""
    path = _write(tmp_path / "settings.json", '{"extensions": ["/opt/keep"]}\n')
    real_read = inst.os.read

    def one_byte(fd, n):
        return real_read(fd, 1)

    monkeypatch.setattr(inst.os, "read", one_byte)
    assert inst._read(path) == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("source", ["project-settings", "user-yaml", "user-settings"])
@pytest.mark.parametrize("kind", ["oversize", "fifo"])
def test_present_inheritance_source_that_cannot_be_read_is_not_skipped(ws, home, monkeypatch, source, kind):
    """A settings.json over the read cap (the 1 MiB branch; the cap is pointed at a few bytes) or a FIFO user
    YAML must fail the install and leave the source unchanged, not write a project list that dropped it."""
    if source == "project-settings":
        path = ws / ".omp" / "settings.json"
    elif source == "user-yaml":
        path = home / ".omp" / "agent" / "config.yml"
    else:
        path = home / ".omp" / "agent" / "settings.json"
    legacy = None
    if source == "user-yaml":
        legacy = _write(home / ".omp" / "agent" / "settings.json", '{"extensions": ["/opt/legacy"]}\n')
        legacy_before = legacy.read_bytes()
    if kind == "oversize":
        monkeypatch.setattr(inst, "_READ_LIMIT", 8)
        body = "extensions:\n  - /opt/keep\n" if source == "user-yaml" else '{"extensions": ["/opt/keep"]}\n'
        _write(path, body)
        before = path.read_bytes()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(path)
        before = None
    rc, out = _run(ws)
    assert rc == 2
    assert str(path) in out
    if before is not None:
        assert path.read_bytes() == before
    assert not _cfg(ws).exists()
    if legacy is not None:
        assert legacy.read_bytes() == legacy_before


def test_load_hook_config_reassembles_short_reads(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_agents_short_read", ROOT / "scripts" / "build_agents.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path = _write(tmp_path / "settings.json", '{"keep": true}\n')
    real_read = mod.os.read

    def one_byte(fd, n):
        return real_read(fd, 1)

    monkeypatch.setattr(mod.os, "read", one_byte)
    assert mod._load_hook_config(path) == {"keep": True}


@pytest.mark.parametrize("rel", [".claude/settings.json", ".grok/hooks/agent-swarm.json"])
def test_unquoted_stop_hook_is_removed_unless_the_flag_replaces_it(tree, ws, home, rel):
    """An unquoted on_a01_complete.py path still matches. Without the flag it is removed; with the flag this
    checkout's quoted command replaces it. The unrelated hook stays either way."""
    import shlex

    old = "python3 /old checkout/hooks/on_a01_complete.py"
    unrelated = "python3 /opt/hooks/check_on_a01_complete.py"
    payload = {
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": old}]},
                {"hooks": [{"type": "command", "command": unrelated}]},
            ]
        }
    }
    path = _write(ws / rel, json.dumps(payload))
    r = _cli(tree, ws, home, "--no-substrate")
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(path.read_text(encoding="utf-8"))
    commands = [entry["command"] for group in data["hooks"]["Stop"] for entry in group["hooks"]]
    assert commands == [unrelated]
    r = _cli(tree, ws, home, "--no-substrate", "--with-a01-complete-hook")
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(path.read_text(encoding="utf-8"))
    commands = [entry["command"] for group in data["hooks"]["Stop"] for entry in group["hooks"]]
    expected = f"python3 {shlex.quote(str(tree / 'hooks' / 'on_a01_complete.py'))}"
    assert commands == [unrelated, expected]


# ---------------------------------------------------------------- copy mode under a shadowing ancestor (T-07-20)


def _pkg_skill():
    _, skills = inst.package_names()
    return sorted(skills)[0]


def _pkg_agent():
    agents, _ = inst.package_names()
    return sorted(agents)[0]


def _ancestor_skill(parent, name):
    return _write(
        parent / ".omp" / "skills" / name / "SKILL.md",
        f"---\nname: {name}\ndescription: ancestor copy\n---\nancestor\n",
    )


def _ancestor_agent(parent, slug):
    return _write(
        parent / ".omp" / "agents" / f"{slug}.md",
        f"---\nname: {slug}\ndescription: ancestor copy\n---\nancestor\n",
    )


def test_copy_mode_refuses_under_shadowing_ancestor_skill(ws, home):
    skill = _pkg_skill()
    planted = _ancestor_skill(ws.parent, skill)
    before = _snapshot(ws, home)
    rc, out = _run(ws, "copy")
    assert rc == 2
    assert "guard-less" in out and skill in out and "--allow-shadowed-copy" in out
    assert "Nothing was written" in out
    assert not (ws / ".omp").exists()
    assert _snapshot(ws, home) == before
    assert planted.read_text(encoding="utf-8").endswith("ancestor\n")
    problem = inst.preflight(ws, "copy")
    assert problem is not None and "guard-less" in problem


def test_copy_mode_refuses_under_shadowing_ancestor_agent(ws, home):
    slug = _pkg_agent()
    _ancestor_agent(ws.parent, slug)
    rc, out = _run(ws, "copy")
    assert rc == 2
    assert "guard-less" in out and slug in out
    assert not (ws / ".omp").exists()


def test_copy_mode_dry_run_refuses_under_shadowing_ancestor(ws, home):
    _ancestor_skill(ws.parent, _pkg_skill())
    rc, out = _run(ws, "copy", dry_run=True)
    assert rc == 2
    assert "guard-less" in out
    assert not (ws / ".omp").exists()


def test_copy_mode_opt_in_proceeds_with_loud_warnings(ws, home):
    skill = _pkg_skill()
    _ancestor_skill(ws.parent, skill)
    buf = io.StringIO()
    rc = inst.install_omp(ws, "copy", False, buf, home, allow_shadowed_copy=True)
    assert rc == 0
    out = buf.getvalue()
    assert "no tools" in out and "no guard" in out  # the COPY_WARNING stays loud on opt-in
    assert any(
        line.startswith("WARNING shadow:") and skill in line and "(project)" in line
        for line in out.splitlines()
    )
    assert (ws / ".omp" / "skills" / skill / "SKILL.md").is_file()


def test_copy_mode_opt_in_preflight_passes(ws, home):
    _ancestor_skill(ws.parent, _pkg_skill())
    assert inst.preflight(ws, "copy", home, allow_shadowed_copy=True) is None


def test_link_mode_under_shadowing_ancestor_warns_only(ws, home):
    """Link mode keeps full tools/guard, so an ancestor shadow stays a warning, never a refusal."""
    skill = _pkg_skill()
    _ancestor_skill(ws.parent, skill)
    rc, out = _run(ws)
    assert rc == 0
    assert any(skill in line and "(project)" in line for line in _warnings(out))
    assert _cfg(ws).exists()


def test_install_workspace_copy_refusal_and_opt_in_flag(tree, ws, home):
    skill = _pkg_skill()
    _ancestor_skill(ws.parent, skill)
    before = _snapshot(tree, ws)
    r = _cli(tree, ws, home, "--omp-mode", "copy", "--no-substrate")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "guard-less" in (r.stdout + r.stderr)
    assert _snapshot(tree, ws) == before
    assert not (ws / ".omp").exists()
    r = _cli(tree, ws, home, "--omp-mode", "copy", "--no-substrate", "--allow-shadowed-copy")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (ws / ".omp" / "skills" / skill / "SKILL.md").is_file()


def test_allow_shadowed_copy_flag_usage_errors(tree, ws, home):
    before = _snapshot(tree, ws, home)
    r = _cli(tree, ws, home, "--allow-shadowed-copy")
    assert r.returncode == 2, r.stdout + r.stderr
    r = _cli(tree, ws, home, "--install-workspace", str(ws), "--allow-shadowed-copy", "--no-substrate")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "needs --omp-mode copy" in (r.stdout + r.stderr)
    assert _snapshot(tree, ws, home) == before
