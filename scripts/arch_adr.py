#!/usr/bin/env python3
"""A03 — scaffold and index MADR-format Architecture Decision Records.

`--title/--status/--decision` writes docs/adr/NNNN-<slug>.md with the MADR sections
(Status, Context and Problem Statement, Decision Drivers, Considered Options,
Decision Outcome, Consequences). `--list` (the default when no title is given)
indexes existing ADRs and validates that every required section is present.
"""
from __future__ import annotations
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript  # noqa: E402
from swarm.gates import make_finding, derive_verdict  # noqa: E402

STATUSES = ["proposed", "accepted", "rejected", "deprecated", "superseded"]
REQUIRED = ["Status", "Context and Problem Statement", "Decision Outcome", "Consequences"]
OPTIONAL = ["Decision Drivers", "Considered Options"]
FILE_RE = re.compile(r"^(\d{4})-(.+)\.md$")

TEMPLATE = """# {id}. {title}

- **Status:** {status}
- **Date:** {date}
- **Deciders:** A03{supersedes}
- **Task:** {task}

## Context and Problem Statement

{context}

## Decision Drivers

{drivers}

## Considered Options

{options}

## Decision Outcome

Chosen option: "{decision}".

### Positive Consequences

- TBD

### Negative Consequences

- TBD

## Consequences

{consequences}
"""


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "adr"


def index(adr_dir: Path) -> tuple[list[dict], list[dict]]:
    entries, findings = [], []
    for p in sorted(adr_dir.glob("*.md")):
        m = FILE_RE.match(p.name)
        if not m:
            continue
        text = p.read_text(errors="replace")
        heads = [h.strip() for h in re.findall(r"^#{1,3}\s+(.+)$", text, re.M)]
        title_m = re.search(r"^#\s+(?:ADR-)?\d+\.?\s*(.+)$", text, re.M)
        status_m = re.search(r"\*\*Status:\*\*\s*(\w+)|^##\s*Status\s*\n+\s*(\w+)", text, re.M | re.I)
        status = next((g for g in (status_m.groups() if status_m else ()) if g), "unknown").lower()
        missing = [s for s in REQUIRED if s != "Status" and not any(h.lower().startswith(s.lower()) for h in heads)]
        if status == "unknown":
            missing.append("Status")
        entry = {"adr_id": f"ADR-{m.group(1)}", "file": str(p), "title": title_m.group(1).strip() if title_m else p.stem,
                 "status": status, "missing_sections": missing}
        entries.append(entry)
        if missing:
            findings.append(make_finding(f"AF-{len(findings)+1:03d}", "major", "adr-structure",
                                         f"{entry['adr_id']} missing sections: {', '.join(missing)}",
                                         location=str(p), owner_suggestion="A03"))
        if status not in STATUSES and status != "unknown":
            findings.append(make_finding(f"AF-{len(findings)+1:03d}", "minor", "adr-status",
                                         f"{entry['adr_id']} has non-standard status '{status}'",
                                         location=str(p), owner_suggestion="A03"))
    return entries, findings


def scaffold(args, ctx, adr_dir: Path) -> dict:
    existing = [int(FILE_RE.match(p.name).group(1)) for p in adr_dir.glob("*.md") if FILE_RE.match(p.name)] \
        if adr_dir.exists() else []
    num = max(existing, default=0) + 1
    adr_id = f"ADR-{num:04d}"
    path = adr_dir / f"{num:04d}-{slugify(args.title)}.md"
    options = "\n".join(f"- {o.strip()}" for o in (args.alternatives or "").split(";") if o.strip()) or f"- {args.decision}"
    content = TEMPLATE.format(
        id=adr_id, title=args.title, status=args.status, date=time.strftime("%Y-%m-%d"),
        supersedes=f"\n- **Supersedes:** {args.supersedes}" if args.supersedes else "",
        task=ctx.task_id or "unassigned", context=args.context or "TBD — describe the forces at play.",
        drivers=args.drivers or "- TBD", options=options, decision=args.decision,
        consequences=args.consequences or "TBD — note follow-up tasks for A05/A06/A07/A11.")
    if ctx.dry_run:
        return {"status": "ok", "summary": f"dry-run: would write {path}", "dry_run": True,
                "adr": {"adr_id": adr_id, "status": args.status, "file": str(path)}, "content": content}
    adr_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return {"status": "ok", "summary": f"wrote {adr_id} → {path}",
            "adr": {"adr_id": adr_id, "status": args.status, "file": str(path), "supersedes": args.supersedes},
            "findings": []}


def run(args, ctx) -> dict:
    adr_dir = ctx.root / args.adr_dir
    if args.title:
        if not args.decision:
            return {"status": "fail", "summary": "E-INPUT: --decision is required with --title", "findings": [
                make_finding("AF-000", "major", "input", "--decision missing", owner_suggestion="A03")]}
        return scaffold(args, ctx, adr_dir)
    if ctx.dry_run:
        return {"status": "ok", "summary": "dry-run: 1 ADR indexed, 0 findings", "dry_run": True, "verdict": "pass",
                "adrs": [{"adr_id": "ADR-0001", "title": "Record architecture decisions", "status": "accepted",
                          "file": "docs/adr/0001-record-architecture-decisions.md", "missing_sections": []}],
                "findings": []}
    if not adr_dir.exists():
        return {"status": "ok", "summary": f"no ADR directory at {adr_dir} (use --title to create the first ADR)",
                "adrs": [], "findings": [], "verdict": "pass"}
    entries, findings = index(adr_dir)
    verdict = derive_verdict(findings)
    return {"status": "ok" if verdict == "pass" else "fail", "verdict": verdict, "adrs": entries,
            "findings": findings,
            "summary": f"ADR index {verdict.upper()} — {len(entries)} ADR(s) in {adr_dir}, {len(findings)} finding(s)"}


def add_args(p):
    p.add_argument("--list", action="store_true", help="index and validate existing ADRs (default action)")
    p.add_argument("--adr-dir", default="docs/adr")
    p.add_argument("--title")
    p.add_argument("--status", choices=STATUSES, default="proposed")
    p.add_argument("--decision", help="chosen option (one line)")
    p.add_argument("--context", help="context / problem statement")
    p.add_argument("--drivers", help="decision drivers (markdown)")
    p.add_argument("--alternatives", help="semicolon-separated considered options")
    p.add_argument("--consequences")
    p.add_argument("--supersedes", help="ADR id this decision supersedes")


if __name__ == "__main__":
    sys.exit(AgentScript("A03", "arch_adr", run, description=__doc__, add_args=add_args).main())
