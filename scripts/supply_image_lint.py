#!/usr/bin/env python3
"""A18 - supply_image_lint: lint Dockerfiles and CI workflow files for supply-chain hygiene.

Scans Dockerfiles (Dockerfile, Dockerfile.*, *.Dockerfile, Containerfile) and GitHub Actions workflow files under --root and
reports unpinned images and actions, root users, secrets in build args and missing health checks. The Dockerfile and pin rules
are the ones of the registered tools supply_dockerfile_lint and supply_pin_audit (reused, not copied); workflow build-arg
secrets and `container.options` that run as root or privileged are checked here.

Read-only: it reads files under --root and writes nothing but the run log. Stdlib only, bounded (the scan stops after 2000 files or
100000 entries, symlinks are never followed, files over 1 MiB and binary files are skipped). Workflow `container.options` need PyYAML;
without it, or for a workflow that is not valid YAML or is over 256 KiB, that check is skipped and reported. Directories are scanned
unless they are in SKIP_DIRS (VCS data, third-party installs, caches and tool state, out of scope by design and not reported).
Every other input the scan did not analyse (a skipped file or directory, a bound, a missing tool, an unparseable workflow, an
unlistable directory, or no input at all) makes the status `fail` as a `scan/incomplete` finding (see swarm/scan_status.py). An `ok`
status means every in-scope file was read. Findings use severity info|minor|major|blocker and the status is `fail` when any major or
worse finding exists.
A credential that contains a space is not masked, because the redaction rule works on whitespace-free tokens.
`--dry-run` prints deterministic canned output and touches nothing.
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools.a18_tools import _BOOLEAN_VALUES, _audit_workflow, _f, _is_action_metadata, _pin_kind, _scrub, _secret_name, dockerfile_lint  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_YAML_BYTES = 256 * 1024
MAX_FILES = 2000
MAX_ENTRIES = 100_000
MAX_SKIPPED_LISTED = 50
MAX_FINDINGS_LISTED = 500
# Out of scope by design: VCS data, third-party installs, caches and swarm tool state, never the repository's own images.
# build, dist and vendor are scanned: build outputs and vendored trees can hold real Dockerfiles, so skipping them silently would pass them.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".swarm", ".tox"}
RANK = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
_BUILD_ARG_FLAG = re.compile(r"--build-arg(?:=|\s+)([A-Za-z_][A-Za-z0-9_]*)(?:=(\$\{\{.*?\}\}|\S*))?")
_BUILD_ARGS_KEY = re.compile(r"^(\s*)(?:-\s+)?build-args:[ \t]*(.*?)\s*$")
_BLOCK_SCALAR = re.compile(r"[|>][-+]?(?:\s+#.*)?")
_INLINE_BUILD_ARG = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\$\{\{.*?\}\}|\S*)")
_USER_OPTION = re.compile(r"(?:^|\s)(?:--user|-u)(?:=|\s+)(root|0)(?::\S*)?(?=\s|$)")
_PRIVILEGED_OPTION = re.compile(r"(?:^|\s)--privileged(?:=true)?(?=\s|$)")


def _secret_build_arg(name: str, value: str, at: str) -> dict | None:
    if "${{" in value and "secrets." in value:
        return _f("workflow.secret-in-build-arg", "blocker", f"build arg {name} is fed from the secrets context; it lands in image history. Use a build secret", at)
    if _secret_name(name) and value.lower() in _BOOLEAN_VALUES:
        return None  # a flag such as ALLOW_EMPTY_PASSWORD=yes carries no credential
    if _secret_name(name) and value and "${{" not in value:
        return _f("workflow.secret-in-build-arg", "major", f"build arg {name} carries a literal secret value", at)
    if _secret_name(name):
        return _f("workflow.secret-name-in-build-arg", "major", f"build arg {name} looks like a secret; build args are visible in image history", at)
    return None


def workflow_build_arg_findings(text: str, rel: str) -> list[dict]:
    """Secrets passed as build args: `docker build --build-arg` in run steps and `build-args:` of docker/build-push-action."""
    findings: list[dict] = []
    lines = text.split("\n")
    block_indent: int | None = None
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        at = f"{rel}:{number}"
        if block_indent is not None:
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= block_indent:
                block_indent = None
            elif stripped and not stripped.startswith("#"):
                name, _, value = stripped.lstrip("-").strip().partition("=")
                finding = _secret_build_arg(name.strip(), value.strip(), at)
                if finding:
                    findings.append(finding)
                continue
        if stripped.startswith("#"):
            continue
        match = _BUILD_ARGS_KEY.match(line)
        if match:
            inline = match.group(2)
            if inline and not _BLOCK_SCALAR.fullmatch(inline):  # `build-args: NAME=value` on the key line itself
                for name, value in _INLINE_BUILD_ARG.findall(inline):
                    finding = _secret_build_arg(name, value, at)
                    if finding:
                        findings.append(finding)
            else:
                block_indent = len(match.group(1)) + (2 if line.lstrip().startswith("- ") else 0)
            continue
        for name, value in _BUILD_ARG_FLAG.findall(line):
            finding = _secret_build_arg(name, value or "", at)
            if finding:
                findings.append(finding)
    return findings


def workflow_option_findings(text: str, rel: str, yaml_module) -> list[dict]:
    """`container.options` / `services.*.options` that run a job container as root or privileged (needs PyYAML)."""
    try:
        document = yaml_module.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - any YAML error is a finding, never a crash
        mark = getattr(exc, "problem_mark", None)  # the message of a YAML error quotes the offending source line: report the position only
        where = f" at line {mark.line + 1}" if mark is not None else ""
        return [_f("workflow.unparseable", "minor", f"workflow is not valid YAML ({type(exc).__name__}{where})", rel)]
    jobs = document.get("jobs") if isinstance(document, dict) else None
    findings: list[dict] = []
    for job_id in sorted(jobs, key=str) if isinstance(jobs, dict) else []:
        job = jobs[job_id]
        if not isinstance(job, dict):
            continue
        containers = [("container", job.get("container"))]
        services = job.get("services")
        containers += [(f"services.{name}", svc) for name, svc in sorted(services.items(), key=lambda kv: str(kv[0]))] if isinstance(services, dict) else []
        for label, spec in containers:
            options = spec.get("options") if isinstance(spec, dict) else None
            if not isinstance(options, str):
                continue
            where = rel
            if _USER_OPTION.search(options):
                findings.append(_f("workflow.container-root", "major", f"jobs.{job_id}.{label} runs as root via options", where))
            if _PRIVILEGED_OPTION.search(options):
                findings.append(_f("workflow.container-privileged", "major", f"jobs.{job_id}.{label} runs privileged via options", where))
    return findings


def _load_yaml():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, probed on demand
    except ImportError:
        return None
    return yaml


def _read_text(path: Path) -> tuple[str | None, str]:
    """(text, reason): reason names why the file was skipped (binary, unreadable)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"unreadable:{type(exc).__name__}"
    if b"\x00" in data[:8192]:
        return None, "binary"
    return data.decode("utf-8", errors="replace"), ""


