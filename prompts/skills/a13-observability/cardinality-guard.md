---
name: a13-observability--cardinality-guard
description: Use when a new metric or label set is proposed and its worst-case series count must be checked against the series budget before it ships.
owner: a13-observability
tools: [obs_cardinality_estimate, shared_handover_validate, shared_handover_write]
---

## When to Use

- A change adds or alters a metric, or adds a label to an existing one, and the series budget may be at risk.
- This audits series shape only. Alert rules and runbook links are `skill://a13-observability--alert-hygiene`.
- Raw customer PII never enters labels; telemetry comes from the scrubbed pipeline only (spec section 7). If a proposed label looks like a user identifier, say so in the report and do not ask for raw values.

## Procedure

1. Use `grep` over the change (metric definitions or the instrumentation diff) for each label key and its allowed values; for open-ended labels such as ids, use the task-supplied distinct-value count.
2. Build `labels` as objects `{label, distinct_values}` (integer, at least 1, label names unique, at most 50 entries) and take `series_budget` from the task input. If no budget is given, stop with an open question; do not assume one.
3. Call `obs_cardinality_estimate` with `labels` and `series_budget`. Read `series_estimate` (product of distinct values), `exceeds_budget` and `highest_cardinality_label`.
4. If `exceeds_budget` is true, name `highest_cardinality_label` as the first drop candidate. Call `obs_cardinality_estimate` again with that label removed (or its `distinct_values` replaced by a bounded bucket count) to show whether the estimate falls within budget.
5. From the two `obs_cardinality_estimate` results, state the verdict per label: keep, bucket or drop, with the estimate before and after. The tool reports an estimate; the change author decides.
6. Call `shared_handover_validate`, then `shared_handover_write` to the change author.

## Outputs

- Label verdict: estimate with all labels, estimate after the proposed drop or bucketing, budget and result. No code or rule is changed by this skill.

## Failure Handling

- INVALID_INPUT on `labels` (empty list, more than 50 entries, duplicate label names or `distinct_values` below 1): fix the list from the change; split metrics with more than 50 labels.
- DEPENDENCY_UNAVAILABLE: retry twice, then record the check as `skipped:tool-missing`; a product you multiply yourself is noted as unverified, never reported as the finding.
- Distinct-value counts unknown: record them as open questions; an estimate with a guessed count is not reported as fact.

## Handover

- `to_agent`: the agent that proposed the metric (the task assigner; `A01` when unknown, often A05 or A06 in practice). `goal`: apply the label verdict.
- `decisions`: keep, bucket or drop per label with the estimate. `remaining`: apply the change. `open_questions`: unknown counts. `verification`: verified when the estimate was tool-computed.
- Tools: `shared_handover_validate` then `shared_handover_write`.
