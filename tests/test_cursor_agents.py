"""Cursor agent export: generated .cursor/agents, drift check, and the substrate-free installer."""
import hashlib
import json
import os
import re
import sqlite3
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
        body = build_agents.apply_cursor_substitutions(agent["id"], prompt)
        assert text.rstrip().endswith(body)
        if agent["id"] in build_agents.CURSOR_BODY_SUBSTITUTIONS:
            assert body != prompt
        else:
            assert body == prompt
        if agent["id"] == "A01":
            assert "Task tool" in pre
            assert "must not call the Task tool" not in pre
        else:
            assert "must not call the Task tool" in pre
            assert "spawn_subagent" not in text
            assert "run_terminal_command" not in text
            assert "mcp__" not in text


_MERGE_GRANT = re.compile(r"auto-mergeable|merge semver-compatible|producer still merges")
_BRANCH_FORBIDDEN = ("swarm/<task_id>", "swarm/T-", "auto-fix/")
_DESK_BRANCH_RE = "^bot-0[0-6]-[a-z0-9-]+$"
_MERGE_PROHIBITION = (
    "Never merge a pull request, enable auto-merge, push to a protected branch, or delete a branch."
)


def test_cursor_agents_forbid_merge_and_auto_merge():
    seen = 0
    for agent in load_manifest():
        text = (CURSOR / f"{agent['slug']}.md").read_text(encoding="utf-8")
        pre = _preamble(text)
        body = text[len(pre):]
        seen += 1
        assert _MERGE_PROHIBITION in pre, agent["slug"]
        assert "open a draft PR and report instead" in pre, agent["slug"]
        assert _MERGE_GRANT.search(text) is None, agent["slug"]
        assert _MERGE_GRANT.search(body) is None, agent["slug"]
    assert seen == 15


def test_cursor_agents_ban_headless_runners_even_on_request():
    """The Cursor line names both runners and overrides the shared rule's operator exception."""
    for path in sorted(CURSOR.glob("*.md")):
        pre = _preamble(path.read_text(encoding="utf-8"))
        line = next((row for row in pre.splitlines() if row.startswith("- Do not start an unattended headless runner")), "")
        assert "scripts/swarm_run.py" in line and "hooks/autonomous_run.py" in line, path.name
        assert "even when an assignment asks for one" in line, path.name
        assert "overrides the shared runner rule" in line, path.name


def test_cursor_agents_keyless_advisory_is_not_e_dep():
    for agent in load_manifest():
        text = (CURSOR / f"{agent['slug']}.md").read_text(encoding="utf-8")
        pre = _preamble(text)
        assert "Gate scripts record nothing." in text, agent["slug"]
        assert "Nothing this session produces counts as APPROVED." in text, agent["slug"]
        assert "SWARM_ED25519_KEY" in pre and "SWARM_SIGNING_KEY" in pre and "SWARM_REQUIRE_KEY" in pre
        assert "is not E-DEP" in pre, agent["slug"]
        assert "unsigned task.assign" in pre, agent["slug"]
        assert "do not sign" in pre, agent["slug"]
        assert "reject unsigned assignments" in pre, agent["slug"]
        assert "signing key" in pre and "E-DEP" in pre
        assert "Task Store" in pre and "python3" in pre and "still E-DEP" in pre


def test_cursor_agents_use_desk_branch_names():
    seen = 0
    for agent in load_manifest():
        text = (CURSOR / f"{agent['slug']}.md").read_text(encoding="utf-8")
        seen += 1
        for bad in _BRANCH_FORBIDDEN:
            assert bad not in text, (agent["slug"], bad)
        assert _DESK_BRANCH_RE in text, agent["slug"]
        assert "bot-0N-<seat>/<task_id>" in text, agent["slug"]
        assert "ownership.yaml" in text, agent["slug"]
    assert seen == 15
    # Cursor rewrites these; the shared prompts stay the claude/grok/omp contract.
    assert "swarm/<task_id>" in (ROOT / "prompts/A05-backend.md").read_text(encoding="utf-8")
    assert "swarm/<task_id>" in (ROOT / "prompts/A06-frontend.md").read_text(encoding="utf-8")
    assert "auto-fix/*" in (ROOT / "prompts/A09-reviewer.md").read_text(encoding="utf-8")


