"""A01 orchestrator checks over a supplied plan, gate verdicts, and rework count.

Pure functions: every input comes from the payload. A01 uses them to decide which tasks
may dispatch, whether a task may be approved, and whether a failed gate gets another rework loop.

Tools:
- orch_dag_blocked: classify PLANNED/RETRY tasks as ready, waiting, or blocked, and report unorderable (cycle) tasks.
- orch_gate_coverage: check the latest verdict per gate against the gates a risk class requires.
- orch_rework_bound: apply the rework-loop cap (default 2, then ESCALATED) to a gate failure.
"""
from __future__ import annotations

from swarm.tools.support import respond

DISPATCHABLE = frozenset({"PLANNED", "RETRY"})
SATISFIED = frozenset({"IN_REVIEW", "APPROVED", "DONE"})  # dependency states that count as met
DEAD = frozenset({"ESCALATED", "CANCELLED"})  # dependency states that can never be met
DEFAULT_MAX_LOOPS = 2  # mirrors swarm.taskstore.MAX_REWORK_LOOPS; the payload may override it


def _unresolved(deps: dict[str, set[str]]) -> set[str]:
    """Kahn's algorithm over in-payload edges. Tasks it cannot order are cycle members or depend on one."""
    known = set(deps)
    pending = {tid: len(needs & known) for tid, needs in deps.items()}
    dependents: dict[str, list[str]] = {tid: [] for tid in deps}
    for tid, needs in deps.items():
        for need in needs & known:
            dependents[need].append(tid)
    ready = [tid for tid, count in pending.items() if count == 0]
    while ready:
        done = ready.pop()
        for tid in dependents[done]:
            pending[tid] -= 1
            if pending[tid] == 0:
                ready.append(tid)
    return {tid for tid, count in pending.items() if count > 0}


def dag_blocked(payload: dict) -> dict:
    def work() -> dict:
        tasks = payload["tasks"]
        ids = [task["id"] for task in tasks]
        if len(set(ids)) != len(ids):
            dupes = sorted({tid for tid in ids if ids.count(tid) > 1})
            return {"state": "INVALID_INPUT", "field": "tasks", "message": f"duplicate task ids: {', '.join(dupes)}"}
        state = {task["id"]: task["state"] for task in tasks}
        deps = {task["id"]: set(task["depends_on"]) for task in tasks}
        unresolved = _unresolved(deps)
        ready: list[str] = []
        waiting: list[str] = []
        blocked: list[dict] = []
        for tid in sorted(state):
            if state[tid] not in DISPATCHABLE:
                continue
            missing = sorted(dep for dep in deps[tid] if dep not in state)
            dead = sorted(dep for dep in deps[tid] if state.get(dep) in DEAD)
            if tid in unresolved:
                cycle = sorted(dep for dep in deps[tid] if dep in unresolved)
                blocked.append({"id": tid, "reason": "cycle", "causes": cycle})
            elif missing:
                blocked.append({"id": tid, "reason": "missing_dependency", "causes": missing})
            elif dead:
                blocked.append({"id": tid, "reason": "dead_dependency", "causes": dead})
            elif all(state[dep] in SATISFIED for dep in deps[tid]):
                ready.append(tid)
            else:
                waiting.append(tid)
        return {
            "state": "SUCCESS",
            "ready": ready,
            "waiting": waiting,
            "blocked": blocked,
            "unresolved": sorted(unresolved),
        }

    return respond(payload, work)


def gate_coverage(payload: dict) -> dict:
    def work() -> dict:
        rules = payload["rules"]
        risks = [rule["risk"] for rule in rules]
        if len(set(risks)) != len(risks):
            return {"state": "INVALID_INPUT", "field": "rules", "message": "duplicate risk class"}
        matched = [rule for rule in rules if rule["risk"] == payload["risk"]]
        if not matched:
            return {"state": "INVALID_INPUT", "field": "risk", "message": f"no rule for risk {payload['risk']!r}"}
        required = list(dict.fromkeys(matched[0]["gates"]))
        last: dict[str, str] = {}
        for row in payload["results"]:
            last[row["gate"]] = row["verdict"]  # results are chronological; the latest verdict per gate counts
        return {
            "state": "SUCCESS",
            "covered": all(last.get(gate) in ("pass", "waive") for gate in required),
            "passed": [gate for gate in required if last.get(gate) == "pass"],
            "waived": [gate for gate in required if last.get(gate) == "waive"],
            "failed": [gate for gate in required if last.get(gate) == "fail"],
            "missing": [gate for gate in required if gate not in last],
            "extra": sorted(gate for gate in last if gate not in required),
        }

    return respond(payload, work)


def rework_bound(payload: dict) -> dict:
    def work() -> dict:
        cap = payload.get("max_loops", DEFAULT_MAX_LOOPS)
        loops = payload["rework_loops"]
        if payload["blocking_findings"] == 0:
            decision, after = "PASS", loops
        elif loops < cap:
            decision, after = "REWORK", loops + 1
        else:
            decision, after = "ESCALATED", loops
        return {
            "state": "SUCCESS",
            "decision": decision,
            "rework_loops": after,
            "loops_remaining": max(0, cap - after),
        }

    return respond(payload, work)
