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
sender's own token from `workspace.credential()`: its env file for the workspace (the runner's `--repo`), else its
`SUBSTRATE_TOKEN_<SURFACE>` ([substrate-workspace.md](substrate-workspace.md)). `coord_handoff` has no argument that
names a sender: the substrate records whoever the token is, signs that, and reads the sender's chain tip
(`prev_event_hash`) from the ledger itself. So A05 cannot sign as A14, and a packet re-labelled after signing fails
verification (`bad-signature`). The runner's own `coord_handoff_list` read goes the same way, as A01; `reconstruct`
reads as the receiving agent.

`coord_handoff` names no surface, so the server cannot refuse a packet sent on the wrong token the way it refuses a
claim. The runner therefore checks first, with `substrate_lease.has_own_token()`, which asks the same
`workspace.credential()` lookup. A sender with no token of its own sends nothing: the runner's `SUBSTRATE_TOKEN` (A01's)
never stands in, because that would record and sign A01 as the sender. It is recorded as `handoff.refused` instead. If
the ledger records a packet under another surface than the intended sender (a token configured under the wrong name),
the runner says so with `handoff.misattributed`.

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
| `notes` | `boundary: <key>`, then `boundary-at: <Unix seconds>`, a blank line and the upstream summary | same protected header, then the instruction | same protected header, then attempt and rework counts |
| `lease_id`, `release_lease` | never | never | the producer's lease, when held |

`lease_id` is a request field. The server releases with it only after the packet is stored, and records only
`lease_handover: true`, never the id. A receiver calls `graph_claim` rather than reading the node's state off a packet.
When the release lands, the lease bridge stops beating the lease and mirrors it as `released` / `handover`. The
transition, handoff attempt and settlement hold the keeper's per-task lock: a beat cannot release ESCALATED work while
its packet is in flight. If storage or handover fails, `settle` still attempts the normal fenced release (an unavailable
substrate leaves the lease to lapse). This is the intentional fail-open fallback, not a stored-packet guarantee on
failure. FAILED normally releases before max-attempt escalation; that later packet does not recreate a hold.

The runner fits the complete request, including graph/node/session identifiers and an envelope reserve, within 9 kB
(goal at most 500 characters; at most 40 files, 24 DoD lines and 16 blockers of 300 characters each). It trims files,
then blockers, then DoD; if necessary, prose and goal are shortened too, with a trim notice in `notes`. Notes respect
the server's 1200 UTF-16-unit cap without truncating the boundary key or logical-time header. Routing fields that cannot fit even without
optional content are refused visibly. The server's 12 kB fitting would otherwise drop `notes` and the idempotency key.

## Idempotency

The key is `<round>/<part>`: `dispatch@T/dep:D`, `rework<n>@T/gate:<gate>`, `escalated-a<attempt>r<loops>@T/<sender>`.
The round names one boundary event on one node, and the part one packet of it. A round or part over 256 UTF-8 bytes is
represented by `sha256-<digest>`; ordinary keys stay unchanged, and packets in one round still group together.
Before its first send, a runner reads the graph's packets with `coord_handoff_list`. Only verified entries or explicit
`ok: false` unsigned/no-keys-configured verdicts with well-formed graph/node/sender/key fields contribute to dedupe.
It skips a boundary already held **from that sender's surface on that node**, and keeps the set current as it writes.
A re-run, retry or restarted runner therefore writes no second normal boundary. Rejected, tampered or malformed rows,
and another agent's packet quoting a key, cannot suppress the real one. Unsigned mode remains explicitly unauthenticated.

After an uncertain write, the next send refreshes the ledger keys before retrying. A stored packet whose reply was
lost is skipped when that read finds it; an unanswered list defers the write. The refresh retains locally confirmed
packets and in-flight reservations, and a snapshot crossed by another uncertain write is not used to authorize a retry.

Two runner processes reconciling one Task Store cannot both write a gate or escalation packet: the transition behind it
is a compare-and-swap that only one of them wins. A dependency packet is written by the replica that holds the
dispatched task's lease.

## Fail-open

| Outcome | When | Recorded | Then |
|---|---|---|---|
| `sent` | stored | the server's `handoff` event (and `handoff.unsigned` once, see below) | |
| `duplicate` | the ledger already holds the key from this sender | nothing | |
| `unsent` | no answer, a server error, `stored: false`, the list did not answer, or no Graph ID | `handoff.unsent` (`warning`), once per key | retried by `flush()` at the start of every round and after the last one; a retry never hands a lease over |
| `refused` | the server refused the call, the sender has no token of its own, or immutable routing fields exceed the packet budget | `handoff.refused` (`warning`) | not retried: it is configuration/input, not an outage |

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
| `goal`, `dod`, `sender`, `lease_handover` | the usable packet with the newest signed logical boundary time, not the latest retry arrival |
| `blockers` | every usable packet of the newest round, e.g. all gates that failed in one review |
| `files` | every usable packet |
| `signed` | every packet of the newest round verified |
| `events` | the node's recent ledger events, newest first: claims, takeovers, releases, handoffs, teed run-log warnings |
| `status` | `ok`, or the list's outcome (`unreachable`, `refused`, `error`, `off`) |

Trust comes from the verdict. `verified` (`ok: true`) is used. `unsigned` (`unsigned`, `no-keys-configured`, which the
server calls unauthenticated rather than forged) is used and flagged. `rejected` (`bad-signature`, `unknown-key`,
`unknown-alg`, `malformed`) is listed and never shapes the context.

Routing objects, graph/node identity, version, required strings and string lists must also be well formed. A malformed
row is listed as rejected and cannot throw away healthy handoffs or shape the context even if it claims a good verdict.

`boundary-at` anchors initial dependency context to the receiving task's immutable `created_at`, and gate/escalation
context to its transition's `updated_at`. Regenerating a dependency packet on redispatch therefore cannot make it newer
than rework instructions. Queued retries retain that anchor; the timestamp comes from the existing Task Store, not a
new store or a distributed causal clock. Previously persisted packets without the header use their signed packet time.
The packet list remains in ledger storage order for the audit trail; only current-boundary selection uses this anchor.

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
