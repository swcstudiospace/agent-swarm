"""Gate-verdict recording (D-12/D-13): gate scripts are the only writers of verdict rows.

A gate script invoked with a leased (IN_PROGRESS) gate task's id (notes.gate) records one signed verdict per
target in that task's Task Store notes.gate_for, signed with the target's correlation — agents cannot choose
or spoof targets. Any other --task-id writes only the envelope file and emits gate.verdict.unrecorded.
--dry-run verdicts are signed dry_run: true and recorded only for gate tasks A01 flagged as a runner dry-run.
Inside a headless agent session (SWARM_AGENT_SESSION=1, no signing keys) nothing is recorded: the runner re-runs
the gate script with its keys after the session (WR-12).
"""
from __future__ import annotations
import os
from typing import Callable

from .errors import SwarmError, ErrorCode
from .gates import make_verdict

GATE_SCRIPTS = {"quality": "qa_gate", "review": "rev_gate", "security": "sec_gate", "release": "rel_plan"}
SIM_FINDING = {"id": "SIM-1", "severity": "major", "kind": "functional", "summary": "simulated gate failure",
               "evidence": "", "ac_ref": None, "owner_suggestion": None, "location": None}


def simulated_failures() -> set[str]:
    """SWARM_DRYRUN_FAIL='T7f3a-be:quality,T7f3a-fe:review' makes those dry-run gate verdicts fail every time."""
    return {x.strip() for x in os.environ.get("SWARM_DRYRUN_FAIL", "").split(",") if x.strip()}


def simulated_per_target(targets: list[str] | None, gate: str) -> dict[str, list[dict]]:
    """Dry-run findings per target: SIM-1 (major) for targets named in SWARM_DRYRUN_FAIL, else none."""
    fails = simulated_failures()
    return {t: ([dict(SIM_FINDING)] if f"{t}:{gate}" in fails else []) for t in targets or []}


def resolve_targets(store, task_id: str | None, *, gate: str, correlation_id: str | None = None) -> list[str] | None:
    """gate_for of a gate task; None when task_id is not a gate task. E-POLICY when its gate differs, when
    correlation_id is given and is not the gate task's own correlation, or when a gate task with targets is
    not leased (IN_PROGRESS) — only a running gate task records verdicts."""
    if not task_id:
        return None
    try:
        task = store.get(task_id)
    except SwarmError as e:
        if e.code is not ErrorCode.E_INPUT:
            raise
        return None
    notes = task.get("notes_json") or {}
    if not notes.get("gate"):
        return None
    if notes["gate"] != gate:
        raise SwarmError(ErrorCode.E_POLICY, f"{task_id} is a {notes['gate']} gate task; this script issues {gate}",
                         task_id=task_id)
    own = task["correlation_id"]
    if correlation_id and correlation_id != own:
        raise SwarmError(ErrorCode.E_POLICY,
                         f"gate task {task_id} belongs to correlation {own!r}, not {correlation_id!r}; --correlation-id "
                         "defaults to the SWARM_CORRELATION_ID env var: unset a stale SWARM_CORRELATION_ID export or "
                         f"pass --correlation-id {own}", task_id=task_id)
    targets = list(notes.get("gate_for") or [])
    if targets and task["state"] != "IN_PROGRESS":
        raise SwarmError(ErrorCode.E_POLICY, f"{task_id} is not a running gate task (state {task['state']}); "
                         "lease it first", task_id=task_id)
    return targets


