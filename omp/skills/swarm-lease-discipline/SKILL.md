---
name: "swarm-lease-discipline"
description: "Use when you edit files or artifacts a task owns; check the lease mirror before long writes, stop when it is lost, and never touch lease ids or force."
disable-model-invocation: true
---

# Lease discipline

## When to Use

- At task start, before each long or multi-file write, and before the final result.
- Whenever a tool or log mentions a lease, claim, holder or a node taken by another session.

## Procedure

1. Call `shared_lease_state` with `{task_id}` at start. Read `lease.state`: `held` (you may work), `none` or `unleased` (lease integration off or fail-open: work, but do not depend on exclusivity), `denied`, `refused`, `lost`, `released`, `completed` or `unsettled` (do not write).
2. Before each long write batch, call `shared_lease_state` again. `lease.expired: true` with state `held` means the keeper missed its beat: finish the file in progress, then recheck before the next one.
3. If the state is `denied`, `refused` or `lost`, stop writing at once. Call `shared_escalate` with `reason_code: conflict`, a summary naming `lease.state`, `lease.holder` and `lease.reason`, and `needs: ["lease holder released the node"]`. Make no further edits.
4. If the state is `released` or `completed` while you still work, the task moved on (for example cancelled or escalated): call `shared_cancel_check` with `{task_id}` and apply `swarm-cooperative-cancel`.
5. Never claim, renew, release or force a lease, and never call substrate graph tools to change one. The runner holds and heartbeats leases on your behalf; only the lease mirror is yours to read.
6. Never copy a lease id into outputs, handovers, `shared_scratchpad_put` values or `shared_log_emit` logs. The tools redact it, so a redacted `lease_id` is expected; do not try to recover it.
7. Record each lease surprise with `shared_log_emit` (level warn, fields `task_id`, `lease_state`, `holder`). Log only names and states, never ids or tokens.

## Outputs

- A lease state you acted on, or a BLOCKED result when the node is not yours.

## Failure Handling

- `task_known: false`: no mirror exists; treat it as `unleased` and log it.
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry once; if still unavailable, continue as `unleased` and log a warning rather than guessing the node is free.
- Two agents editing one file is a conflict even without a lease: escalate it.

## Handover

- Name any file you edited under an unclear lease state in the handover's `blockers` or `constraints`, so the receiver verifies it.

<!-- generated from prompts/skills/shared/lease-discipline.md; edit the source and run scripts/build_agents.py -->
