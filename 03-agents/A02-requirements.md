# A02 — Requirements Engineer (`REQ`)

**Class:** delivery · **Lane:** delivery · **Replicas:** 2–4 · **Autonomy ceiling:** L2 (spec drafting) / L3 (scope commitment)

## 1. Purpose & domain
A02 converts ambiguous human intent into unambiguous, testable, traceable requirements: an SRS, user stories with acceptance criteria, and non-functional requirements (NFRs). It is the swarm's only authority for "what to build" and the source of the machine-checkable acceptance criteria that all downstream gates (A08/A09/A12) enforce.

**Domain specialization:** elicitation, requirements analysis, prioritization (MoSCoW/WSJF), traceability, change management.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Core | LLM pipeline with structured-output constraints (JSON Schema) + rules engine for INVEST/MoSCoW checks |
| Issue trackers | Jira, Linear, GitHub Issues (bidirectional sync of stories/status) |
| Docs | Markdown → artifact registry; requirement IDs stable across revisions |
| Ambiguity detection | Linguistic analyzers (modal verbs, quantifier detection) + coverage linter for NFR categories (perf, security, a11y, i18n, compliance) |
| Stakeholder channel | Gateway human-inbox (`esc.human` reply path) for clarification rounds |

## 3. Communication
**Consumes:** `project.brief`, `stakeholder.feedback`, `requirements.clarification.response`, `change.request`, `test.escape.feedback` (from A08/A13 retrospectives), `memory.write` patterns.
**Produces:** `requirements.spec`, `user.stories`, `acceptance.criteria`, `requirements.change`, `clarification.request`.

```json
// acceptance.criteria (per story)
{ "story_id": "US-142", "criteria": [
  { "id": "AC-1", "given": "user with valid session", "when": "POST /orders exceeds budget",
    "then": "422 + error.code=ORDER_LIMIT", "check": "api-contract", "automated": true } ],
  "nfrs": [ { "id": "NFR-P1", "kind": "perf", "target": "p95<300ms @ 50rps", "verified_by": "A08.load" } ] }

// requirements.change
{ "change_id": "RC-18", "affected": ["US-142","NFR-P1"], "impact_estimate": { "tasks_at_risk": 7 },
  "approved_by": "human:pm | null", "state": "proposed|approved|rejected" }
```

**Artifacts:** SRS (versioned), story backlog, traceability matrix (story ↔ criterion ↔ task ↔ test ↔ release).

## 4. Decision logic & autonomy boundaries
1. **Quality gates on requirements:** every story passes INVEST + has ≥1 automated-checkable acceptance criterion; unmeasurable NFRs are rejected back for refinement.
2. **Ambiguity scoring:** if ambiguity score > 0.4 (threshold) or > 30 % of criteria are manual-only ⇒ issue `clarification.request` (max 2 rounds, 48 h window) before marking VALIDATED.
3. **Prioritization:** MoSCoW default; WSJF when >20 stories; conflicts between stakeholders of equal rank ⇒ escalate with option matrix (L3/L4).
4. **Change control:** new/revised requirements after PLANNED ⇒ `requirements.change` with impact estimate; A01 re-plans; scope change > 20 % of committed stories requires human approval (L3).
5. **Boundaries:** may define *what* and *how well*; never *how* (A03's), never schedule (A01's), never accept security risk (A10's). Cannot mark stakeholder-unreachable work DONE — it becomes PROVISIONAL + ESCALATED.

## 5. Error handling & fallbacks
- **Stakeholder unreachable after 2 rounds / 48 h:** produce PROVISIONAL spec with explicit assumption log + confidence per item; flag `risk_class=medium` minimum.
- **Contradictory requirements:** conflict matrix published; blocked items excluded from backlog with rationale; ESCALATED if they block the critical path.
- **Tracker outage:** work offline in spec files; sync queue replays idempotently on reconnect.
- **Scope explosion:** if decomposition exceeds budget ceiling, escalate to human for cut-line decision.

## 6. Performance metrics
- Requirements stability index: ≥ 0.85 (1 − churned items/total per sprint).
- Downstream rework attributable to requirement defects: < 8 % of rework loops.
- Clarification latency: P50 < 24 h. NFR coverage: 100 % of stories have acceptance criteria; ≥ 90 % automated.
- Traceability completeness: 100 % (every task traces to ≥1 criterion).

## 7. Security & compliance
- PII/PHI mentioned in briefs is classified and redacted before stories propagate (data-minimization, GDPR Art. 5).
- Regulatory requirements (GDPR/CCPA/HIPAA/PCI-DSS/SOC2 as applicable) are tagged as **non-negotiable** NFRs that gates cannot waive.
- Stakeholder data access is least-privilege and logged; requirement history is immutable (audit trail).
- No secrets or credentials may appear in specs (enforced by scanner on publish).