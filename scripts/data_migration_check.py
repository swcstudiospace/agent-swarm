#!/usr/bin/env python3
"""A07 — audit schema migrations for reversibility, destructive DDL and version order.

Scans alembic/versions, migrations/, db/migrate, prisma/migrations, supabase/migrations,
and loose *.sql migration files (Flyway V__/U__ pairs, *.up.sql/*.down.sql). Per migration:
  * reversible path present (downgrade()/down()/reverse_sql/U__/.down.sql)  else major
  * destructive DDL — DROP TABLE/COLUMN, TRUNCATE, ALTER … TYPE without USING,
    ADD COLUMN … NOT NULL without DEFAULT — flagged blocker/critical: L4 human approval
  * version ids unique and monotonically increasing (timestamp / Vn / alembic chain)
Blockers are never auto-waived
the verdict is advisory input to A09/A10 gates.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, iter_files  # noqa: E402
from swarm.gates import make_finding  # noqa: E402

MIG_DIRS = ("alembic/versions", "migrations", "db/migrate", "prisma/migrations", "supabase/migrations",
            "sql/migrations", "database/migrations", "migrate")
MIG_EXTS = (".sql", ".py", ".rb", ".ts", ".js", ".go")
DESTRUCTIVE = [
    ("blocker", "drop-table", re.compile(r"\bDROP\s+TABLE\b|\bop\.drop_table\(|\bdrop_table\b|\bdropTable\(", re.I)),
    ("blocker", "drop-column", re.compile(r"\bDROP\s+COLUMN\b|\bop\.drop_column\(|\bremove_column\b|\bdropColumn\(", re.I)),
    ("blocker", "truncate", re.compile(r"\bTRUNCATE\s+(TABLE\s+)?\w", re.I)),
    ("critical", "type-change-no-using", re.compile(r"\bALTER\s+COLUMN\s+\S+\s+(?:SET\s+DATA\s+)?TYPE\b(?![^;]*\bUSING\b)[^;]*", re.I)),
    ("critical", "not-null-no-default", re.compile(r"\bADD\s+(?:COLUMN\s+)?\S+\s+[^;,]*?\bNOT\s+NULL\b(?![^;,]*\bDEFAULT\b)", re.I)),
    ("critical", "not-null-no-default", re.compile(r"\bALTER\s+COLUMN\s+\S+\s+SET\s+NOT\s+NULL\b", re.I)),
    ("critical", "rename", re.compile(r"\bRENAME\s+(?:COLUMN|TO)\b|\bop\.alter_column\([^)]*new_column_name|\brename_column\b", re.I)),
]
DOWN_MARKERS = re.compile(r"\bdef\s+downgrade\b|\bdef\s+down\b|\bdown\s*[:(=]|\breverse_sql\b|\bmigrations\.RunSQL\([^)]*,\s*reverse|"
                          r"--\s*(?:migrate:)?down\b|--\s*\+goose\s+Down|\bexport\s+(?:async\s+)?function\s+down\b", re.I)
VERSION_RE = re.compile(r"^(?:V|U)?(\d{1,20})(?:[_\-.]|$)")
FORWARD_END = re.compile(r"\bdef\s+downgrade\b|\bdef\s+down\b|\bexport\s+(?:async\s+)?function\s+down\b|\bdown\s*[:(=]|"
                         r"--\s*(?:migrate:)?down\b|--\s*\+goose\s+Down", re.I)


def find_migrations(root: Path, extra: str | None) -> list[Path]:
    dirs = [root / d for d in MIG_DIRS] + ([Path(extra) if Path(extra).is_absolute() else root / extra] if extra else [])
    seen: dict[Path, None] = {}
    for d in dirs:
        if d.is_dir():
            for f in iter_files(d, exts=MIG_EXTS):
                if f.name not in ("__init__.py", "env.py", "schema.rb", "schema.prisma", "migration_lock.toml") \
                        and not f.name.endswith((".test.ts", ".spec.ts", "_test.go")):
                    seen[f] = None
    if not seen:  # loose Flyway-style *.sql anywhere
        for f in iter_files(root, exts=(".sql",)):
            if VERSION_RE.match(f.name):
                seen[f] = None
    return sorted(seen)


def version_of(f: Path) -> str | None:
    m = VERSION_RE.match(f.name) or VERSION_RE.match(f.parent.name)
    if m:
        return m.group(1)
    m = re.search(r"^revision\s*[:=]\s*['\"]?([\w\-]+)", f.read_text(errors="ignore"), re.M)
    return m.group(1) if m else None


def has_down(f: Path, text: str) -> bool:
    if DOWN_MARKERS.search(text):
        return True
    n = f.name
    if n.startswith("V") and (f.parent / ("U" + n[1:])).exists():
        return True                                    # Flyway undo pair
    if ".up." in n and (f.parent / n.replace(".up.", ".down.")).exists():
        return True
    if f.suffix == ".rb" and re.search(r"\bdef\s+change\b", text) and not re.search(r"remove_column|drop_table|change_column\b", text):
        return True                                    # Rails reversible `change`
    return f.parent.parent.name == "prisma" and (f.parent / "down.sql").exists()


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "migrations": 3, "irreversible": [], "destructive": [], "version_issues": [], "findings": [],
                "dry_run": True, "summary": "dry-run: 3 migrations, all reversible, no destructive DDL, versions ordered"}
    files = find_migrations(ctx.root, args.dir)
    if args.file:
        files = [Path(x) if Path(x).is_absolute() else ctx.root / x for x in args.file]
    findings, irreversible, destructive, version_issues, versions = [], [], [], [], []
    for f in files:
        text = f.read_text(errors="ignore")
        rel = str(f.relative_to(ctx.root)) if f.is_relative_to(ctx.root) else str(f)
        if f.name.startswith("U") or ".down." in f.name or f.name == "down.sql":
            continue                                       # undo scripts are not migrations themselves
        if not has_down(f, text):
            irreversible.append(rel)
            findings.append(make_finding(f"DM-{len(findings)+1:03d}", "major", "reversibility",
                                         "migration has no down/rollback path", location=rel, owner_suggestion="A07"))
        m_end = FORWARD_END.search(text)
        forward = text[:m_end.start()] if m_end else text       # only the forward path is applied to prod
        for sev, kind, pat in DESTRUCTIVE:
            for m in pat.finditer(forward):
                line = text.count("\n", 0, m.start()) + 1
                if text[max(0, text.rfind("\n", 0, m.start())):m.start()].lstrip().startswith(("--", "#", "//")):
                    continue
                destructive.append(f"{rel}:{line} {kind}")
                findings.append(make_finding(f"DM-{len(findings)+1:03d}", sev, f"destructive:{kind}",
                                             f"destructive DDL ({kind}) requires L4 human approval + rollback rehearsal",
                                             evidence=m.group(0)[:160].strip(), location=f"{rel}:{line}", owner_suggestion="A07"))
        v = version_of(f)
        if v:
            versions.append((str(f.parent), v, rel))
    seen: dict[tuple[str, str], str] = {}
    prev: dict[str, str] = {}
    for d, v, rel in versions:                                  # ordered by path ⇒ per-dir filename order
        if (d, v) in seen:
            version_issues.append(f"duplicate version {v}: {seen[(d, v)]} and {rel}")
        seen.setdefault((d, v), rel)
        if v.isdigit() and prev.get(d, "").isdigit() and int(v) <= int(prev[d]) and (d, v) not in seen:
            version_issues.append(f"version {v} ({rel}) does not increase over preceding {prev[d]}")
        if v.isdigit():
            prev[d] = max(v, prev.get(d, "0"), key=int)
    for issue in version_issues:
        findings.append(make_finding(f"DM-{len(findings)+1:03d}", "major", "version-order", issue, owner_suggestion="A07"))
    blocking = [f for f in findings if f["severity"] in ("major", "critical", "blocker")]
    status = "fail" if blocking else "ok"
    return {"status": status, "migrations": len(files), "files": [str(f) for f in files], "irreversible": irreversible,
            "destructive": destructive, "version_issues": version_issues, "findings": findings,
            "summary": f"migration check {status.upper()} — {len(files)} migrations, {len(irreversible)} irreversible, "
                       f"{len(destructive)} destructive DDL, {len(version_issues)} version issues"}


def add_args(p):
    p.add_argument("--dir", help="additional migration directory (relative to --root)")
    p.add_argument("--file", action="append", help="check only these migration file(s)")


if __name__ == "__main__":
    sys.exit(AgentScript("A07", "data_migration_check", run, description=__doc__, add_args=add_args).main())
