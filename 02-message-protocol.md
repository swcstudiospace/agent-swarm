# AgentSwarm — Message Protocol & Task Lifecycle

**Version:** 1.0.0 · **Conformance:** All 15 agents MUST implement this protocol exactly.
Schemas are JSON Schema Draft 2020-12; every message is validated on publish and on consume.

---

## 1. Message envelope `swarm.envelope.v1`

Every inter-agent message is wrapped in the common envelope. Agents validate envelopes
before processing and MUST drop (with metric) malformed messages — never crash on them.

```json
{
  "msg_id":        "018f3a2b-... (UUIDv7, unique)",
  "correlation_id":"UUID linking all messages of one business request",
  "trace_id":      "W3C trace-id (propagated into OTel spans)",
  "causation_id":  "msg_id of the direct predecessor (builds the causal chain)",
  "source":        "A08@replica-3   (agent-id@replica)",
  "target":        "A05 | role:code | broadcast:verify | human:pm | gateway",
  "type":          "task.assign | review.verdict | quality.gate.verdict | ...",
  "schema":        "swarm.v1.task.assign",
  "ts":            "2026-08-29T10:00:00.000Z",
  "ttl_s":         300,
  "priority":      "P0 | P1 | P2 | P3",
  "risk_class":    "low | medium | high",
  "payload":       { "...type-specific..." },
  "sig":           "ed25519 base64 — REQUIRED for task.assign, gate verdicts, promote/rollback"
}
```

**Rules**

- `type` namespaced `<domain>.<Verb|Noun>`; full catalog in §4.
- Breaking payload changes ⇒ new major version in `schema` (e.g., `swarm.v2.…`) + parallel subject + deprecation window (see [07-scalability.md §4](07-scalability.md)).
- Unknown `type` ⇒ consumer emits `swarm.error.unroutable` to the bus (for A01 audit) and acks; unknown but required fields ⇒ `swarm.error.schema_violation` to `source` and A01.
- Duplicate `msg_id` ⇒ idempotent ack (dedupe window 24 h on `msg_id + consumer class`).

## 2. Subjects & delivery semantics

| Pattern | Subject template | Semantics |
|---------|------------------|-----------|
| Event (pub/sub) | `evt.<domain>.<type>` | Durable stream, at-least-once, consumer groups per class |
| Request | `req.<target-class>.<type>` | Request/reply, timeout 30 s default, retry ×2 |
| Broadcast offer | `offer.<capability>` | Competing bidders; A01 awards one |
| Control | `ctl.<agent-id>.<cmd>` | Pause, resume, degrade, drain, reload-policy |
| Escalation | `esc.human` | Human inbox, never auto-consumed by agents |

Delivery: at-least-once ⇒ all handlers MUST be idempotent. Ordering: guaranteed only
within `(correlation_id, subject)` — consumers must not assume global ordering.

## 3. Task lifecycle (Task Store `swarm.task.v1`)

```
CREATED ──► VALIDATED ──► PLANNED ──► CLAIMED ──► IN_PROGRESS ──► IN_REVIEW ──► APPROVED ──► DONE
   │            │             │           │             │              │
   └─► CANCELLED┴─► BLOCKED ─┴─► FAILED ─┴─► RETRY      └─► CHANGES_REQUESTED (→ IN_PROGRESS, max 2 loops)
                                      FAILED ─► ESCALATED (human)      APPROVED ─► CANCELLED (pre-DONE only, audited)
```

State transitions are written **only** by A01 (single-writer for task state). Agents report
via `task.status`; A01 validates legality (illegal transition ⇒ `task.status.rejected`).

| Field (Task Store) | Notes |
|---|---|
| `task_id, correlation_id, parent_id, dag_depth` | Identity & DAG position |
| `capability` | e.g. `code.backend`, `test.e2e`, `deploy.env` |
| `inputs[] / outputs[]` | Artifact refs `{ kind, uri, version, digest }` |
| `acceptance[]` | Machine-checkable criteria fed to gates |
| `budget` | `{ max_tokens, max_wall_s, max_cost_usd }` — hard limits enforced by A01 |
| `risk_class` | Drives autonomy ceiling and required gates |
| `attempt, max_attempts` | Default `max_attempts=3` |

## 4. Core message catalog (shared types)

| Type | Producer → Consumer | Payload (abridged) |
|------|--------------------|--------------------|
| `project.brief` | human/gateway → A02, A01 | `{ brief_md, stakeholders[], constraints[], deadline?, priority }` |
| `task.offer` | A01 → `offer.<capability>` | `{ task_id, capability, inputs[], acceptance[], budget, risk_class, deadline? }` |
| `task.bid` | agent → A01 | `{ task_id, agent_id, load, eta_s, confidence, degraded_mode? }` |
| `task.assign` | A01 → agent (signed) | `{ task_id, agent_id, lease_s, inputs[], acceptance[], budget }` |
| `task.status` | agent → A01 | `{ task_id, state, progress_pct?, artifacts[], notes?, error? }` |
| `task.result` | agent → A01 + consumers | `{ task_id, outputs[], metrics{}, summary_md }` |
| `gate.verdict` | A08/A09/A10 → A01, producer | `{ gate: quality|review|security, task_id, verdict: pass|fail|waive, findings[], expires_s }` |
| `escalation.request` | any → A01 → human | `{ task_id, reason_code, evidence[], options[], deadline_s }` |
| `agent.heartbeat` | all → A01 | `{ agent_id, load, queue_depth, degraded, version, capabilities[] }` |
| `conflict.report` | any → A01 | `{ subject, parties[], claims[], evidence[] }` |
| `memory.write` / `memory.query` | any → memory svc | `{ scope, kind: decision|retro|pattern, content_md, tags[] }` |

Per-agent payload schemas are defined in each agent spec ([03-agents/](03-agents/)).

## 5. Autonomy levels (used by every agent spec)

| Level | Meaning |
|-------|---------|
| L0 | Observe only — read, analyze, report |
| L1 | Act and log — autonomous, post-hoc audit |
| L2 | Act and notify — autonomous; notifies A01/humans of material outcomes |
| L3 | Approval required — must obtain human (or designated agent) approval before acting |
| L4 | Human-only — agent may propose, never execute |

Each agent declares an `autonomy_ceiling` per action class in its manifest; the ceiling can be
temporarily lowered (never raised) by operator policy at runtime.

## 6. Error taxonomy (shared)

| Code | Meaning | Standard handling |
|------|---------|-------------------|
| `E-INPUT` | Invalid/missing input artifact | Nack + `task.status: FAILED` with `E-INPUT`; producer of the artifact notified |
| `E-TIMEOUT` | Budget/lease/SLA exceeded | Checkpoint, release lease, requeue once, then ESCALATED |
| `E-DEP` | Upstream dependency unavailable | Exponential backoff (1 s→60 s, jitter, max 5), then degraded mode |
| `E-CAPACITY` | Overload / WIP cap hit | Refuse bid; backpressure signal in next heartbeat |
| `E-CONTRACT` | Output rejected by consumer/schema | Fix loop per agent spec (bounded) |
| `E-POLICY` | Action blocked by security/compliance policy | Fail-closed; escalation to A10/A01 |
| `E-INTERNAL` | Unexpected agent fault | Checkpoint + crash; A01 requeue; 3 strikes ⇒ ESCALATED |

Every agent MUST translate unexpected exceptions into one of these codes before reporting.