def record_gate_verdicts(store, *, gate_task_id: str | None, gate: str, agent_id: str, findings: list[dict],
                         runs: dict, correlation_id: str | None, expires_s: int, extra: dict | None = None,
                         verdict: str | None = None, per_target: dict[str, list[dict]] | None = None,
                         emit: Callable[..., object], root=None) -> dict[str, dict]:
    """Record one signed verdict row per gate_for target of `gate_task_id`; return {target: envelope}.
    Each target verdict is signed with the target's own correlation; a given `correlation_id` must be the
    gate task's (E-POLICY otherwise). All rows (and fail feedback) of one run commit together or not at all (D-13).
    `root` is the caller's --root, so dev-key signing events land in the same state dir."""
    targets = resolve_targets(store, gate_task_id, gate=gate, correlation_id=correlation_id)
    if not targets:
        emit("gate.verdict.unrecorded", {"task_id": gate_task_id, "gate": gate,
                                         "reason": "not a gate task" if targets is None else "empty gate_for"})
        return {}
    out = {}
    with store.transaction():
        for target in targets:
            tf = (per_target or {}).get(target, findings)
            env = make_verdict(gate=gate, task_id=target, agent_id=agent_id, findings=tf, runs=runs,
                               verdict=None if per_target and target in per_target else verdict,
                               expires_s=expires_s, correlation_id=store.get(target)["correlation_id"],
                               extra={**(extra or {}), "gate_task": gate_task_id}, root=root)
            store.record_verdict(target, env)
            if env["payload"]["verdict"] == "fail":
                store.append_feedback(target, {"gate": gate, "source": "script", "findings": tf})
            out[target] = env
    return out



def issue_gate(ctx, *, gate: str, agent_id: str, findings: list[dict], runs: dict, expires_s: int = 86400,
               extra: dict | None = None, simulate: bool = False,
               per_target: dict[str, list[dict]] | None = None) -> tuple[dict, dict[str, dict]]:
    """Gate-script tail: sign the verdict for ctx.task_id, write verdicts/<id>.<gate>.json, and record rows on
    the gate task's targets. per_target ({target: findings}) gives each target a verdict derived from its own
    findings (D-13); targets it omits get `findings`. The file envelope always carries `findings`.
    simulate (--dry-run): canned pass, SIM-1 fail for SWARM_DRYRUN_FAIL targets, plus
    the caller's findings; every envelope carries a signed dry_run: true, and rows are recorded only when A01
    flagged the gate task as a runner dry-run (notes.dry_run), where they also count only on flagged targets."""
    import json
    from .paths import swarm_dir
    from .taskstore import TaskStore
    store = TaskStore(root=ctx.root) if ctx.task_id else None
    targets = (resolve_targets(store, ctx.task_id, gate=gate, correlation_id=ctx.correlation_id)
               if store else None)  # E-POLICY before any write
    gate_task = store.get(ctx.task_id) if store and targets is not None else None
    # file envelope: the caller's correlation, else the gate task's; a non-gate/unknown id keeps a minted one
    corr = ctx.correlation_id or (gate_task["correlation_id"] if gate_task else None)
    if simulate:
        extra = {**(extra or {}), "dry_run": True}
    task = ctx.task_id or ("T-dry" if simulate else "T-unassigned")
    env = make_verdict(gate=gate, task_id=task, agent_id=agent_id, findings=findings, runs=runs, expires_s=expires_s,
                       correlation_id=corr, extra=extra, root=ctx.root)
    out_dir = swarm_dir(ctx.root, create=True) / "verdicts"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{task}.{gate}.json").write_text(json.dumps(env, indent=2))
    if os.environ.get("SWARM_AGENT_SESSION") == "1":
        ctx.emit("gate.verdict.unrecorded", {"task_id": ctx.task_id, "gate": gate,
                                             "reason": "agent session: the runner records this gate after the session"})
        return env, {}
    if store is None:
        ctx.emit("gate.verdict.unrecorded", {"task_id": None, "gate": gate, "reason": "no --task-id"})
        return env, {}
    if simulate:
        if targets and not (gate_task and gate_task["notes_json"].get("dry_run")):
            ctx.emit("gate.verdict.unrecorded", {"task_id": ctx.task_id, "gate": gate,
                                                 "reason": "dry-run outside a runner dry-run"})
            return env, {}
        per_target = {t: sim + list(findings) for t, sim in simulated_per_target(targets, gate).items()}
    recorded = record_gate_verdicts(store, gate_task_id=ctx.task_id, gate=gate, agent_id=agent_id, findings=findings,
                                    runs=runs, correlation_id=ctx.correlation_id, expires_s=expires_s, extra=extra,
                                    per_target=per_target, emit=ctx.emit, root=ctx.root)
    return env, recorded
