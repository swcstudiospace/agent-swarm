---
name: a20-finops--storage-lifecycle
description: Use when a finops.model task asks to cut storage cost by tier moves, lifecycle rules and current-generation volumes, net of transition and retrieval cost.
owner: a20-finops
tools: [finops_storage_tiering, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Storage lifecycle and tiering

## When to Use

- Object, log or volume storage is growing and the task wants lifecycle rules, tier moves and volume type changes with their break-even.
- Not for idle volumes nobody uses (`skill://a20-finops--idle-cleanup-proposal`) or for data-transfer cost (`skill://a20-finops--egress-reduction`).
- Autonomy: the study is L2. Writing the lifecycle rules into IaC is A11's change (L3 in production). Expiration rules delete data: that is a retention decision for the data owner and a human (L4); A20 proposes tiers and never an expiry.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A16 `cloud.architecture` (data classes, residency, retention and durability needs). A dataset with legal retention or residency constraints keeps those; if they are not stated, BLOCKED `needs=input`.
2. Run `python3 scripts/finops_cost_scan.py --task-id $TASK --correlation-id $CORR --root <repo> --path <iac-dir> --required-tags "" --json` (empty `--required-tags` switches the tag check off; `skipped:no-required-tags` is then expected). Read findings by `rule`: `iac-missing-lifecycle` (bucket without lifecycle rules, log group without retention, image repository without lifecycle policy) and `iac-previous-generation-volume` (`gp2`, `io1` or `standard`, also in StorageClass parameters of AWS EBS provisioners). Both are minor; `fail` also comes from `scan-incomplete`. Any expiry is the data owner's decision, never an A20 proposal.
3. For volumes, use the `iac-previous-generation-volume` findings of `scripts/finops_cost_scan.py`: `standard` is the type AWS calls previous generation; `gp2` to `gp3` and `io1` to `io2` are newer-type advice, not a deprecation. Price and performance of the target come from the task's rate card and spec: check that provisioned IOPS and throughput still fit. Recommend to A11 with the finding's `id` and `location`; apply only from a plan or diff after L3 approval.
4. For data tiers, take the rate card from the task: 2 to 10 tiers with unique `name`, `storage_per_gb_month`, `retrieval_per_gb`, `min_storage_days` (the early-deletion window, 0 if none) and `transition_in_per_gb` (fold per-request transition fees into it). Per dataset (1 to 200): `name`, `size_gb`, `monthly_read_gb` (measured from access logs, never assumed), `retention_days` (days the data stays from now) and `current_tier`. A missing access pattern is BLOCKED `needs=input`, or offer the rate card's automatic tiering as the alternative and say why.
5. Call `finops_storage_tiering` with `tiers` and `datasets`. A tier is eligible only when `retention_days` is at least its `min_storage_days`; the best eligible tier other than the current one has the largest net benefit (monthly saving x `retention_days` / 30 minus transition cost) and the dataset moves only when that is above 0. The tool does not model an early-deletion charge for leaving the current tier (the data's age there is not an input): when the rate card lists one and the data is younger than that window, fold it per GB into the target's `transition_in_per_gb` and say so. Read `recommended_tier` (`current_tier` when no move), `monthly_saving`, `transition_cost`, `payback_months`, `move_count`, `verdict` (`tiering_available` or `optimal`).
6. Cross-check the `finops_storage_tiering` rows: for a moved dataset `monthly_cost_now - monthly_cost_after` equals `monthly_saving` and `payback_months` equals `transition_cost / monthly_saving`, within rounding; unmoved datasets show 0. A `payback_months` above 6 is flagged for the owner (a convention of this skill); one at or above `retention_days / 30` cannot occur for a moved dataset: if it does, E-CONTRACT.
7. Re-run `finops_storage_tiering` with `monthly_read_gb` at 2x and 4x for each moved dataset. A `recommended_tier` that changes (back to `current_tier` or to another tier) is fragile: mark it "moves only if reads stay below X GB per month" with X the largest tested read volume that still gave the same tier.
8. Write the `cost.report` with `write` at the path the task names, else `docs/finops/storage-<scope>.md`: lifecycle gaps with file and `location`, the tier table, the volume changes, savings net of transition, and the draft transition days. Production data moves need approval: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "apply production storage tier moves", "needs": ["human-approval"]}`.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `cost.report` (storage section): lifecycle and volume findings, tier recommendations with payback, sensitivity; evidence is the script counts and `finops_storage_tiering` outputs.

## Failure Handling

- INVALID_INPUT: fix the named field (`current_tier` must be a listed tier, unique tier and dataset names, 2 to 10 tiers) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name datasets without access data.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Script `skipped` entry `yaml-manifests` (PyYAML missing) is a named gap; exit 2 means fix `--path`.
- Minimum object sizes, retrieval latency classes and early-deletion terms are provider facts: defer to the rate card supplied and flag anything not in it.
- L3 actions end BLOCKED `needs=human-approval`; never propose a delete or expiry as an A20 action.

## Handover

- `to_agent`: `A16` (consumes `cost.report`; the design owner confirms durability and retention). `goal`: approve tiers, then A11 implements.
- `decisions`: tiers and the retention assumed; `artifacts`: report digest, producer `A20`; `open_questions`: missing access data; `verification`: `partial`.
- End with `shared_handover_validate` then `shared_handover_write`.
