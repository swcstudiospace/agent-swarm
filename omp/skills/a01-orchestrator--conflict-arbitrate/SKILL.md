---
name: "a01-orchestrator--conflict-arbitrate"
description: "Use when a conflict.report pits two agents' claims against each other; weigh ownership and evidence, record the arbitration, escalate L3 or ambiguous cases."
disable-model-invocation: true
---

# Conflict arbitrate

## When to Use

- A `conflict.report` names two claims on one subject (a contract, a path, a decision), or a third gate failure where two gates disagree. A human decision on a stuck task: `skill://a01-orchestrator--escalate-human`.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for each claimant's task; take each claim from `handover.decisions` and `handover.open_questions`. `found` false means that side has no recorded claim.
2. Call `shared_artifact_check` with `{task_id, agent: <claimant agent id>}` for each side. Evidence counts only for entries whose `status` is `ok`; a side is `verified` only when every output checked out, and unchecked uris are not evidence.
3. `grep` the `capabilities` of each claimant in `agents.json` (runtime root) to find the capability owner.
4. Pick the winner from the `shared_artifact_check` results: the capability owner when evidence is equal, the verified side when it is not. Draft `{subject, winner_claim_id, rationale_md, evidence[], appealable_until}` (agents/A01 message shape). Convention: set `appealable_until` to an ISO-8601 time within one planning cycle, since the spec only requires the field.
5. Autonomy L2 covers intra-sprint conflicts only. If the choice kills a release, cancels human-approved work or stays ambiguous after step 4, finish with `yield` BLOCKED needs=`human-approval: arbitration` and run `skill://a01-orchestrator--escalate-human` with `reason_code: conflict`.
6. Otherwise call `shared_scratchpad_put` with `{correlation_id, key: "arbitration-<subject slug>", value: <record>}`. The key matches `[a-z0-9._-]{1,64}`, so lowercase the slug; use a new key per conflict and never overwrite one (append-only audit).
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` to the losing agent. The record in step 6 and this handover are the delivery: no ACP route carries `conflict.arbitration` to a specialist.

## Outputs

- One arbitration record, appealable until its `appealable_until`.

## Failure Handling

- Missing claim data: BLOCKED needs=input naming the claim.
- Do not decide on unchecked evidence, and do not record gate verdicts.

## Handover

- `to_agent` the losing claimant as an agent id (for example `A06`). Fill `decisions` (winner and rationale), `constraints` (what it must follow), `blockers`, `open_questions` (appeal path).

<!-- generated from prompts/skills/a01-orchestrator/conflict-arbitrate.md; edit the source and run scripts/build_agents.py -->
