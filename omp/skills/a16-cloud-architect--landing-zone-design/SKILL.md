---
name: "a16-cloud-architect--landing-zone-design"
description: "Use when a cloud.landing_zone task needs an account/OU hierarchy, guardrail set and break-glass design lint-clean before A11 builds it as IaC."
disable-model-invocation: true
---

# Landing zone design

## When to Use

- A `cloud.landing_zone` task asks for the multi-account layout of a new or changed cloud organization (accounts, OUs, guardrails, emergency access).
- Not for choosing region pairs or DR (`skill://a16-cloud-architect--dr-strategy-selection`), IP plans (`skill://a16-cloud-architect--network-topology-plan`) or writing Terraform (A11 does that from your spec).
- Autonomy: the design is L2. Naming a new provider or adding or removing an allowed region is L3. A provider commitment or exit plan is L4 and never decided here.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the `architecture.blueprint` (A03) and `requirements.spec` (A02). Extract provider, candidate regions, environments and compliance zones. Anything missing is `shared_escalate` with `reason_code: "ambiguous-input"`, never a guess.
2. Use `read` and `glob` on the existing IaC and account inventory, then draft the account list, one row per account with `id`, `role` (`management`, `security_audit`, `log_archive`, `network`, `shared_services`, `workload_prod`, `workload_nonprod`, `sandbox`), `ou` path (`Security`, `Infrastructure`, `Workloads/Prod`, `Workloads/NonProd`, `Sandbox`; `""` only for the management account), `root_mfa` (true when root MFA is on or the root credentials were removed centrally), `iam_users` (0 target: humans use federated SSO) and `guardrails` keys. Every non-management account needs `deny-leave-organization`, `deny-root-user-actions`, `protect-audit-logging`, `restrict-regions`; `workload_prod` also `deny-public-storage`, `require-encryption-at-rest`; `log_archive` also `deny-log-deletion`; `sandbox` also `budget-alarm`.
3. Call `cloud_landing_zone_lint` with `{accounts, break_glass: {configured, hardware_mfa, alert_on_use, last_tested_days_ago}}`. Take the break-glass fields from the task; an unstated field is BLOCKED `needs=input`, never `true`.
4. Read `verdict`, `findings`, `guardrail_coverage_pct`, `ou_count`. Any `blocker` or `major` (`no-log-archive-account`, `root-mfa-missing:*`, `prod-nonprod-shared-ou:*`, `guardrail-missing:*`, `break-glass-*`) means the design is wrong: fix it and call again. Accept the spec only with `verdict` `pass` or `warn` and `guardrail_coverage_pct` 100; the tool counts the management account out of that figure.
5. Cross-check the region guardrail: call `cloud_data_residency` with `regions` (every region in `restrict-regions`), `rules` (audit logs: the jurisdictions the task allows) and `placements` (the audit log copy in `log_archive`, `kind: "log"`). `violation_count` must be 0; otherwise the allowed-region list and the log archive region disagree: fix the design, do not edit the rule.
6. Write the spec with `write` at the path the task names, else `docs/cloud/landing-zone.md`: the account table, OU tree, guardrail keys per role, allowed regions, break-glass test cadence (the lint flags `break-glass-untested` past 180 days), and the lint inputs and outputs verbatim. State it as a recommendation: no account is created or policy attached by you.
7. When provider or allowed regions differ from the blueprint, call `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]` (L3). Any commitment or exit-plan question goes in `open_questions` for an L4 human.
8. Call `shared_artifact_digest` with `{path}` of the spec, then `shared_handover_validate` (`draft` true) and `shared_handover_write`.

## Outputs

- `landing.zone.spec`: accounts, OUs, guardrails, regions, break-glass, plus the `cloud_landing_zone_lint` and `cloud_data_residency` results as evidence.

## Failure Handling

- `INVALID_INPUT`: fix the named field (for example an unknown `role`) or BLOCKED `needs=input`.
- `PARTIAL_SUCCESS`: use `completed_fraction` and name the unchecked accounts in `open_questions`.
- `DEPENDENCY_UNAVAILABLE`: retry once, then record `skipped:tool-missing` and do not call the spec lint-clean.
- Guardrail key names are the tool's own vocabulary; mapping them to a provider's SCP, org policy or Azure Policy syntax is A11's job: say so, do not invent policy JSON.
- Autonomy L3 or L4 actions end BLOCKED `needs=human-approval`; do not retry an escalation.
- The lint and the residency check disagreeing on regions is E-CONTRACT: report both and stop.

## Handover

`to_agent` A11 (builds IaC from the spec); write a second one to A17 (platform onboarding). Fill `goal`, `completed`, `decisions` (account roles, guardrails, L3 asks), `artifacts` (kind `landing.zone.spec`, `uri`, `digest`, `producer` A16), `open_questions`, `verification` (`partial` when any tool was skipped). End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a16-cloud-architect/landing-zone-design.md; edit the source and run scripts/build_agents.py -->
