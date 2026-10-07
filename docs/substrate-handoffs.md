# Signed handoffs at accountable-agent boundaries (ADR 0001, phase S2)

This document covers the handoff half of S2 in
[ADR 0001](https://github.com/swcstudiospace/agent-substrate/blob/main/docs/adr/0001-agent-swarm-as-sdlc-mesh-on-substrate.md)
(Linear SPE-5053, criteria 7-9, requirements HAND-01..03). When work crosses from one accountable agent to another, the
sender writes down what the receiver needs as a `coord_handoff` packet. The substrate signs the packet as the sender
and appends it to the ledger, so the receiver can rebuild its task's context from the ledger alone.

Code: `swarm/substrate_handoff.py` (`HandoffBridge`, `reconstruct`, `render`), `scripts/swarm_run.py`
(`handoff_bridge`, `dispatchable`, `_execute_one`, `assignment_prompt(handoffs=)`), `swarm/results.py`
(`reconcile(..., handoffs=)`). The server side is agent-substrate's `coord_handoff`, `coord_handoff_list` and
`coord_handoff_verify` (`docs/coordination.md` there). Tests: `tests/test_substrate_handoff.py`.

## When it is on

Exactly when the run's leases are ([substrate-leases.md](substrate-leases.md)): `SUBSTRATE_URL` set, a real `swarm_run`,
never `--dry-run`. With the integration off nothing is sent and every prompt is unchanged. Signing is the server's:
with `SUBSTRATE_HANDOFF_KEY` set on the substrate the packets are HMAC-signed, and without it they are recorded
unsigned (see *Unsigned*).

## The boundaries

A boundary is a point where the agent accountable for a node changes. These are derived from the interaction matrix
(`04-integration-plan.md` §1, §3) and found in code the runner already runs:

| Boundary | Where | Sender → receiver | `node_id` | Lease |
|---|---|---|---|---|
| **Dependency.** A task is dispatched, and a `depends_on` task belongs to another agent (A03 → A05 for `api.contract`, A05 → A08/A09/A10 for code, A14 → A05 for a patch order). | `_execute_one`, before the session starts | the upstream task's agent → the dispatched task's agent | the dispatched task | kept: the sender's lease is on its own node, held through review until DONE (LEASE-07) |
| **Gate.** A gate fails a task and it goes back for rework (CHANGES_REQUESTED → IN_PROGRESS). | `reconcile` | the agent that issued the failing verdict (A08/A09/A10/A12) → the producer | the failed task | none: the gate agent holds no lease on it; the producer's rework releases and re-claims (Phase 12) |
| **Escalation.** The swarm gives up on a task: the rework cap (`reconcile`) or `max_attempts` (`dispatchable`). | `reconcile`, `dispatchable` | the producer → **A14** when a direct upstream task is A14's (hotfix `rca` → `patch`, dependency `patch` → `bump`), else **A01** | the escalated task | handed over when still held: at the rework cap the producer holds the node through review, so the packet carries `release_lease` |

One packet per boundary: per dependency edge, per failing gate, per escalation. Same-agent edges are not boundaries.

Not boundaries, and why:

- APPROVED → DONE. Gate verdicts go to A01, but the node's work is finished; nothing is handed on.
- A gate stall's `escalation.request`. No transition happens, and the producer keeps the node in review.
- BLOCKED. The agent itself resumes once A01 releases it (BLOCKED → PLANNED/CLAIMED, same agent).
- `orch_status --transition` and `--ingest`. The in-session path writes no packets in this phase, as it holds no
  leases.

A14 is the escalation owner for a task that carries out an A14 work order because A14 writes `patch.task` and
`hotfix.task` (the matrix's single writer), and its decision rules own a patch whose gates will not pass ("bump breaks
gates" in `03-agents/A14-maintenance.md`). `escalation.request` to A01 is still emitted as before. The packet is
additive, and the Task Store keeps every row (criterion 10).

## Identity

Every packet is sent **as its sender**, through `substrate_lease._call()`, the seam the lease calls use, with the
sender's own `SUBSTRATE_TOKEN_<SURFACE>`. `coord_handoff` has no argument that names a sender: the substrate records
whoever the token is, signs that, and reads the sender's chain tip (`prev_event_hash`) from the ledger itself. So A05
cannot sign as A14, and a packet re-labelled after signing fails verification (`bad-signature`).

`coord_handoff` names no surface, so the server cannot refuse a packet sent on the wrong token the way it refuses a
claim. The runner therefore checks first, with `substrate_lease.has_own_token()`. A sender with no token of its own is
not sent on the fallback `SUBSTRATE_TOKEN`, the runner's (A01's), because that would record and sign A01 as the sender.
It is recorded as `handoff.refused` instead. If the ledger records a packet under another surface than the intended
sender (a token configured under the wrong name), the runner says so with `handoff.misattributed`.

Sessions: `session_id` is the sender's `<AGENT>@<replica>:<graph_id>`, and for a dependency or gate packet
`to_session_id` is the receiver's, since this runner dispatches it.

## Fields

| Field | Dependency | Gate | Escalation |
|---|---|---|---|
| `goal` | the task's title and capability, and what it builds on | `Rework <title>: the <gate> gate failed (rework n of 2)` | `Take over <title>: <reason>` |
| `dod` | the receiving task's `acceptance[]`, one line each (an object criterion as compact JSON) | the failed task's `acceptance[]` | the escalated task's `acceptance[]` |
| `files` | the upstream task's `outputs[].uri` | the task's `outputs[].uri`, plus each finding's `file`/`path` | the same |
| `blockers` | `[]`, which claims that nothing is known to be in the way | one line per finding: `<gate> [<severity>] <summary> (<file>:<line>)` | the failing findings, and the reason |
| `graph_id`, `node_id` | the run's Graph ID; the node the receiver works next | | |
| `to`, `to_session_id` | the receiver's surface and session | | surface only |
| `notes` | first line `boundary: <key>`, then the upstream's `summary_md` | first line, then the instruction | first line, then attempt and rework counts |
| `lease_id`, `release_lease` | never | never | the producer's lease, when held |

`lease_id` is a request field. The server releases with it only after the packet is stored, and records only
`lease_handover: true`, never the id. A receiver calls `graph_claim` rather than reading the node's state off a packet.
When the release lands, the lease bridge stops beating the lease and mirrors it as `released` / `handover`. When it
does not land, `settle` releases as before.

The runner keeps each packet under 9 kB (goal at most 500 characters; at most 40 files, 24 DoD lines and 16 blockers of
300 characters each). Over that, it trims files first, then blockers, then DoD, in the server's own order, and says so
in `notes`. The server's 12 kB fitting would drop `notes` first, and with it the idempotency key.

## Idempotency

The key is `<round>/<part>`: `dispatch@T/dep:D`, `rework<n>@T/gate:<gate>`, `escalated-a<attempt>r<loops>@T/<sender>`.
The round names one boundary event on one node, and the part one packet of it. Before its first send, a runner process
reads the graph's packets once with `coord_handoff_list`. It skips a boundary whose key the ledger already holds **from
that sender's surface**, and keeps the set current as it writes. A re-run, a retried or reworked dispatch, or a runner
restarted after a crash therefore writes nothing twice. Another agent's packet that quotes the key cannot suppress the
real one.

Two runner processes reconciling one Task Store cannot both write a gate or escalation packet: the transition behind it
is a compare-and-swap that only one of them wins. A dependency packet is written by the replica that holds the
dispatched task's lease.

## Fail-open

| Outcome | When | Recorded | Then |
|---|---|---|---|
| `sent` | stored | the server's `handoff` event (and `handoff.unsigned` once, see below) | |
| `duplicate` | the ledger already holds the key from this sender | nothing | |
| `unsent` | no answer, a server error, `stored: false`, the list did not answer, or no Graph ID | `handoff.unsent` (`warning`), once per key | retried by `flush()` at the start of every round and after the last one; a retry never hands a lease over |
| `refused` | the server refused the call, or the sender has no token of its own | `handoff.refused` (`warning`) | not retried: it is configuration, not an outage |

A handoff never changes a transition or stops a dispatch. Every bridge method catches its own failures.

## Unsigned (HAND-03)

With `SUBSTRATE_HANDOFF_KEY` unset on the substrate, `coord_handoff` stores the packet with `signature: null` and
answers `signed: false`. The runner records `handoff.unsigned` once per run. `coord_handoff_list` gives each such packet
the verdict `unsigned`. The receiver uses it, and the prompt section labels it *UNSIGNED … unauthenticated*. The run
completes as it would have.

## The receiving side (HAND-02)

`reconstruct(graph_id, node_id, agent=)` rebuilds a task's context from two reads made with the receiving agent's
token: `coord_handoff_list {graph_id}` and `events_query {graph_id, node_id, order: desc, limit: 50}`. It reads no Task
Store, no run log (`events.jsonl`, the local stand-in for the bus), no Graph ID cache and no file. It takes the Graph ID
as an argument, so it never looks one up. The test proves this by deleting the state directory, failing any Task Store
construction, and watching `open` / `sqlite3.connect` with an audit hook.

| `Context` field | From |
|---|---|
| `packets` | every packet for the node, oldest first, each with `from`, `to`, `boundary`, the server's `verdict` and a `trust` |
| `goal`, `dod`, `sender`, `lease_handover` | the newest usable packet |
| `blockers` | every usable packet of the newest round, e.g. all gates that failed in one review |
| `files` | every usable packet |
| `signed` | every packet of the newest round verified |
| `events` | the node's recent ledger events, newest first: claims, takeovers, releases, handoffs, teed run-log warnings |
| `status` | `ok`, or the list's outcome (`unreachable`, `refused`, `error`, `off`) |

Trust comes from the verdict. `verified` (`ok: true`) is used. `unsigned` (`unsigned`, `no-keys-configured`, which the
server calls unauthenticated rather than forged) is used and flagged. `rejected` (`bad-signature`, `unknown-key`,
`unknown-alg`, `malformed`) is listed and never shapes the context.

The runner uses it: each assignment carries a `## Handoffs (from the substrate ledger)` section, rendered by
`render()`, after the Task Store's upstream section, whenever the node has packets. The dependency packets are written
before the prompt is built, so a session starts from what its senders wrote. The Task Store sections stay: the bridge
is additive. An agent outside the runner, such as A14 picking up an escalated patch, calls `reconstruct` itself, or the
same two MCP tools.

## Run-log types

| Swarm type | Kind | Meaning |
|---|---|---|
| `handoff.unsent` | `warning` | The substrate did not take a packet; retried next round. |
| `handoff.refused` | `warning` | Refused, or the sender has no token of its own; not written. |
| `handoff.unsigned` | `warning` | No handoff key on the substrate; packets are recorded unsigned (once a run). |
| `handoff.misattributed` | `warning` | The ledger recorded a packet under another surface than its sender's. |

A packet that lands is not re-emitted: the substrate writes its own `handoff` event carrying the whole packet.

## Limits

- The runner writes every agent's packets from one process with every agent's token, as it does for leases. That is
  attributable, but not unforgeable among agents that share an account. Phase 14 gives each agent process its own token,
  and the runner still writes the packets on the agent's behalf.
- `prev_event_hash` anchors a packet to the sender's session chain. A sender that has emitted nothing under that session
  yet gets a packet with no anchor, which the server names in `gaps`.
- Retries end with the run. A packet still unsent when the run stops is recorded by its `handoff.unsent` warning, and
  the next run writes it if the boundary recurs, which a dispatch does and a past transition does not.
