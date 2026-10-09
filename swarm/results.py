"""The one task.result path shared by the headless runner and `orch_status.py --ingest`.

Both callers do: parse_result → validate_result → apply_result → reconcile.

The ONE intended mode difference:
  * mode="headless": the session is over, so a final state of IN_PROGRESS is a contract failure
    (FAILED "E-CONTRACT: session ended in IN_PROGRESS"); a missing/invalid result also becomes
    FAILED E-CONTRACT and counts as an attempt (retry ladder → ESCALATED at max_attempts).
  * mode="ingest": IN_PROGRESS is a claim/heartbeat; PLANNED/RETRY tasks whose dependencies are satisfied are
    auto-claimed (CLAIMED → IN_PROGRESS, event task.claimed) before the reported state is applied. A BLOCKED
    task (e.g. needs: human-approval) or one with unmet dependencies is rejected: A01 releases BLOCKED only with
    `orch_status.py --transition` after approval. Invalid input is rejected with E-CONTRACT and no transition
    (never consumes an attempt).
Both modes accept a gate task's IN_REVIEW only when its gate script recorded a verdict on every gate_for target
during the current lease (WR-08); otherwise "E-CONTRACT: gate script not run" (headless FAILED, ingest exit 2).
A review gate task's IN_REVIEW is also refused when its verdicts{} fails a target (agent_failed) whose review row from
this lease is not `fail`: "E-CONTRACT: review verdict mismatch: ..." (headless FAILED, ingest exit 2, no transition).
reconcile approves a target only from rows whose gate task (the signed gate_task) has an accepted result (IN_REVIEW,
APPROVED or DONE), so a refused gate task's rows hold its targets even after it leaves the lease FAILED or BLOCKED.
Rejections on both paths emit task.result.rejected {task_id, mode, reason}.
"""
from __future__ import annotations
import json
import re
from contextlib import nullcontext
from typing import Callable

from .errors import SwarmError, ErrorCode
from .gates import BLOCKING_SEVERITY, SEVERITIES, validate_verdict
from .schema import load_schema, validate
from .taskstore import TaskStore, TaskState as S

JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
SCHEMA_NAME = "task.result.v1"
Emit = Callable[[str, dict], object]


def parse_result(text: str) -> dict | None:
    """Last parseable fenced ```json block, else the whole text as a bare JSON object."""
    for raw in reversed(JSON_BLOCK.findall(text or "")):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    try:
        obj = json.loads(text or "")
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def validate_result(result, *, task_id: str) -> dict:
    """Schema-check `result` and bind it to the assigned task; raise E-CONTRACT otherwise."""
    if result is None:
        raise SwarmError(ErrorCode.E_CONTRACT, "no JSON result block from agent", task_id=task_id)
    errors = validate(result, load_schema(SCHEMA_NAME))
    if errors:
        raise SwarmError(ErrorCode.E_CONTRACT, f"task.result invalid: {'; '.join(errors)}"[:500], task_id=task_id)
    if result["task_id"] != task_id:
        raise SwarmError(ErrorCode.E_CONTRACT, f"task_id mismatch: result names {result['task_id']!r}, assigned {task_id!r}",
                         task_id=task_id)
    return result


def reject(store: TaskStore, task_id: str, *, reason: str, mode: str, emit: Emit) -> str:
    """Record a rejected result. Headless: FAILED (counts as an attempt). Ingest: event only."""
    emit("task.result.rejected", {"task_id": task_id, "mode": mode, "reason": reason})
    if mode == "headless":
        store.transition(task_id, S.FAILED, reason=reason[:500])
        return S.FAILED.value
    return store.get(task_id)["state"]


def _lease_start(store: TaskStore, task_id: str) -> float | None:
    """ts of the task's latest CLAIMED transition (its current lease start), else None."""
    claims = [h["ts"] for h in store.history(task_id) if h["to_state"] == S.CLAIMED.value]
    return claims[-1] if claims else None


def _gate_script_missing(store: TaskStore, task: dict) -> list[str]:
    """gate_for targets of a gate task lacking a verifying row that its gate script issued during the current lease
    (since the latest CLAIMED). With no CLAIMED row the lease start is unknown, so every target counts as missing."""
    notes = task["notes_json"]
    targets = list(notes.get("gate_for") or [])
    since = _lease_start(store, task["task_id"])
    if since is None:
        return targets
    return [t for t in targets
            if not store.gate_verdict_since(t, gate=notes["gate"], gate_task_id=task["task_id"], since=since)]


