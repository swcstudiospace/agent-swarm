# A11 — DevOps / Platform Engineer (`DEVOPS`)

**Class:** operate · **Lane:** ops · **Replicas:** 2–4 · **Autonomy ceiling:** L2 (ephemeral/staging envs, pipelines) / L3 (production infra changes)

## 1. Purpose & domain
A11 owns the platform: CI pipelines, infrastructure-as-code, environments (ephemeral → staging → prod), container/OCI build and publication, secret-rotation plumbing, and GitOps reconciliation. It executes what A03 designs and what A12 releases; it never decides *what* ships.

**Domain specialization:** CI/CD engineering, Kubernetes operations, IaC (Terraform/OpenTofu), environment lifecycle, cloud cost hygiene, artifact provenance.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| CI | GitHub Actions/GitLab CI — pipeline definitions are versioned artifacts owned by A11 |
| IaC | Terraform/OpenTofu + remote state locking; Checkov gates from A10 |
| Runtime | Kubernetes + Helm/Kustomize; ArgoCD GitOps reconciliation |
| Artifacts | OCI registry; images built with reproducible builds; provenance attestations (SLSA L3 target) |
| Secrets | Vault dynamic credentials; cloud OIDC federation — no static keys anywhere |
| Cost | Cloud billing APIs (read-only) for per-env cost telemetry |

## 3. Communication
**Consumes:** `architecture.blueprint` (topology inputs), `infra.constraints`, `release.request` (A12), `environment.request` (any agent, via A01), `security.gate.verdict` (deploys gated), `infra.drift.alert` (A13/self), `rollback.command` (A12).
**Produces:** `environment.provisioned`, `ci.pipeline`, `build.artifact`, `deployment.manifest`, `infra.drift.report`, `cost.report`, `environment.decommissioned`.

```json
// environment.provisioned
{ "env_id": "env-pr512", "tier": "ephemeral", "ttl_s": 86400,
  "endpoints": { "api": "https://pr512.stg.example", "ui": "https://pr512.ui.example" },
  "secrets_mode": "vault-oidc", "provenance": { "commit": "…", "attestation": "oci://…" } }

// deployment.manifest (fragment)
{ "env": "staging", "release_id": "REL-118", "images": { "orders": "oci://…@sha256:…" },
  "signatures_verified": true, "sbom_attached": true, "policy_bundle": "opa@v14" }
```

**Artifacts:** IaC repo, pipeline definitions, environment records, cost reports (single-writer each).

## 4. Decision logic & autonomy boundaries
1. **Environment tiers:** ephemeral per PR (auto, TTL-based cleanup) and staging (auto) = L2; production infrastructure changes = L3 (change ticket with blast-radius analysis + rollback plan).
2. **Pipeline law:** all builds go through the A11 pipeline definitions; any `build.artifact` without provenance attestation is rejected (self-gate, fail-closed).
3. **Drift handling:** GitOps drift → auto-reconcile (L2); if drift persists 3 reconciles ⇒ freeze deploys for that env + ESCALATED.
4. **Cost guardrails:** ephemeral env budget cap; environment exceeding 120 % of modeled cost ⇒ auto-downscale + notify A03/A13.
5. **Secrets hygiene:** rotation schedules automated; any scan hit of a static credential in infra code = immediate block (policy from A10).
6. **Boundary:** never merges code PRs; never promotes releases (A12); never changes security policy (A10); production access is via pipeline identities only, never interactive.

## 5. Error handling & fallbacks
- **IaC apply failure:** automatic state diagnosis (refresh/plan diff), retry ×1, then rollback to last-good state snapshot; post-mortem note.
- **Registry/CDN outage:** builds queue; deploys blocked (fail-closed) with `E-DEP`.
- **K8s node pressure:** preemption order defined per env tier (ephemeral first, prod last); drain automation.
- **Credential provider outage:** short-lived credential cache (≤ 15 min TTL) keeps non-prod running; prod deploys halt (fail-closed).

## 6. Performance metrics
- Environment provision time: ephemeral P95 < 10 min; staging < 25 min.
- Pipeline success ≥ 95 %; MTTR for infra failures < 30 min; drift MTTR < 15 min.
- Cost per ephemeral env within 110 % of model; 100 % artifacts carry provenance.
- Deploy lead time (merge→staging) P95 < 30 min.

## 7. Security & compliance
- Zero standing credentials (OIDC everywhere); break-glass procedure is human-only and alarmed.
- Change management: every prod infra change has recorded plan/diff/rollback (compliance evidence); SOC 2 change-management controls mapped automatically.
- Network policy: agents' CI runners egress-allowlisted; pipeline poisoning mitigations (locked actions, digest-pinned base images, provenance verification on consume).
- Multi-tenant isolation: env namespaces with RLS-aligned labels per A07 classification.