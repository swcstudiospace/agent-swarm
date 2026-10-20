---
name: "a24-cluster-security--exception-lifecycle"
description: "Use when a policy exception register must be audited for expiry, lifetime, review and approver hygiene, or a new exception is requested and must go to a human."
disable-model-invocation: true
---

# Policy exception lifecycle

## When to Use

- A periodic register review, or a request to exempt a workload, namespace or subject from a cluster policy (PSA exemption, Kyverno `exclude`, Gatekeeper `excludedNamespaces`, a webhook `namespaceSelector`).
- A24 audits the register and prepares the request; it NEVER grants, extends or accepts an exception: `exception_grant` is L4, human only. Software risk acceptance stays with A10 (`skill://a10-security--risk-exception`).

## Procedure

1. Read the register with `read` (JSON list of `{id, policy, scope, owner, approved_by, granted_at, expires_at, last_reviewed_at}`). Take `now` from `date -u +%Y-%m-%dT%H:%M:%SZ` through `bash`; every timestamp needs an explicit offset.
2. Call `kpol_exception_register_check` with `exceptions`, `now`, `warn_days` 14, `max_duration_days` 90, `review_interval_days` 30 (these are this skill's defaults; the task's values win). Read `exception_count`, `active_count`, `expired_count`, `expiring_soon_count`, `no_expiry_count`, `earliest_expiry`, `finding_count`, `verdict`.
3. Rules by `kpol_exception_register_check` finding id: `exception.expired` (major): not active, recommend removal; `exception.no-expiry` (major): a permanent waiver, return it for an end date; `exception.too-long` (major): lifetime above `max_duration_days`; `exception.no-approver` and `exception.self-approved` (major): invalid, the entry does not count as approved; `exception.stale-review` and `exception.expiring-soon` (minor): schedule a review. Check `active_count` equals `exception_count - expired_count`; a mismatch is E-CONTRACT.
4. Cross-check against the policies: call `kpol_admission_policy_lint` with `policies` and compare every `admission.namespace-exclusion`, `admission.identity-exclusion` and `admission.object-selector-exclusion` location with a register `scope`. An exclusion without a register entry is an unregistered exception: a finding for A10; a register entry without a matching exclusion is stale.
5. Call `shared_open_findings` with `{task_id}` and note any open gate finding that cites an exception id; those cannot be closed by expiry alone.
6. For a new or extended exception, do not edit the register. Prepare the request: policy, scope, owner, the finding id it covers, compensating control, proposed `expires_at` (at most `max_duration_days` from `granted_at`) and review date. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "exception request <policy> / <scope>, L4", needs: ["human-approval", "named approver other than owner"]}` and stop; the answer is BLOCKED until a human replies.
7. Write the audited register view (status per id: active, expiring, expired, invalid) to `cluster-reports/<cluster>/exceptions.md` with `write`, digest it with `shared_artifact_digest`.

## Outputs

- `policy.exception.register`: status per entry, counts, `earliest_expiry`, unregistered exclusions, pending L4 requests.
- `cluster.security.findings` for each major exception finding.

## Failure Handling

- INVALID_INPUT (a timestamp without offset, `expires_at` before `granted_at`): fix the named field in your copy of the input or BLOCKED needs=input. Never alter the register to make it valid.
- PARTIAL_SUCCESS: use `completed_fraction`; name unchecked entries. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Removing an expired exclusion tightens production policy: L3 through A22. Deleting an entry or a policy: L4. Never retry an escalation; E-CONTRACT when register and policies disagree.

## Handover

`to_agent` A10 (risk owner; A14 gets the same artifact for the expiry and debt register). Fill `goal`, `completed`, `decisions`, `artifacts` (register view and digest), `blockers` (`human-approval` per request), `open_questions` (owners to confirm). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a24-cluster-security/exception-lifecycle.md; edit the source and run scripts/build_agents.py -->
