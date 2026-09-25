"""Gate-verdict recording (D-12/D-13): gate scripts are the only writers of verdict rows.

A gate script invoked with a gate task's id (notes.gate) records one signed verdict per target in that
task's Task Store notes.gate_for — agents cannot choose or spoof targets. Any other --task-id writes only
the envelope file and emits gate.verdict.unrecorded.
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
    """gate_for of a gate task; None when task_id is not a gate task. E-POLICY when its gate differs, or when
    correlation_id is given and is not the gate task's own correlation."""
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
    return list(notes.get("gate_for") or [])


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
               extra: dict | None = None, simulate: bool = False) -> tuple[dict, dict[str, dict]]:
    """Gate-script tail: sign the verdict for ctx.task_id, write verdicts/<id>.<gate>.json, and record rows on
    the gate task's targets (dry-run: canned pass, SIM-1 fail for SWARM_DRYRUN_FAIL targets)."""
    import json
    from .paths import swarm_dir
    from .taskstore import TaskStore
    store = TaskStore(root=ctx.root) if ctx.task_id else None
    targets = (resolve_targets(store, ctx.task_id, gate=gate, correlation_id=ctx.correlation_id)
               if store else None)  # E-POLICY before any write
    # file envelope: the caller's correlation, else the gate task's; a non-gate/unknown id keeps a minted one
    corr = ctx.correlation_id or (store.get(ctx.task_id)["correlation_id"] if store and targets is not None else None)
    task = ctx.task_id or ("T-dry" if simulate else "T-unassigned")
    env = make_verdict(gate=gate, task_id=task, agent_id=agent_id, findings=findings, runs=runs, expires_s=expires_s,
                       correlation_id=corr, extra=extra, root=ctx.root)
    out_dir = swarm_dir(ctx.root, create=True) / "verdicts"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{task}.{gate}.json").write_text(json.dumps(env, indent=2))
    if store is None:
        ctx.emit("gate.verdict.unrecorded", {"task_id": None, "gate": gate, "reason": "no --task-id"})
        return env, {}
    recorded = record_gate_verdicts(store, gate_task_id=ctx.task_id, gate=gate, agent_id=agent_id, findings=findings,
                                    runs=runs, correlation_id=ctx.correlation_id, expires_s=expires_s, extra=extra,
                                    per_target=simulated_per_target(targets, gate) if simulate else None, emit=ctx.emit,
                                    root=ctx.root)
    return env, recorded
