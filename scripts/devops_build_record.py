#!/usr/bin/env python3
"""A11 — produce a provenance-bearing `build.artifact` record for the repository at --root.

Captures git sha / branch / dirty flag, a reproducible tree digest (sha256 over the sha256 of every
tracked file, path-sorted), the detected build system, an optional --image OCI ref and timestamp.
Writes .swarm/artifacts/<sha>.json and registers the artifact in the Task Store when --task-id
names a known task. Without git it degrades to a filesystem walk and reports `skipped:tool-missing`.
"""
from __future__ import annotations
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, sh, which, iter_files  # noqa: E402
from swarm.taskstore import TaskStore  # noqa: E402
from swarm.runlog import SWARM_DIR  # noqa: E402
from swarm.errors import SwarmError, ErrorCode  # noqa: E402

BUILD_SYSTEMS = [("Dockerfile", "docker"), ("pyproject.toml", "python-pyproject"), ("setup.py", "python-setuptools"),
                 ("package.json", "node"), ("go.mod", "go"), ("Cargo.toml", "cargo"), ("pom.xml", "maven"),
                 ("build.gradle", "gradle"), ("build.gradle.kts", "gradle"), ("Makefile", "make"),
                 ("CMakeLists.txt", "cmake"), ("requirements.txt", "python-requirements")]


def git_info(root: Path) -> dict:
    if not which("git"):
        return {"sha": None, "branch": None, "dirty": None, "tracked": None, "git": "skipped:tool-missing"}
    rev = sh(["git", "rev-parse", "HEAD"], cwd=root)
    if rev.returncode != 0:
        return {"sha": None, "branch": None, "dirty": None, "tracked": None, "git": "skipped:not-a-repo"}
    branch = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root).stdout.strip()
    status = sh(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root).stdout
    files = sh(["git", "ls-files", "-z"], cwd=root).stdout.split("\0")
    return {"sha": rev.stdout.strip(), "branch": branch, "dirty": bool(status.strip()),
            "tracked": sorted(f for f in files if f), "git": "ok"}


def tree_digest(root: Path, tracked: list[str] | None) -> tuple[str, int]:
    """sha256 over '<path>\\0<sha256(file)>\\n' lines, path-sorted — reproducible independent of mtime."""
    paths = [root / f for f in tracked] if tracked is not None else sorted(iter_files(root))
    h, n = hashlib.sha256(), 0
    for p in sorted(paths):
        if not p.is_file():
            continue
        fh = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                fh.update(chunk)
        h.update(f"{p.relative_to(root).as_posix()}\0{fh.hexdigest()}\n".encode())
        n += 1
    return h.hexdigest(), n


def detect_build_systems(root: Path) -> list[str]:
    return sorted({name for marker, name in BUILD_SYSTEMS if (root / marker).exists()})


def run(args, ctx) -> dict:
    if ctx.dry_run:
        rec = {"kind": "build.artifact", "sha": "0" * 40, "branch": "main", "dirty": False,
               "tree_digest": "sha256:" + "0" * 64, "file_count": 0, "build_systems": ["python-pyproject"],
               "image": args.image, "timestamp": "2026-01-01T00:00:00Z", "producer": "A11@dry",
               "provenance": {"builder": "A11/devops_build_record", "reproducible": True}}
        return {"status": "ok", "record": rec, "dry_run": True, "summary": "dry-run: canned build.artifact record"}

    root = ctx.root
    g = git_info(root)
    if not g["tracked"]:  # no git, or nothing tracked under root (e.g. untracked subproject) → filesystem walk
        g["tracked"], g["git"] = None, g["git"] if g["git"] != "ok" else "ok:untracked-root"
    digest, count = tree_digest(root, g["tracked"])
    sha = g["sha"] or f"tree-{digest[:12]}"
    rec = {"kind": "build.artifact", "sha": g["sha"], "branch": g["branch"], "dirty": g["dirty"],
           "tree_digest": f"sha256:{digest}", "file_count": count, "build_systems": detect_build_systems(root),
           "image": args.image, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "producer": "A11@local", "task_id": ctx.task_id, "correlation_id": ctx.correlation_id,
           "provenance": {"builder": "A11/devops_build_record", "git": g["git"], "reproducible": g["tracked"] is not None,
                          "root": str(root)}}
    if args.version:
        rec["version"] = args.version
    out_dir = SWARM_DIR / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{sha}.json"
    out.write_text(json.dumps(rec, indent=2))

    registered = False
    if ctx.task_id:
        try:
            TaskStore().add_artifact(ctx.task_id, kind="build.artifact", uri=args.image or str(out),
                                     version=args.version or (g["sha"] or digest)[:12], digest=rec["tree_digest"],
                                     producer="A11")
            registered = True
        except SwarmError as e:
            if e.code is not ErrorCode.E_INPUT:
                raise
    warnings = []
    if g["dirty"]:
        warnings.append("working tree dirty — record is not reproducible from sha alone")
    if args.image and "@sha256:" not in args.image:
        warnings.append("image ref is not digest-pinned")
    return {"status": "ok", "record": rec, "path": str(out), "registered": registered, "warnings": warnings,
            "summary": f"build.artifact {sha[:12]} ({g['branch'] or 'no-git'}{', dirty' if g['dirty'] else ''}) "
                       f"digest {digest[:12]} over {count} files; build={rec['build_systems'] or 'none'}"}


def add_args(p):
    p.add_argument("--image", help="OCI image reference produced by the build (prefer @sha256 digest)")
    p.add_argument("--version", help="semantic version / tag for the artifact")


if __name__ == "__main__":
    sys.exit(AgentScript("A11", "devops_build_record", run, description=__doc__, add_args=add_args).main())
