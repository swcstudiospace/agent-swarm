# Swarm tasks under substrate leases (ADR 0001, phase S2)

This document covers the lease half of S2 in
[ADR 0001](https://github.com/swcstudiospace/agent-substrate/blob/main/docs/adr/0001-agent-swarm-as-sdlc-mesh-on-substrate.md)
(Linear SPE-5053, requirements LEASE-05..09). Every swarm task is worked under a substrate lease on its Graph-of-Thought
node. The runner holds that lease for the agent it dispatches the task to. The result: two replicas never work one node,
and a dead replica's node frees itself. The lease decides **who may touch the work**. The Task Store stays A01's record
of **lifecycle state**, keeps the DAG, budgets, bids and arbitration records, and loses nothing (LEASE-09).

Code: `swarm/substrate_lease.py` (the bridge), `scripts/swarm_run.py` (`select_batch`, `execute_one`,
`run_agent_headless(on_session=)`, `run_gate_script(on_process=)`, `DispatchWatch`, `ChildGroup`, `lease_bridge`),
`swarm/results.py` (`reconcile(..., leases=)`), `scripts/orch_status.py` (`--transition`), `swarm/runlog.py`
(`redact_leases`). Transport, timeouts and back-off are the ones in [substrate-tee.md](substrate-tee.md). The server side
(session shape, holder-only `lease_id`, `not-holder`, no swarm operator) is agent-substrate's Phase 11 and
`docs/coordination.md` there. Tests: `tests/test_substrate_lease.py`.

## When it is on

The bridge is on when `SUBSTRATE_URL` is set (and `SUBSTRATE_DISABLED` is not `1`), and only for a real `swarm_run`.
`--dry-run` never touches the substrate. With the integration off nothing is sent or recorded, and the run is
byte-for-byte what it was. The in-session path (`orch_status --ingest`, Agent-tool delegation) does not lease in this
phase.

| Variable | Meaning |
|---|---|
| `SWARM_REPLICA` | This runner's replica name, default `r0`, 1-64 characters of `[A-Za-z0-9._-]`. Anything else stops the run at start with `E-INPUT`: the substrate would refuse every claim anyway (LEASE-01). |
| `SWARM_LEASE_TTL_S` | TTL to request, default 900 (the substrate's default), clamped to [30, 86400]. A value that is not a whole number stops the run with `E-INPUT`. |
| `SUBSTRATE_TOKEN_<SURFACE>` | The token each lease call carries: the executing agent's own, e.g. `SUBSTRATE_TOKEN_SWARM_A05_BE`. See *Identity*. |

## Identity

A claim is made **as the executing agent**. The call carries `surface: swarm-aNN-xxx` and that agent's token, and the
session is `<AGENT>@<replica>:<graph_id>`, the same `substrate_tee.session_id()` the event tee uses. Two replicas of
A05 are therefore two holders (`A05@r1:…`, `A05@r2:…`), and the session is derived from the replica, never chosen per
call. A runner restarted under the same replica is the same holder: its claim on its own still-live lease is `renewed`,
and the lease keeps its id.

All four lease calls, and the handoff packets of [substrate-handoffs.md](substrate-handoffs.md), go through one function,
`substrate_lease._call()`. Today it lets `substrate_client` pick
`SUBSTRATE_TOKEN_<SURFACE>` from the runner's environment. Phase 14 (INST-04) changes only that function and its sibling
`has_own_token()`, to read the
token from the agent's own env file. If the agent's token is missing, the client falls back to `SUBSTRATE_TOKEN`, which
is the runner's (A01's) token. The server refuses that claim, the refusal is recorded, and the task is not dispatched.
A deployment that never delivered an agent's token cannot quietly run that agent unleased.

`force` is never sent. Taking a node off another holder is an operator action, and no swarm token is an operator.

## Lifecycle

| Swarm event | Substrate call | Notes |
|---|---|---|
| Run start (`run`, before the first reconcile) | `graph_claim` for every IN_REVIEW / APPROVED task of the run (`LeaseBridge.resume`) | Work an earlier runner process left in review (`--once`, `--max-rounds`, a restart) is held again, so the keeper beats it through the gates, DONE completes it and a rework releases it. The same replica's claim on its own live lease is `renewed` and keeps the id. |
| Task picked for a round (`select_batch`), before `CLAIMED` | `graph_claim {graph_id, node_id: task_id, session_id, ttl_seconds, surface}` | `lease_s` in `task.assign` is the granted TTL: `decision.ttl_seconds`, else the requested TTL. |
| Session running, gate script running, and the task waiting in review | `graph_heartbeat` every TTL/3 | One keeper thread per run beats every held lease, not only the leases of running sessions. |
| `DONE` (`reconcile`, after APPROVED) | `graph_complete`, fenced on the held `lease_id` | A process with no lease in memory claims first as the producer (`renewed` if its lease is live), then completes. |
| `CHANGES_REQUESTED` → rework (`reconcile`) | `graph_release`, then `graph_claim` | The trail reads `released` then `claim`, so the rework loop is visible. The rework dispatch reuses the new lease. |
| `FAILED`, `BLOCKED`, `ESCALATED` (`execute_one`'s `finally`, `reconcile`) | `graph_release`, fenced | `execute_one` settles only after the session's and the gate script's process groups are gone. An `ESCALATED` producer still holding the node (the rework cap is reached in review) hands the lease over inside its `coord_handoff` packet instead (`release_lease`), and the mirror reads `released` with action `handover`. |
| `CANCELLED` or any manual transition (`orch_status --transition`) | `graph_release` (or `graph_complete` for DONE) with the `lease_id` mirrored in `notes.lease` | Not while a runner still works the task (`notes.running` set): see *A task that moves on elsewhere*. A live runner's keeper also settles a task that left the holding states, on its next beat. |

The producer's lease is held **through review**: it is not released at IN_REVIEW. LEASE-07 has DONE go through
`graph_complete` fenced on `lease_id`, and has CHANGES_REQUESTED *release* and re-claim. Both assume a lease that is still
held when review ends. Holding it also keeps the node reading as the producer's while the producer is the only agent
that may touch the artifact next.

Gate reruns (`<base>.rN`) are created after `orch_plan` registered the node set. The first claim on one comes back
`unknown node`. The bridge then registers the node as `swarm-a01-orch` (`graph_register {graph_id, nodes: [{node_id}]}`,
which upserts the node and leaves edges alone) and claims once more.

## Outcomes of a claim

| Outcome | When | Dispatched | Recorded |
|---|---|---|---|
| `held` | `claimed: true` with a `lease_id` | yes, leased | `notes.lease` `held` |
| `denied` | another session holds a live lease | **no** | `notes.lease` `denied` with the holder and expiry; run-log `lease.denied` (`note`) |
| `refused` | the server said no to this caller: HTTP 401/403, or an `isError` reply `{"error": …}` (wrong token for the surface, malformed session) | **no** | `notes.lease` `refused`; `lease.refused` (`warning`) |
| `busy` | the call deadline expires waiting for a local request slot (including an uncached Graph ID lookup) | **no** | task and lease mirror unchanged; runner reports `waiting on leases` |
| `unleased` | no answer (network, deadline, back-off), any other server error (no Postgres, a DB outage), or no Graph ID bound to the run | yes, fail-open | `notes.lease` `unleased` with the reason; `lease.unleased` (`warning`) |
| `off` | integration off | yes, as before | nothing |

A task whose claim is denied, refused or busy stays where it was (PLANNED/RETRY, or IN_PROGRESS for a rework) and takes no
parallel slot. Its attempt counter does not move. When a round has candidates but every one of them is waiting on a
lease, the run ends with `waiting on leases: […]` in its log instead of spinning through `--max-rounds`. The next round
or the next run asks again.

`substrate_client.mcp_call_outcome` makes the "no" versus "no answer" distinction. `mcp_call` / `mcp_call_json`
still fold everything into `None`, unchanged for their callers.

## Heartbeats and refusals

The interval is TTL/3 of the clamped TTL, so two beats can be missed before the lease lapses, and beats are never more
often than every 10 s. After a beat that gets no answer, the next one comes after `min(TTL/3, 30 s)`, the client's
outage back-off, so one blip cannot run the TTL out.

Held leases are not capped by `--max-parallel`: review leases outlive the round that produced them. The keeper therefore
schedules every lease on its own due time and beats up to `HEARTBEAT_WORKERS` of them at once, on a small thread pool.
That is `substrate_client.MAX_IN_FLIGHT - 1` (3): one request slot stays free for the run's claims, settlements and tee.
If other calls fill it too, requests wait for capacity within their own deadline; local contention never starts the
client's outage back-off. With 1 s replies and a 30 s TTL, three workers renew 30 leases within TTL/3, where one at a time
renewed 10.

Claims, beats, settlements and reworks of one task run one at a time (a per-task lock in the bridge). A worker that
settles a task while the keeper's re-claim of it is on the wire waits for that claim, then releases the lease it brought
back. A re-claim that can no longer be installed is released at once, so no grant is left held by nobody.

The keeper watches the whole dispatch, not only the agent session: `execute_one` attaches a `DispatchWatch` before the
session spawns and detaches it once the result is written. While a dispatch is watched, a refused heartbeat applies this
table (`substrate_lease.SESSION_REFUSALS`):

| Refusal | Action | Task |
|---|---|---|
| `expired` | Re-claim as the same session. The lease lapsed but nobody took the node, so it comes back under a new id (`reclaimed` in the trail), and the session carries on. | unchanged |
| `expired`, and the re-claim is denied or refused | stop the dispatch | FAILED `E-TIMEOUT: lease expired and the re-claim was not granted` |
| `expired`, and the re-claim is locally busy or unanswered | keep the dispatch and retry after `min(TTL/3, 30 s)`; no server refusal has arrived | unchanged |
| `lost` (another session holds it) | stop the dispatch | FAILED `E-TIMEOUT: lease lost: …` |
| `unheld` (released or force-released under it) | stop the dispatch | FAILED `E-TIMEOUT: lease unheld: …` |
| `not-holder` (the runner's token is not the holder's surface) | stop the dispatch | FAILED `E-POLICY: lease not-holder: …` |
| any other `ok: false` reason | stop the dispatch | FAILED `E-TIMEOUT: lease refused (<reason>)` |
| no answer, or a server error | keep working; retry sooner | unchanged |

Stopping a dispatch stops whichever child works the task at that moment: the agent session, or afterwards the runner's
gate script, which runs in its own process group too and is registered for the runner's signal teardown. Each is
SIGTERMed as a group and SIGKILLed `STOP_GRACE_S` (10 s) later. The runner forgets a stopped group only once no process
is left in it: a tool with its own pipes can outlive SIGTERM after its parent has exited, so the leader's exit proves
nothing, and whatever is still there at the grace is SIGKILLed first. The session's partial output is kept in
`results/`, and its result is **never applied**: `LeaseStopped` carries the table's reason to FAILED. A stop recorded
between the session's exit and the result write rejects the result as well, and a gate script stopped half-way leaves
its gate task FAILED, so the verdict rows it may have written never approve a target (T-05-11). The keeper's stop waits
while a result is being written; that result was written under a held lease. FAILED rather than RETRY, because FAILED
runs the existing ladder: RETRY behind a fresh claim, bounded by `max_attempts`, then ESCALATED. A lease that keeps
slipping therefore escalates rather than looping. A lease lost before the session spawned stops the dispatch before
it starts.

Runner shutdown marks every signaled child group stopped, including children spawned during teardown, so a gate script
cannot approve a result after shutdown even if it exits cleanly. Gate scripts terminated by a signal (negative return
code or a shell's `128 + signal` exit code) are rejected too: committed verdict rows cannot approve a target while a
release plan is missing or incomplete. Exit 1 remains the normal, accepted findings exit code.

Between rounds no session is running, so nothing is at risk. `expired` and `unheld` re-claim if the task still needs the
node, and every other refusal drops the lease with a `lease.lost` warning. The task's state is left to A01. If the
gates then pass, DONE stands and the completion's claim is denied, which is recorded as `lease.unsettled`.

## A task that moves on elsewhere

`orch_status --transition` (CANCELLED, or any manual move out of the holding states) runs in another process. When the
task has no running dispatch it releases (or, for DONE, completes) the mirrored lease at once. While a runner still works
the task (`notes.running` is set) it leaves the lease alone (`settle_mirrored` returns `deferred`): releasing then would
free the node while the agent is still editing, and another replica could claim it. On its next beat the runner's
keeper sees the new state, stops the dispatch (reason `the task moved on to <STATE> outside this runner`) and keeps
beating the lease. The dispatch records no transition of its own, since the other process's transition stands, and
`execute_one` releases the lease once the session's process group is gone. A runner that died with `notes.running` set
leaves the lease to lapse within one TTL.

## A dead replica

A killed runner stops beating, and that is its whole contribution. Its lease lapses one TTL after the last beat, and the
next claimant's `graph_claim` is granted (`stolen`, a `warning` in the trail), with no reaper on the path. Until then
every other claim is denied, so two replicas never hold the node at once. A runner that only stalled finds the node
gone at its next beat (`lost`) and stops its session.

## The mirror in the Task Store

`notes.lease` holds the latest lease state only: `state` (`held | denied | refused | unleased | released | completed |
lost | unsettled`), plus `lease_id`, `agent`, `surface`, `session_id`, `graph_id`, `ttl_s`, `action`, `reason`, `holder`
and `at` as they apply. It is advisory, the "advisory mirror of `lease_id`" the ADR names, and never a second authority.
It is what lets `orch_status --transition` release a lease it does not hold in memory. No `lease_id` goes into any
run-log event: `runlog.emit` drops every `lease_id` key from the payload it records and tees (`redact_leases`), including
the copy inside a task row's raw `notes` JSON. A script's exit record, such as `orch_status --transition`'s, which
carries the whole task, is covered by that; the CLI output and the Task Store still show the mirror.

## Run-log types

| Swarm type | Kind | Meaning |
|---|---|---|
| `lease.denied` | `note` | Another session holds the node; the task waits. |
| `lease.unleased` | `warning` | Dispatched without a lease (fail-open). |
| `lease.refused` | `warning` | The server refused this agent's claim; not dispatched. |
| `lease.lost` | `warning` | A refused heartbeat stopped a session (`session_stopped: true`) or dropped a held lease. |
| `lease.unsettled` | `warning` | A release or completion did not land, or DONE could not be completed because another session holds the node. |

The runner (A01) emits them, so the tee attributes them to `swarm-a01-orch`. Grants, re-claims, steals, releases and
completions are not re-emitted: the substrate writes its own `claim`, `warning` and `note` events for them.

## Limits

- The runner holds every agent's lease from one process with every agent's token. That is attributable but not
  unforgeable among agents sharing an account. Phase 14 delivers one token per agent process, but the runner keeps
  holding the child's lease with that child's token.
- A run that stops early (`--once`, `--max-rounds`, a signal) leaves leases on work that is still IN_REVIEW. They lapse
  within one TTL unless the same replica runs again first: its next run re-claims them as the same holder (`renewed`)
  before it reconciles (`LeaseBridge.resume`).
- The in-session path (`orch_status --ingest`) does not lease in this phase. `coord_handoff` packets at
  accountable-agent boundaries are in [substrate-handoffs.md](substrate-handoffs.md).
