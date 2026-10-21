---
name: "a27-contract-audit--finding-writeup-poc"
description: "Use when a confirmed vulnerability candidate needs a reproducible proof-of-concept test, a scored severity and a schema-valid entry in the findings file."
disable-model-invocation: true
---

## When to Use

- A candidate from triage, an invariant counterexample, a fuzz failure or an economic model that now needs to become a finding.
- Candidate discovery is the review skills (for example `skill://a27-contract-audit--static-analysis-triage`). Re-checking a fix is `skill://a27-contract-audit--fix-verification-reaudit`.
- You `write` only the findings file, reports and PoC tests under `audit/`; contract source stays untouched. PoC runs are local (Foundry, anvil); a mainnet fork only through an RPC alias the human provides in the environment, never a literal URL or key. Severity changes are L3.

## Procedure

1. State the exploit in four lines in a `write` draft (`audit/drafts/<ID>.md`): preconditions, attacker steps, effect, who loses what. A candidate that cannot be stated this way is not a finding: keep it in `open_questions`.
2. If it involves an external call, build the trace and call `audit_reentrancy_order` with `functions` (`name`, `non_reentrant`, `steps` of `{kind, target, trusted}`); the `REENT-` finding it returns must match your four-line story, else the story or the trace is wrong.
3. `write` the PoC as a Foundry test `audit/poc/<ID>.t.sol` that deploys the in-scope contracts at `scope_commit` locally, sets realistic balances, runs the attacker contract, and asserts the loss with exact numbers (`assertEq`, attacker balance gain, victim balance loss). A PoC that only reverts or prints is not accepted.
4. Run it with `bash`: `forge test --match-path audit/poc/<ID>.t.sol -vvv`. The test must pass on `scope_commit` (the exploit works). Save the output and measured loss. A test that fails is an unconfirmed candidate: no finding, no severity.
5. Call `audit_score_severity` with `loss_usd` (measured loss converted at the price the test used; not above `tvl_usd`), `tvl_usd`, `attacker_privilege`, `preconditions` (independent conditions beyond the call), `irrecoverable`. Put `impact`, `likelihood` and the returned `severity` into the finding. Pass `proposed_severity` only when a human proposed one: `requires_l3_approval` true then means BLOCKED `needs=human-approval`.
6. `write` the entry into `audit/findings.json` (list under `findings`): `id` (`F-###`), `title`, `severity`, `category` (SWC id or class), `location` (`path:line`), `description`, `recommendation`, `status` `open`, `impact`, `likelihood`, `poc` (`audit/poc/<ID>.t.sol::test_name`, path relative to the repo root, existing). High and critical require `poc`.
7. Run `python3 scripts/audit_findings_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path audit/findings.json --json`. Exit 0 means `ok`; exit 1 means a `major` or `blocker` in `findings[]`, for example `poc-missing`, `poc-not-found`, `severity-mismatch`, `missing-field`, `open-blocking`, `scan-incomplete` (critical still open is expected at this stage: it is the finding, not an error to hide). Exit 2 means an input problem (path outside root, missing, or a symlink or a path through one). Fix the named field; never delete the finding to pass.
8. Cross-check: script `by_severity` equals your count; `possible-duplicate` entries (minor) are merged with the older id. Call `shared_verdict` with `severities` mapped to the gate scale (info to `info`, low and medium to `minor`, high to `major`, critical to `critical`); `fail` equals a high or critical finding. Digest the findings file and each PoC with `shared_artifact_digest`.

## Outputs

- `audit.findings`: schema-valid finding entries each with measured evidence, a PoC reference and an `impact` x `likelihood` consistent severity.
- Evidence cited: PoC test output, `audit_score_severity` result, script `by_severity`.

## Failure Handling

- INVALID_INPUT: fix the named field (`loss_usd` above `tvl_usd`, step count above 16) or BLOCKED `needs=input`.
- DEPENDENCY_UNAVAILABLE (`forge`): retry once, then `skipped:tool-missing`; without an executed PoC a high or critical finding is submitted as "unconfirmed candidate" in `open_questions`, not as a finding.
- Script `severity-mismatch`: the stated severity differs from `impact` x `likelihood`; rescore. Keeping a different value needs L3 approval and `severity_override_approved: true` set by a human decision, never by you.
- PoC needs a private key or credentialed endpoint: refuse; use test accounts and local state.

## Handover

- `to_agent`: `A10` (consumer of `audit.findings` for the gate) and `A26` (fixes); address `A26` when no gate is pending. `artifacts`: findings file and PoC files with digests. `blockers`: pending L3. `verification`: verified when every high/critical PoC passed.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a27-contract-audit/finding-writeup-poc.md; edit the source and run scripts/build_agents.py -->
