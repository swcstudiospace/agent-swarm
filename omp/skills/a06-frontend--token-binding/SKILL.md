---
name: "a06-frontend--token-binding"
description: "Use when styling code was written or changed; scan declarations for raw colour and spacing literals and replace each with a design-token reference."
disable-model-invocation: true
---

## When to Use
- Any change to CSS modules, Tailwind classes, styled-component blocks or inline style objects (A06 spec section 4 rule 1: "no hard-coded colors/spacing; token violations block publish").
- Styling only. Accessibility is `a06-frontend--a11y-selfgate`; a token the set does not contain is `a06-frontend--design-deviation`.

## Procedure
1. Take the token set version and token names from the bound-inputs note (`a06-frontend--bind-inputs`). After a resume call `shared_handover_read` with `{task_id}` and read `handover.constraints`; with `status` other than `ok`, `read` the `design.system.tokens` input itself.
2. Use `grep` over the changed style files for declarations of colour, background, border, outline, fill, shadow, margin, padding, gap, width, height, radius and font-size. Build one record per declaration: `{id: "<file>:<line>", property, value}`. For Tailwind, pass each arbitrary value (`p-[13px]`, `bg-[#0a5fd8]`) as `property: "padding"` or `"background"` with the bracket content as `value`.
3. Call `fe_token_literal_scan` with `{declarations}` (at most 500 per call; split larger sets and add the counts). The default `token_ref_patterns` are `var(`, `theme(`, `token(` and `$`. Pass your own list only when the blueprint uses another syntax, and then repeat the defaults you still need: a list you pass replaces them (a pattern ending in `(` is a function, any other is a sigil).
4. For every id in `literal_ids`, use `edit` to replace the literal with the matching token reference from the bound set. `by_kind` tells whether it was colour or spacing. Never edit the token files (A04 owns them) and never invent a token name.
5. A literal with no matching token: leave it, keep the id, and open `a06-frontend--design-deviation` for it; do not mark the task ready.
6. Call `fe_token_literal_scan` again on the same declarations. Require `literal_count` 0 and `tokens_ok` true before recording `tokens_bound`. After one failed second pass, report the remaining ids rather than looping.

## Outputs
- `tokens_bound: "design.system.tokens@<version>"` with the scan evidence (`declaration_count`, `literal_count` 0), or the list of unresolved ids with their reason.

## Failure Handling
- `INVALID_INPUT` field `declarations`: a duplicate id or blank value; make ids `<file>:<line>` and drop empty values.
- `fe_token_literal_scan` unavailable after two retries: report the token check as unrun; never claim `tokens_bound`.
- Named colours count only in colour-like properties, and the scan does not look inside `var(--x, #fff)` fallbacks: review fallbacks by hand.

## Handover
Fields filled: `constraints` (token version bound), `verification.checks` (`token scan literal_count 0` or the unresolved ids), `open_questions` (tokens missing from the set). Written with `shared_handover_write` at the task's closing handover to A09, or to A04 when a missing token blocks (`skill://swarm-handover-emit`).

<!-- generated from prompts/skills/a06-frontend/token-binding.md; edit the source and run scripts/build_agents.py -->
