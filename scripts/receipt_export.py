#!/usr/bin/env python3
"""Export a desk verification receipt from .swarm state.

Reads the Task Store, verdict rows and run log the way orch_status does, and prints the
verification-receipt shape pinned in tests/fixtures/receipt/desk_fields.json. Every swarm
verdict is advisory: approved_by stays empty, approvals stay empty, and the word APPROVED
is never written. Free text goes through redact_leases. If a credential shape is still
present after that, the script exits 2 and does not write --out.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.envelope import verify_envelope  # noqa: E402
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.paths import latest_correlation, swarm_dir  # noqa: E402
from swarm.runlog import read_events, redact_leases  # noqa: E402
from swarm.script_base import AgentScript, sh  # noqa: E402
from swarm.taskstore import TaskStore  # noqa: E402

# Same credential shapes as scripts/rev_gate.py SECRET_RES. tests/test_receipt_export.py pins the copy.
CREDENTIAL_RES = [re.compile(p) for p in (
    r"AKIA[0-9A-Z]{16}", r"\bgh[pousr]_[A-Za-z0-9]{36,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bxox[baprs]-[0-9A-Za-z-]{10,}", r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password|passwd)\b\s*[:=]\s*['\"]([^'\"\s]{16,})['\"]")]

BOT = "agent-swarm"
DESK_FIELDS = (
    "task_id", "bot", "started_at", "completed_at", "commands", "claims", "unverified",
    "files_changed", "contract_changes", "rollback_plan", "approvals", "loop_acks", "approved_by",
)
GATE_EVENT = {"quality": "qa_gate", "review": "rev_gate", "security": "sec_gate", "release": "rel_plan"}
REFUSAL = "credential-shaped text remains after redaction; receipt not written"


def _iso(ts: float | None) -> str | None:
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _credential(value) -> bool:
    return any(rx.search(text) for text in _strings(value) for rx in CREDENTIAL_RES)


def _observed_commit(root: Path) -> str:
    try:
        proc = sh(["git", "-C", str(root), "rev-parse", "HEAD"], timeout=30)
    except OSError:
        return "not recorded"
    sha = (proc.stdout or "").strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return "not recorded"
    return sha


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _signature(store: TaskStore, task_id: str, gate: str) -> tuple[str, str]:
    """(signature label, recorded verdict word). Never the approval token."""
    try:
        row = store.latest_verdicts(task_id).get(gate)
    except SwarmError:
        return "not run", "none"
    if row is None:
        return "not run", "none"
    recorded = row.get("verdict") or "none"
    if not isinstance(recorded, str) or recorded == "APPROVED":
        recorded = "untrusted"
    raw = row.get("envelope_json")
    if not raw:
        return "unsigned", recorded
    try:
        env = json.loads(raw)
    except json.JSONDecodeError:
        return "unsigned", recorded
    if not isinstance(env, dict):
        return "unsigned", recorded
    payload = env.get("payload") if isinstance(env.get("payload"), dict) else {}
    try:
        task = store.get(task_id)
    except SwarmError:
        task = {"correlation_id": None}
    bound = (payload.get("task_id") == task_id and payload.get("gate") == gate
             and env.get("correlation_id") == task.get("correlation_id"))
    if verify_envelope(env) and bound:
        return "signed", recorded
    if env.get("sig"):
        return "unsigned keyless", recorded
    return "unsigned", recorded


def _evidence(store: TaskStore, task_id: str) -> str:
    try:
        task = store.get(task_id)
    except SwarmError:
        return ""
    uris = [o.get("uri") for o in task.get("outputs") or []
            if isinstance(o, dict) and isinstance(o.get("uri"), str)]
    return ", ".join(uris)


def _commands(events: list[dict]) -> tuple[list[dict], dict[str, int], list[str]]:
    """Recorded script runs. An event with no exit_code and no status is not given a passing exit."""
    commands, index, skipped = [], {}, []
    for event in events:
        kind = event.get("type") or ""
        if not isinstance(kind, str) or not kind.startswith("script.") or kind.endswith(".error"):
            continue
        name = kind.removeprefix("script.")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        recorded = payload.get("cmd")
        cmd = recorded if isinstance(recorded, str) and recorded else f"python3 scripts/{name}.py"
        code = payload.get("exit_code")
        if isinstance(code, bool) or not isinstance(code, int):
            status = payload.get("status")
            code = {"ok": 0, "fail": 1, "error": 2}.get(status)
        if not isinstance(code, int):
            skipped.append(f"{kind} has no recorded exit_code; it was not turned into a command")
            continue
        entry = {"cmd": cmd, "exit_code": code}
        duration = payload.get("duration_s")
        if _number(duration):
            entry["duration_s"] = duration
        summary = payload.get("summary")
        if isinstance(summary, str):
            entry["output_tail"] = summary
        commands.append(entry)
        index.setdefault(name, len(commands) - 1)
    return commands, index, skipped


def _files(tasks: list[dict]) -> list[str]:
    found = []
    for task in tasks:
        for output in task.get("outputs") or []:
            uri = output.get("uri") if isinstance(output, dict) else None
            if isinstance(uri, str) and uri not in found:
                found.append(uri)
    return sorted(found)


def _canned() -> dict:
    return {
        "task_id": "dry-run",
        "bot": BOT,
        "started_at": "1970-01-01T00:00:00Z",
        "completed_at": "1970-01-01T00:00:00Z",
        "commands": [{
            "cmd": "python3 scripts/receipt_export.py --dry-run",
            "exit_code": 0,
            "duration_s": 0,
            "output_tail": "canned advisory receipt; no .swarm state was read",
        }],
        "claims": [{
            "claim": "quality gate on dry-run is advisory; signature not run; commit not recorded",
            "evidence_command_index": 0,
        }],
        "unverified": ["dry-run reads no .swarm state and invents no gate result"],
        "files_changed": [],
        "contract_changes": [],
        "rollback_plan": None,
        "approvals": [],
        "loop_acks": [],
        "approved_by": "",
    }


def _receipt(store: TaskStore, tasks: list[dict], events: list[dict], *, task_id: str, commit: str) -> dict:
    commands, index, skipped = _commands(events)
    unverified_extra = list(skipped)
    claims: list[dict] = []
    unverified = [
        "Swarm verdicts in this receipt are advisory. approved_by is empty because no independent reviewer recorded an approval.",
        "contract_changes and rollback_plan were not recorded in .swarm state.",
    ]
    if not tasks:
        unverified.append("no tasks in the task store; no gates were run and none were invented")
    covered: set[tuple[str, str]] = set()
    for task in tasks:
        notes = task.get("notes_json") or {}
        gate = notes.get("gate")
        if not isinstance(gate, str):
            continue
        targets = [t for t in (notes.get("gate_for") or []) if isinstance(t, str)]
        script = GATE_EVENT.get(gate)
        cmd_index = index.get(script) if script else None
        for target in targets:
            covered.add((target, gate))
            signature, recorded = _signature(store, target, gate)
            evidence = _evidence(store, target)
            tail = f"; evidence {evidence}" if evidence else ""
            if signature == "not run":
                unverified.append(
                    f"{gate} gate task {task['task_id']} for {target} was not run; commit {commit}")
                continue
            sentence = (f"{gate} gate task {task['task_id']} for {target} is advisory; "
                        f"signature {signature}; recorded {recorded}; commit {commit}{tail}")
            if cmd_index is None:
                unverified.append(sentence + "; no script run recorded")
                continue
            claims.append({"claim": sentence, "evidence_command_index": cmd_index})
    for task in tasks:
        if (task.get("notes_json") or {}).get("gate"):
            continue
        for gate in store.required_gates(task["task_id"]):
            if (task["task_id"], gate) in covered:
                continue
            unverified.append(f"{gate} gate on {task['task_id']} was not run; no verdict row")
    for task in tasks:
        unverified.append(f"task {task['task_id']} title: {task.get('title') or ''}")
        for gate, row in store.latest_verdicts(task["task_id"]).items():
            for finding in row.get("findings") or []:
                summary = finding.get("summary") if isinstance(finding, dict) else None
                if isinstance(summary, str) and summary:
                    unverified.append(f"finding {task['task_id']} {gate}: {summary}")
    unverified.extend(unverified_extra)
    for i, command in enumerate(commands):
        if "duration_s" not in command:
            unverified.append(f"command {i} ({command['cmd']}) has no recorded duration_s")
    started = _iso(min(t["created_at"] for t in tasks)) if tasks else None
    completed = _iso(max(t["updated_at"] for t in tasks)) if tasks else None
    return {
        "task_id": task_id,
        "bot": BOT,
        "started_at": started,
        "completed_at": completed,
        "commands": commands,
        "claims": claims,
        "unverified": unverified,
        "files_changed": _files(tasks),
        "contract_changes": [],
        "rollback_plan": None,
        "approvals": [],
        "loop_acks": [],
        "approved_by": "",
    }


def _open_store(root: Path) -> TaskStore:
    sdir = swarm_dir(root, create=False)
    if not sdir.is_dir():
        raise SwarmError(ErrorCode.E_INPUT, f"no .swarm directory at {sdir}; nothing to export")
    db = sdir / "tasks.db"
    if not db.is_file():
        raise SwarmError(ErrorCode.E_INPUT, f"no task store at {db}; nothing to export")
    return TaskStore(path=db)


def _guard(receipt: dict) -> dict:
    """Redact, then refuse rather than write a credential or an approval token."""
    redacted = redact_leases(receipt)
    if not isinstance(redacted, dict) or list(redacted.keys()) != list(DESK_FIELDS):
        raise SwarmError(ErrorCode.E_INTERNAL, "redaction changed the receipt shape")
    if _credential(redacted) or _credential(json.dumps(redacted)):
        raise SwarmError(ErrorCode.E_POLICY, REFUSAL)
    if any("APPROVED" in text for text in _strings(redacted)):
        raise SwarmError(ErrorCode.E_POLICY, "refusing to write a receipt that contains APPROVED")
    return redacted


def _clip_tails(receipt: dict, limit: int = 500) -> dict:
    """Shorten output after the credential scan, so a secret past the cap still refuses the write."""
    for command in receipt["commands"]:
        tail = command.get("output_tail")
        if isinstance(tail, str) and len(tail) > limit:
            command["output_tail"] = tail[:limit]
    return receipt


def _write(path: str, receipt: dict) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(dest)


def run(args, ctx) -> dict:
    if ctx.dry_run:
        receipt = _guard(_canned())
        return {"status": "ok", "dry_run": True, "receipt": receipt,
                "summary": "dry-run: canned advisory receipt; no .swarm state was read"}
    store = _open_store(ctx.root)
    corr = ctx.correlation_id or latest_correlation(ctx.root)
    tasks = store.list(correlation_id=corr)
    events = read_events(correlation_id=corr, root=ctx.root)
    receipt = _clip_tails(_guard(_receipt(
        store, tasks, events, task_id=corr or "unscoped", commit=_observed_commit(ctx.root))))
    if args.out:
        _write(args.out, receipt)
    return {"status": "ok", "receipt": receipt,
            "summary": f"advisory receipt for {receipt['task_id']}: {len(receipt['claims'])} claims, approved_by empty"}


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", dest="root", default=argparse.SUPPRESS, help="alias of --root")
    p.add_argument("--out", help="write the desk receipt JSON here; skipped when a credential shape remains")


if __name__ == "__main__":
    sys.exit(AgentScript("A01", "receipt_export", run, description=__doc__, add_args=add_args).main())