# The only agent verdicts that pass a target (after strip/lower-case). `approve` is A09's legacy alias of `pass`;
# request_changes, block, fail, the human-only waive, unknown and missing verdicts all fail (WR-17/WR-18).
PASS_VERDICTS = frozenset({"pass", "approve"})


def agent_verdict(entry: dict) -> str:
    """A gate agent's verdicts{} entry's verdict string, normalized (stripped, lower-case; "" when missing).
    validate_result guarantees the entry is an object and its verdict a string."""
    return str(entry.get("verdict") or "").strip().lower()


def agent_failed(entry: dict) -> bool:
    """Fail closed: an entry fails its target unless its verdict is in PASS_VERDICTS. Only for entries present in
    verdicts{}."""
    return agent_verdict(entry) not in PASS_VERDICTS


def _review_verdict_mismatch(store: TaskStore, task: dict, result: dict) -> list[str]:
    """Sorted gate_for targets of a review gate task whose agent verdicts{} entry fails (agent_failed) while the review
    row its script issued during the current lease is not `fail`. A failing entry under a key that is not a gate_for
    id applies to every target. Targets without such a row are _gate_script_missing's concern."""
    notes = task["notes_json"]
    targets = list(notes.get("gate_for") or [])
    verdicts = result.get("verdicts")
    failing = {k for k, v in (verdicts if isinstance(verdicts, dict) else {}).items() if agent_failed(v)}
    since = _lease_start(store, task["task_id"])
    if not failing or since is None:
        return []
    accused = targets if failing - set(targets) else sorted(failing)
    out = []
    for t in accused:
        recorded = store.gate_verdict_value_since(t, gate=notes["gate"], gate_task_id=task["task_id"], since=since)
        if recorded is not None and recorded != "fail":
            out.append(t)
    return sorted(out)


# Severities that fail a review target (rev_gate's blocking set).
BLOCKING_SEVERITIES = frozenset(SEVERITIES[SEVERITIES.index(BLOCKING_SEVERITY):])


def _counts_major(finding) -> bool:
    """A finding under a failing entry counts as major or worse unless its severity is a known one below major
    (case-insensitive); non-object findings and missing/unknown severities count as major, as swarm_run normalizes."""
    if not isinstance(finding, dict):
        return True
    sev = finding.get("severity")
    if isinstance(sev, str) and sev.lower() in SEVERITIES:
        return sev.lower() in BLOCKING_SEVERITIES
    return True


def agent_findings(entry: dict) -> tuple[list, bool]:
    """(findings, synthesized) of a verdicts{} entry. A failing entry without a finding of major or worse gets one
    synthesized major `agent-verdict` finding appended (IN-15), so an agent's failure always fails its target instead of
    reducing to an empty list or to minor findings that rev_gate passes. Passing entries are returned unchanged."""
    items = list(entry.get("findings") or [])
    if not agent_failed(entry) or any(_counts_major(f) for f in items):
        return items, False
    verdict = agent_verdict(entry) or "no verdict"
    why = "without a major finding" if items else "without findings"
    return items + [{"severity": "major", "kind": "agent-verdict", "summary": f"gate agent reported {verdict!r} {why}"}], True


def _agent_verdict_feedback(store: TaskStore, task: dict, result: dict) -> None:
    """A gate agent's verdicts{} is advisory text only (D-12): failing entries (agent_failed) become feedback on the
    gate task's own gate_for targets. Verdict rows are written solely by the gate scripts (swarm.verdicts)."""
    notes = task["notes_json"]
    gate = notes.get("gate")
    if not gate:
        return
    verdicts = result.get("verdicts")
    for target, v in (verdicts if isinstance(verdicts, dict) else {}).items():
        if target not in notes.get("gate_for", []) or not agent_failed(v):
            continue
        store.append_feedback(target, {"gate": gate, "source": "agent", "findings": agent_findings(v)[0]})


