"""Cursor agent export: generated .cursor/agents, drift check, and the substrate-free installer."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from swarm.manifest import load_manifest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import _install_cursor as cursor_install  # noqa: E402
import build_agents  # noqa: E402

CURSOR = ROOT / ".cursor" / "agents"
DOCUMENTED = {"name", "description", "model", "readonly", "is_background"}
FORBIDDEN = ("spawn_subagent", "run_terminal_command", "mcp__")
AGENT_TOOL = re.compile(r"(?<![A-Za-z])Agent(?![A-Za-z])")


def _fm_lines(text: str) -> list[str]:
    lines = text.split("\n")
    assert lines[0] == "---", text[:40]
    return lines[1:lines.index("---", 1)]


def _fm(text: str) -> dict[str, str]:
    fm = {}
    for line in _fm_lines(text):
        key, value = line.split(": ", 1)
        fm[key] = json.loads(value) if value.startswith('"') else value
    return fm


def _preamble(text: str) -> str:
    head, _, _rest = text.partition("<agent ")
    return head


def _snapshot(root: Path) -> dict[str, bytes]:
    out = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or path.is_file():
            rel = path.relative_to(root).as_posix()
            out[rel] = path.read_bytes() if path.is_file() and not path.is_symlink() else b"<symlink>"
    return out


def test_exactly_fifteen_cursor_agents():
    agents = load_manifest()
    slugs = {a["slug"] for a in agents}
    assert len(slugs) == 15
    assert {p.name for p in CURSOR.glob("*.md")} == {f"{s}.md" for s in slugs}
    assert not list(CURSOR.glob("*")) or {p.suffix for p in CURSOR.iterdir()} <= {".md"}


def test_frontmatter_is_cursor_documented_fields():
    for agent in load_manifest():
        text = (CURSOR / f"{agent['slug']}.md").read_text(encoding="utf-8")
        fm = _fm(text)
        assert set(fm) <= DOCUMENTED
        assert set(fm) == {"name", "description", "model"}
        assert fm["name"] == agent["slug"]
        assert fm["model"] == "inherit"
        assert fm["description"].startswith(f"{agent['id']} {agent['code']} — ")
        assert "tools:" not in "\n".join(_fm_lines(text))


def test_preamble_uses_cursor_tools_and_swarm_root():
    for agent in load_manifest():
        text = (CURSOR / f"{agent['slug']}.md").read_text(encoding="utf-8")
        pre = _preamble(text)
        for bad in FORBIDDEN:
            assert bad not in pre, agent["slug"]
        assert AGENT_TOOL.search(pre) is None, agent["slug"]
        assert 'python3 "$SWARM_ROOT/scripts/<tool>.py" --root <target repo>' in pre
        assert "advisory" in pre and "record nothing" in pre and "APPROVED" in pre
        assert "Greptile" in pre and "Desk Quality" in pre
        assert "SWARM_ROOT" in pre
        prompt = (ROOT / agent["prompt"]).read_text(encoding="utf-8").strip()
        assert text.rstrip().endswith(prompt)
        if agent["id"] == "A01":
            assert "Task tool" in pre
            assert "must not call the Task tool" not in pre
        else:
            assert "must not call the Task tool" in pre
            assert "spawn_subagent" not in text
            assert "run_terminal_command" not in text
            assert "mcp__" not in text


def test_build_agents_check_passes_and_fails_on_drift(tmp_path):
    clean = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_agents.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    target = CURSOR / "a05-backend.md"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "\n# drift\n", encoding="utf-8")
    try:
        drifted = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_agents.py"), "--check"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert drifted.returncode == 1
        assert "stale:" in drifted.stdout and ".cursor/agents/a05-backend.md" in drifted.stdout
    finally:
        target.write_text(original, encoding="utf-8")


def test_render_matches_committed_file():
    agent = next(a for a in load_manifest() if a["slug"] == "a08-qa")
    assert build_agents.render_cursor(agent, {}) == (CURSOR / "a08-qa.md").read_text(encoding="utf-8")


def test_installer_dry_run_lists_only_cursor_paths_and_writes_nothing(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    (target / "scripts").mkdir()
    (target / "scripts" / "unrelated.py").write_text("print('not swarm')\n", encoding="utf-8")
    (target / ".mcp.json").write_text("{}\n", encoding="utf-8")
    before = _snapshot(target)
    buf_out, buf_err = __import__("io").StringIO(), __import__("io").StringIO()
    rc = cursor_install.install_cursor(target, dry_run=True, out=buf_out, err=buf_err)
    assert rc == 0, buf_err.getvalue()
    lines = [line for line in buf_out.getvalue().splitlines() if line]
    assert lines
    assert all(line.startswith(".cursor/") and ".." not in line for line in lines)
    assert _snapshot(target) == before
    assert not (target / ".cursor").exists()


def test_installer_writes_only_cursor_and_second_run_is_noop(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    (target / "README.md").write_text("keep\n", encoding="utf-8")
    (target / ".mcp.json").write_text('{"mcpServers":{}}\n', encoding="utf-8")
    assert cursor_install.install_cursor(target) == 0
    files = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
    assert files == {
        "README.md",
        ".mcp.json",
        cursor_install.STAMP_REL,
        cursor_install.RULE_REL,
        *(f".cursor/agents/{a['slug']}.md" for a in load_manifest()),
    }
    assert (target / ".mcp.json").read_text(encoding="utf-8") == '{"mcpServers":{}}\n'
    stamp = json.loads((target / cursor_install.STAMP_REL).read_text(encoding="utf-8"))
    assert stamp["installer"] == "agent-swarm-cursor"
    for rel, digest in stamp["files"].items():
        assert rel.startswith(".cursor/")
        body = (target / rel).read_text(encoding="utf-8")
        assert hashlib.sha256(body.encode()).hexdigest() == digest
    mtimes = {p: p.stat().st_mtime_ns for p in target.rglob("*") if p.is_file()}
    out, err = __import__("io").StringIO(), __import__("io").StringIO()
    assert cursor_install.install_cursor(target, dry_run=True, out=out, err=err) == 0
    assert out.getvalue() == ""
    assert cursor_install.install_cursor(target) == 0
    assert {p: p.stat().st_mtime_ns for p in target.rglob("*") if p.is_file()} == mtimes


def test_installer_check_and_foreign_file(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    err = __import__("io").StringIO()
    out = __import__("io").StringIO()
    assert cursor_install.install_cursor(target, check=True, out=out, err=err) == 1
    assert _snapshot(target) == {}
    assert "stale:" in out.getvalue()
    parts = [part.strip() for part in out.getvalue().split("stale:", 1)[1].split(",") if part.strip()]
    assert parts and all(part.startswith(".cursor/") for part in parts)
    assert cursor_install.install_cursor(target) == 0
    out, err = __import__("io").StringIO(), __import__("io").StringIO()
    assert cursor_install.install_cursor(target, check=True, out=out, err=err) == 0
    path = target / ".cursor" / "agents" / "a09-reviewer.md"
    edited = path.read_text(encoding="utf-8") + "\nlocal edit\n"
    path.write_text(edited, encoding="utf-8")
    before = _snapshot(target)
    err = __import__("io").StringIO()
    assert cursor_install.install_cursor(target, err=err) == 2
    assert "did not write" in err.getvalue()
    assert _snapshot(target) == before
    assert path.read_text(encoding="utf-8") == edited


def test_installer_updates_a_file_it_wrote(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    target = tmp_path / "repo"
    target.mkdir()
    # A slim source: the real export plus agents.json, then one agent changes.
    (source / "agents.json").write_text((ROOT / "agents.json").read_text(encoding="utf-8"), encoding="utf-8")
    for rel, text in cursor_install.export_sources().items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert cursor_install.install_cursor(target, source=source) == 0
    changed = source / ".cursor" / "agents" / "a02-requirements.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "\n# regenerated\n", encoding="utf-8")
    assert cursor_install.install_cursor(target, source=source) == 0
    assert (target / ".cursor" / "agents" / "a02-requirements.md").read_text(encoding="utf-8").endswith("# regenerated\n")


def test_installer_refuses_symlink_and_checkout(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    before = _snapshot(real)
    err = __import__("io").StringIO()
    assert cursor_install.install_cursor(link, err=err) == 2
    assert "symlink" in err.getvalue()
    assert _snapshot(real) == before

    nested = tmp_path / "nested"
    outside = tmp_path / "outside"
    outside.mkdir()
    nested.mkdir()
    (nested / ".cursor").symlink_to(outside)
    before = _snapshot(outside)
    err = __import__("io").StringIO()
    assert cursor_install.install_cursor(nested, err=err) == 2
    assert _snapshot(outside) == before
    assert cursor_install.install_cursor(ROOT, err=__import__("io").StringIO()) == 2
    home = Path.home()
    if home.is_dir():
        assert cursor_install.install_cursor(home, err=__import__("io").StringIO()) == 2


def test_install_cursor_cli_dry_run(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    env = os.environ.copy()
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_agents.py"), "--install-cursor", str(target), "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert all(line.startswith(".cursor/") for line in r.stdout.splitlines() if line)
    assert not (target / ".cursor").exists()
    assert "substrate" not in r.stdout
    both = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_agents.py"), "--install-cursor", str(target), "--install-workspace", str(target)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert both.returncode == 2


def test_skill_frontmatter_is_name_and_description_only():
    text = (ROOT / "grokbot" / "skills" / "swarm-cloud-dispatch" / "SKILL.md").read_text(encoding="utf-8")
    fm = _fm(text)
    assert set(fm) == {"name", "description"}
    assert fm["name"] == "swarm-cloud-dispatch"
    assert "a01-orchestrator" in text
    assert "draft pull request" in fm["description"]
    assert "_install_cursor.py" in text
    assert "SWARM_ROOT" in text


_SUBSTRATE_PATHS = ("swarm/substrate_mcp.json", "scripts/_install_substrate.py")
_BRANCH_NAME = re.compile(r"^[A-Za-z0-9._/-]+$")


def _git_bytes(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, env=env)


def _ref_exists(ref: str) -> bool:
    return _git_bytes(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]).returncode == 0


def _branch_ok(name: str) -> bool:
    return bool(name) and not name.startswith("-") and ".." not in name.split("/") and bool(_BRANCH_NAME.fullmatch(name))


def _base_names() -> list[str]:
    """PR base first (GITHUB_BASE_REF), then main. Names only, never a remote URL."""
    names: list[str] = []
    env_base = os.environ.get("GITHUB_BASE_REF", "").strip()
    if _branch_ok(env_base):
        names.append(env_base)
    if "main" not in names:
        names.append("main")
    return names


def _candidate_refs() -> list[str]:
    refs: list[str] = []
    for name in _base_names():
        for ref in (f"origin/{name}", name):
            if ref not in refs:
                refs.append(ref)
    return refs


def _fetch_base(name: str) -> bool:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = _git_bytes(
        ["fetch", "--depth=1", "origin", f"+refs/heads/{name}:refs/remotes/origin/{name}"],
        env=env,
    )
    return proc.returncode == 0 and _ref_exists(f"origin/{name}")


def substrate_base_ref() -> str:
    """Commit-ish of the PR base. A shallow pull checkout has no local main until fetched."""
    for ref in _candidate_refs():
        if _ref_exists(ref):
            return ref
    for name in _base_names():
        if _fetch_base(name):
            return f"origin/{name}"
    pytest.skip(
        "no base ref for the substrate byte compare; "
        f"none of {', '.join(_candidate_refs())} resolved and "
        f"git fetch --depth=1 origin of {', '.join(_base_names())} did not create one"
    )


def test_substrate_spec_is_byte_identical_to_main():
    ref = substrate_base_ref()
    for rel in _SUBSTRATE_PATHS:
        show = _git_bytes(["show", f"{ref}:{rel}"])
        assert show.returncode == 0, f"{ref}:{rel} is not in the base"
        assert (ROOT / rel).read_bytes() == show.stdout, rel


def test_rule_uses_documented_frontmatter():
    text = (ROOT / ".cursor" / "rules" / "agent-swarm.mdc").read_text(encoding="utf-8")
    fm = _fm(text)
    assert set(fm) <= {"description", "globs", "alwaysApply"}
    assert "description" in fm and fm["alwaysApply"] == "false"
    assert 'python3 "$SWARM_ROOT/scripts/<tool>.py"' in text
