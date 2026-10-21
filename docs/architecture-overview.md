# Architecture overview

AgentSwarm is a 30-agent software-delivery loop. A01 owns the plan. The other agents do one lane each. A02 to A15 are the delivery core (requirements, architecture, UX, backend, frontend, data, test, review, security, devops, release, observability, maintenance, docs). A16 to A30 are three specialist domains of five agents each: Cloud-Native (A16 cloud architecture, A17 platform engineering, A18 supply-chain security, A19 event-driven and serverless, A20 FinOps and GreenOps), Cluster (A21 cluster operations, A22 GitOps and progressive delivery, A23 service mesh and networking, A24 cluster security and policy, A25 resilience and chaos) and Web3 (A26 smart contracts, A27 contract audit, A28 chain infrastructure, A29 dApp and wallet integration, A30 custody and compliance). The roster is `agents.json`; the new domains arrived in v2.3. No new agent owns a gate: A08 (quality), A09 (review), A10 (security) and A12 (release) stay the only gate owners, and the new lanes reach them through the plan. This page is the map of the layers added in v2.1 (registry, shared pool, ACP) and v2.2 (continuous runtime, telemetry, handover, skills, the omp bar), and of how the three domains plug into them.

## Topology

```mermaid
flowchart LR
  subgraph control [Control]
    A01[A01 orchestrator]
  end
  subgraph registry [agents.json registry]
    Lane[lane tools]
    Shared[shared pool]
    Matrix[ACP permission matrix]
  end
  subgraph core [Delivery core A02 to A15]
    A02[A02 requirements]
    A03[A03 architect]
    Gates[A08 A09 A10 A12 gates]
  end
  subgraph cloud [Cloud-Native A16 to A20]
    A16[A16 cloud architect]
    A19[A19 event-driven]
    A20[A20 FinOps]
  end
  subgraph cluster [Cluster A21 to A25]
    A21[A21 cluster operator]
    A22[A22 GitOps]
    A24[A24 cluster security]
  end
  subgraph web3 [Web3 A26 to A30]
    A26[A26 smart contracts]
    A27[A27 auditor]
    A30[A30 custody]
  end
  A01 --> Lane
  A01 --> Matrix
  A02 -->|requirements.spec| A03
  A16 -->|cloud.architecture| A03
  A16 -->|cloud.architecture| A20
  A16 -->|cloud.architecture| A19
  A21 -->|cluster.capacity.report| A20
  A22 -->|gitops.manifests| A24
  A26 -->|contract.abi| A27
  A30 -->|custody.policy.set| A26
  A24 -->|cluster.security.findings| Gates
  A27 -->|audit.findings| Gates
  Gates -->|gate.verdict| A01
  Matrix --> Shared
  Matrix --> Lane
```

The edges are examples taken from each agent's `produces` and `consumes` in `agents.json`; the full permission matrix has one row per agent and is derived from them (see [acp-topology.md](acp-topology.md)).

End-to-end trace for one feature:

1. A01 decomposes the brief. `orch_dag_order` orders the task graph. `orch_budget_class` returns the risk budget the planner already uses.
2. A02 checks acceptance text with `req_gwt_check` and may call `shared_verdict` on the finding severities.
3. A03 and A04 publish contracts and tokens. A05 and A06 implement against them and share `shared_toolchain` / `shared_check_plan` (the old `code_checks.py` pair).
4. The A08, A09, A10 and A12 gates call `shared_verdict`. A denial is UNAUTHORIZED plus an audit row. A passing gate is a `gate.verdict` message A01 already consumes.
5. A11 records the git head with `shared_git_head`. A12 builds the canary steps. A13 computes burn. A peer that never answers is retried, then dead-lettered, and a sick peer is circuit-broken. The caller's own lane tools keep running.

The three domains enter that trace through `PATTERNS` in `scripts/orch_plan.py` (`cloud-native`, `cluster`, `web3`) or, for a graph summary, through the file-path routing in `scripts/orch_from_graph.py`. The same gates close each plan: A08, A09 and A10 run on the implementer rows, A12 owns the release, and the cluster and web3 implementer rows default to `risk_class` high, which selects all four. `cloud-native` is 16 tasks (A02, A03, A16, A17, A19, A05, A20, A18 pre-build policy check, the A08, A09, A10 gates, A11, A18 post-build image evidence, A12, A13, A15), `cluster` 14 (A21, A23, A24, A22 manifests, A25, the three gates, A22 rollout, A12, A13, A15, and the A23 and A24 scans after the manifests) and `web3` 15 (A02, A03, A30, A26, A29, A27, A28, the three gates, A11, A30 custody compliance after the audit, A12, A13, A15).

Every swarm session also runs through the omp `tool_call` guard (`omp/src/guard.ts`), which blocks until a human approves the Web3 fund-moving and key rows (`chain-broadcast`, `chain-key-material`) and the cluster lifecycle rows (`argocd-change`, `flux-change`, `mesh-lifecycle`, `velero-change`, `managed-cluster-lifecycle`, `kubeadm-lifecycle`). The rows carry no per-agent allow list.

## Tool layer

