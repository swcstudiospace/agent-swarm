"""Cursor installer: a failed update leaves the previous export, and installer-owned leftovers are pruned."""
import hashlib
import json
import sys
from io import StringIO
from pathlib import Path

from swarm.manifest import load_manifest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import _install_cursor as cursor_install  # noqa: E402


def _snapshot(root: Path) -> dict[str, bytes]:
    cursor = root / ".cursor"
    if not cursor.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in cursor.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def _source_from_export(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    (source / "agents.json").write_text((ROOT / "agents.json").read_text(encoding="utf-8"), encoding="utf-8")
    for rel, text in cursor_install.export_sources().items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return source


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_second_write_failure_restores_and_retry_is_clean(tmp_path, monkeypatch):
    """The second managed write fails; files and the stamp match the pre-install bytes; a retry then checks clean."""
    source = _source_from_export(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    assert cursor_install.install_cursor(target, source=source) == 0
    before = _snapshot(target)

    agents = sorted((source / ".cursor" / "agents").glob("*.md"))
    assert len(agents) >= 2
    for path in agents[:2]:
        path.write_text(path.read_text(encoding="utf-8") + "\n# regenerated\n", encoding="utf-8")

    state = {"n": 0, "armed": True}
    real = Path.write_text

    def wrapped(self, data, *args, **kwargs):
        if state["armed"]:
            state["n"] += 1
            if state["n"] == 2:
                raise OSError("injected write failure")
        return real(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", wrapped)
    err = StringIO()
    rc = cursor_install.install_cursor(target, source=source, err=err)
    assert rc == 2
    lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
    assert lines and "Traceback" not in err.getvalue()
    assert len(lines) == 1
    assert _snapshot(target) == before

    state["armed"] = False
    assert cursor_install.install_cursor(target, source=source) == 0
    out, err = StringIO(), StringIO()
    assert cursor_install.install_cursor(target, source=source, check=True, out=out, err=err) == 0
    assert out.getvalue().startswith("up-to-date")
    for path in agents[:2]:
        rel = path.relative_to(source).as_posix()
        assert (target / rel).read_text(encoding="utf-8").endswith("# regenerated\n")


def test_removed_slug_pruned_and_edited_kept(tmp_path):
    """An untouched installer-owned file for a slug the export no longer produces is removed.
    A locally edited one is kept and named on stderr. --check and --dry-run list remove: lines."""
    source = _source_from_export(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    assert cursor_install.install_cursor(target, source=source) == 0
    agents = target / ".cursor" / "agents"
    untouched = agents / "zz-removed.md"
    edited = agents / "zz-edited.md"
    untouched.write_text("owned\n", encoding="utf-8")
    edited.write_text("owned\n", encoding="utf-8")
    stamp_path = target / cursor_install.STAMP_REL
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    stamp["files"][".cursor/agents/zz-removed.md"] = _sha("owned\n")
    stamp["files"][".cursor/agents/zz-edited.md"] = _sha("owned\n")
    stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    edited.write_text("owned\nlocal\n", encoding="utf-8")

    out, err = StringIO(), StringIO()
    assert cursor_install.install_cursor(target, source=source, dry_run=True, out=out, err=err) == 0
    assert "remove: .cursor/agents/zz-removed.md" in out.getvalue().splitlines()
    assert untouched.read_text(encoding="utf-8") == "owned\n"
    assert ".cursor/agents/zz-edited.md" in err.getvalue()

    out, err = StringIO(), StringIO()
    assert cursor_install.install_cursor(target, source=source, check=True, out=out, err=err) == 1
    assert "remove: .cursor/agents/zz-removed.md" in out.getvalue().splitlines()
    assert ".cursor/agents/zz-edited.md" in err.getvalue()
    assert untouched.exists() and edited.read_text(encoding="utf-8") == "owned\nlocal\n"

    err = StringIO()
    assert cursor_install.install_cursor(target, source=source, err=err) == 0
    assert not untouched.exists()
    assert edited.read_text(encoding="utf-8") == "owned\nlocal\n"
    assert ".cursor/agents/zz-edited.md" in err.getvalue()
    recorded = json.loads(stamp_path.read_text(encoding="utf-8"))["files"]
    assert ".cursor/agents/zz-removed.md" not in recorded
    assert ".cursor/agents/zz-edited.md" not in recorded
    assert all(rel.startswith(".cursor/agents/") or rel == cursor_install.RULE_REL or rel == cursor_install.STAMP_REL
               for rel in recorded)
    # a later install still leaves the edited file alone
    err = StringIO()
    assert cursor_install.install_cursor(target, source=source, err=err) == 0
    assert edited.read_text(encoding="utf-8") == "owned\nlocal\n"
    assert not untouched.exists()
    # the manifest slugs are still present
    for agent in load_manifest():
        assert (agents / f"{agent['slug']}.md").is_file()
