---
name: "a06-frontend--perf-budget"
description: "Use when a build output and a Lighthouse run exist; gate bundle bytes and Core Web Vitals against budget, code-split once on a breach, else escalate."
disable-model-invocation: true
---

## When to Use
- A production build exists and the screen has a `perf.budget` (A03 or A13) to meet (A06 spec section 4 rule 4 and section 6: "regression > 5 % => code-split or escalate", Lighthouse perf >= 90, a11y = 100, CWV "good").
- Performance only. Accessibility is `a06-frontend--a11y-selfgate`; script injection and third-party assets are `a06-frontend--client-security`.

## Procedure
1. Take the bundle budget, the baseline and any threshold overrides from the `perf.budget` input (bound-inputs note). After a resume call `shared_handover_read` with `{task_id}` and read `handover.constraints`; with `status` other than `ok`, `read` the `perf.budget` input again. No budget stated anywhere: BLOCKED needs=input, never invent one.
2. Use `bash` to run the build the blueprint names and read the emitted chunk sizes (stats file or `ls -l` of the output directory) as `{name, bytes}`; pass only the initial-load chunks (entry plus its static imports), since `bundle_kb.initial` is built from this sum. Use `read` on the Lighthouse CI report for `perf`, `a11y`, `bp`, `seo` and the lab LCP, INP and CLS. No repository tool measures these: if no Lighthouse run exists, the CWV figures are unmeasured and go under UNVERIFIED, never estimated.
3. Call `fe_bundle_budget_check` with `{chunks, budget_bytes, baseline_bytes, regression_limit_pct: 5}`. `budget_ok` false with `over_budget` or `regression_breach` true names `largest_chunk` as the first split candidate.
4. Call `fe_cwv_budget_check` with `{cwv: {lcp_s, inp_ms, cls}, lighthouse: {perf, a11y, bp, seo}}` and optional `thresholds` (`lcp_s`, `inp_ms`, `cls`, `perf_min`, `a11y_min`) when `perf.budget` overrides the defaults. Read `breaches[]` (`metric`, `value`, `limit`); a value equal to its limit passes.
5. On a bundle breach, use `edit` to code-split the `largest_chunk` (a dynamic import at the route or heavy component), rebuild with `bash`, and repeat steps 3 and 4 once. A second failure: call `shared_escalate` with `{task_id, reason_code: "budget", summary, needs: ["<metric> <value> over <limit>; decision on budget or scope"]}` and use the returned `result_patch`.
6. Assemble `perf_report` from the `fe_bundle_budget_check` and `fe_cwv_budget_check` results as in the output contract: `screen_id`, `lighthouse`, `bundle_kb` (`initial` = `total_bytes` / 1024 rounded, `budget`), `cwv`, citing both tool results.

## Outputs
- `perf.report` / `perf_report` JSON with a verdict (`perf_ok`), the split performed, and any unmeasured figure listed as UNVERIFIED.

## Failure Handling
- `INVALID_INPUT` field `cwv` or `lighthouse`: a figure is missing or out of range (scores 0 to 100); fix the source figure, never default it.
- Tool unavailable after two retries: report the figure as unrun and the gate as not proven (prompt DEGRADED_MODE).
- Do not raise a budget yourself: that is the owner of `perf.budget`.

## Handover
Fields filled: `verification.checks` (bundle and CWV verdicts with numbers), `decisions` (the split and why), `blockers` (open breaches). Written with `shared_handover_write` to A09, or the escalation result to A01.

<!-- generated from prompts/skills/a06-frontend/perf-budget.md; edit the source and run scripts/build_agents.py -->
