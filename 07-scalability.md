# AgentSwarm — Scalability & Agent Onboarding

**Version:** 1.0.0 · How to grow the swarm: add new agents, evolve contracts, and scale capacity without disrupting existing operations.

---

## 1. Design principles for extensibility

1. **Registry-mediated everything.** Agents are discovered by capability manifest, never hard-wired — consumers bind to artifacts/message types, not to agent identities.
2. **Additive-first.** New message types, fields, and capabilities are always additive; removals go through deprecation windows.
3. **Shadow → probation → full autonomy.** New agents earn trust progressively (see §4).
4. **No special cases in the core.** A01's scheduler, gates, and lifecycle treat every agent class uniformly through the manifest; a new agent needs zero orchestrator changes.

## 2. Agent manifest schema (`swarm.manifest.v1`)

```json
{
  "id": "A16", "code": "ML", "class": "build", "lane": "code",
  "version": "0.1.0",
  "capabilities": ["code.ml-pipeline", "code.model-eval"],
  "consumes":  [ { "type": "task.assign",  "schema": "swarm.v1.task.assign" },
                 { "type": "api.contract", "schema": "swarm.v1.api.contract" } ],
  "produces":  [ { "type": "code.patch",   "schema": "swarm.v1.code.patch" } ],
  "artifact_ownership": ["ml/pipelines/**"],
  "gates_required": ["review", "quality", "security"],
  "max_parallel": 4, "cost_hint_usd_per_task": 0.8,
  "autonomy_ceiling": { "default": "L2", "overrides": { "new_dependency": "L3" } },
  "degraded_mode": "scaffold-only",
  "health": { "heartbeat_s": 10, "readyz": "/readyz" }
}
```

**Invariants enforced at registration:** every produced type must be schema-registered; artifact ownership must not overlap an existing single-writer claim (unless co-signed by both agents as a carve-out); `gates_required` ⊆ {review, quality, security, release}.

## 3. Adding a new agent — checklist (zero-disruption path)

1. **Gap analysis** — confirm the capability isn't already covered (roles are complementary by policy; A01 arbitration history and the capability registry are checked).
2. **Write the spec** — same 7-section template as [03-agents/](03-agents/) (purpose, stack, I/O, autonomy, error handling, metrics, security).
3. **Register contracts** — new schemas into the registry with exemplars; declare `consumes`/`produces`; run Level C contract tests.
4. **Build in `sim`** — golden-task corpus (Level U) + dry-run mode.
5. **Shadow mode (L0)** — deploy alongside; consumes real traffic, outputs recorded but *not* consumed by anyone; verdict/output diffing vs the incumbent (if replacing) or plausibility checks (if net-new).
6. **Probation (L1–L2, scoped)** — real outputs, restricted autonomy ceiling, reduced traffic share (10 % → 50 % via bid weight); KPIs evaluated against spec targets for ≥ 2 weeks.
7. **Full membership** — autonomy per spec; add to interaction matrix ([04-integration-plan.md §1](04-integration-plan.md)), lane scaling profile, and E2E benchmark corpus.

Rollback at any stage: `ctl.<agent>.drain` + registry deregistration; since it owns only *its* artifacts, existing flows continue (its tasks requeue or re-route to fallback capabilities declared in the registry).

## 4. Traffic shaping & graceful coexistence

- **Bid weights** implement traffic share (probation weights < 1.0).
- **Capability aliases** allow two classes to serve one capability during migration (e.g., `code.backend` served by A05 and, at 10 %, a new class); A01's tie-breaker (rework rate) converges traffic to the better performer.
- **Replacement protocol:** shadow-diff for 1 week → probation → incumbent degrades to fallback → incumbent deprecation notice with 30-day window.

## 5. Scaling capacity (not new roles)

| Lever | Mechanism | Guardrail |
|---|---|---|
| Horizontal replicas | KEDA on bus lag + queue depth (per class) | WIP caps in Task Store |
| Vertical | resource requests/limits per class profile | cost ceiling alarms |
| Parallel DAG widening | A01 splits tasks when `max_parallel` saturated and acceptance criteria independent | split quality gate (A08) |
| Multi-project fairness | per-project weight in A01 scheduler + namespace quotas | starvation alarms |
| Regional expansion | lane-level deployments, bus federation, artifact-registry replication | single-writer stays global (lease-based) |

Bottleneck playbook (run weekly by A01 + operators): compute per-class utilization; if any class P95 queue wait > SLA for 3 consecutive days → raise replicas or split tasks; if a verify gate is the bottleneck → widen gate-lane capacity first (never widen build capacity past verify capacity — prevents merge-queue thrash).

## 6. Contract evolution & deprecation

- **Additive change** (new optional field, new message type): minor version; consumers tolerate unknowns (Level C property test enforces).
- **Breaking change:** new major schema (`swarm.v2.*`), parallel subject for ≥ 30 days, producer writes both during window, consumers migrate, old version deregistered at window end. Registry refuses step violations.
- **Artifact format changes:** versioned artifact refs (`uri@version`) — readers pin versions; writers never mutate published versions (immutable digest addressing).
- **Task Store schema:** append-only migrations; A01 handles dual-read during transitions.

## 7. Organizational scaling (multi-swarm)

- Swarms instantiate per program/org with shared platform (bus cluster, registry) but isolated namespaces, task stores, and policy bundles.
- Cross-swarm reuse happens at the artifact level (shared component contracts, shared memory patterns) — never shared in-flight tasks.
- Global policy board (humans) owns cross-swarm autonomy ceilings and compliance baselines; swarms may be stricter, never looser.

## 8. Deprecating an agent

1. Fallback capability declared in registry must cover its `produces`.
2. In-flight tasks drained; ownership of immutable artifacts remains (history preserved).
3. Interaction matrix updated; memory entries tagged `agent:deprecatd:<code>` for retrospective learning.
4. Decommission only after 30 days of zero bid awards.