_GATE_SCRIPTS = ("qa_gate", "rev_gate", "sec_gate", "rel_plan")
_KEY_ASSIGN = re.compile(r"SWARM_(?:SIGNING_KEY|ED25519_KEY|ALLOW_INSECURE_DEV_KEY)\s*=")
_STRIPPED_KEYS = ("SWARM_SIGNING_KEY", "SWARM_ED25519_KEY", "SWARM_REQUIRE_KEY", "SWARM_ALLOW_INSECURE_DEV_KEY",
                  "SWARM_AGENT_SESSION")


def test_cursor_gate_scripts_are_non_recording_previews():
    seen = 0
    for agent in load_manifest():
        text = (CURSOR / f"{agent['slug']}.md").read_text(encoding="utf-8")
        pre = _preamble(text)
        seen += 1
        assert "SWARM_AGENT_SESSION=1" in pre, agent["slug"]
        for script in _GATE_SCRIPTS:
            assert script in pre, (agent["slug"], script)
        assert "Never set SWARM_SIGNING_KEY, SWARM_ED25519_KEY or SWARM_ALLOW_INSECURE_DEV_KEY" in pre
        assert _KEY_ASSIGN.search(text) is None, agent["slug"]
        assert "never ingest a gate result" in pre, agent["slug"]
        assert "advisory preview recorded no verdict rows; human records the gate" in pre, agent["slug"]
        assert "stops the scheduling loop" in pre, agent["slug"]
        assert "Do not use bun scripts/ts/sec_gate.ts" in pre, agent["slug"]
        assert "through python3 or the bun twin" not in pre, agent["slug"]
        assert "Gate scripts record nothing." in text
        assert "Nothing this session produces counts as APPROVED." in text
    assert seen == 15
    a01 = (CURSOR / "a01-orchestrator.md").read_text(encoding="utf-8")
    assert a01.count('--transition <id> BLOCKED --reason "advisory preview recorded no verdict rows; human records the gate"') == 2
    assert a01.count("stop the scheduling loop and do not spawn tasks that depend on it") == 2
    assert a01.count("--ingest --advisory") == 2
    assert "the task stays leased" not in a01
    sec = (CURSOR / "a10-security.md").read_text(encoding="utf-8")
    sec_body = sec.split("</swarm_runtime>", 1)[1]
    assert "bun scripts/ts/sec_gate.ts" not in sec_body
    assert "python3 scripts/sec_gate.py" in sec_body


def _verdict_rows(swarm: Path) -> list[dict]:
    con = sqlite3.connect(swarm / "tasks.db")
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM verdicts ORDER BY id")]
    con.close()
    return rows


def _events_of(swarm: Path, etype: str) -> list[dict]:
    path = swarm / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if f'"{etype}"' in ln]


def _advisory_file(swarm: Path) -> dict:
    return json.loads((swarm / "verdicts" / "X-qa.quality.json").read_text(encoding="utf-8"))


