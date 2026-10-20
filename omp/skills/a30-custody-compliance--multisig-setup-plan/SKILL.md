---
name: "a30-custody-compliance--multisig-setup-plan"
description: "Use when a custody.policy task needs the m-of-n signer set, diversity rules and value tiers for a multisig; designs and verifies, never deploys."
disable-model-invocation: true
---

# Multisig setup plan

## When to Use

- A `custody.policy` task asks for a new multisig (Safe or similar) or a redesign of the signer set protecting an owner, treasury, pauser or upgrade role.
- Policy design is L2. Adding, removing or swapping a signer on a live wallet is not this skill: that is L3 (`skill://a30-custody-compliance--key-rotation-plan`). Deploying the wallet and any key generation is L4 human work; never done here.
- Delay, cancel and guardian design belongs to `skill://a30-custody-compliance--timelock-governance-ops`; per-signer limits to `skill://a30-custody-compliance--signer-policy-authoring`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` for the `contract.deploy.plan` (A26: which roles exist, which chain) and the `architecture.blueprint` (A03: what each role protects). List every controlled role with its value at risk in USD from the task. Any role or value missing is BLOCKED needs=input; never guess a USD figure.
2. Use `read` or `grep` on the blueprint and plan files for named entities and regions, then draft the signer set as a table of `signer_id` (a label, never a key or address you generated), `entity`, `region`, `device` (`hardware`, `hsm`, `software`, `mobile`, `contract`). Starting rules of this skill: `m` above half of `n`; `n - m >= 1`; no entity holds `m` or more signers; no region holds `m` or more signers (so at least 2 regions); fewer than `m` software or mobile signers. Typical shapes: 3-of-5 or 4-of-7 for a treasury.
3. Call `custody_multisig_policy` with `threshold: <m>`, `signers: [{signer_id, entity, region, device}]` and, when value tiers apply, `tiers: [{min_value_usd, required_signatures}]` (first tier at 0). Tiers above `threshold` are an off-chain signer-policy concern: the on-chain threshold always governs.
4. Decide from the `custody_multisig_policy` `verdict` and `findings`: `fail` (any major or blocker: `single-signer`, `threshold-one`, `entity-controls-quorum`, `no-loss-tolerance`, `hot-signers-reach-quorum`, `tier-unsatisfiable:N`) means redesign and rerun; `warn` (minor, for example `single-region`, `region-controls-quorum`, `no-majority-quorum`) needs a written acceptance in the plan; `pass` continues.
5. Cross-check the `custody_multisig_policy` output against your table: `signer_count` equals the row count, `loss_tolerance` equals `n - m`, `compromise_tolerance` equals `m - 1`, `min_entities_for_quorum` is at least 2, `hot_signers` is below `m`. Any difference is E-CONTRACT: report both values and stop.
6. `write` the policy file `custody/<wallet>.multisig.json` as `{"multisig": {<the exact tool input>}}` (the file name carries `multisig`), then run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path custody --json`. Exit 0 and `status: ok` must agree with step 4; exit 1 names `multisig:<id>` findings; exit 2 is a taxonomy error. Read `secret_findings`: it must be 0.
7. `write` the plan `custody/<wallet>.multisig-plan.md`: signer table, thresholds, tiers, accepted warnings and the rollout order: testnet deployment and a test transaction first, then the mainnet deployment as an explicit L4 human step with named humans. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "mainnet multisig deployment needs L4 human execution", needs: ["human-approval"]}` and carry its `result_patch`.
8. Call `shared_artifact_digest` for the policy file and the plan, then see Handover.

## Outputs

- `custody.policy.set` (policy JSON plus plan) with the `custody_multisig_policy` figures and the script counts cited.

## Failure Handling

- INVALID_INPUT from `custody_multisig_policy` (`threshold` above signer count, duplicate `signer_id`, tiers sharing `min_value_usd`, a key-shaped label): fix the named field once, then BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unchecked part. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; a design without a tool verdict is not `pass`.
- The script and the tool disagree: E-CONTRACT, state no verdict. A signer or threshold change on a live wallet: BLOCKED needs=human-approval (L3), never retried.
- The thresholds in step 2 are this skill's defaults, not a standard; defer to `findings`.

## Handover

- `to_agent`: `A26` (the roles and owner addresses the contracts must use); add a second write to `A28` when signer hosting is infrastructure. `goal`: build against this policy set; `completed`: checks run; `decisions`: threshold, accepted warnings; `artifacts`: policy and plan with `shared_artifact_digest` digests; `open_questions`: unnamed entities; `verification`: partial until a human confirms the testnet run.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a30-custody-compliance/multisig-setup-plan.md; edit the source and run scripts/build_agents.py -->
