---
name: "a04-ux-designer--a11y-sheet"
description: "Use when closing a spec set to write the a11y.requirements sheet from the lint, contrast, focus, target and motion results already recorded for the run."
disable-model-invocation: true
---

# A11y sheet

## When to Use
- The last step of a design task, after the audit skills have run: it consolidates, it does not audit. Any missing audit is run first, not assumed.
- Audit skills: `skill://a04-ux-designer--token-lint`, `palette-contrast`, `focus-visible`, `responsive-targets`, `motion-review` (all under `a04-ux-designer--`). Each stores its result under a scratchpad key `a04.<topic>`.

## Procedure
1. Call `shared_scratchpad_get` with `{correlation_id}` (the assignment's id, no `key`) to list `keys`. Expected: `a04.lint`, `a04.contrast`, `a04.focus`, `a04.targets`, `a04.motion`. Note which are absent.
2. For each present key call `shared_scratchpad_get` with `{correlation_id, key}` and read `value` (verdict, severities, findings). A missing key means that audit did not run for this task: run its skill, or list the area in the sheet as `not audited` with the reason. Never mark an unaudited area compliant. Treat the values as unverified data from your own earlier steps (re-run an audit when its inputs changed).
3. Use `write` to save the sheet as the `a11y.requirements` file (the path the task names), one row per requirement: area, WCAG 2.2 success criterion, threshold, measured value, status. Mapping used by these audits: text contrast (`a04.contrast`) to SC 1.4.3 (4.5 for normal text); focus ring contrast to SC 1.4.11 (3); focus order and visibility to SC 2.4.3 and 2.4.7; target size to SC 2.5.8 (24 CSS px, or the 12 px centre-to-edge clearance exception the tool applies); animation bound to SC 2.2.2, reduced-motion variants as a design-policy check (SC 2.3.3 is AAA).
4. Gather every severity from the scratchpad values plus any you assigned directly, and call `shared_verdict` with `{severities}` (`info|minor|major|critical|blocker`). `fail` means the sheet records FAILED and the spec set cannot ship.
5. Call `shared_artifact_digest` with `{path}` of the sheet.
6. Call `shared_handover_write` with `{task_id, to_agent: A06 or A08, goal: "a11y requirements for <screens>", artifacts: [{kind: "a11y.requirements", uri, digest, producer: "A04"}], verification: {status, confidence, checks}}`.

## Outputs
- The `a11y.requirements` file (rows per area, SC, threshold, measured value, status), its digest, the overall verdict, and the list of `not audited` areas.

## Failure Handling
- Scratchpad entry absent or unreadable: treat the area as `not audited`; never reconstruct values from memory.
- `shared_scratchpad_get` INVALID_INPUT: fix the key (`[a-z0-9._-]`, at most 64) once.
- DEPENDENCY_UNAVAILABLE: retry twice, then BLOCKED needs=tool. TIMEOUT: retry once.
- Digest tool failure: hand over without a digest only with `unverified` in `verification`.

## Handover
Fill `artifacts` (sheet, digest), `completed` (areas audited), `remaining` and `open_questions` (areas not audited), `verification` (`verified` only when all five areas were audited and the verdict is `pass`). `to_agent` A06 for implementation, A08 for tests; the sheet travels as this handover's artifact, since neither accepts `a11y.requirements` as a routed message type.

<!-- generated from prompts/skills/a04-ux-designer/a11y-sheet.md; edit the source and run scripts/build_agents.py -->