def test_cursor_gate_preview_records_no_rows(tmp_path, swarm_dir):
    """A leased non-dry-run quality gate records nothing under SWARM_AGENT_SESSION=1.

    #14 (892a8e1) is on main: a keyless gate without that variable also records no rows.
    It writes an unsigned advisory envelope and emits gate.verdict.unrecorded. The session
    run keeps the agent-session reason.
    """
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    (work / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_KEYS}
    env["SWARM_DIR"] = str(swarm_dir)
    plan = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "orch_plan.py"), "--brief-text", "x",
         "--pattern", "feature", "--prefix", "X", "--risk-class", "low", "--repo", str(work), "--json"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert plan.returncode == 0, plan.stdout + plan.stderr
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    for state in ("CLAIMED", "IN_PROGRESS"):
        ts.transition("X-qa", state)
    assert ts.get("X-qa")["state"] == "IN_PROGRESS"
    assert "dry_run" not in ts.get("X-qa")["notes_json"]
    gate = [sys.executable, str(ROOT / "scripts" / "qa_gate.py"),
            "--root", str(work), "--task-id", "X-qa", "--json"]
    session = subprocess.run(gate, capture_output=True, text=True, env={**env, "SWARM_AGENT_SESSION": "1"}, cwd=ROOT)
    assert session.returncode in (0, 1), session.stdout + session.stderr
    assert _verdict_rows(swarm_dir) == []
    session_events = _events_of(swarm_dir, "gate.verdict.unrecorded")
    assert any(e["payload"].get("task_id") == "X-qa" and "agent session" in e["payload"].get("reason", "")
               for e in session_events)
    session_env = _advisory_file(swarm_dir)
    assert session_env["payload"]["advisory"] is True and session_env["sig"] is None
    control = subprocess.run(gate, capture_output=True, text=True, env=env, cwd=ROOT)
    assert control.returncode in (0, 1), control.stdout + control.stderr
    assert _verdict_rows(swarm_dir) == []
    control_events = _events_of(swarm_dir, "gate.verdict.unrecorded")
    assert any("no signing key configured" in e["payload"].get("reason", "") for e in control_events)
    control_env = _advisory_file(swarm_dir)
    assert control_env["payload"]["advisory"] is True and control_env["sig"] is None


def test_cursor_blocked_gate_stops_downstream_without_approving(tmp_path, swarm_dir):
    """The handoff A01 is told to make: a leased gate moved to BLOCKED records no rows,
    leaves the producer unapproved, and keeps build unscheduled."""
    work = tmp_path / "work"
    work.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_KEYS}
    env["SWARM_DIR"] = str(swarm_dir)
    plan = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "orch_plan.py"), "--brief-text", "x",
         "--pattern", "feature", "--prefix", "H", "--risk-class", "medium", "--repo", str(work), "--json"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert plan.returncode == 0, plan.stdout + plan.stderr
    from swarm.results import reconcile
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    for tid in ("H-req", "H-arch", "H-data", "H-be"):
        for state in ("CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
            ts.transition(tid, state)
    for tid in ("H-qa", "H-rev", "H-sec"):
        for state in ("CLAIMED", "IN_PROGRESS", "BLOCKED"):
            ts.transition(tid, state, reason="advisory preview recorded no verdict rows; human records the gate")
    ready = {t["task_id"] for t in ts.ready()}
    assert "H-build" not in ready
    reconcile(ts, ts.get("H-be")["correlation_id"], lambda *a, **k: None)
    assert ts.get("H-be")["state"] == "IN_REVIEW"
    assert ts.get("H-qa")["state"] == "BLOCKED"
    assert _verdict_rows(swarm_dir) == []


def _task_states(swarm: Path, tid: str) -> tuple[str, list[str]]:
    con = sqlite3.connect(swarm / "tasks.db")
    state = con.execute("SELECT state FROM tasks WHERE task_id=?", (tid,)).fetchone()[0]
    hist = [row[0] for row in con.execute(
        "SELECT to_state FROM transitions WHERE task_id=? ORDER BY id", (tid,))]
    con.close()
    return state, hist


def test_cursor_advisory_ingest_skips_approval(tmp_path, monkeypatch):
    """A keyless requirements result exits 2 on plain ingest and stays IN_REVIEW under --advisory.

    The parent process's signing keys are cleared before either subprocess starts.
    """
    for key in _STRIPPED_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert "SWARM_SIGNING_KEY" not in os.environ
    assert "SWARM_ED25519_KEY" not in os.environ

    def run(prefix: str, advisory: bool) -> subprocess.CompletedProcess[str]:
        swarm = tmp_path / prefix
        env = os.environ.copy()
        env["SWARM_DIR"] = str(swarm)
        plan = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "orch_plan.py"), "--brief-text", "x",
             "--pattern", "feature", "--prefix", prefix, "--risk-class", "low", "--repo", str(tmp_path), "--json"],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        assert plan.returncode == 0, plan.stdout + plan.stderr
        result = tmp_path / f"{prefix}.json"
        result.write_text(json.dumps({"task_id": f"{prefix}-req", "state": "IN_REVIEW", "summary_md": "spec"}),
                          encoding="utf-8")
        cmd = [sys.executable, str(ROOT / "scripts" / "orch_status.py"), "--ingest", str(result), "--json"]
        if advisory:
            cmd.append("--advisory")
        got = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
        got.swarm = swarm  # type: ignore[attr-defined]
        return got

    plain = run("P", False)
    assert plain.returncode == 2, plain.stdout + plain.stderr
    assert "E-POLICY" in plain.stdout
    state, hist = _task_states(plain.swarm, "P-req")  # type: ignore[attr-defined]
    assert state == "IN_REVIEW"
    assert "APPROVED" not in hist and "DONE" not in hist

    saved = run("A", True)
    assert saved.returncode == 0, saved.stdout + saved.stderr
    body = json.loads(saved.stdout)
    assert body["status"] == "ok"
    assert body["task"]["state"] == "IN_REVIEW"
    state, hist = _task_states(saved.swarm, "A-req")  # type: ignore[attr-defined]
    assert state == "IN_REVIEW"
    assert "APPROVED" not in hist and "DONE" not in hist
    assert any("advisory, approval not attempted" in line for line in body["reconcile"])