`agents.json` is the only registry (`tool_contract: swarm.tool.v1`). Each agent has `registered_tools` (10 to 15, set by `capability_policy`). Cross-lane operations live in top-level `shared_tools`; `shared_baseline` names the ones all 30 agents may call. `python3 -m swarm.tool_registry call` and, for omp sessions, `swarm_catalog` / `swarm_tool_call` (through `scripts/swarm_tool.py`) are the only paths that import a handler. An unknown name returns `INVALID_INPUT` and does not import anything. Existing `scripts/*.py` entrypoints still run as they did in v2.0. New tools arrive as `registry/intake/` fragments merged by `scripts/_registry_intake.py`, never by hand edits.

Every registered tool ends in exactly one of: SUCCESS, INVALID_INPUT (names the field), UNAUTHORIZED (echoes the caller), DEPENDENCY_UNAVAILABLE, TIMEOUT, or PARTIAL_SUCCESS (includes `completed_fraction`). Two registry guards apply to every tool: a `number` input must be finite (NaN and Infinity are `INVALID_INPUT`), and a result must be strict JSON and at most `SWARM_TOOL_OUTPUT_MAX_BYTES` (default 262144); a larger one is withheld as `PARTIAL_SUCCESS` with `output_withheld: true`.

## Shared pool

The pool is the set of functions two or more lanes already called, plus the v2.2 baseline every agent has: 13 handover tools, 8 runtime tools and the four original ones (`shared_verdict`, `shared_toolchain`, `shared_check_plan`, `shared_git_head`). The baseline and its call path are described in [swarm-command-center.md](swarm-command-center.md).

## ACP fabric

Agents exchange `acp.v1` envelopes through `swarm/acp.py`. The in-process transport derives endpoints (`inproc://A03`) and the allow list from each agent's `produces` and `consumes`. There is no peer list in code. Default is deny. The signed `swarm.v1` task-store envelopes are a different channel and are not replaced by ACP.

## Runtime (v2.2)

`scripts/swarm_run.py` no longer runs in rounds. A persistent worker pool is refilled the moment a session finishes, bounded by `--max-parallel` (default 4, ceiling 32) and each agent's own `max_parallel`. `SWARM_SCHEDULER=rounds` restores the per-round barrier.

```mermaid
flowchart LR
  DB[(Task Store)] --> SCH[Scheduler: reconcile, dispatchable, select_batch]
  SCH -->|slot free| POOL[WorkerPool]
  POOL --> SESS[claude / omp session per task]
  SESS -->|result| DB
  SCH --> TEL[Telemetry: lifecycle state machine]
  TEL --> EV[(events.jsonl)]
  TEL --> LIVE[(live.json)]
  LIVE --> BAR[omp bar and swarm_live.py]
  SESS -->|shared_handover_write| HO[(handovers/)]
  HO -->|## Handover block| SCH
```

Data flow of one task: `reconcile()` and `dispatchable()` find ready tasks; `select_batch()` applies the global cap, the per-agent ceilings and `notes.claims`; the task becomes a run (`queued`, `starting`, `running`, then `completed`, `failed`, `cancelled` or `timed_out`) whose every transition is a `swarm.run.*` event and a row in `live.json`. The session receives a signed `task.assign` plus the latest handover of each dependency; it calls registry tools through `swarm_tool_call`, writes its own handover (filed under its own task id, addressed with `to_agent`), and ends with a `task.result` whose `.md` evidence the runner redacts before writing it. A transient failure waits `notes.retry_after` (capped exponential backoff); an isolated failure never lowers the parallelism, but a provider rate limit, or `SWARM_FAIL_STREAK` (default 3) consecutive failures of distinct tasks, halves the effective parallelism (AIMD) and is reported as `swarm.degraded`. SIGINT, SIGTERM, SIGHUP or `--swarm-timeout` start a cooperative cancel (`cancel/<correlation_id>` flag, grace, then the process-group kill).

| Module | Role |
| --- | --- |
| `swarm/lifecycle.py`, `swarm/clock.py` | Run state machine; monotonic clock and `format_elapsed` |
| `swarm/telemetry.py`, `swarm/redact.py` | One telemetry channel (`events.jsonl` + `live.json`), redaction of every payload |
| `swarm/claims.py`, `swarm/retry.py`, `swarm/limiter.py` | File claims, backoff and transient classifier, AIMD limiter |
| `swarm/subagent_lifecycle.py` | Global slot cap and overdue watchdog |
| `swarm/handover.py`, `swarm/tools/shared_handover.py`, `swarm/tools/shared_runtime.py` | `swarm.handover.v1` and the shared baseline tools |
| `swarm/skill_registry.py` | Validates and documents `prompts/skills/` |
| `omp/src/swarm_bar.ts` | The swarm bar ([swarm-bar.md](swarm-bar.md)): always mounted, two full-width rows; merges the omp bus, `live.json` (an idle `stat` watcher every `SWARM_BAR_IDLE_POLL_MS`) and the specialists A01 spawns in-session (`inflightTaskDetails`); shows the session's token savings (`token_savings.ts`, `token-opt:savings`) and keeps its place above Ultrathink's bar with the `bars:restack` protocol (`bar_stack.ts`) |

## Where to read next

- The v2.2 runtime, telemetry, handover, skills, how-to guides, configuration and known limits: [swarm-command-center.md](swarm-command-center.md)
- Reading the swarm bar: [swarm-bar.md](swarm-bar.md)
- Tool catalog and the 30 per-agent registry pages: `docs/tools/`; skill catalog: `docs/skills/`
- Envelope, retries, and two worked messages: `docs/acp-topology.md`
- How to add a tool or skill, regenerate, cancel a run or diagnose a dead peer: `docs/operations-runbook.md`
