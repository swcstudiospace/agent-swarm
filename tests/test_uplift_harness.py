"""Harness tests for dual Claude/Grok agent generation and TS script_base."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_build_agents_writes_grok_and_claude():
    import subprocess, sys

    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_agents.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for slug in ("a01-orchestrator", "a15-docs"):
        claude = (ROOT / ".claude" / "agents" / f"{slug}.md").read_text()
        grok = (ROOT / ".grok" / "agents" / f"{slug}.md").read_text()
        assert claude.startswith("---\n")
        assert "model: inherit" in claude.split("---", 2)[1]
        assert "prompt_mode: full" in grok
        assert "agents_md: true" in grok
        assert "model: opus" not in claude and "model: sonnet" not in claude


def test_ts_script_base_dry_run_json():
    import os, shutil, subprocess

    ts = ROOT / "scripts" / "ts" / "script_base.ts"
    assert ts.exists()
    bun = shutil.which("bun") or "/root/.local/share/reflex/bun/bin/bun"
    if not os.path.exists(bun):
        return
    r = subprocess.run(
        [bun, "test", str(ROOT / "tests" / "ts" / "script_base.test.ts")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
