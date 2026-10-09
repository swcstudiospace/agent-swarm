"""Cursor installer: a failed update leaves the previous export, and installer-owned leftovers are pruned."""
import hashlib
import json
import os
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


def test_failed_restore_leaves_backup_and_names_unrestored(tmp_path, monkeypatch):
    """A publish failure followed by a failed restore leaves the backup and names the unrestored file.
    The success line is printed only when every restore worked."""
    source = _source_from_export(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    assert cursor_install.install_cursor(target, source=source) == 0
    before = _snapshot(target)
    agents = sorted((source / ".cursor" / "agents").glob("*.md"))
    assert len(agents) >= 2
    for path in agents[:2]:
        path.write_text(path.read_text(encoding="utf-8") + "\n# regenerated\n", encoding="utf-8")
    first = target / agents[0].relative_to(source)
    state = {"n": 0, "fail_restore": False}
    real_write = Path.write_text
    real_replace = os.replace

    def wrapped_write(self, data, *args, **kwargs):
        state["n"] += 1
        if state["n"] == 2:
            state["fail_restore"] = True
            raise OSError("injected write failure")
        return real_write(self, data, *args, **kwargs)

    def wrapped_replace(src, dst, *args, **kwargs):
        if state["fail_restore"] and Path(dst).name == first.name and not str(dst).endswith(".agent-swarm-bak"):
            raise OSError("injected restore failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", wrapped_write)
    monkeypatch.setattr(cursor_install.os, "replace", wrapped_replace)
    err = StringIO()
    rc = cursor_install.install_cursor(target, source=source, err=err)
    text = err.getvalue()
    assert rc == 2
    assert "Traceback" not in text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "Restored the previous Cursor export" not in text
    backup = first.parent / f".{first.name}.agent-swarm-bak"
    assert backup.is_file()
    assert first.as_posix() in text and backup.as_posix() in text
    assert backup.read_bytes() == before[first.relative_to(target).as_posix()]
    stamp = json.loads((target / cursor_install.STAMP_REL).read_text(encoding="utf-8"))
    assert stamp == json.loads(before[cursor_install.STAMP_REL].decode())


def test_symlinked_agents_dir_is_not_unlinked(tmp_path, monkeypatch):
    """An export that retires agents must not delete through a symlinked .cursor/agents directory."""
    source = _source_from_export(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    assert cursor_install.install_cursor(target, source=source) == 0
    real = tmp_path / "real-agents"
    agents = target / ".cursor" / "agents"
    agents.rename(real)
    agents.symlink_to(real)
    held = {p.name: p.read_bytes() for p in real.iterdir() if p.is_file()}
    assert held
    original = cursor_install.export_sources

    def rule_only(src=None):
        files = original(src)
        return {rel: text for rel, text in files.items() if not rel.startswith(".cursor/agents/")}

    monkeypatch.setattr(cursor_install, "export_sources", rule_only)
    err = StringIO()
    rc = cursor_install.install_cursor(target, source=source, err=err)
    assert rc == 2, err.getvalue()
    assert "symlink" in err.getvalue().lower()
    assert "Traceback" not in err.getvalue()
    after = {p.name: p.read_bytes() for p in real.iterdir() if p.is_file()}
    assert after == held


def test_non_utf8_retired_file_is_a_local_edit(tmp_path):
    """A retired file that is not valid UTF-8 is kept, named on stderr, and left out of the new stamp."""
    source = _source_from_export(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    assert cursor_install.install_cursor(target, source=source) == 0
    binary = target / ".cursor" / "agents" / "zz-binary.md"
    payload = b"owned\xff\xfe"
    binary.write_bytes(payload)
    stamp_path = target / cursor_install.STAMP_REL
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    stamp["files"][".cursor/agents/zz-binary.md"] = _sha("owned\n")
    stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    err = StringIO()
    assert cursor_install.install_cursor(target, source=source, err=err) == 0
    assert binary.read_bytes() == payload
    assert ".cursor/agents/zz-binary.md" in err.getvalue()
    recorded = json.loads(stamp_path.read_text(encoding="utf-8"))["files"]
    assert ".cursor/agents/zz-binary.md" not in recorded


def test_existing_backup_is_not_overwritten(tmp_path):
    """A local file already at the backup name blocks the install before any managed file changes."""
    source = _source_from_export(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    assert cursor_install.install_cursor(target, source=source) == 0
    agents = sorted((source / ".cursor" / "agents").glob("*.md"))
    assert agents
    agents[0].write_text(agents[0].read_text(encoding="utf-8") + "\n# regenerated\n", encoding="utf-8")
    live = target / agents[0].relative_to(source)
    before = _snapshot(target)
    backup = live.parent / f".{live.name}.agent-swarm-bak"
    saved = b"user-saved-backup\n"
    backup.write_bytes(saved)
    err = StringIO()
    rc = cursor_install.install_cursor(target, source=source, err=err)
    text = err.getvalue()
    assert rc == 2, text
    assert "Traceback" not in text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "backup already exists" in text
    assert backup.as_posix() in text
    assert "Nothing was written" in text
    assert "Restored the previous Cursor export" not in text
    assert backup.read_bytes() == saved
    assert _snapshot(target) == {**before, backup.relative_to(target).as_posix(): saved}
    assert not live.read_text(encoding="utf-8").endswith("# regenerated\n")


def test_backup_cleanup_failure_reports_completed_install(tmp_path, monkeypatch):
    """After the new stamp is published, a backup that cannot be deleted is named and the new export stays."""
    source = _source_from_export(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    assert cursor_install.install_cursor(target, source=source) == 0
    agents = sorted((source / ".cursor" / "agents").glob("*.md"))
    assert len(agents) >= 2
    for path in agents[:2]:
        path.write_text(path.read_text(encoding="utf-8") + "\n# regenerated\n", encoding="utf-8")
    live = target / agents[0].relative_to(source)
    previous = live.read_bytes()
    real_drop = cursor_install._drop_backup

    def wrapped(dest):
        if Path(dest).name == live.name:
            raise OSError("injected backup cleanup failure")
        return real_drop(dest)

    monkeypatch.setattr(cursor_install, "_drop_backup", wrapped)
    err = StringIO()
    rc = cursor_install.install_cursor(target, source=source, err=err)
    text = err.getvalue()
    assert rc == 2, text
    assert "Traceback" not in text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "install completed" in text
    assert "left backups behind" in text
    backup = live.parent / f".{live.name}.agent-swarm-bak"
    assert backup.as_posix() in text
    assert "Restored the previous Cursor export" not in text
    assert "stamp was left unchanged" not in text
    assert backup.is_file()
    assert backup.read_bytes() == previous
    assert live.read_text(encoding="utf-8").endswith("# regenerated\n")
    other = target / agents[1].relative_to(source)
    assert other.read_text(encoding="utf-8").endswith("# regenerated\n")
    assert not (other.parent / f".{other.name}.agent-swarm-bak").exists()
    stamp = json.loads((target / cursor_install.STAMP_REL).read_text(encoding="utf-8"))
    assert stamp["files"][agents[0].relative_to(source).as_posix()] == _sha(agents[0].read_text(encoding="utf-8"))


def test_failed_retirement_drops_unused_backup_and_retries(tmp_path, monkeypatch):
    """A delete that fails after its backup is saved must not leave that backup to block the next install."""
    source = _source_from_export(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    assert cursor_install.install_cursor(target, source=source) == 0
    retired = target / ".cursor" / "agents" / "zz-removed.md"
    retired.write_text("owned\n", encoding="utf-8")
    stamp_path = target / cursor_install.STAMP_REL
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    stamp["files"][".cursor/agents/zz-removed.md"] = _sha("owned\n")
    stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    before = _snapshot(target)
    backup = retired.parent / f".{retired.name}.agent-swarm-bak"
    real_unlink = Path.unlink

    def wrapped(self, *args, **kwargs):
        if self.name == retired.name:
            raise OSError("injected unlink failure")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", wrapped)
    err = StringIO()
    rc = cursor_install.install_cursor(target, source=source, err=err)
    text = err.getvalue()
    assert rc == 2, text
    assert "Traceback" not in text
    assert "backup already exists" not in text
    assert retired.read_text(encoding="utf-8") == "owned\n"
    assert not backup.exists()
    assert _snapshot(target) == before

    monkeypatch.undo()
    assert cursor_install.install_cursor(target, source=source) == 0
    assert not retired.exists()
    assert not backup.exists()
