# Swarm tasks under substrate leases (ADR 0001, phase S2)

This document covers the lease half of S2 in
[ADR 0001](https://github.com/swcstudiospace/agent-substrate/blob/main/docs/adr/0001-agent-swarm-as-sdlc-mesh-on-substrate.md)
(Linear SPE-5053, requirements LEASE-05..09). Every swarm task is worked under a substrate lease on its Graph-of-Thought
node. The runner holds that lease for the agent it dispatches the task to. The result: two replicas never work one node,
and a dead replica's node frees itself. The lease decides **who may touch the work**. The Task Store stays A01's record
of **lifecycle state**, keeps the DAG, budgets, bids and arbitration records, and loses nothing (LEASE-09).

Code: `swarm/substrate_lease.py` (the bridge), `scripts/swarm_run.py` (`select_batch`, `execute_one`,
`run_agent_headless(on_session=)`, `lease_bridge`), `swarm/results.py` (`reconcile(..., leases=)`), `scripts/orch_status.py`
(`--transition`). Transport, timeouts and back-off are the ones in [substrate-tee.md](substrate-tee.md). The server side
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

All four lease calls go through one function, `substrate_lease._call()`. Today it lets `substrate_client` pick
`SUBSTRATE_TOKEN_<SURFACE>` from the runner's environment. Phase 14 (INST-04) changes only that function, to read the
token from the agent's own env file. If the agent's token is missing, the client falls back to `SUBSTRATE_TOKEN`, which
is the runner's (A01's) token. The server refuses that claim, the refusal is recorded, and the task is not dispatched.
A deployment that never delivered an agent's token cannot quietly run that agent unleased.

`force` is never sent. Taking a node off another holder is an operator action, and no swarm token is an operator.

## Lifecycle

| Swarm event | Substrate call | Notes |
|---|---|---|
| Task picked for a round (`select_batch`), before `CLAIMED` | `graph_claim {graph_id, node_id: task_id, session_id, ttl_seconds, surface}` | `lease_s` in `task.assign` is the granted TTL: `decision.ttl_seconds`, else the requested TTL. |
| Session running, and the task waiting in review | `graph_heartbeat` every TTL/3 | One keeper thread per run beats every held lease, not only the leases of running sessions. |
| `DONE` (`reconcile`, after APPROVED) | `graph_complete`, fenced on the held `lease_id` | A process with no lease in memory claims first as the producer (`renewed` if its lease is live), then completes. |
| `CHANGES_REQUESTED` → rework (`reconcile`) | `graph_release`, then `graph_claim` | The trail reads `released` then `claim`, so the rework loop is visible. The rework dispatch reuses the new lease. |
| `FAILED`, `BLOCKED`, `ESCALATED` (`execute_one`'s `finally`, `reconcile`) | `graph_release`, fenced | |
| `CANCELLED` or any manual transition (`orch_status --transition`) | `graph_release` (or `graph_complete` for DONE) with the `lease_id` mirrored in `notes.lease` | A live runner's keeper also settles a task that left the holding states, on its next beat. |

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
| `unleased` | no answer (network, deadline, back-off), any other server error (no Postgres, a DB outage), or no Graph ID bound to the run | yes, fail-open | `notes.lease` `unleased` with the reason; `lease.unleased` (`warning`) |
| `off` | integration off | yes, as before | nothing |

A task whose claim is denied or refused stays where it was (PLANNED/RETRY, or IN_PROGRESS for a rework) and takes no
parallel slot. Its attempt counter does not move. When a round has candidates but every one of them is waiting on a
lease, the run ends with `waiting on leases: […]` in its log instead of spinning through `--max-rounds`. The next round
or the next run asks again.

`substrate_client.mcp_call_outcome` makes the "no" versus "no answer" distinction. `mcp_call` / `mcp_call_json`
still fold everything into `None`, unchanged for their callers.

## Heartbeats and refusals

The interval is TTL/3 of the clamped TTL, so two beats can be missed before the lease lapses, and beats are never more
often than every 10 s. After a beat that gets no answer, the next one comes after `min(TTL/3, 30 s)`, the client's
outage back-off, so one blip cannot run the TTL out.

While a session runs, a refused heartbeat applies this table (`substrate_lease.SESSION_REFUSALS`):

| Refusal | Action | Task |
|---|---|---|
| `expired` | Re-claim as the same session. The lease lapsed but nobody took the node, so it comes back under a new id (`reclaimed` in the trail), and the session carries on. | unchanged |
| `expired`, and the re-claim is denied or refused | stop the session | FAILED `E-TIMEOUT: lease expired and the re-claim was not granted` |
| `lost` (another session holds it) | stop the session | FAILED `E-TIMEOUT: lease lost: …` |
| `unheld` (released or force-released under it) | stop the session | FAILED `E-TIMEOUT: lease unheld: …` |
| `not-holder` (the runner's token is not the holder's surface) | stop the session | FAILED `E-POLICY: lease not-holder: …` |
| any other `ok: false` reason | stop the session | FAILED `E-TIMEOUT: lease refused (<reason>)` |
| no answer, or a server error | keep working; retry sooner | unchanged |

Stopping a session SIGTERMs its process group and SIGKILLs it 10 s later. The session's partial output is kept in
`results/`, and its result is **never applied**: `LeaseStopped` carries the table's reason to FAILED. FAILED rather than
RETRY, because FAILED runs the existing ladder: RETRY behind a fresh claim, bounded by `max_attempts`, then ESCALATED.
A lease that keeps slipping therefore escalates rather than looping. A lease lost before the session registered stops
the session the moment it registers.

Between rounds no session is running, so nothing is at risk. `expired` and `unheld` re-claim if the task still needs the
node, and every other refusal drops the lease with a `lease.lost` warning. The task's state is left to A01. If the
gates then pass, DONE stands and the completion's claim is denied, which is recorded as `lease.unsettled`.

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
run-log event.

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
  within one TTL, and the same replica's next run re-claims them as the same holder.
- The in-session path (`orch_status --ingest`) does not lease in this phase, and `coord_handoff` packets at
  accountable-agent boundaries are Phase 13.