def apply_result(store: TaskStore, task: dict, *, agent_id: str, result: dict, meta: dict, emit: Emit, mode: str) -> str:
    """Apply a validated result to the assigned task only; returns the task's new state."""
    tid = task["task_id"]
    state = result["state"]
    if mode == "headless" and state == S.IN_PROGRESS.value:
        return reject(store, tid, reason="E-CONTRACT: session ended in IN_PROGRESS", mode=mode, emit=emit)
    if mode == "ingest" and task["state"] == S.BLOCKED.value:
        raise SwarmError(ErrorCode.E_CONTRACT, f"{tid} is BLOCKED; A01 releases it with orch_status --transition after approval",
                         task_id=tid)
    if state == S.IN_REVIEW.value and task["notes_json"].get("gate"):
        missing = _gate_script_missing(store, task)  # D-12: only script rows satisfy a gate; checked before any transition
        if missing:
            err = SwarmError(ErrorCode.E_CONTRACT, f"gate script not run for {missing}", task_id=tid)
            if mode == "headless":
                return reject(store, tid, reason=str(err), mode=mode, emit=emit)
            raise err
        mismatch = _review_verdict_mismatch(store, task, result) if task["notes_json"]["gate"] == "review" else []
        if mismatch:
            err = SwarmError(ErrorCode.E_CONTRACT,
                             f"review verdict mismatch: agent verdicts fail {mismatch} but the review rows recorded this "
                             f"lease do not; re-run swarm_gate with a major finding for each of {mismatch}",
                             task_id=tid, targets=mismatch)
            if mode == "headless":
                return reject(store, tid, reason=str(err), mode=mode, emit=emit)
            raise err
    if mode == "ingest" and task["state"] in (S.PLANNED.value, S.RETRY.value):
        with store.transaction():  # the dependency check and the claim see one snapshot
            if not store.deps_satisfied(store.get(tid)):
                raise SwarmError(ErrorCode.E_CONTRACT, f"{tid} has unmet dependencies", task_id=tid)
            store.transition(tid, S.CLAIMED, actor=agent_id, reason="claimed via task.result ingest")
            store.transition(tid, S.IN_PROGRESS, actor=agent_id, reason="lease started")
        emit("task.claimed", {"task_id": tid, "agent": agent_id, "mode": mode})
    current = store.get(tid)["state"]
    if state == S.BLOCKED.value:
        target, reason = S.BLOCKED, str(result.get("needs", "blocked"))[:500]
    elif state == S.FAILED.value:
        target, reason = S.FAILED, json.dumps(result.get("error", {}))[:500]
    elif state == S.IN_REVIEW.value:
        target, reason = S.IN_REVIEW, "agent reported IN_REVIEW"
    else:  # ingest heartbeat: only legal while (now) IN_PROGRESS
        target, reason = None, ""
        if current != S.IN_PROGRESS.value:
            raise SwarmError(ErrorCode.E_CONTRACT, f"illegal transition {current} → IN_PROGRESS for {tid}", task_id=tid)
    if target is not None:
        store.transition(tid, target, actor=agent_id, reason=reason)
    store.set_notes(tid, result=result, meta=meta)
    for o in result.get("outputs", []) or []:
        store.add_artifact(tid, kind=o.get("kind", "artifact"), uri=o.get("uri", ""), version=str(o.get("version", "1")),
                           digest=o.get("digest", ""), producer=agent_id)
    if target is S.IN_REVIEW:
        _agent_verdict_feedback(store, task, result)
    return store.get(tid)["state"]


def _stalled_gates(store: TaskStore, task: dict, reasons: dict[str, str], corr: str) -> list[str]:
    """Sorted "gate:reason" for required gates missing for a reason other than fail that no live gate task (state
    outside DONE/CANCELLED/ESCALATED) of that gate covers: nothing is left to issue them, so the task would stall."""
    tid = task["task_id"]
    live = {g["notes_json"].get("gate") for g in store.list(correlation_id=corr)
            if tid in (g["notes_json"].get("gate_for") or [])
            and g["state"] not in (S.DONE.value, S.CANCELLED.value, S.ESCALATED.value)}
    return sorted(f"{g}:{r}" for g, r in reasons.items() if r != "fail" and g not in live)


RERUN_SUFFIX = re.compile(r"(\.r\d+)+$")


def _rerun_index(task: dict) -> int:
    """Rerun sequence of a gate task within its lineage: N for "<base>.rN", 0 for the original gate task."""
    m = re.search(r"\.r(\d+)$", task["task_id"])
    return int(m.group(1)) if m else 0


