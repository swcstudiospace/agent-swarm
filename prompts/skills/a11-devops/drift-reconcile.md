---
name: a11-devops--drift-reconcile
description: Use when an infra.drift.alert reports GitOps drift and you must reconcile it, or freeze deploys after the third persisting reconcile.
owner: a11-devops
tools: [devops_env_tier, devops_drift_count, shared_scratchpad_get, shared_scratchpad_put, shared_escalate, shared_handover_validate, shared_handover_write]
---

# Drift reconcile

## When to Use

- `infra.drift.alert` from A13 or from yourself for one environment.
- Rule (03-agents/A11-devops.md section 4.3): auto-reconcile at L2; drift that persists across 3 reconciles freezes deploys for that environment and ends ESCALATED. Spend, not drift, is `skill://a11-devops--cost-guardrail`.

## Procedure

1. Call `devops_env_tier` with `{"env_name": "<name>"}`. `production` is L3: do not reconcile; hand the change to `skill://a11-devops--prod-infra-change`.
2. Read the previous streak (prior consecutive reconciles that still reported drift): call `shared_scratchpad_get` with `{correlation_id, "key": "drift-streak.<env_id>"}` (key characters `[a-z0-9._-]`, at most 64; lower-case the env id). If `found` is false, use the count in the alert payload; if it has none, use 0 and add an `open_questions` entry saying the streak was unknown. The scratchpad is per correlation: a streak recorded under another correlation is invisible here.
3. Build `desired` (Git manifests) and `actual` (live state) as `{key, value}` lists with unique keys from the alert payload, then call `devops_drift_count` with `{"desired": [...], "actual": [...], "drift_streak": <n>}`. Note `drift_count`, `drifted_keys`, `action`.
4. `action` `none`: call `shared_scratchpad_put` with `{correlation_id, "key": "drift-streak.<env_id>", "value": {"streak": 0}}` and report no drift.
5. `action` `auto-reconcile`: sync the environment from Git on a non-prod tier through the GitOps controller (for example `argocd app sync <app>`; if no controller CLI is available, BLOCKED `needs=tool`). Your guard refuses `helm upgrade`, `terraform apply` and prod `kubectl` changes. Then repeat step 3 with the new live state. If drift remains, store `streak + 1` with `shared_scratchpad_put` and `if_version` set to the version you read (0 when the key did not exist).
6. `action` `freeze-and-escalate`: call `shared_scratchpad_put` with `{correlation_id, "key": "drift-freeze.<env_id>", "value": {"frozen": true, "streak": <n>}}`, then `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "drift persisted 3 reconciles, deploys frozen for <env_id>", "needs": ["unfreeze decision"]}` and return its `result_patch`. The freeze must also reach readers outside this correlation: state `drift-freeze <env_id>` in the handover `blockers`.
7. Assemble `infra.drift.report`, then call `shared_handover_write` (see Handover).

## Outputs

- `infra.drift.report`: `env_id`, `drift_count`, `drifted_keys`, `action`, `streak`, `frozen`.
- Scratchpad keys `drift-streak.<env_id>` and (on freeze) `drift-freeze.<env_id>`.

## Failure Handling

- INVALID_INPUT `keys must be unique`: dedupe the alert payload once, then BLOCKED `needs=input`.
- Scratchpad conflict (`if_version` mismatch): re-read and retry once.
- DEPENDENCY_UNAVAILABLE on `devops_drift_count`: retry twice; never report drift as clear unchecked.
- Reconcile apply failure: `skill://a11-devops--apply-recovery`.

## Handover

Fill `goal` (observe the environment after reconcile or lift the freeze), `completed` (reconcile result), `remaining` (verify drift MTTR under 15 minutes), `decisions` (`action`, streak), `blockers` (`drift-freeze <env_id>` when frozen), `constraints`, `verification`. Call `shared_handover_validate`, then `shared_handover_write` to A13 (A01 when escalated).