def test_advisory_skip_events_use_each_task_id(tmp_path, swarm_dir):
    """A second --advisory ingest must not stamp the first task's skip event with --task-id."""
    env = os.environ.copy()
    env["SWARM_DIR"] = str(swarm_dir)
    plan = {
        "tasks": [
            {"id": "one", "capability": "req.spec", "agent": "A02", "title": "one", "depends_on": [], "gates": []},
            {"id": "two", "capability": "req.spec", "agent": "A02", "title": "two", "depends_on": [], "gates": []},
        ]
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    planned = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "orch_plan.py"), "--plan", str(plan_path),
         "--prefix", "T", "--json", "--repo", str(tmp_path)],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr

    def ingest(tid: str, *extra: str) -> subprocess.CompletedProcess[str]:
        result = tmp_path / f"{tid}.json"
        result.write_text(json.dumps({"task_id": tid, "state": "IN_REVIEW", "summary_md": tid}), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "orch_status.py"), "--ingest", str(result),
             "--advisory", "--json", *extra],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )

    first = ingest("T-one")
    assert first.returncode == 0, first.stdout + first.stderr
    second = ingest("T-two", "--task-id", "T-two")
    assert second.returncode == 0, second.stdout + second.stderr
    skipped = _events_of(swarm_dir, "task.approval.skipped")
    by_task = {}
    for event in skipped:
        by_task.setdefault(event["task_id"], []).append(event["payload"]["task_id"])
    assert by_task["T-one"] == ["T-one", "T-one"]
    assert by_task["T-two"] == ["T-two"]
    assert all(event["task_id"] == event["payload"]["task_id"] for event in skipped)


def test_cursor_substitution_raises_when_pattern_missing():
    table = build_agents.CURSOR_BODY_SUBSTITUTIONS
    assert set(table) >= {"A05", "A06", "A09", "A14"}
    olds = [old for pairs in table.values() for old, _ in pairs]
    for new in (new for pairs in table.values() for _, new in pairs):
        for old in olds:
            assert old not in new, old
    for agent_id, substitutions in table.items():
        with pytest.raises(ValueError, match=agent_id):
            build_agents.apply_cursor_substitutions(agent_id, "pattern missing")
        body = "\n".join(old for old, _ in substitutions)
        build_agents.apply_cursor_substitutions(agent_id, body)
        for old, _ in substitutions:
            with pytest.raises(ValueError, match=agent_id):
                build_agents.apply_cursor_substitutions(agent_id, f"{body}\n{old}")


def test_a02_change_object_does_not_reuse_task_state():
    rendered = (CURSOR / "a02-requirements.md").read_text(encoding="utf-8")
    prompt = (ROOT / "prompts/A02-requirements.md").read_text(encoding="utf-8")
    for text in (rendered, prompt):
        assert '"state": "proposed|approved|rejected"' not in text
        assert '"change_state": "proposed|approved|rejected"' in text


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