# A gate task in one of these states had its result accepted by apply_result (never refused, FAILED or BLOCKED).
ACCEPTED_ISSUER_STATES = frozenset({S.IN_REVIEW.value, S.APPROVED.value, S.DONE.value})


def _unaccepted_gates(store: TaskStore, tid: str, reasons: dict[str, str]) -> dict[str, str]:
    """T-05-11: required gates of `tid` whose current row passes (not in `reasons`) but was recorded by a gate task
    (the signed `gate_task`) whose result has not been accepted — leased, refused (review verdict mismatch), FAILED,
    BLOCKED, RETRY, ESCALATED or CANCELLED — → "unaccepted". Such a row may be contradicted by its own gate agent, so
    it never approves the target. Fails closed on an unreadable envelope or unknown gate task."""
    latest = store.latest_verdicts(tid)
    out = {}
    for g in store.required_gates(tid):
        if g in reasons or g not in latest:
            continue
        try:
            issuer = validate_verdict(json.loads(latest[g]["envelope_json"])).get("gate_task")
            accepted = issuer is None or store.get(issuer)["state"] in ACCEPTED_ISSUER_STATES
        except Exception:
            accepted = False
        if not accepted:
            out[g] = "unaccepted"
    return out


def reconcile(store: TaskStore, corr: str, emit: Emit, leases=None, handoffs=None, *, approve: bool = True) -> list[str]:
    """Apply A01 gate/rework rules to IN_REVIEW tasks; reopen gate tasks after rework; escalate stalled gates.

    `leases` is the runner's substrate_lease.LeaseBridge, or None (ingest, dry runs, substrate off). It is told about
    each transition here that changes who may hold the node: DONE completes it, CHANGES_REQUESTED releases and re-claims
    it for the rework, ESCALATED releases it (LEASE-07). `handoffs` is the runner's substrate_handoff.HandoffBridge, or
    None likewise. It writes a packet at each transition here that changes the accountable agent: a failing gate hands
    the rework to the producer, and ESCALATED hands the task on (HAND-01). The transition, packet and settlement share
    the keeper's per-task lock, so neither thread releases the lease before the handoff finishes."""
    notes_log = []
    for t in store.list(correlation_id=corr, state=S.IN_REVIEW.value):
        tid = t["task_id"]
        latest = store.latest_verdicts(tid, verified_only=True)
        failing = [g for g in store.required_gates(tid) if latest.get(g, {}).get("verdict") == "fail"]
        if failing:
            before = t["rework_loops"]
            with leases.transition(tid) if leases is not None else nullcontext():
                nt = store.transition(tid, S.CHANGES_REQUESTED, reason=f"gates failed: {failing}")
                if nt["state"] == S.ESCALATED.value:
                    emit("escalation.request", {"task_id": tid, "reason_code": "E-CONTRACT", "evidence": failing,
                                                "options": ["human review", "cancel", "waive gate (L3)"]})
                    notes_log.append(f"{tid}: ESCALATED after {before} rework loops")
                    if handoffs is not None:
                        handoffs.escalated(tid, reason=f"the rework cap was reached with gates {failing} failing",
                                           failing=failing, verdicts=latest)
                    if leases is not None:
                        leases.settle(tid)
                else:
                    store.transition(tid, S.IN_PROGRESS, reason="rework loop")
                    notes_log.append(f"{tid}: CHANGES_REQUESTED → rework #{nt['rework_loops']} ({failing})")
                    if handoffs is not None:  # the verdict rows read above: the transition made them stale
                        handoffs.gate_failed(tid, failing, latest, rework=nt["rework_loops"])
                    if leases is not None:  # the rework is a new hold, visible in the trail, not a silent re-run
                        leases.rework(tid)
            if nt["state"] != S.ESCALATED.value:
                # reopen gate tasks that target this task so they re-run after rework: one rerun per gate lineage
                # (<base>, <base>.r1, <base>.r2, ...), cloned from its latest member, never from a superseded one, and
                # numbered by the lineage (a multi-target gate's members are triggered by different targets). A leased
                # latest member runs against the pre-rework artifact, so it gets a rerun too (WR-04); a member that has
                # not started (PLANNED/RETRY) or is left to A01 (FAILED/BLOCKED/ESCALATED/CANCELLED) gets none
                lineages: dict[str, list[dict]] = {}
                for g in store.list(correlation_id=corr):
                    if tid in g["notes_json"].get("gate_for", []):
                        lineages.setdefault(RERUN_SUFFIX.sub("", g["task_id"]), []).append(g)
                created: dict[str, tuple[str, set[str]]] = {}  # new rerun id → (its gate, its lineage's member ids)
                for base, members in lineages.items():
                    g = max(members, key=_rerun_index)
                    if g["state"] not in (S.DONE.value, S.IN_REVIEW.value, S.APPROVED.value,
                                          S.CLAIMED.value, S.IN_PROGRESS.value):
                        continue
                    new_id = f"{base}.r{_rerun_index(g) + 1}"
                    store.create(task_id=new_id, correlation_id=corr, capability=g["capability"],
                                 title=g["title"].removesuffix(" (rerun)") + " (rerun)",
                                 agent_id=g["agent_id"], dag_depth=g["dag_depth"], depends_on=g["depends_on"],
                                 acceptance=g["acceptance"], budget=g["budget"], risk_class=g["risk_class"],
                                 priority=g["priority"], notes={k: v for k, v in g["notes_json"].items() if k not in ("result", "meta")})
                    store.transition(new_id, S.VALIDATED)
                    store.transition(new_id, S.PLANNED, reason="gate rerun")
                    created[new_id] = (g["notes_json"].get("gate"), {m["task_id"] for m in members})
                # once every rerun of this rework exists: downstream of any lineage member waits for that lineage's
                # rerun, and a release-gate rerun (it reads the other gates' verdicts) waits for the other reruns
                for d in store.list(correlation_id=corr):
                    if d["state"] == S.DONE.value:
                        continue
                    release_rerun = created.get(d["task_id"], ("", set()))[0] == "release"
                    extra = [nid for nid, (gate, mids) in created.items()
                             if nid != d["task_id"] and nid not in d["depends_on"]
                             and (mids.intersection(d["depends_on"]) or (release_rerun and gate != "release"))]
                    if extra:
                        store.update(d["task_id"], depends_on=d["depends_on"] + extra)
            continue
        # CR-01: a leased (CLAIMED/IN_PROGRESS) gate task of a required gate for this target has no accepted result
        # yet, or its result was refused (review verdict mismatch): a row it recorded may be contradicted, so hold
        # the target until that gate task's result is accepted
        leased = {g["notes_json"].get("gate") for g in store.list(correlation_id=corr)
                  if tid in (g["notes_json"].get("gate_for") or [])
                  and g["state"] in (S.CLAIMED.value, S.IN_PROGRESS.value)}
        if leased.intersection(store.required_gates(tid)):
            continue
        # T-05-11: the hold outlives the lease. A passing row counts only once the gate task that recorded it has an
        # accepted result, so a refused gate task ingested FAILED/BLOCKED, or moved FAILED → RETRY, never approves
        # its targets; its live lineage waits (no stall), an ESCALATED/CANCELLED issuer escalates below
        reasons = store.missing_gate_reasons(tid)
        reasons.update(_unaccepted_gates(store, tid, reasons))
        if not reasons:
            if not approve:
                emit("task.approval.skipped", {"task_id": tid, "reason": "advisory ingest: approval not attempted"},
                     task_id=tid)
                notes_log.append(f"{tid}: advisory, approval not attempted")
                continue
            store.transition(tid, S.APPROVED, reason="all required gates pass")
            store.transition(tid, S.DONE, reason="approved")
            notes_log.append(f"{tid}: DONE")
            if leases is not None:
                leases.settle(tid)
            continue
        # WR-08: a gate absent/expired/... with no gate task left to issue it would stall silently. Escalate once per
        # distinct stall (notes.gate_stall); no transition — A01 or a human decides.
        stall = _stalled_gates(store, t, reasons, corr)
        if stall != (t["notes_json"].get("gate_stall") or []):
            store.set_notes(tid, gate_stall=stall)
            if stall:
                emit("escalation.request", {"task_id": tid, "reason_code": "E-CONTRACT", "evidence": stall,
                                            "options": ["re-run gate", "human review", "cancel"]})
                notes_log.append(f"{tid}: gate stall {stall} → escalation.request")
    return notes_log
