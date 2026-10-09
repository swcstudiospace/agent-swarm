"""build_agents.py refuses to generate or prune through a symlinked export directory."""
import os
import shutil
import subprocess
import sys

import pytest


def _run(tree):
    env = {k: v for k, v in os.environ.items() if k != "SWARM_AGENTS_FILE"}
    return subprocess.run(
        [sys.executable, str(tree / "scripts" / "build_agents.py")],
        cwd=tree, env=env, capture_output=True, text=True,
    )


def _names(path):
    return sorted(p.name for p in path.iterdir())


@pytest.mark.parametrize("link_rel", [".cursor/agents", ".cursor"], ids=["agents", "parent"])
def test_cursor_export_symlink_refused(tree, tmp_path, link_rel):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "custom.md").write_text("keep\n", encoding="utf-8")
    before = _names(victim)
    link = tree / link_rel
    if link.is_dir() and not link.is_symlink():
        shutil.rmtree(link)
    else:
        link.unlink(missing_ok=True)
    link.symlink_to(victim, target_is_directory=True)
    r = _run(tree)
    assert r.returncode == 1, r.stdout + r.stderr
    err_lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert len(err_lines) == 1, r.stderr
    assert "symlink" in err_lines[0]
    assert "Traceback" not in r.stderr and "Traceback" not in r.stdout
    assert (victim / "custom.md").read_text(encoding="utf-8") == "keep\n"
    assert _names(victim) == before
