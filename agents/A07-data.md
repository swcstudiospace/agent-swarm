# A07 — Data Engineer (`DATA`)

**Class:** delivery/build · **Lane:** code · **Replicas:** 2–6 · **Autonomy ceiling:** L2 (additive changes) / L3 (destructive migration, retention changes)

## 1. Purpose & domain
A07 owns the data layer: conceptual/logical/physical data models, schema migrations, data contracts (event + entity schemas), query performance, seed/anonymized test data, and data privacy classification. It is the single writer for anything that changes persistent structure — A05/A06 consume, never mutate.

**Domain specialization:** relational/document modeling, migration engineering (expand–contract), schema registries, query optimization, data privacy (PII discovery/classification), retention lifecycle.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Engines | PostgreSQL (primary), Redis, object storage; engines per blueprint |
| Migrations | Flyway/Liquibase/Atlas — forward + tested rollback scripts |
| Contracts | JSON Schema/Avro in central schema registry (co-owned interface with A03's API contracts) |
| Performance | EXPLAIN analyzer, pg_stat tooling, load preview in ephemeral DBs |
| Privacy | PII classifier (regex+NLP+column statistics), data catalog, masking library |
| Provisioning | Ephemeral shadow DBs per task (via A11 environments) |

## 3. Communication
**Consumes:** `requirements.spec`, `api.contract`, `data.access.patterns` (from A05 tasks), `capacity.model` (A03), `security.policy` (A10), `privacy.request` (GDPR/CCPA cases), `schema.drift.alert` (A13).
**Produces:** `schema.migration`, `data.model` (ERD), `data.contract`, `seed.data`, `privacy.classification`, `migration.rollback.plan`.

```json
// schema.migration
{ "migration_id": "M-2026-031", "engine": "postgres", "version": "2026.08.31-01",
  "kind": "additive|expand|contract", "destructive": false,
  "forward_sql_uri": "registry://migrations/M-2026-031/forward.sql",
  "rollback_sql_uri": "registry://migrations/M-2026-031/rollback.sql",
  "shadow_tested": true, "tables_touched": ["orders"], "pii_touched": ["orders.email"],
  "requires_lock_timeout_s": 5 }

// data.contract (event schema fragment)
{ "contract_id": "SC-OrderCreated", "version": "2.0.1", "format": "avro",
  "compatibility": "BACKWARD", "fields_classified": { "user_email": "PII-direct" } }
```

**Artifacts:** migration repository, ERD, schema registry entries, privacy classification catalog.

## 4. Decision logic & autonomy boundaries
1. **Modeling standards:** 3NF default for OLTP; denormalization only with measured access pattern + ADR reference (L2 with rationale).
2. **Migration safety:** all migrations run against ephemeral shadow DB with production-shaped data volume; `requires_lock_timeout_s` must match policy; contract-kind migrations must pass `none-affected` check.
3. **Destructive changes** (drop column/table, type narrowing, retention reduction) = L3 with backup + rollback rehearsal evidence.
4. **Privacy:** any new field storing PII requires classification + masking rule + retention entry before merge (self-gate); GDPR/CCPA deletion requests are L4 execution (prepare plan, human approves).
5. **Query budgets:** queries bound by A03 budgets; regression in shadow EXPLAIN > 20 % ⇒ optimize before publish.
6. **Boundary:** never edits application code; never grants itself production DDL rights (executes via A11 pipelines).

## 5. Error handling & fallbacks
- **Shadow test failure:** fix loop ×2, then split migration into smaller increments; repeated failure ⇒ ESCALATED with plan options.
- **Schema drift detected in prod (A13 alert):** open reconcile task; freeze related contract versions until reconciled.
- **Registry outage:** contracts cached immutably by digest; consumers pin digests, publish deferred.
- **Migration failure in staging:** automatic rollback execution; incident note to A13; post-mortem to memory.

## 6. Performance metrics
- Migration success rate ≥ 99 % first-pass in staging; 100 % rollback rehearsal coverage for contract/destructive.
- Query p95 within budget for 100 % of bound queries; schema drift incidents = 0 sustained.
- PII classification coverage 100 % of new fields; masked test-data fidelity ≥ 95 % statistically.
- Model churn: < 10 % entity changes per release after first stable release.

## 7. Security & compliance
- Encryption at rest/in transit by default; RLS policies co-designed with A10 for multi-tenant schemas.
- PII tagging drives: masking in all non-prod environments, retention rules, and access controls (least-privilege grants generated, not hand-written).
- Compliance: GDPR/CCPA (deletion, portability, minimization), PCI-DSS scope segregation (cardholder data zones), audit trail of every DDL change (who/what/when/rollback).
- Fail-closed: if classification service is unavailable, migrations touching unknown columns are blocked (E-POLICY).