---
name: "a02-requirements--nfr-elicitation"
description: "Use when a spec has no NFRs or misses a category (perf, security, a11y, i18n); propose measurable targets and reject unmeasurable ones."
disable-model-invocation: true
---

# NFR Elicitation

Defines how well the system must behave. Targets must be measurable: unmeasurable NFRs are rejected back for
refinement and never published (spec section 4, rule 1). Regulatory NFRs are the next skill's work.

## When to Use

- The spec has no NFR list, or `req_nfr_coverage` reported missing categories.
- Not for regulated-data rules (`a02-requirements--compliance-tagging`); not for criteria wording.

## Procedure

1. Call `shared_handover_read` with `task_id`; use `handover.constraints` for stated limits (users, budgets, devices).
2. Use `read` on `docs/requirements/nfr.md` (convention path) if it exists; keep existing `NFR-` ids unchanged.
3. Draft (saved with `write` in step 6) one NFR per missing kind from `perf`, `security`, `a11y`, `i18n`, each with an id such as `NFR-P1`, a numeric `target` (for example `p95<300ms @ 50rps`) and `verified_by` naming the owner of the check (for example `A08.load`). Leave `compliance` to the next skill.
4. Call `req_ambiguity_scan` with `text` set to each target sentence. A target with a flagged word (modal, quantifier or vague adjective), or without a number and unit, is rejected: rewrite it with a measurable threshold or move it to `open_questions`.
5. Call `req_nfr_coverage` with `nfrs` as `{id, kind}` and `required_kinds` set to the categories the brief needs (omit `compliance` when no regime applies). A `compliance` gap is expected until `a02-requirements--compliance-tagging` runs; every other `missing_kinds` entry is fixed or listed as a question.
6. Use `write` (or `edit` when the file exists) to save the accepted list to `docs/requirements/nfr.md`, then call `shared_artifact_digest` with that `path`.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write`.

## Outputs

- NFR list with id, kind, measurable target and `verified_by`.
- `req_nfr_coverage` result (`coverage`, `missing_kinds`) and the rejected, unmeasurable targets.

## Failure Handling

- `req_nfr_coverage` INVALID_INPUT: an `nfrs` item with an unknown `kind` is the cause; map it to one of the five kinds or drop it with a note.
- A stakeholder gives only adjectives ("fast"): do not invent a number; add the question and mark the NFR `unmeasured`, not published.
- Tool TIMEOUT or DEPENDENCY_UNAVAILABLE: retry once, then E-DEP; coverage is then reported as unverified.

## Handover

Fill `completed` (NFRs accepted), `remaining` (compliance mapping, publish gate), `decisions` (each target and
why that number), `open_questions` (rejected NFRs), `artifacts` (kind nfr, the saved path, its digest, `producer` `A02`) and `verification`
(`checks` with the coverage ratio). Call `shared_handover_write` with `to_agent` `A01` (an agent id, never the slug).

<!-- generated from prompts/skills/a02-requirements/nfr-elicitation.md; edit the source and run scripts/build_agents.py -->
