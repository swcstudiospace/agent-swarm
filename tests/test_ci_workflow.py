"""Structural pin of the D-10 CI drift gate (stdlib only, no YAML parser)."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"

REQUIRED_RUNS = [
    "python3 -m pytest",
    "python3 scripts/build_agents.py --check",
    "python3 scripts/build_trae_agents.py --check",
    "bun test tests/ts",
    "bun run test",
]

FORBIDDEN = re.compile(
    r"\b(pip3?|uv|poetry|pipenv)\s+(install|sync|add)\b"
    r"|\bnpm\s+(install|ci|i)\b|\b(bun|pnpm|yarn)\s+(install|add|i)\b"
    r"|requirements\S*\.txt"
)


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _runs() -> list[str]:
    runs = []
    for line in _text().splitlines():
        m = re.match(r"\s*(?:-\s+)?run:\s*(.+?)\s*$", line)
        if m:
            runs.append(m.group(1).strip("'\""))
    return runs


def test_workflow_runs_exactly_the_gate_commands_in_order():
    assert _runs() == REQUIRED_RUNS


def test_no_bare_root_bun_test():
    assert "bun test" not in _runs()


def test_omp_suite_runs_in_omp_dir():
    text = _text()
    assert re.search(r"working-directory:\s*omp\s*\n\s*run:\s*bun run test\s*$", text, re.M)


def test_triggers_and_python_version():
    text = _text()
    assert re.search(r"^\s*push:", text, re.M)
    assert re.search(r"^\s*pull_request:", text, re.M)
    assert re.search(r"python-version:\s*[\"']?3\.12[\"']?\s*$", text, re.M)


def test_no_install_steps_or_secrets():
    text = _text()
    for line in text.splitlines():
        assert not FORBIDDEN.search(line), line
    assert "secrets." not in text
