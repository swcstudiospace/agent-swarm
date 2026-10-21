---
name: a03-architect--tech-stack
description: Use when a technology must be picked from the approved catalog, or a catalog miss needs an ADR, with supply-chain policy and L3 escalation.
owner: a03-architect
tools: [shared_handover_read, arch_adr_slug, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- Producing or revising `tech.stack.selection` for a blueprint, or when a task needs a library, datastore or service not yet selected.
- Rule (03-agents/A03 section 4.1): choose from the approved catalog's golden path first; a catalog miss is proposed through an ADR, L3 when it brings new infrastructure or a new first-party dependency.
- The ADR mechanics are in `skill://a03-architect--adr-record`; this skill decides what to pick and which supply-chain evidence to require.

## Procedure
1. Call `shared_handover_read` with `{task_id}`; take `infra.constraints`, `security.policy` and the blueprint from `artifacts`. The approved catalog is whatever `infra.constraints` attaches; none attached means add that to `open_questions` and never invent catalog entries.
2. Use `read` on the catalog and match each concern (runtime, datastore, messaging, auth) to its golden-path entry.
3. For a miss, call `arch_adr_slug` with `{title}` and follow `skill://a03-architect--adr-record` to write and status the ADR.
4. Mark L3 when the pick adds infrastructure or a first-party dependency: leave the ADR `proposed`, call `shared_escalate` with `{task_id, reason_code: policy, summary, needs: ["human-approval: novel_infrastructure"]}`, and return its `result_patch` as BLOCKED after the remaining steps.
5. For each pick use `write` to add the supply-chain rows: SBOM required, dependency allowlist entry, licence compatibility result, risk-feed (OSV) status. Evidence you did not obtain stays marked `unknown`.
6. Put environment or infrastructure change proposals for A11 into the `remaining` of a `shared_handover_write` to A11 (step 8); never apply them yourself.
7. Call `shared_artifact_digest` with the `tech.stack.selection` `path`.
8. Call `shared_handover_validate` (`draft` true) and `shared_handover_write`.

## Outputs
- `tech.stack.selection`: concern, choice, golden path or ADR id, supply-chain rows, level (L2 or L3).

## Failure Handling
- Licence or allowlist conflict: do not pick; record it in `blockers` and route to A10.
- ADR script or status errors: handled in `skill://a03-architect--adr-record`.
- Catalog missing: `shared_escalate` with `reason_code` `ambiguous-input`.
- Never accept security risk; that is A10.

## Handover
Fill `goal`, `completed`, `decisions` (each pick, rationale, ADR id), `artifacts` (stack file and ADRs, digests, `producer` A03), `constraints` (allowlist, licence matrix), `blockers` (L3 approvals), `open_questions` and `verification` (`status`, `confidence`, `checks`). Write once to A10 (dependency review) and once to A11 (infrastructure proposals).
