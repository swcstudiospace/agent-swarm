#!/usr/bin/env python3
"""Copy the generated Cursor agents into another repo. No substrate, no MCP, no env files.

Writes only under ``<target>/.cursor/``:

- ``.cursor/agents/<slug>.md`` for each agents.json slug
- ``.cursor/rules/agent-swarm.mdc``
- ``.cursor/.agent-swarm-cursor.json`` (provenance: sha256 of each file this installer wrote)

A file that already matches is left untouched. A file this installer previously wrote (the
provenance stamp records its sha256) is updated when the source changes. A differing file
with no such record is refused, and nothing is written. Symlinks on the target path or on
a destination are refused the same way as ``_install_omp.unsafe_destinations``.

A real install stages each managed file in its own directory, replaces those files, then
publishes the stamp last with ``os.replace``. If a step fails, files already replaced are
restored and the previous stamp is left in place. When the stamp is ours, a regular file
it records under ``.cursor/agents/`` that this export no longer produces is removed if its
sha256 still matches; a locally edited file is left in place and named on stderr.

The target must be an existing directory outside this agent-swarm checkout and not ``$HOME``.
A follow-up run against swcstudiospace/programming-desk uses this script; that repo does not
vendor the swarm runtime.

``python3 scripts/build_agents.py --install-cursor <dir>`` calls ``install_cursor`` and does
not regenerate agents and does not run the substrate step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from swarm.manifest import load_manifest  # noqa: E402
from _install_omp import UnsafeDestination, unsafe_destinations  # noqa: E402

STAMP_REL = ".cursor/.agent-swarm-cursor.json"
STAMP_INSTALLER = "agent-swarm-cursor"
RULE_REL = ".cursor/rules/agent-swarm.mdc"


class CursorInstallError(Exception):
    """The install cannot proceed. Exit 2, nothing written."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stamp_text(files: dict[str, str]) -> str:
    payload = {
        "installer": STAMP_INSTALLER,
        "files": {rel: _sha(files[rel]) for rel in sorted(files)},
    }
    return json.dumps(payload, indent=2) + "\n"


def export_sources(source: Path | None = None) -> dict[str, str]:
    """Rel path under the target → file text, for the agents and the rule. No stamp."""
    root = source or ROOT
    files: dict[str, str] = {}
    missing: list[str] = []
    for agent in load_manifest(root / "agents.json"):
        rel = f".cursor/agents/{agent['slug']}.md"
        path = root / rel
        if not path.is_file() or path.is_symlink():
            missing.append(rel)
            continue
        files[rel] = path.read_text(encoding="utf-8")
    rule = root / RULE_REL
    if not rule.is_file() or rule.is_symlink():
        missing.append(RULE_REL)
    else:
        files[RULE_REL] = rule.read_text(encoding="utf-8")
    if missing:
        raise CursorInstallError(
            "error: Cursor export sources missing or symlinked: " + ", ".join(missing)
            + ". Run python3 scripts/build_agents.py in the agent-swarm checkout first. Nothing was written."
        )
    return files


