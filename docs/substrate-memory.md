# Swarm memory on Agent Substrate (ADR 0001, phase S3)

Implements S3 criteria 1–5 and 7 of
[ADR 0001](https://github.com/swcstudiospace/agent-substrate/blob/main/docs/adr/0001-agent-swarm-as-sdlc-mesh-on-substrate.md)
(Linear SPE-5054), swarm side: swarm memory **is** substrate memory. The swarm has no memory store of its own and this module
adds none; it is a thin client over the substrate's one governed write door (`memory_write`, gate in `memory-write.ts`).

Code: `swarm/memory.py` (client), `swarm/substrate_client.py` (HTTP; gained `mcp_call_json`, because `memory_search` answers a
JSON *array* and `mcp_call` only returns objects), `scripts/swarm_run.py` (`assignment_prompt`, A01's run-start context).
Tests: `tests/test_swarm_memory.py`. Environment, timeout and back-off are the ones in [substrate-tee.md](substrate-tee.md).

Not done here: agents calling this through tooling is S4 (projector/installer wiring); gate verdicts as memory is S5. Nothing
in the swarm calls `memory_write` on its own yet — the module is the contract those phases build on.

## API (`swarm.memory`)

| Function | Resolves to | Failure mode |
|---|---|---|
| `memory_write(kind, scope, text, *, correlation_id, task_id, attempt=1, graph_id=None, repo=None, surface=None, subject=None, expected_version=None, env=None, sleep=time.sleep)` | `POST /memory` | never pretends (below); raises `SwarmError` `E-INPUT` / `E-POLICY` |
| `memory_query(query, *, scope=None, limit=10, env=None)` | MCP `memory_search` | fail-open: `[]` |
| `run_start_context(repo, graph_id=None, env=None)` | `POST /brief` (`memory_brief`) as surface `swarm-a01-orch` | fail-open: `''` |
| `idempotency_key(correlation_id, task_id, attempt, kind, scope, text, *, subject=None)` | — | `E-INPUT` on empty ids / `attempt < 1` / non-string `subject` |

`memory_write` returns a `WriteOutcome(status, reason, remedy, idempotency_key, verified_persisted, entry_id, version, review_id,
quarantine_id, attempts)`. **`outcome.ok` is true only when the server said `accepted` and `verified_persisted` is true.**
Nothing may treat a write as done unless `ok`.

## Kind map

| Swarm kind | Substrate `MemoryKind` | Notes |
|---|---|---|
| `decision` | `decision` | Versioned **when a `subject` is given**: replacing the decision in force needs `expected_version`. Without a subject the gate appends. |
| `retro` | `fact` | |
| `pattern` | `fact` | Default scope is `repo`; a swarm-wide pattern (`global`) is a proposal. |

Any other kind is `SwarmError(E-INPUT)`; the substrate's `MEMORY_KINDS` is not widened.

## Scope map

| Swarm scope | Needs | Substrate scope |
|---|---|---|
| `graph` | `graph_id` | `graph:<graph_id>` |
| `repo` | `repo` (`owner/name`) | `repo:<owner/name>` |
| `agent` | `surface` — one of the 15 swarm surfaces (`swarm-a01-orch` … `swarm-a15-doc`) | `agent:<surface>` (the server's `self` grant refuses any surface other than the token's) |
| `global` | — | `global` (**require-review**: a proposal until a reviewer token releases it) |

An unknown scope, or a scope whose id was not supplied, is `SwarmError(E-INPUT)` and nothing is sent. The body never carries a
`writer`: the server decides the writer from the bearer token (`SUBSTRATE_TOKEN`).

## Outcomes and how the swarm handles them

Wire status comes from the server's `memoryWriteStatus`; the decision uses the body's `outcome` / `reason`.

| Server reply | HTTP | Swarm handling | `status` | `ok` |
|---|---|---|---|---|
| `accepted`, `verified_persisted: true` | 200 | done | `accepted` | **yes** |
| `accepted`, `verified_persisted: false` | 202 | retry with the **same** idempotency key, up to 3 POSTs, sleeping 0.5 s then 1.0 s (injected `sleep`); still unverified → give up | `accepted-unverified` | no |
| `denied` (policy: `rbac.*`, …) | 403 | **fail-closed, never retried**: `raise SwarmError(E-POLICY, "<reason>: <remedy>")` (details: `reason`, `remedy`, `writer`, `idempotency_key`) | — (raises) | — |
| `denied`, reason `store.*` | 503 | a store outage, not a policy decision: treated as unavailable | `unavailable` | no |
| `quarantined` (`quarantine.secret` / `.pii` / `.speculative`) | 422 | returned with `reason` and `quarantine_id`; **not retried** | `quarantined` | no |
| `conflict`, `review.required` | 409 | carried as a proposal (`review_id`); **not retried** | `proposal` | no |
| `conflict`, `version.required` / `version.stale` (and the race forms `version.raced` / `standing.raced`) on a `decision` with a `subject` | 409 | re-read the version in force: first a targeted `memory_search` (the subject as query text, at the scope), then, only if that misses, the scope-wide read (empty query); match by `conflict_with` id or subject. Re-submit with `expected_version` (or without it when no decision is in force and the server named none), at most 2 re-submits, same key; still conflicting → give up | `conflict` | no |
| `conflict`, `write.in-flight` | 409 | another attempt holds the key: retry with the same key like an unverified write | `conflict` after 3 POSTs | no |
| any other `conflict` (e.g. `idempotency.reused`) | 409 | returned, not retried | `conflict` | no |
| no reply: substrate off, unreachable, in the client's 30 s outage back-off, or the reply was lost (timeout, reset) | — | the result is **unknown**: the request may have reached the server and been stored before the reply was lost (only `substrate.disabled` guarantees nothing was sent). Not retried inside the call; recover as below | `unavailable` (`substrate.disabled` / `substrate.unreachable`) | no |
| a non-result body (401/403 auth refusal) | 401/403 | fail-closed like `denied` | raises `E-POLICY` | — |
| a non-result body (any other status: audit-gate 5xx, proxy page) | other | not a write result | `unavailable` (`http.<status>`) | no |

A re-read that cannot be answered (`memory_search` down) leaves the conflict as `conflict`; the client never guesses a version.
The same holds when the server named a standing decision (`conflict_with`) that neither the targeted nor the scope-wide read
finds: the client stops with `conflict` rather than re-submitting without `expected_version`.

**Recovering from `unavailable`.** Treat it as "unknown", never as "not written". Retry the **same** logical write — same
`correlation_id`, `task_id`, `attempt`, `kind`, `scope`, `subject` and `text` — so it carries the same idempotency key and the
server replays the stored result instead of writing a second entry. Never retry under a new `attempt` number just because of a
network failure: a new attempt is a new key and can duplicate an entry the lost reply had already stored.

## Idempotency (criterion 5)

`idempotency_key = sha256_hex(json.dumps([correlation_id, task_id, attempt, kind, scope, subject, text], separators=(",", ":"),
ensure_ascii=False))` (UTF-8), where `kind` is the substrate kind (after the kind map), `scope` the resolved substrate scope
(e.g. `repo:acme/widgets`) and `subject` is normalised to `subject.strip().lower()` (`""` when absent or blank). A JSON array is
unambiguous, so `("a|b", "c")` and `("a", "b|c")` are different keys, and two `decision` writes in one task attempt with the same
text and scope but different subjects are different writes. `expected_version` is deliberately **not** in the key. The key is
sent as `idempotency_key` on **every** POST of one write — unverified retries and version re-submits included — so the server's
`memory_writes` record dedupes at-least-once redelivery and retries after a lost reply: the same write for the same task attempt
can never produce two entries, and a different `attempt`, text, subject, scope or stored kind is a different key. The server never
replays a *refused* key (only an `accepted` one), which is what makes a re-submit with a new `expected_version` under the same key
legitimate.

Under the substrate's default grants a `decision` at `agent:` scope is not granted (only `graph` and `repo`, and `global` as a
review proposal), so a swarm `decision` written at scope `agent` comes back denied and raises `E-POLICY`; an operator grant in the
substrate's `SUBSTRATE_MEMORY_GRANTS` can widen that. The client never refuses a write itself, it acts on the gate's verdict. Use
`graph` or `repo` scope for decisions.

## Fail modes

- **Read side is fail-open.** `memory_query` returns `[]` and `run_start_context` returns `''` when the substrate is off,
  unreachable or answers badly. Same 1.5 s timeout and 30 s back-off as the tee. A read never stops a run.
- **Write side never pretends.** Memory is the one deliberate fail-*closed* exception (enforced at the server). The client only
  reports `ok` for an accepted-and-verified write; every other result is a named status the caller must handle, and a refusal
  raises. With `SUBSTRATE_URL` unset a write returns `unavailable` / `substrate.disabled` and nothing is sent.
- **Run-start context.** When the substrate is enabled and `run_start_context` returns text, `assignment_prompt` prepends a
  `## Substrate memory` section (computed once per run and repo, cached, fail-open). With the substrate off the prompt is
  byte-for-byte unchanged.

## Criterion 7: no swarm-side memory store (proven here, not in the substrate repo)

The substrate repository's `store-lock.test.ts` scans *that* repository's TypeScript for memory-table SQL and `insertEntry`; it
knows nothing about this repository or any vector store or local key-value store on an agent's context path. The claim is
therefore proven in this repository, by `tests/test_swarm_memory.py` (an AST/source scan over every `*.py` under `swarm/`,
`scripts/` and `hooks/`, nested packages included and `__pycache__` skipped, reported by repo-relative path; plus the import
specifiers of `scripts/ts/*.ts` in every form: `import … from`, `export … from`, side-effect `import "x"`, dynamic `import("x")`
and `require("x")`):

1. the only module defining `memory_write` / `memory_query` is `swarm/memory.py`;
2. no module imports `chromadb`, `faiss`, `lancedb`, `qdrant_client`, `pgvector`, `sqlite_vss`, `redis`, `shelve` or `dbm` (also via
   `import_module` / `__import__` with a literal name);
3. `swarm/memory.py` imports only pure stdlib (`hashlib`, `json`, `time`, `dataclasses`, `typing`), `swarm.errors` and
   `swarm.substrate_client`, calls only `substrate_client.rest_post` / `mcp_call_json` / `enabled`, and never calls `open()`.

The test also feeds the scanner synthetic sources (a second `memory_query`, `import chromadb`, a `sqlite3` import inside the
memory module, a forbidden import in a nested module of a temporary tree, each TypeScript import form, …) and asserts each is
caught, so it fails if a second memory store appears.
