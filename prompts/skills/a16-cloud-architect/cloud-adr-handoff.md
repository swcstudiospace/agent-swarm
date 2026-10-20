---
name: a16-cloud-architect--cloud-adr-handoff
description: Use when a cloud decision (region, DR, managed service, failure domain) needs A03 to record an ADR; package it with re-verified numbers, never edit A03 files.
owner: a16-cloud-architect
tools: [cloud_composite_availability, cloud_dr_feasibility, cloud_blast_radius, shared_handover_read, shared_escalate, shared_artifact_digest, shared_artifact_check, shared_handover_validate, shared_handover_write]
---

# Cloud ADR handoff

## When to Use

- You reached a cloud placement decision (which regions, which DR strategy, managed or self-hosted, how failure domains are split) that has architectural consequences and must live in the ADR log.
- A03 owns ADRs, API contracts and the blueprint: this skill produces a decision package and a change request, never an ADR file and never an edit under A03's paths (`docs/adr/`, contract files). To record the ADR itself see A03 `skill://a03-architect--adr-record`.
- Autonomy: the package is L2. A decision that adds a provider or region is L3; one that commits spend or sets an exit plan is L4: mark it `proposed` and BLOCKED `needs=human-approval`.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the blueprint you decided against, and `read` the existing ADR index (`docs/adr/`) with `glob`/`grep` for the same topic. A conflicting accepted ADR is not overridden: the package states `supersedes: <ADR id>` as a request to A03.
2. For each decision write: title, context, drivers (the NFR or incident that forced it), options considered (at least two), chosen option, consequences (good, bad, operations cost) and reversibility. A decision without a rejected alternative is not packaged.
3. Re-verify every quantitative claim with the tool that backs it, using the final inputs rather than earlier ones: availability claims through `cloud_composite_availability` (`target_pct`, `tiers`), recovery claims through `cloud_dr_feasibility` (`strategy`, `rto_target_min`, `rpo_target_min` and the timing fields), isolation claims through `cloud_blast_radius` (`services`, `max_az_blast_pct`). Copy `meets_target`, `meets_rto`/`meets_rpo`, `worst_blast_pct` and `verdict` into the package; a claim whose tool says `fail` is a rejected option, not a consequence.
4. Cross-check against the blueprint: the services, tiers and regions in your inputs must match the A03 blueprint exactly. A mismatch (an extra region, a renamed service) is E-CONTRACT: call `shared_escalate` with `reason_code: "conflict"` and both versions; no package is written until it is resolved.
5. Classify the level per decision: within current provider and regions L2; new region or provider L3; commitment or exit plan L4. Put the classification in the package and, for L3 or L4, call `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]`.
6. Write the package with `write` at the task path, else `docs/cloud/decisions/<topic>.md` (outside A03 paths): one section per decision with the fields of step 2, the step-3 tool outputs, the level, and a `Requested A03 action` line (`record new ADR`, `supersede ADR-NNNN`, or `blueprint change request: <what>`). A blueprint or contract change is a request only.
7. Call `shared_artifact_digest` with `{path}` of the package, then `shared_artifact_check` with `{task_id, outputs}`; `ok` must be true and the producer A16.
8. Call `shared_handover_validate` (`draft` true) and `shared_handover_write` with `to_agent` A03.

## Outputs

- `cloud.architecture` decision package: decisions, alternatives, re-verified evidence, levels and the requested A03 actions.

## Failure Handling

- `INVALID_INPUT`: fix the named field or BLOCKED `needs=input`. `PARTIAL_SUCCESS`: use `completed_fraction`; unchecked claims are listed as unverified in the package.
- `DEPENDENCY_UNAVAILABLE`: retry once, then record `skipped:tool-missing`; the decision stays `proposed`.
- A re-verification result that differs from the earlier figure is reported with both values; never reuse the old figure.
- Never retry an L3 or L4 escalation; never edit an ADR or contract file.
- ADR numbering and status transitions belong to A03's tools; do not number or accept anything yourself.

## Handover

`to_agent` A03. Fill `goal` (record these decisions), `completed`, `decisions` (each with rationale), `artifacts` (kind `cloud.architecture`, uri, digest, producer A16), `blockers` (L3/L4 approvals), `open_questions`, `constraints` (A03 files untouched), `verification`. End with `shared_handover_validate` then `shared_handover_write`.