def logical_target(raw: str) -> Path:
    """Absolute target directory. Refuse '..', a symlink on any component, a missing dir, $HOME, or this checkout."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if ".." in p.parts:
        raise CursorInstallError(f"error: refusing target {raw}: the path contains '..'. Nothing was written.")
    cur = Path(p.parts[0])
    for part in p.parts[1:]:
        cur = cur / part
        if cur.is_symlink():
            raise UnsafeDestination(f"{cur} is a symlink")
    if not cur.is_dir():
        raise CursorInstallError(f"error: target {cur} is not an existing directory. Nothing was written.")
    home = Path.home()
    if cur == home:
        raise CursorInstallError(f"error: refusing target {cur}: it is $HOME. Nothing was written.")
    checkout = ROOT.resolve()
    try:
        inside = cur == checkout or cur.is_relative_to(checkout)
    except (OSError, ValueError):
        inside = False
    if inside:
        raise CursorInstallError(
            f"error: refusing target {cur}: it is inside the agent-swarm checkout, which already holds the "
            "generated Cursor agents. Install into the target repo. Nothing was written."
        )
    return cur


def _dest(target: Path, rel: str) -> Path:
    if not rel.startswith(".cursor/") or rel.startswith(".cursor/../") or ".." in Path(rel).parts:
        raise CursorInstallError(f"error: refusing to write outside .cursor/: {rel}. Nothing was written.")
    dest = target / rel
    cursor = target / ".cursor"
    if not dest.is_relative_to(cursor):
        raise CursorInstallError(f"error: refusing to write outside .cursor/: {rel}. Nothing was written.")
    return dest


def _read_stamp(path: Path) -> tuple[str | None, dict | None]:
    if path.is_symlink():
        raise UnsafeDestination(f"{path} is a symlink")
    if not path.exists():
        return None, None
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    return raw, data if isinstance(data, dict) else None


def _stamp_is_ours(stamp: dict | None, target: Path, sources: dict[str, str]) -> bool:
    if not stamp or stamp.get("installer") != STAMP_INSTALLER:
        return False
    recorded = stamp.get("files")
    if not isinstance(recorded, dict):
        return False
    for rel in sources:
        dest = _dest(target, rel)
        if dest.is_symlink():
            raise UnsafeDestination(f"{dest} is a symlink")
        if not dest.exists():
            continue
        prev = recorded.get(rel)
        if not isinstance(prev, str) or prev != _sha(dest.read_text(encoding="utf-8")):
            return False
    return True


def _agents_rel(rel: str) -> bool:
    """A managed agent file. Removals never leave ``.cursor/agents/``."""
    parts = Path(rel).parts
    name = parts[-1] if parts else ""
    return parts[:2] == (".cursor", "agents") and len(parts) == 3 and name.endswith(".md") and name not in (".", "..")


def plan_install(target: Path, sources: dict[str, str]) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """(writes, refusals, removals, kept).

    ``writes`` maps rel → new text. Refusals name files we must not overwrite.
    ``removals`` are installer-owned ``.cursor/agents/`` files the export no longer produces
    whose bytes still match the stamp. ``kept`` are recorded agent files we will not delete
    (local edits, or a symlink). An edited file is omitted from the new stamp: recording
    its new hash would make the next install treat the edit as ours and delete it.
    """
    expected = stamp_text(sources)
    stamp_path = _dest(target, STAMP_REL)
    raw, stamp = _read_stamp(stamp_path)
    ours = _stamp_is_ours(stamp, target, sources)
    recorded = stamp.get("files") if ours and isinstance(stamp, dict) else {}
    writes: dict[str, str] = {}
    refusals: list[str] = []
    for rel, content in sources.items():
        dest = _dest(target, rel)
        if dest.is_symlink():
            raise UnsafeDestination(f"{dest} is a symlink")
        if not dest.exists():
            writes[rel] = content
            continue
        current = dest.read_text(encoding="utf-8")
        if current == content:
            continue
        prev = recorded.get(rel) if isinstance(recorded, dict) else None
        if isinstance(prev, str) and prev == _sha(current):
            writes[rel] = content
        else:
            refusals.append(rel)
    if raw is None:
        writes[STAMP_REL] = expected
    elif raw != expected:
        if ours:
            writes[STAMP_REL] = expected
        else:
            refusals.append(STAMP_REL)
    removals: list[str] = []
    kept: list[str] = []
    if ours and isinstance(recorded, dict) and not refusals:
        for rel in sorted(recorded):
            if rel in sources or not _agents_rel(rel):
                continue
            prev = recorded.get(rel)
            if not isinstance(prev, str):
                continue
            dest = _dest(target, rel)
            if dest.is_symlink():
                kept.append(rel)
                continue
            if not dest.is_file():
                continue
            if _sha(dest.read_text(encoding="utf-8")) == prev:
                removals.append(rel)
            else:
                kept.append(rel)
    if refusals:
        return {}, refusals, [], []
    return writes, [], removals, kept


def _stage_text(dest: Path, text: str) -> Path:
    """Temp file in ``dest``'s directory. Never created through a symlink."""
    if dest.is_symlink():
        raise UnsafeDestination(f"{dest} is a symlink")
    parent = dest.parent
    if parent.is_symlink():
        raise UnsafeDestination(f"{parent} is a symlink")
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise UnsafeDestination(f"{parent} is a symlink")
    fd, name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=os.fspath(parent))
    os.close(fd)
    path = Path(name)
    try:
        path.write_text(text, encoding="utf-8")
    except Exception:
        if path.exists() and not path.is_symlink():
            path.unlink()
        raise
    return path


def _publish(dest: Path, text: str, replaced: list[tuple[Path, bytes | None]]) -> None:
    """Stage ``text`` and replace ``dest``. ``replaced`` records the previous bytes (None if new)."""
    prev = dest.read_bytes() if dest.is_file() and not dest.is_symlink() else None
    if dest.exists() and (dest.is_symlink() or not dest.is_file()):
        raise UnsafeDestination(f"{dest} is a symlink") if dest.is_symlink() else OSError(f"{dest} is not a regular file")
    tmp = _stage_text(dest, text)
    try:
        os.replace(tmp, dest)
    except Exception:
        if tmp.exists() and not tmp.is_symlink():
            tmp.unlink()
        raise
    replaced.append((dest, prev))


