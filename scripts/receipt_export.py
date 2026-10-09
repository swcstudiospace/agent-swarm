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
import os
import re
import sys
import tempfile
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
# Quoted JSON keys never match SECRET_RES: a quote sits between the name and the colon.
JSON_CREDENTIAL_RES = [re.compile(
    r'(?i)"(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password|passwd)"'
    r'\s*:\s*"([^"\\]{16,})"')]
_SHA = re.compile(r"[0-9a-f]{40}")
_FINISHED = frozenset({"IN_REVIEW", "APPROVED", "DONE"})

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
    patterns = (*CREDENTIAL_RES, *JSON_CREDENTIAL_RES)
    return any(rx.search(text) for text in _strings(value) for rx in patterns)


# Whitespace-delimited only. A path such as docs/APPROVED.md must not be renamed.
_APPROVAL_TOKEN = re.compile(r"(?<!\S)APPROVED(?!\S)")


def _withhold_approval(value: str) -> str:
    """Same label as a task state. Status tables copy the stored word; the receipt must not."""
    return _APPROVAL_TOKEN.sub("advisory-complete", value)


def _redact_text(value) -> str:
    """Redact lease ids before any prefix. A prefix makes the string invalid JSON, so a later
    redact_leases pass would leave the token in place."""
    if not isinstance(value, str):
        return ""
    redacted = redact_leases(value)
    return redacted if isinstance(redacted, str) else ""


def _display(value) -> str | None:
    """Status prose with a whitespace-delimited approval word relabeled.

    None when the token is embedded in a path or other evidence that cannot be relabeled
    without renaming it.
    """
    text = _withhold_approval(_redact_text(value))
    if "APPROVED" in text:
        return None
    return text


def _state_label(state) -> str:
    if state == "APPROVED":
        return "advisory-complete"
    return state if isinstance(state, str) else ""


def _sha_field(obj) -> str | None:
    if not isinstance(obj, dict):
        return None
    for key in ("commit", "sha", "git_sha", "head"):
        val = obj.get(key)
        if isinstance(val, str) and _SHA.fullmatch(val):
            return val
    return None


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


def _recorded_word(verdict) -> str:
    if not isinstance(verdict, str) or verdict == "APPROVED":
        return "untrusted"
    return verdict


def _payload(env) -> dict:
    if not isinstance(env, dict):
        return {}
    payload = env.get("payload")
    return payload if isinstance(payload, dict) else {}


def _load_env(raw) -> dict | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        env = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return env if isinstance(env, dict) else None


def _gate_task_of(row: dict) -> str | None:
    payload = _payload(_load_env(row.get("envelope_json")))
    gate_task = payload.get("gate_task")
    return gate_task if isinstance(gate_task, str) else None


def _rows(store: TaskStore, task_id: str, gate: str) -> list[dict]:
    try:
        store.get(task_id)
    except SwarmError:
        return []
    out = []
    for record in store.conn.execute(
            "SELECT * FROM verdicts WHERE task_id=? AND gate=? ORDER BY id", (task_id, gate)):
        row = dict(record)
        raw = row.get("findings")
        if isinstance(raw, str):
            try:
                row["findings"] = json.loads(raw)
            except json.JSONDecodeError:
                row["findings"] = []
        out.append(row)
    return out


def _pick_row(store: TaskStore, target: str, gate: str, gate_task_id: str, siblings: list[str]) -> dict | None:
    """The verdict this gate task issued. A row with no gate_task is used only when this is the only attempt."""
    rows = _rows(store, target, gate)
    bound = [row for row in rows if _gate_task_of(row) == gate_task_id]
    if bound:
        return bound[-1]
    if len(siblings) != 1:
        return None
    loose = [row for row in rows if _gate_task_of(row) is None]
    return loose[-1] if loose else None


def _classify_row(store: TaskStore, task_id: str, gate: str, row: dict) -> tuple[str, str]:
    """(signature label, recorded verdict word). Never the approval token."""
    recorded = _recorded_word(row.get("verdict") or "none")
    env = _load_env(row.get("envelope_json"))
    if env is None:
        return "unsigned", recorded
    payload = _payload(env)
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


def _advisory_file(sdir: Path, gate_task_id: str, target: str, gate: str) -> dict | None:
    """Keyless gate scripts write verdicts/<gate task>.<gate>.json and record no row."""
    for name in (f"{gate_task_id}.{gate}.json", f"{target}.{gate}.json"):
        if "/" in name or ".." in name:
            continue
        path = sdir / "verdicts" / name
        env = _load_env(path.read_text(encoding="utf-8")) if path.is_file() else None
        if env is not None:
            return env
    return None


