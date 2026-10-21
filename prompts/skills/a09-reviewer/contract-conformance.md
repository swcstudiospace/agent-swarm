---
name: a09-reviewer--contract-conformance
description: Use when the diff touches an API contract, a schema migration or acceptance-criteria references and its conformance to them must be checked.
owner: a09-reviewer
tools: [shared_handover_bundle, rev_sensitive_paths, shared_dod_check]
---

## When to Use
- `consumes` (`code.patch`, `api.contract`, `acceptance.criteria`, `schema.migration`) includes a contract, migration or criteria input and the diff changes related files.
- An architecture or contract violation makes the verdict `fail` (decision_logic item 1).
- Not for general quality; `a09-reviewer--semantic-pass` covers that.

## Procedure
1. Call `shared_handover_bundle` with the gated target's `task_id` (a `gate_for` PR task). Take `acceptance` (criteria strings), `upstream[]` and `artifacts[]` (the contract and migration files, each with `uri` and `digest`) and the `verdicts[]` summaries.
2. Call `rev_sensitive_paths` with the changed `paths` (`git diff --name-only <base>` through `bash`; `<base>` is the one the gate uses, `HEAD~1` else `origin/main`). Keep the files whose category is `contracts` or `migrations` (`categories`, `flagged_paths`); they are the ones to compare against the pinned contract. A category is a path-token match, so also check by hand that no contract file was missed.
3. Use `read` on each of those files and on the contract artifact. Check each changed operation, field, status code or migration step against the contract text; use `grep` for the operation name in the diff. A mismatch is a finding at `major` or above.
4. Call `shared_dod_check` with `result` (an object whose `summary_md` has one line per criterion you verified, citing `path:line`, and `completed` the files compared) and `criteria` (the `acceptance` strings). `met` is a lexical match of the criterion against those lines, not proof: confirm each `met` against the code you read. `unknown` means no supporting line, or a contradicting or outstanding one (see `reason`): raise a finding with that `ac_ref`. Record each finding (kept in your notes for `a09-reviewer--verdict-assembly`) with the fields `swarm_gate` accepts: `severity`, `summary`, `kind` (rule ID by convention, for example `CONTRACT.response-shape`), `location` (`path:line`), `ac_ref`, `evidence`.

## Outputs
- Contract findings at `major` or above (blocking), each with `ac_ref`; the list of criteria met and unknown.

## Failure Handling
- Contract or migration artifact missing from `artifacts[]`: BLOCKED needs=input naming it.
- `shared_dod_check` returns `unknown` only: that is "no evidence" or "contradicted", never "pass".
- A migration step the schema contract does not describe is reported as a finding.
- Never edit the contract or the producer's code (A09 has no write or edit tool).

## Handover
- No handover of its own: the findings, the files compared and the criteria still `unknown` feed `a09-reviewer--verdict-assembly`, which writes the one handover (A01 or the producer).
- On cancel or pause, hand over per `swarm-cooperative-cancel`: `completed` (files compared), `remaining` (criteria left), `open_questions` (criteria still unknown).
