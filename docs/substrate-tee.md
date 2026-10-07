# Swarm → Agent Substrate event tee (ADR 0001, phase S1)

Implements S1 criteria 3–6 and 8 of
[ADR 0001](https://github.com/swcstudiospace/agent-substrate/blob/main/docs/adr/0001-agent-swarm-as-sdlc-mesh-on-substrate.md)
(Linear SPE-5052): one `correlation_id` is one Graph ID, every run-log record is attributed to the agent that wrote it and
teed to substrate-mcp's `/events`, and none of it can change what the swarm does when substrate is absent.

Code: `swarm/substrate_client.py` (HTTP), `swarm/substrate_tee.py` (tables, Graph ID, cache, dedupe, tee),
`swarm/runlog.py` (the hook), `scripts/orch_plan.py` (binding + `graph_register`).
Tests: `tests/test_substrate_tee.py`.

## Environment

| Variable | Meaning |
|---|---|
| `SUBSTRATE_URL` | Base URL of substrate-mcp. Empty/unset (or not `http(s)://`) = the whole integration is off; no socket is opened, no sqlite file is created. |
| `SUBSTRATE_DISABLED=1` | Force off even when `SUBSTRATE_URL` is set. |
| `SUBSTRATE_TOKEN` | This process's single bearer token (sent as `Authorization: Bearer …`). The server maps it to a surface via `SUBSTRATE_TOKEN_<SURFACE, '-'→'_', upper>`, e.g. `SUBSTRATE_TOKEN_SWARM_A05_BE`. It is only ever placed in that header; it is never logged, printed or put in an event. |
| `SUBSTRATE_GRAPH_ID` | Graph ID to offer for a new run (see *Graph ID*). |
| `SWARM_REPLICA` | Replica name in the session id; default `r0`. |

Timeout is 1.5 s per request. A network-level failure additionally pauses all substrate calls of that process for 30 s, so
a dead host costs one timeout, not one per emitted record.

## Identity

`surface` is the emitting agent's surface; the server refuses (REST 403 / MCP `isError`) a body whose `surface` differs from
the token's, it never re-attributes. The tee drops such an event (and releases its dedupe claim).

| Agent | Surface | Agent | Surface |
|---|---|---|---|
| A01 | `swarm-a01-orch` | A09 | `swarm-a09-rev` |
| A02 | `swarm-a02-req` | A10 | `swarm-a10-sec` |
| A03 | `swarm-a03-arch` | A11 | `swarm-a11-devops` |
| A04 | `swarm-a04-uxd` | A12 | `swarm-a12-rel` |
| A05 | `swarm-a05-be` | A13 | `swarm-a13-obs` |
| A06 | `swarm-a06-fe` | A14 | `swarm-a14-maint` |
| A07 | `swarm-a07-data` | A15 | `swarm-a15-doc` |
| A08 | `swarm-a08-qa` | | |

`AGENT_SURFACES` in `substrate_tee.py` is this table; a test checks it against `agents.json` (`swarm-<id>-<code>`, lower case).

**Which agent emitted a record.** `Ctx.emit` already stamps `source = "<agent id>@<script>"` (the id the script was constructed
with, e.g. `AgentScript("A05", "code_checks", …)`), so no change to `script_base.py` was needed: the agent id is the
`A\d\d` prefix of `source`. The one non-agent source with an identity is `swarm.envelope` (`security.dev_key`), whose
payload names the signing agent in `payload.source`. A record that names no known agent is not tee'd. Events a runner
writes on behalf of others (`task.result.raw`, `escalation.request` from `swarm_run`) are attributed to the runner, A01.

**Session.** `session_id = <AGENT_ID>@<replica>:<graph_id>`, e.g. `A05@r0:ut-mabc123-0123abcd`: one (agent, replica,
Graph ID) triple. Substrate chains events per session and its `prev` lookup ignores the graph, so a session shared across
graphs would interleave chains; including the Graph ID prevents a false "broken chain" in `events_verify`.

**Event body** (`POST {SUBSTRATE_URL}/events`): `kind`, `summary` (`<swarm type> <task_id or correlation_id>`), `surface`,
`session_id`, `graph_id`, `node_id` (= `task_id`, omitted when the record has none), `actor: "agent"`, `repo` (the `owner/name`
slug parsed from `git remote get-url origin`, the same value every other surface and the substrate CLI use, so swarm events
show up in other agents' briefs; the directory name when there is no origin remote or git fails; memoized per process, also
used for `graph_register`), and `payload`:
`{correlation_id, msg_id, swarm_type, status?, trace_id?, causation_id?}`. `status` is the record payload's own `status` when
it is a string. `trace_id`/`causation_id` are copied from the record when present (the run log does not carry them yet).
Only 2xx counts as accepted.

`runlog.emit()` adds an additive `msg_id` (uuid4 hex) to every JSONL record; all other fields and the file format are
unchanged. The tee is called after the local write.

## Swarm type → EVENT_KINDS

`EVENT_KINDS` is closed (`session.start, prompt, claim, tool.call, file.edit, shell, commit, pr, handoff, session.end, note,
warning`); the swarm does not widen it. The table is `TYPE_KINDS` / `PREFIX_KINDS` in `substrate_tee.py`. **Every** type the
repo emits is listed; a test parses (with `ast`) every `emit(` / `ctx.emit(` call in `swarm/`, `scripts/` and `hooks/`, fails on any type
that is unmapped or any emit site whose type is not a string literal, and fails on a table entry nothing emits. An unmapped
type at runtime is skipped, never downgraded to `note`. The exact swarm type always rides in `payload.swarm_type`.

| Swarm type | Emitted by | Kind | Why |
|---|---|---|---|
| `plan.updated` | `orch_plan` | `note` | Plan/bookkeeping record; no tool ran, nothing needs attention. |
| `task.transition` | `orch_status` | `note` | State-machine bookkeeping. |
| `task.result.raw` | `swarm_run` | `note` | Raw agent output captured before validation. |
| `task.claimed` | `results.apply_result` | `claim` | An agent took a task: the closest EVENT_KINDS meaning. |
| `gate.verdict.unrecorded` | `swarm.verdicts` | `warning` | A gate ran but its verdict was not recorded against a task. |
| `gate.findings.coerced` | `swarm_run` | `warning` | A gate's findings had to be coerced into shape. |
| `gate.findings.synthesized` | `swarm_run` | `warning` | Findings were synthesized rather than reported. |
| `gate.findings.unattributed` | `swarm_run` | `warning` | Findings named no known gate target. |
| `task.result.rejected` | `results.reject`, `orch_status` | `warning` | A result failed validation; the task is failed or left as is. |
| `escalation.request` | `results.reconcile`, `swarm_run` | `warning` | Rework/attempts exhausted; a human is needed. (`handoff` is reserved for lease handoffs, which the swarm does not yet emit.) |
| `security.dev_key` | `swarm.envelope` | `warning` | An envelope was signed with the development key. |
| `script.<name>` (prefix) | `AgentScript.main` | `tool.call` | Exit record of one agent script run. |
| `script.<name>.error` (prefix) | `AgentScript.main` | `tool.call` | Same, for a failed run; `payload.status` is `error`. |

Gate verdict types map to `warning`, plan/transition types to `note`, script-exit types to `tool.call`. The signed
`gate.verdict` envelopes themselves are written to `.swarm/verdicts/` and are not run-log records, so they are not tee'd
in S1; the gate script's own `script.<gate>` exit record is.

## Graph ID and binding

Format unchanged: `ut-<base36 epoch ms>-<8 hex>` (`mint_graph_id()`).

**Offer order** (`resolve_graph_id`): `--graph-id` (orch_plan) > env `SUBSTRATE_GRAPH_ID` > a Graph ID found in the brief or
spec text (`ut-[0-9a-z]+-[0-9a-f]{8}`, e.g. in `<ISSUES graphId="…">` or `Graph ID: …`) > freshly minted. Invalid explicit
or env values are ignored by the resolver; `orch_plan --graph-id` rejects a malformed value with `E-INPUT`.

**Binding.** After a *new* plan is created (not on the reuse path, not on `--dry-run`), `orch_plan` offers the id via the MCP
tool `graph_bind {correlation_id, graph_id}` (`POST {SUBSTRATE_URL}/mcp`, stateless JSON-RPC `tools/call`, JSON or SSE
reply) and **always adopts the `graph_id` the server returns**, including when `conflict` is true (another run bound the
correlation first). It then registers the node set: `graph_register {graph_id, repo, surface: "swarm-a01-orch", status:
"planned", nodes: [{node_id: <task_id>}…]}`. This happens before `plan.updated` is emitted so that event and the script
exit record are tee'd. Nothing time-varying enters the brief text or `notes_json`, so re-running the same plan still hits the
reuse path (and does not bind or register again).

**Cache.** The adopted binding is stored in `<swarm dir>/substrate-tee.db` (`bindings(correlation_id PK, graph_id)`), so later
scripts (separate processes) tee without asking. On a cache miss the tee does a forward lookup
`graph_bind {correlation_id}`; `existing` is cached, `unbound` or no answer means the record is skipped (no event, and no
Graph ID is ever invented by the tee). Nothing is cached when substrate did not answer a bind. A skipped correlation is
remembered in process memory for 60 s, so a burst of records for one unbound run costs one forward lookup instead of one per
record; that negative result is never written to sqlite, and a successful lookup or `bind_graph` clears it.

## Dedupe

Same sqlite file, table `seen(surface, msg_id, first_seen)`, primary key `(surface, msg_id)`, 24 h window pruned on every
write. The tee claims the key with an atomic `INSERT OR IGNORE` before sending, so a republished record (or two processes
racing it) produces exactly one request. If substrate does not answer 2xx the claim is released so a later republish can try
again.

## Fail-open guarantees

- No `SUBSTRATE_URL`, `SUBSTRATE_DISABLED=1`, or a non-http(s) URL: no request, no sqlite file; behaviour and output are
  unchanged (the JSONL record only gains `msg_id`).
- Timeout (1.5 s), connection error, non-2xx, malformed reply, `isError`, an unmapped type, an unattributable record, an
  unbound correlation: the tee sends nothing more and returns. `rest_post`, `mcp_call`, `tee` and `runlog`'s call to it
  never raise; `orch_plan`'s bind/register block is wrapped as well, so the plan, its output fields and the exit code are
  identical with substrate unreachable.
- Tokens are never logged, printed or returned.

## Not in S1

- Fetching a brief from substrate before planning (S2+), and any consumption of other agents' briefs.
- Leases / `graph_claim` / `coord_handoff` from the swarm, and `handoff` events.
- Tee'ing signed `swarm.v1` envelopes that are not run-log records (verdict files, `task.assign`).
- `trace_id`/`causation_id` production in the run log (they are forwarded when a record carries them).
- A `--graph-id` entry in the omp extension's `orch_plan` tool schema (`omp/src/tools.ts`); the CLI flag and the
  `SUBSTRATE_GRAPH_ID` env var work for it already.
- Binding for plans created by paths other than `orch_plan` (e.g. `scripts/swarm_run.py` on an unbound correlation tees
  nothing until the correlation is bound).