def _rollback(replaced: list[tuple[Path, bytes | None]], removed: list[tuple[Path, bytes]]) -> None:
    for path, data in reversed(removed):
        path.write_bytes(data)
    for path, prev in reversed(replaced):
        if prev is None:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        else:
            path.write_bytes(prev)


def _apply(dest_root: Path, writes: dict[str, str], removals: list[str]) -> None:
    """Replace managed files, drop installer-owned leftovers, then publish the stamp last.

    On failure, restore every file already replaced or removed and leave the old stamp.
    """
    replaced: list[tuple[Path, bytes | None]] = []
    removed: list[tuple[Path, bytes]] = []
    try:
        for rel in sorted(r for r in writes if r != STAMP_REL):
            _publish(_dest(dest_root, rel), writes[rel], replaced)
        for rel in removals:
            if not _agents_rel(rel):
                continue
            path = _dest(dest_root, rel)
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            path.unlink()
            removed.append((path, data))
        if STAMP_REL in writes:
            _publish(_dest(dest_root, STAMP_REL), writes[STAMP_REL], replaced)
    except (OSError, UnsafeDestination):
        _rollback(replaced, removed)
        raise


def _check_symlinks(target: Path, rels: list[str]) -> None:
    dests = [_dest(target, rel) for rel in rels]
    reasons = unsafe_destinations(target, dests)
    if reasons:
        raise UnsafeDestination("; ".join(reasons))


def install_cursor(
    target: Path | str,
    *,
    dry_run: bool = False,
    check: bool = False,
    source: Path | None = None,
    out=None,
    err=None,
) -> int:
    """Copy the Cursor export into ``target``. Returns 0 (ok or no-op), 1 (``--check`` drift), or 2 (refused)."""
    stdout = out or sys.stdout
    stderr = err or sys.stderr
    if dry_run and check:
        print("error: --dry-run and --check both write nothing; pass only one.", file=stderr)
        return 2
    try:
        dest_root = logical_target(os.fspath(target))
        sources = export_sources(source)
        _check_symlinks(dest_root, list(sources) + [STAMP_REL])
        writes, refusals, removals, kept = plan_install(dest_root, sources)
    except UnsafeDestination as exc:
        print(
            f"error: refusing to install into {target}: {exc}; the installer never writes through a symlink. "
            "Nothing was written.",
            file=stderr,
        )
        return 2
    except CursorInstallError as exc:
        print(exc, file=stderr)
        return 2
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}. Nothing was written.", file=stderr)
        return 2
    if refusals:
        print(
            "error: refusing to overwrite "
            + ", ".join(refusals)
            + ": the file differs and this installer did not write it. Nothing was written.",
            file=stderr,
        )
        return 2
    paths = sorted(writes)
    for rel in kept:
        why = "symlink" if _dest(dest_root, rel).is_symlink() else "local edits"
        print(f"kept: {rel} ({why}; left in place)", file=stderr)
    if check:
        if paths or removals:
            print("stale:", ", ".join(paths), file=stdout)
        else:
            print("up-to-date", ", ".join(paths), file=stdout)
        for rel in removals:
            print(f"remove: {rel}", file=stdout)
        return 1 if paths or removals else 0
    if dry_run:
        for rel in paths:
            print(rel, file=stdout)
        for rel in removals:
            print(f"remove: {rel}", file=stdout)
        if not paths and not removals:
            print("up-to-date", file=stderr)
        return 0
    try:
        _apply(dest_root, writes, removals)
    except UnsafeDestination as exc:
        print(
            f"error: refusing to install into {target}: {exc}; the installer never writes through a symlink. "
            "Nothing was written.",
            file=stderr,
        )
        return 2
    except OSError as exc:
        print(f"error: {exc}. Restored the previous Cursor export; the stamp was left unchanged.", file=stderr)
        return 2
    if paths or removals:
        print(f"installed {len(paths)} file(s) under {dest_root / '.cursor'}", file=stderr)
    else:
        print("up-to-date", file=stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True, help="existing repo directory to copy .cursor/ agents into")
    ap.add_argument("--dry-run", action="store_true", help="print the .cursor/** paths that would be written; write nothing")
    ap.add_argument("--check", action="store_true", help="exit 1 if the target would change; write nothing")
    args = ap.parse_args(argv)
    return install_cursor(args.target, dry_run=args.dry_run, check=args.check)


if __name__ == "__main__":
    sys.exit(main())