def _scan_dockerfile(rel: str, text: str) -> list[dict]:
    result = dockerfile_lint({"content": text, "require_healthcheck": True, "require_digest_pin": False})
    if result["state"] == "INVALID_INPUT":
        return [_f("docker.no-from", "major", "the file has no FROM instruction", rel)]
    findings = []
    for f in result["findings"]:
        number = f["location"].removeprefix("line ")
        findings.append({**f, "location": f"{rel}:{number}" if number.isdigit() else rel})
    unlisted = result["findings_total"] - len(result["findings"])  # dockerfile_lint lists at most 200: say so, never undercount silently
    if unlisted > 0:
        findings.append(_f("docker.findings-unlisted", "info", f"{unlisted} further finding(s) are not listed; the listing cap was reached", rel))
    return findings


def _scan_workflow(rel: str, text: str, yaml_module, skipped: list[str]) -> list[dict]:
    # Called directly, not through pin_audit: its payload-path check rejects '..' segments, which a repository filename may hold.
    findings = _audit_workflow(rel, text)[1]
    findings += workflow_build_arg_findings(text, rel)
    if _is_action_metadata(rel):
        return findings  # action.yml has no jobs: the container-options check does not apply to it
    if yaml_module is None:
        skipped.append(f"{rel}: skipped:tool-missing:pyyaml (container options)")
    elif len(text.encode("utf-8")) > MAX_YAML_BYTES:
        skipped.append(f"{rel}: skipped:yaml-too-large (container options)")
    else:
        options = workflow_option_findings(text, rel, yaml_module)
        if any(f["id"] == "workflow.unparseable" for f in options):  # the container check never ran on this file: incomplete
            skipped.append(f"{rel}: skipped:unparseable-yaml (container options)")
        findings += options
    return findings


def _is_link(path: Path) -> bool:
    """True when `path` is a symbolic link, by os.lstat: an unreadable path raises OSError, which the walk turns into a skipped entry."""
    return stat.S_ISLNK(os.lstat(path).st_mode)


