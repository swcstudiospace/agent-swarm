---
name: a27-contract-audit--static-analysis-triage
description: Use when Slither or Aderyn output must become deduplicated, SWC-classified, contextually scored findings with false positives recorded.
owner: a27-contract-audit
tools: [audit_classify_vulnerability, audit_dedupe_findings, audit_score_severity, shared_verdict, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- An `audit.static` task where an analyzer report exists or must be produced, after `skill://a27-contract-audit--audit-scoping` fixed `scope_commit`.
- Not for exploit proof (`skill://a27-contract-audit--finding-writeup-poc`) and not for a PR gate verdict (A10 `sec_gate`).
- Triage is L2. A severity different from the computed one is L3; accepting a finding is L4 and never yours.

## Procedure

1. Run the analyzers read-only with `bash`: `slither . --json audit/slither.json --filter-paths "lib|node_modules"`, plus Aderyn if installed. Record `slither --version`. A missing binary is `skipped:tool-missing`; never write results you did not receive.
2. `read` the JSON. Each `results.detectors[]` entry has `check`, `impact`, `confidence` and `elements[].source_mapping` (file, lines). Map a detector to a signal of `audit_classify_vulnerability` only when the mapping is obvious (for example `reentrancy-eth` to `reentrant-external-call`, `tx-origin` to `tx-origin-auth`, `suicidal` to `unprotected-selfdestruct`, `controlled-delegatecall` to `delegatecall-untrusted`, `arbitrary-send-eth` to `unprotected-ether-withdrawal`, `unchecked-lowlevel` to `unchecked-call-return`, `solc-version` to `outdated-compiler`). Slither's `pragma` detector means "different pragma directives are used", not a floating pragma: send it to manual review and use `floating-pragma` only for a `^`/`>=` pragma you read yourself. Detector names change between versions: an unmapped detector goes to manual review, never to a forced signal.
3. Call `audit_classify_vulnerability` with `observations` = `[{id, signal, location}]` (at most 200, ids unique). `counts` and `swc_ids` give the default classes; the default severity is a starting point only.
4. Call `audit_dedupe_findings` with `findings` = `[{id, title, category, file, severity, function, root_cause}]`. Keep each group `primary_id`, fold `duplicate_ids` into it. Check `unique_count` + `duplicate_count` = `input_count`. `severity_conflict` true: keep the higher severity, record the conflict, and do not resolve it yourself (L3).
5. Read every remaining high or critical candidate with `read`, trace the call path, and decide: exploitable, or false positive with the reason (access guard, unreachable, trusted caller) written down. Detector `confidence` is a hint, not a reason.
6. Call `audit_score_severity` for each confirmed finding with `loss_usd`, `tvl_usd` (from the scope manifest or deploy plan, else BLOCKED `needs=input`), `attacker_privilege`, `preconditions`, `irrecoverable`. Do not pass `proposed_severity` unless a human proposed one. The returned `severity` is the finding severity; `impact` and `likelihood` go into the finding. `requires_l3_approval` true means the proposed value differs from the computed one: BLOCKED `needs=human-approval` (L3), never apply it yourself.
7. Call `shared_verdict` with `severities` = the final severities mapped to the gate scale (this skill's convention: info to `info`, low and medium to `minor`, high to `major`, critical to `critical`). `fail` must coincide with a high or critical finding; a mismatch is E-CONTRACT.
8. `write` the findings file `audit/static-findings.json` (`findings` list with `id, title, severity, category, location, description, recommendation, status`, plus `impact`, `likelihood`). Run `python3 scripts/audit_findings_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path audit/static-findings.json --json`. Exit 0 is `ok`, exit 1 is `fail` (a `major` or `blocker` in `findings[].severity`, including `scan-incomplete` for a skipped, truncated or unparseable input or zero analysed input), exit 2 is an input error (a `--path` that is or passes through a symlink is refused). Its `by_severity` must equal your own counts. Digest the file with `shared_artifact_digest`.

## Outputs

- `audit.findings` (static part): findings file with SWC class, contextual severity and `impact`/`likelihood`, plus the list of false positives with reasons.
- Evidence: `counts`, `unique_count`, `by_severity`, script exit code.

## Failure Handling

- INVALID_INPUT (duplicate observation id, unknown signal, `loss_usd` above `tvl_usd`): fix the named field; unknown signal means manual review, not a guess.
- PARTIAL_SUCCESS: if one analyzer ran, say which and which detectors were not covered.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Script `severity-mismatch` (major): the severity differs from `impact` x `likelihood`; rescore, never edit the number.

## Handover

- `to_agent`: `A26` (fix owner), or `A10` when a gate is pending. `artifacts`: findings file with digest. `decisions`: false positives and why. `open_questions`: unmapped detectors. `verification`: verified when the step 8 counts agree.
- End with `shared_handover_validate`, then `shared_handover_write`.