def _saved_target_verdicts(payload: dict) -> dict[str, str]:
    """Per-target verdicts saved on a gate event. The event's own verdict is the combined result."""
    recorded = payload.get("recorded")
    if not isinstance(recorded, dict):
        return {}
    out = {}
    for target, env in recorded.items():
        if not isinstance(target, str) or not isinstance(env, dict):
            continue
        verdict = _payload(env).get("verdict")
        if isinstance(verdict, str):
            out[target] = verdict
    return out


def _describe(store: TaskStore, target: str, gate: str, row: dict | None, advisory: dict | None, *,
              ran: bool, event_verdict: str | None = None) -> tuple[str, str]:
    if row is not None:
        return _classify_row(store, target, gate, row)
    if advisory is not None or ran:
        # A file or a script event with no row is an unsigned run, not a missing run, and not a signature.
        # event_verdict is this target's saved envelope only. The combined script verdict is not copied here.
        if advisory is not None:
            recorded = _recorded_word(_payload(advisory).get("verdict") or "none")
        elif event_verdict:
            recorded = _recorded_word(event_verdict)
        else:
            recorded = "unknown"
        return "unsigned keyless", recorded
    return "not run", "none"


def _siblings(tasks: list[dict], target: str, gate: str) -> list[str]:
    found = []
    for task in tasks:
        notes = task.get("notes_json") or {}
        targets = notes.get("gate_for") or []
        if notes.get("gate") == gate and target in targets:
            found.append(task["task_id"])
    return found


def _artifact_commit(store: TaskStore, task_id: str) -> str | None:
    try:
        task = store.get(task_id)
    except SwarmError:
        return None
    for output in task.get("outputs") or []:
        if not isinstance(output, dict) or output.get("kind") not in ("commit", "git"):
            continue
        for key in ("uri", "digest", "version"):
            val = output.get(key)
            if isinstance(val, str) and _SHA.fullmatch(val):
                return val
    return None


def _evidence(store: TaskStore, task_id: str) -> tuple[str, int]:
    try:
        task = store.get(task_id)
    except SwarmError:
        return "", 0
    kept, omitted = [], 0
    for output in task.get("outputs") or []:
        uri = output.get("uri") if isinstance(output, dict) else None
        if not isinstance(uri, str):
            continue
        if "APPROVED" in uri:
            omitted += 1
            continue
        kept.append(uri)
    return ", ".join(kept), omitted


def _commands(events: list[dict]) -> tuple[list[dict], dict[tuple[str, str | None], int], list[str], list[dict],
                                         dict[tuple[str, str | None], dict]]:
    """Recorded script runs, latest event per (script, task). A dry-run event keeps --dry-run.

    An event with no recorded cmd is not given an invented command line. An event with no
    exit_code and no status is not given a passing exit.
    """
    commands, index, skipped, meta = [], {}, [], []
    bare: dict[tuple[str, str | None], dict] = {}
    for event in events:
        kind = event.get("type") or ""
        if not isinstance(kind, str) or not kind.startswith("script.") or kind.endswith(".error"):
            continue
        name = kind.removeprefix("script.")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        dry = payload.get("dry_run") is True
        recorded = payload.get("cmd")
        owner = event.get("task_id") if isinstance(event.get("task_id"), str) else None
        if not isinstance(recorded, str) or not recorded.strip():
            # Gate scripts record status and executed runners, not the argv the runner used.
            note = f"{kind} has no recorded cmd"
            if dry:
                note += "; dry-run"
            skipped.append(note + "; it was not turned into a command")
            bare[(name, owner)] = {
                "dry": dry, "commit": _sha_field(payload),
                "summary": payload.get("summary") if isinstance(payload.get("summary"), str) else None,
                "verdict": payload.get("verdict") if isinstance(payload.get("verdict"), str) else None,
                "per_target": _saved_target_verdicts(payload),
            }
            continue
        cmd = recorded
        if "APPROVED" in cmd:
            skipped.append(f"{kind} command line contained the approval token and was omitted")
            summary = payload.get("summary")
            if isinstance(summary, str) and summary:
                shown = _display(summary)
                if shown:
                    skipped.append(f"{kind} recorded output: {shown}")
                else:
                    skipped.append(
                        f"{kind} output tail omitted; it contained the approval token "
                        "and could not be preserved")
            continue
        if dry and "--dry-run" not in cmd.split():
            cmd = f"{cmd} --dry-run"
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
            shown = _display(summary)
            if shown:
                entry["output_tail"] = shown
            elif "APPROVED" in summary:
                skipped.append(f"{kind} output tail omitted; it contained the approval token and could not be preserved")
        slot = len(commands)
        commands.append(entry)
        index[(name, owner)] = slot
        meta.append({"dry": dry, "commit": _sha_field(payload)})
    return commands, index, skipped, meta, bare