def scan(root: Path, target: Path) -> dict:
    """Walk `target` (inside `root`) and lint every Dockerfile and workflow file. Pure reads, bounded and deterministic."""
    yaml_module = _load_yaml()
    findings: list[dict] = []
    skipped: list[str] = []
    counts = {"dockerfiles": 0, "workflows": 0}
    scanned = visited = 0
    truncated = False

    def unreadable_directory(error: OSError) -> None:
        skipped.append(f"{Path(error.filename or target).relative_to(root).as_posix()}: unreadable")

    walker = [(str(target.parent), [], [target.name])] if target.is_file() else os.walk(target, followlinks=False, onerror=unreadable_directory)
    for base, dirnames, filenames in walker:
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in dirnames:  # os.walk lists a symlinked directory without descending into it: its files are unseen, so say so
            child = Path(base, name)
            try:
                link = _is_link(child)
            except OSError as exc:
                skipped.append(f"{child.relative_to(root).as_posix()}: unreadable:{type(exc).__name__}")
                continue
            if link:
                skipped.append(f"{child.relative_to(root).as_posix()}: symlink")
        for name in sorted(filenames):
            visited += 1
            if visited > MAX_ENTRIES:
                truncated = True
                break
            path = Path(base, name)
            rel = path.relative_to(root).as_posix()
            kind = _pin_kind(rel)
            if kind not in ("dockerfile", "github-actions"):
                continue
            try:
                link = _is_link(path)
            except OSError as exc:
                skipped.append(f"{rel}: unreadable:{type(exc).__name__}")
                continue
            if link:
                skipped.append(f"{rel}: symlink")
                continue
            if scanned >= MAX_FILES:
                truncated = True
                break
            try:
                info = path.stat()
            except OSError:
                skipped.append(f"{rel}: unreadable")
                continue
            if not stat.S_ISREG(info.st_mode):  # a FIFO or device named Dockerfile would block the read forever
                skipped.append(f"{rel}: unreadable")
                continue
            if info.st_size > MAX_FILE_BYTES:
                skipped.append(f"{rel}: over-1MiB")
                continue
            text, reason = _read_text(path)
            if text is None:
                skipped.append(f"{rel}: {reason}")
                continue
            scanned += 1
            if kind == "dockerfile":
                counts["dockerfiles"] += 1
                findings += _scan_dockerfile(rel, text)
            else:
                counts["workflows"] += 1
                findings += _scan_workflow(rel, text, yaml_module, skipped)
        if truncated:
            break
    shown = [_scrub(entry) for entry in skipped]  # output boundary for paths: skipped entries, reasons and locations are redacted here
    reasons = list(shown)
    bound = f"scan stopped at its bounds ({MAX_FILES} files, {MAX_ENTRIES} entries)"
    if truncated:
        reasons.append(bound)
        findings.append(_f("scan.truncated", "info", bound, ""))
    findings = [{**f, "location": _scrub(f["location"])} for f in findings]
    findings += [{**f, "location": ""} for f in incomplete_findings(reasons, scanned)]
    findings.sort(key=lambda f: (RANK[f["severity"]], f["location"], f["id"], f["summary"]))
    blocking = sum(1 for f in findings if RANK[f["severity"]] <= RANK["major"])
    return {
        "status": scan_status(findings),
        "files_scanned": {**counts},
        "findings_total": len(findings),
        "blocking_count": blocking,
        "findings": findings[:MAX_FINDINGS_LISTED],
        "skipped": sorted(shown)[:MAX_SKIPPED_LISTED],
        "truncated": truncated,
        "tools": {"pyyaml": "available" if yaml_module is not None else "skipped:tool-missing"},
        "summary": f"{counts['dockerfiles']} Dockerfile(s) and {counts['workflows']} workflow(s) scanned, {len(findings)} finding(s), {blocking} blocking",
    }


def _no_symlink_on_path(root: Path, relative: str) -> None:
    """Symlinks are never followed: refuse a --path whose own components cross one, as the walk does not follow them either."""
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise SwarmError(ErrorCode.E_INPUT, f"--path {_scrub(relative)!r} crosses a symlink; symlinks are never followed")


def _failed(exc: Exception) -> dict:
    """The result of a scan that could not finish: one scan/incomplete finding whose reason is the exception's text after _scrub, so a
    path or a credential in the message cannot leave the script. The exception object and its traceback are never returned."""
    reason = f"scan could not finish ({type(exc).__name__}): {_scrub(str(exc))}"
    findings = [{**f, "location": ""} for f in incomplete_findings([reason], 0)]
    return {
        "status": scan_status(findings),
        "files_scanned": {"dockerfiles": 0, "workflows": 0},
        "findings_total": len(findings),
        "blocking_count": sum(1 for f in findings if RANK[f["severity"]] <= RANK["major"]),
        "findings": findings,
        "skipped": [],
        "truncated": False,
        "tools": {"pyyaml": "not-checked"},
        "summary": f"scan failed: {reason}",
    }


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {
            "status": "ok", "dry_run": True, "files_scanned": {"dockerfiles": 0, "workflows": 0}, "findings_total": 0, "blocking_count": 0,
            "findings": [], "skipped": [], "truncated": False, "tools": {"pyyaml": "not-checked"},
            "summary": "dry-run: supply_image_lint would scan Dockerfiles and workflow files under --root",
        }
    try:
        _no_symlink_on_path(ctx.root, args.path)
        target = (ctx.root / args.path).resolve()
        if not target.is_relative_to(ctx.root):
            raise SwarmError(ErrorCode.E_INPUT, f"--path {_scrub(args.path)!r} leaves the repository root")
        if not target.exists():
            raise SwarmError(ErrorCode.E_INPUT, f"--path {_scrub(args.path)!r} does not exist under {_scrub(str(ctx.root))}")
        return scan(ctx.root, target)
    except SwarmError:
        raise
    except Exception as exc:  # no raw exception text or trace leaves the script: the reason is the redacted message only
        return _failed(exc)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A18", "supply_image_lint", run, description=__doc__, add_args=add_args).main())
