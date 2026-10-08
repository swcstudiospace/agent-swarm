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


def plan_install(target: Path, sources: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """(writes, refusals). ``writes`` maps rel → new text. Refusals name files we must not overwrite."""
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
    if refusals:
        return {}, refusals
    return writes, []


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
        writes, refusals = plan_install(dest_root, sources)
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
    if check:
        print("stale:" if paths else "up-to-date", ", ".join(paths), file=stdout)
        return 1 if paths else 0
    if dry_run:
        for rel in paths:
            print(rel, file=stdout)
        if not paths:
            print("up-to-date", file=stderr)
        return 0
    for rel in paths:
        path = _dest(dest_root, rel)
        if path.exists() and path.is_symlink():
            print(f"error: {path} is a symlink. Nothing further was written.", file=stderr)
            return 2
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink():
            print(f"error: {path.parent} is a symlink. Nothing further was written.", file=stderr)
            return 2
        path.write_text(writes[rel], encoding="utf-8")
    if paths:
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