_OCI_REF = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?::[0-9]+)?(?:/[A-Za-z0-9][A-Za-z0-9._-]*)+"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?(?:@sha256:[0-9a-f]{64})?")
_OCI_DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}")


def _recorded_images(sdir: Path) -> set[str]:
    """image fields from devops_build_record JSON. Those URIs are not files."""
    found: set[str] = set()
    directory = sdir / "artifacts"
    if not directory.is_dir():
        return found
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        image = record.get("image")
        if isinstance(image, str) and image.strip():
            found.add(image)
    return found


def _filesystem_path(uri: str) -> bool:
    return uri.startswith(("/", "./", "../", ".swarm/")) or "\\" in uri


def _is_image(uri: str, recorded: set[str]) -> bool:
    """An image named by a build record, or an OCI ref that is not a filesystem path."""
    if uri in recorded:
        return True
    if _filesystem_path(uri):
        return False
    return _OCI_DIGEST.fullmatch(uri) is not None or _OCI_REF.fullmatch(uri) is not None


def _files(tasks: list[dict], recorded_images: set[str]) -> tuple[list[str], list[str], int]:
    """File paths, non-file image lines, and a count of paths omitted for the approval token."""
    found, other = [], []
    omitted = 0
    for task in tasks:
        for output in task.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            uri = output.get("uri")
            if not isinstance(uri, str) or not uri:
                continue
            if "APPROVED" in uri:
                omitted += 1
                continue
            if output.get("kind") == "build.artifact" and _is_image(uri, recorded_images):
                line = f"non-file artifact {task['task_id']} build.artifact: {uri}"
                if line not in other:
                    other.append(line)
                continue
            if uri not in found:
                found.append(uri)
    return sorted(found), other, omitted


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


def _checked_commit(info: dict, row: dict | None, advisory: dict | None, store: TaskStore, target: str) -> str:
    """A sha recorded on the event, the verdict, or a commit artifact. Never the export-time HEAD."""
    found = info.get("commit") or _sha_field(_payload(_load_env((row or {}).get("envelope_json"))))
    found = found or _sha_field(_payload(advisory)) or _artifact_commit(store, target)
    return found or "not recorded"


