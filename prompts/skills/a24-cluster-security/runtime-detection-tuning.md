---
name: a24-cluster-security--runtime-detection-tuning
description: Use when Falco rules are noisy, missing fields or disabled and alert counts must be turned into scoped exceptions without losing detections.
owner: a24-cluster-security
tools: [kpol_runtime_rule_lint, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Runtime detection tuning (Falco)

## When to Use

- A `kpol.runtime` task: a rule file needs review, alert volume is too high, or a detection was disabled and its replacement must be checked.
- Tuning a rule's exceptions is L2 as a proposal; rolling the changed rules out to production sensors is L3; disabling a detection is L3 and never done to quiet noise alone. Incident response is `skill://a24-cluster-security--cluster-incident-containment`; audit logs are `skill://a24-cluster-security--audit-logging-setup`.

## Procedure

1. Parse the Falco rules files from the task into a list of items (`rule`, `macro`, `list`) with `bash` or `eval` (YAML to JSON, nothing else); keep the file name per item. Collect alerts per rule over a fixed window from the task's exported alert counts: `[{rule, count}]` and `window_hours` (for example 168). No counts: lint structure only and say noise is unmeasured.
2. Call `kpol_runtime_rule_lint` with `rules` and, when counts exist, `alert_counts`, `window_hours` and `max_alerts_per_hour` (the task's value; if none, 5 per hour per rule is this skill's default, stated). `window_hours` is required together with `alert_counts`. Read `rule_count`, `macro_count`, `noisy_rules`, `finding_count`, `verdict`.
3. Cross-check the `kpol_runtime_rule_lint` counts: `rule_count` and `macro_count` must equal the number of `rule` and `macro` items you parsed; `append: true` and `override` items are not independent rules, so a difference equal to their number is noted, any other difference is E-CONTRACT.
4. Order the `kpol_runtime_rule_lint` findings by severity: blockers first (`falco.missing-field` for a missing `desc`, `condition`, `output` or `priority` on a non-append rule; `falco.invalid-priority`, a value outside EMERGENCY, ALERT, CRITICAL, ERROR, WARNING, NOTICE, INFORMATIONAL, DEBUG, with INFO accepted as an alias of INFORMATIONAL; `falco.exception-shape` with names, `fields`, `comps` and `values` that disagree). Then `falco.noisy` (minor, major beyond 10 times the limit), `falco.disabled-detection` (minor: a rule of priority ERROR or higher has `enabled: false`), `falco.no-evt-type` (every syscall is evaluated), `falco.duplicate-rule`.
5. For each noisy rule, find the top benign producer in the sample events with `grep` (container image, process name, namespace). Propose a Falco `exceptions:` entry on that rule (fields, comps, values), not a weakened `condition` and not `enabled: false`. A proposed exception must name the single workload and cover less than the whole noise; list what stays alerting.
6. Re-lint the edited rules file with `kpol_runtime_rule_lint` (same `rules`, `alert_counts` unchanged). The finding set must shrink only by the intended ids; a new blocker means the proposal is invalid. The expected rate after tuning is computed only from the excluded events actually present in the sample, never estimated.
7. Roll out: staging sensors first (L2), then production via A22 (L3). Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "Falco rule changes to production sensors, L3", needs: ["human-approval"]}` for production or for any disabled rule. Write the diff and lint results to `cluster-reports/<cluster>/runtime-rules.md` with `write` and digest them with `shared_artifact_digest`.

## Outputs

- `cluster.policy.set` (runtime variant): changed rules and exceptions with before/after lint counts.
- `cluster.security.findings`: remaining lint findings, noisy rules, disabled detections.

## Failure Handling

- INVALID_INPUT (`window_hours` missing with counts, negative counts, a non-object item): fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; name unlinted files. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The lint is structural: it does not parse the Falco condition grammar and cannot prove a condition matches a real event. Falco version differences (macro and exception semantics) must be checked with `falco --validate <file>` where available; if not, say unproven.
- Never retry a rollout; L3 or L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A22 (rollout of the rule files). A10 gets the findings. Fill `goal`, `completed`, `decisions` (exception per rule, threshold used), `artifacts` with digests, `blockers`, `verification` (lint before and after). Call `shared_handover_validate`, then `shared_handover_write`.
