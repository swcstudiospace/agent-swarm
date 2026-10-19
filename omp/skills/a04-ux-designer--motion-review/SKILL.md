---
name: "a04-ux-designer--motion-review"
description: "Use when a spec contains any animation or transition and each one needs a duration bound and a reduced-motion variant before the spec is closed."
disable-model-invocation: true
---

# Motion review

## When to Use
- A screen or component spec declares an animation, transition, auto-advancing carousel or looping effect.
- Motion only. It does not judge whether an animation is a good idea, only duration and reduced-motion coverage.

## Procedure
1. `grep` the spec and the tokens file for animation, transition, duration and motion entries. Build the list: name, duration in ms, and whether the spec defines a reduced-motion variant (a static or instant alternative for users who ask for less motion).
2. Pick the `max_duration_ms` bound by `read`ing the design system's motion tokens (the longest allowed duration). If no motion limit is defined, use 5000 ms, a convention taken from the 5 seconds of SC 2.2.2 (moving content that starts automatically), and record that in `decisions`.
3. Call `ux_motion_duration_check` with `{max_duration_ms, animations: [{name, duration_ms, has_reduced_motion_variant}]}` (1 to 200 animations, durations 0 to 60000).
4. Read `over_limit` (shorten the duration or split the animation) and `without_reduced_motion` (add a variant to the spec). Apply both with `edit` and set `has_reduced_motion_variant` true only for variants you actually wrote.
5. Call `ux_motion_duration_check` again with the revised list; `compliant` must be true. Never report the first call's result as final after a revision.
6. Assign severity: each `over_limit` or `without_reduced_motion` entry left after revision is `major` (the reduced-motion rule is this design system's policy; SC 2.3.3 itself is AAA); compliant is `info`. Call `shared_verdict` with `{severities}`, then `shared_scratchpad_put` with `{correlation_id, key: "a04.motion", value: {verdict, severities, animations: <count>, max_observed_ms}}`.

## Outputs
- Animation table (name, duration, variant), `compliant`, `max_observed_ms`, verdict, the `a04.motion` key.

## Failure Handling
- INVALID_INPUT naming `animations` or `max_duration_ms`: a duration above 60000 or a blank name; correct once from the spec, retry, then BLOCKED needs=input.
- No animations in the spec: record `no motion` and skip the tool (an empty list is invalid input); still write `a04.motion` with that note so the a11y sheet sees an audit.
- DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`. TIMEOUT: retry once.

## Handover
Fill `completed` (animations reviewed), `decisions` (bound used and why, variants added), `constraints` (A06 must honour the reduced-motion variant), `verification` (`status`, `confidence`, `checks`). `shared_handover_write` to A06 when variants change its work, else the downstream agent the task names.

<!-- generated from prompts/skills/a04-ux-designer/motion-review.md; edit the source and run scripts/build_agents.py -->