def _receipt(store: TaskStore, tasks: list[dict], events: list[dict], *, task_id: str, root: Path) -> dict:
    sdir = swarm_dir(root, create=False)
    commands, index, skipped, meta, bare = _commands(events)
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
        key = (script, task["task_id"]) if script else None
        slot = index.get(key) if key is not None else None
        if slot is not None:
            info = meta[slot]
        elif key is not None and key in bare:
            info = bare[key]
        else:
            info = {"dry": False, "commit": None}
        ran = slot is not None or (key is not None and key in bare)
        for target in targets:
            covered.add((target, gate))
            siblings = _siblings(tasks, target, gate)
            row = _pick_row(store, target, gate, task["task_id"], siblings)
            advisory = None if row is not None else _advisory_file(sdir, task["task_id"], target, gate)
            per_target = info.get("per_target") if isinstance(info.get("per_target"), dict) else {}
            specific = per_target.get(target) if isinstance(per_target.get(target), str) else None
            signature, recorded = _describe(
                store, target, gate, row, advisory, ran=ran, event_verdict=specific)
            commit = _checked_commit(info, row, advisory, store, target)
            evidence, omitted_evidence = _evidence(store, target)
            if omitted_evidence:
                unverified.append(
                    f"omitted {omitted_evidence} evidence path(s) for {target}; "
                    "they contained the approval token and could not be preserved")
            tail = f"; evidence {evidence}" if evidence else ""
            if signature == "not run":
                unverified.append(
                    f"{gate} gate task {task['task_id']} for {target} was not run; commit {commit}")
                continue
            sim = " simulation;" if info["dry"] else ""
            sentence = (f"{gate} gate task {task['task_id']} for {target} is advisory;{sim} "
                        f"signature {signature}; recorded {recorded}; commit {commit}{tail}")
            if slot is None:
                missing = "command line was not recorded" if ran else "no script run recorded"
                unverified.append(sentence + f"; {missing}")
                continue
            claims.append({"claim": sentence, "evidence_command_index": slot})
        if key is not None and key in bare:
            summary = info.get("summary") if isinstance(info.get("summary"), str) else None
            if summary:
                shown = _display(summary)
                if shown:
                    unverified.append(f"script {script} result: {shown}")
                else:
                    unverified.append(
                        f"script {script} result omitted; it contained the approval token and could not be preserved")
            combined = info.get("verdict") if isinstance(info.get("verdict"), str) else None
            if combined:
                unverified.append(f"script {script} combined verdict: {_recorded_word(combined)}")
    for task in tasks:
        if (task.get("notes_json") or {}).get("gate"):
            continue
        for gate in store.required_gates(task["task_id"]):
            if (task["task_id"], gate) in covered:
                continue
            row = None
            try:
                row = store.latest_verdicts(task["task_id"]).get(gate)
            except SwarmError:
                row = None
            if row is None:
                unverified.append(f"{gate} gate on {task['task_id']} was not run; no verdict row")
                continue
            signature, recorded = _classify_row(store, task["task_id"], gate, row)
            unverified.append(
                f"{gate} gate on {task['task_id']} recorded signature {signature}; recorded {recorded}; "
                "execution evidence is missing (no gate task)")
    for task in tasks:
        unverified.append(f"task {task['task_id']} state: {_state_label(task.get('state'))}")
        title = _display(task.get("title") or "")
        if title is None:
            unverified.append(
                f"task {task['task_id']} title omitted; it contained the approval token and could not be preserved")
        else:
            unverified.append(f"task {task['task_id']} title: {title}")
        try:
            verdicts = store.latest_verdicts(task["task_id"])
        except SwarmError:
            verdicts = {}
        for gate, row in verdicts.items():
            for finding in row.get("findings") or []:
                summary = finding.get("summary") if isinstance(finding, dict) else None
                if isinstance(summary, str) and summary:
                    shown = _display(summary)
                    if shown:
                        unverified.append(f"finding {task['task_id']} {gate}: {shown}")
                    else:
                        unverified.append(
                            f"finding {task['task_id']} {gate} omitted; "
                            "it contained the approval token and could not be preserved")
    files_changed, non_files, omitted_files = _files(tasks, _recorded_images(sdir))
    unverified.extend(non_files)
    if omitted_files:
        unverified.append(
            f"omitted {omitted_files} files_changed entry that contained the approval token "
            "and could not be preserved")
    unverified.extend(unverified_extra)
    for i, command in enumerate(commands):
        if "duration_s" not in command:
            unverified.append(f"command {i} ({command['cmd']}) has no recorded duration_s")
    observed = _observed_commit(root)
    if observed != "not recorded":
        unverified.append(
            f"export observed HEAD {observed}; this is not the commit the gates checked")
    started = _iso(min(t["created_at"] for t in tasks)) if tasks else None
    completed = (_iso(max(t["updated_at"] for t in tasks))
                 if tasks and all(t.get("state") in _FINISHED for t in tasks) else None)
    return {
        "task_id": task_id,
        "bot": BOT,
        "started_at": started,
        "completed_at": completed,
        "commands": commands,
        "claims": claims,
        "unverified": unverified,
        "files_changed": files_changed,
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
    """Redact, then refuse a leftover credential or an approval token that evidence still carries."""
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
    fd, name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
        os.replace(name, dest)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


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
        store, tasks, events, task_id=corr or "unscoped", root=ctx.root)))
    if args.out:
        _write(args.out, receipt)
    return {"status": "ok", "receipt": receipt,
            "summary": f"advisory receipt for {receipt['task_id']}: {len(receipt['claims'])} claims, approved_by empty"}


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", dest="root", default=argparse.SUPPRESS, help="alias of --root")
    p.add_argument("--out", help="write the desk receipt JSON here; skipped when a credential shape remains")


if __name__ == "__main__":
    sys.exit(AgentScript("A01", "receipt_export", run, description=__doc__, add_args=add_args).main())
