#!/usr/bin/env python3
"""A05/A06 — detect the repository's language toolchains and run the available
linters, formatters, type-checkers and test runners.

Python: ruff / flake8, mypy, pytest.  JS/TS: eslint, tsc, npm test.
Go: go vet, go test.  Rust: cargo clippy, cargo test.
Each tool is reported as pass / fail / skipped:tool-missing
every failure becomes
a finding so the implementing agent can fix before entering IN_REVIEW.
--changed-only scopes file-based tools to `git diff --name-only <diff-base>`.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, sh, which, iter_files  # noqa: E402
from swarm.gates import make_finding  # noqa: E402

PY = sys.executable


def _py_module(mod: str) -> bool:
    return sh([PY, "-c", f"import {mod}"]).returncode == 0


def detect_toolchains(root: Path) -> dict[str, bool]:
    py = any((root / f).exists() for f in ("pyproject.toml", "setup.py", "requirements.txt")) \
        or any(True for _ in iter_files(root, exts=(".py",)))
    js = (root / "package.json").exists()
    ts = js and ((root / "tsconfig.json").exists())
    return {"python": py, "js": js, "ts": ts, "go": (root / "go.mod").exists(), "rust": (root / "Cargo.toml").exists()}


def changed_files(root: Path, base: str) -> list[str]:
    proc = sh(["git", "diff", "--name-only", base], cwd=root)
    if proc.returncode != 0:
        return []
    return [f for f in proc.stdout.split("\n") if f.strip() and (root / f).exists()]


def plan(root: Path, tc: dict, scoped: list[str] | None) -> list[dict]:
    """Return tool entries: name, kind, cmd (None ⇒ tool missing), files-aware."""
    def pick(files, exts):
        return [f for f in files if f.endswith(exts)] if files is not None else []
    entries: list[dict] = []
    if tc["python"]:
        pyf = pick(scoped, (".py",))
        if which("ruff") or _py_module("ruff"):
            base = ["ruff"] if which("ruff") else [PY, "-m", "ruff"]
            entries.append({"name": "ruff", "kind": "lint", "cmd": [*base, "check", *(pyf or ["."])]})
        elif which("flake8") or _py_module("flake8"):
            base = ["flake8"] if which("flake8") else [PY, "-m", "flake8"]
            entries.append({"name": "flake8", "kind": "lint", "cmd": [*base, *(pyf or ["."])]})
        else:
            entries.append({"name": "ruff/flake8", "kind": "lint", "cmd": None})
        entries.append({"name": "mypy", "kind": "typecheck",
                        "cmd": [PY, "-m", "mypy", "--ignore-missing-imports", *(pyf or ["."])] if _py_module("mypy") else None})
        entries.append({"name": "pytest", "kind": "test",
                        "cmd": [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"] if _py_module("pytest") else None})
    if tc["js"]:
        try:
            scripts = json.loads((root / "package.json").read_text()).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        npx = which("npx")
        jsf = pick(scoped, (".js", ".jsx", ".ts", ".tsx", ".vue"))
        has_eslint = npx and ((root / "node_modules" / ".bin" / "eslint").exists() or "lint" in scripts)
        entries.append({"name": "eslint", "kind": "lint",
                        "cmd": ["npx", "--no-install", "eslint", *(jsf or ["."])] if has_eslint else None})
        if tc["ts"]:
            has_tsc = npx and (root / "node_modules" / ".bin" / "tsc").exists()
            entries.append({"name": "tsc", "kind": "typecheck", "cmd": ["npx", "--no-install", "tsc", "--noEmit"] if has_tsc else None})
        entries.append({"name": "npm test", "kind": "test",
                        "cmd": ["npm", "test", "--silent"] if ("test" in scripts and which("npm")) else None})
    if tc["go"]:
        go = which("go")
        entries.append({"name": "go vet", "kind": "lint", "cmd": ["go", "vet", "./..."] if go else None})
        entries.append({"name": "go test", "kind": "test", "cmd": ["go", "test", "./..."] if go else None})
    if tc["rust"]:
        cargo = which("cargo")
        entries.append({"name": "cargo clippy", "kind": "lint", "cmd": ["cargo", "clippy", "-q", "--", "-D", "warnings"] if cargo else None})
        entries.append({"name": "cargo test", "kind": "test", "cmd": ["cargo", "test", "-q"] if cargo else None})
    return entries


def run(args, ctx) -> dict:
    if ctx.dry_run:
        results = {"ruff": "pass", "mypy": "pass", "pytest": "pass", "eslint": "skipped:tool-missing"}
        return {"status": "ok", "toolchains": ["python", "js"], "results": results, "findings": [],
                "dry_run": True, "summary": "dry-run: canned toolchain results (all pass)"}

    tc = detect_toolchains(ctx.root)
    scoped = changed_files(ctx.root, args.diff_base) if args.changed_only else None
    if args.changed_only and not scoped:
        return {"status": "ok", "toolchains": [k for k, v in tc.items() if v], "results": {}, "findings": [],
                "summary": f"no changed files vs {args.diff_base}; nothing to check"}
    entries = plan(ctx.root, tc, scoped)
    results, findings, executed = {}, [], []
    for e in entries:
        if e["cmd"] is None:
            results[e["name"]] = "skipped:tool-missing"
            continue
        try:
            proc = sh(e["cmd"], cwd=ctx.root, timeout=args.timeout)
        except Exception as ex:  # noqa: BLE001 - timeouts / spawn errors become findings
            results[e["name"]] = "fail"
            findings.append(make_finding(f"CC-{len(findings)+1:03d}", "major", e["kind"],
                                         f"{e['name']} could not complete: {type(ex).__name__}", evidence=str(ex)[:500]))
            continue
        out = (proc.stdout + proc.stderr).strip()
        # pytest exit 5 = no tests collected: not a failure of the code under test
        ok = proc.returncode == 0 or (e["name"] == "pytest" and proc.returncode == 5)
        results[e["name"]] = "pass" if ok else "fail"
        executed.append({"tool": e["name"], "returncode": proc.returncode, "tail": out[-1200:]})
        if not ok:
            sev = "major" if e["kind"] in ("test", "typecheck") else "minor"
            findings.append(make_finding(f"CC-{len(findings)+1:03d}", sev, e["kind"],
                                         f"{e['name']} failed (exit {proc.returncode})", evidence=out[-800:],
                                         owner_suggestion=ctx.agent_id, location=str(ctx.root)))
    failed = [k for k, v in results.items() if v == "fail"]
    return {"status": "fail" if failed else "ok", "toolchains": [k for k, v in tc.items() if v],
            "changed_files": scoped, "results": results, "findings": findings, "executed": executed,
            "summary": f"code checks {'FAIL' if failed else 'OK'} — {len(results)} tools, failed: {failed or 'none'}"}


def add_args(p):
    p.add_argument("--changed-only", action="store_true", help="scope file-based tools to git diff vs --diff-base")
    p.add_argument("--diff-base", default="HEAD", help="git ref for --changed-only (default HEAD)")
    p.add_argument("--timeout", type=int, default=900, help="per-tool timeout in seconds")


if __name__ == "__main__":
    sys.exit(AgentScript("A05", "code_checks", run, description=__doc__, add_args=add_args).main())
