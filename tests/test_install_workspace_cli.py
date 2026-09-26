"""`build_agents.py --install-workspace` guards (PKG-01, 07-VALIDATION G1): refusals, usage errors and preflight
ordering, run as a subprocess from a copy of the checkout so a regression can only write into tmp."""
import os
import subprocess
import sys

import pytest

from install_helpers import _ENV_KEYS, _cfg, _snapshot, _write


def _build(tree, home, *args):
    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    env["HOME"] = str(home)
    cmd = [sys.executable, str(tree / "scripts" / "build_agents.py"), *args]
    return subprocess.run(cmd, cwd=tree, capture_output=True, text=True, env=env)


@pytest.mark.parametrize("where", ["checkout", "subdir"])
def test_refuses_the_checkout_itself(tree, home, where):
    _write(_cfg(tree), "extensions:\n  - ./omp\n")
    target = tree if where == "checkout" else tree / "omp"
    before = _snapshot(tree, home)
    for mode in ("link", "copy"):
        r = _build(tree, home, "--install-workspace", str(target), "--omp-mode", mode)
        assert r.returncode == 2, r.stdout + r.stderr
    assert _snapshot(tree, home) == before
    assert not (tree / "omp" / ".omp").exists()


@pytest.mark.parametrize("where", ["home", "omp-dir"])
def test_refuses_home_and_the_omp_dir(tree, home, where):
    target = home if where == "home" else home / ".omp" / "agent"
    target.mkdir(parents=True, exist_ok=True)
    before = _snapshot(tree, home)
    r = _build(tree, home, "--install-workspace", str(target))
    assert r.returncode == 2, r.stdout + r.stderr
    assert _snapshot(tree, home) == before


def test_missing_workspace_exits_2(tree, home, tmp_path):
    missing = tmp_path / "nope"
    before = _snapshot(tree, home)
    r = _build(tree, home, "--install-workspace", str(missing))
    assert r.returncode == 2, r.stdout + r.stderr
    assert not missing.exists()
    assert _snapshot(tree, home) == before


@pytest.mark.parametrize(
    "args",
    [["--install-workspace", "{ws}", "--check"], ["--omp-mode", "copy"], ["--dry-run"]],
    ids=["check-with-install", "omp-mode-alone", "dry-run-alone"],
)
def test_install_flag_usage_errors(tree, ws, home, args):
    before = _snapshot(tree, ws, home)
    r = _build(tree, home, *(a.format(ws=ws) for a in args))
    assert r.returncode == 2, r.stdout + r.stderr
    assert _snapshot(tree, ws, home) == before


def test_unsupported_config_exits_2_before_any_copy(tree, ws, home):
    text = "extensions: [/opt/x]\n"
    _write(_cfg(ws), text)
    before = _snapshot(tree, home)
    r = _build(tree, home, "--install-workspace", str(ws))
    assert r.returncode == 2, r.stdout + r.stderr
    assert [p for p in ws.rglob("*") if p.is_file()] == [_cfg(ws)]
    assert _cfg(ws).read_text(encoding="utf-8") == text
    assert _snapshot(tree, home) == before
