---
name: "a10-security--scanner-conflict"
description: "Use when two scanners such as Semgrep, CodeQL and sec_gate report the same weakness with different severities and you must merge them into one finding."
disable-model-invocation: true
---

## When to Use
- Findings from more than one scanner point at the same CWE and location, or one scanner rates it critical and another major (A10 prompt, error_handling: deduplicate by CWE + location, severity = max, log the conflicting tools as advisory).
- The input is a handful of overlapping findings in a single gate run. Many hits from one new CVE use `skill://a10-security--vuln-flood`, which groups by rule instead.

## Procedure
1. Gather every finding of the run (the verdict file `.swarm/verdicts/<gate task_id>.security.json` read with `read`, plus any scanner output the assignment supplies) into `findings` with `id`, `tool` (`sec_gate` for the gate's own, semgrep, codeql, trivy and so on), `rule` (for `sec_gate` the summary text before the colon), `severity`, and where known `cwe` (for `sec_gate` the finding's `ac_ref`) and `location` (`file:line`). Use the gate scale only (`info`, `minor`, `major`, `critical`, `blocker`); translate a scanner's `low`, `medium`, `high` by calling `sec_cvss_band` with its numeric score when it has one, and map band `high` to `major`, `critical` to `critical`, `medium` to `minor`, `low` to `info`.
2. Call `sec_finding_dedup` with those `findings` and `group_by` `cwe_location`. Findings without a CWE or a location stay as their own `unkeyed:<id>` group; do not guess a CWE to force a merge.
3. From the `sec_finding_dedup` result, for each entry of `conflicts`, record the key, the maximum severity (the group `severity`) and each tool's own rating from `by_tool`. The group keeps the maximum; the lower ratings are not discarded, they become an advisory line.
4. When a conflicting group carries a CVSS score from either scanner, call `sec_cvss_band` with the higher `score` and check that the band agrees with the maximum severity; if the score implies a higher band, raise the group to it.
5. Call `shared_verdict` with the severity of each group (one entry per group, not per raw finding). A merged group that stays `major` or above fails the gate. Findings from scanners other than `sec_gate` reach a verdict only through `per_target_findings` of the `swarm_gate` call (`skill://a10-security--pr-gate` step 3), so merge before that call when you can; a verdict already recorded counts every raw finding, so merging cleans the advisory and never lowers it.
6. Write the advisory: key, tools involved, ratings, final severity, and why the maximum was kept. Keep the original finding `ids` in the `ids` list so the verdict can reference the stable ids.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover).

## Outputs
- A deduplicated finding list (`groups`) and a `conflicts` advisory for the verdict's `advisories` array. No rating is dropped.

## Failure Handling
- `sec_finding_dedup` INVALID_INPUT names the offending `findings[n]`: fix a blank tool or rule, an unknown severity or a duplicate `id` once; unfixable input is BLOCKED needs=input.
- PARTIAL_SUCCESS: treat the merge as incomplete, report raw findings and do not lower any severity.
- Never downgrade to the minimum to quiet a conflict; the rule is the maximum.

## Handover
`shared_handover_write` to A01 (advisory) with `goal` (record the merged findings), `completed` (groups merged, conflicts logged), `decisions` (final severity per conflict with the tool that set it), `artifacts` (none unless a report file is saved, then kind `security.findings` with digest), `open_questions` (a scanner whose rating you could not reproduce), and `verification` {status `verified`; checks `sec_finding_dedup:groups=<n>`, `shared_verdict:<verdict>`}.

<!-- generated from prompts/skills/a10-security/scanner-conflict.md; edit the source and run scripts/build_agents.py -->
