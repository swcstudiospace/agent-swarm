---
name: "a06-frontend--component-catalog"
description: "Use when a component is finished; type its props, write one Storybook story per state and Playwright component tests, and hand the catalog to A08 with evidence."
disable-model-invocation: true
---

## When to Use
- A component is code-complete and must ship with its stories and component tests (A06 spec sections 2 and 3: "Storybook stories + Playwright component tests delivered with each component (consumed by A08)"; produces `component.catalog`).
- Delivery to A08. Whether the code matches the spec states is `a06-frontend--state-parity`; this skill proves every state has a story and a test.

## Procedure
1. Take the spec states, acceptance criteria and focus order from the bound-inputs note (`a06-frontend--bind-inputs`). After a resume call `shared_handover_read` with `{task_id}` and read `handover.constraints`; with `status` other than `ok`, `read` the `ux.spec` input again.
2. Call `fe_prop_type_coverage` with `{components: [{name, props: [{name, declared_type}]}], min_ratio: 1.0}`. `failing_components` lists those with blank, `any` or `unknown` prop types; type them with `edit` and call it again until `components_ok`.
3. With `write`, create one Storybook story per spec state (story name equal to the state name, a convention of this skill, args set to reach that state) and one Playwright component test per story, plus a keyboard test that follows the spec focus order. Use the repo's existing Storybook and Playwright setup (find it with `grep` and `read` of `package.json`); when neither is installed, stop BLOCKED needs=tool, since installing one is a new dependency (L3).
4. Call `fe_state_parity_check` with `{screens: [{id: "<component>", spec_states, implemented_states: <story names>}]}`. `missing` is a state without a story; `extra` is a story for a state the spec does not have: remove it.
5. Run the component tests with `bash` using the repo's test script (read it in `package.json`; `shared_check_plan` gives only check names) and record pass or fail from its output. `python3 scripts/code_checks.py ... --root <repo> --json` also runs `npm test`; its `results` is the machine-readable line.
6. Call `shared_dod_check` with `{task_id, criteria: [<acceptance criteria text>], result: {summary_md, checks: <test results, for example "AC-1: <story> passes">}}`. A criterion reported `unknown` has no test evidence: add the test or state the criterion as unmet.
7. Assemble the `component.catalog` entry (component, story files, test files, states covered) in `summary_md`. Call `shared_artifact_digest` with `{path}` for each story and test file, then `shared_artifact_check` with `{task_id, outputs: [{kind: "component.tests", uri, digest, producer: "A06"}]}` and require `failed` 0 (an output without a digest only comes back `no_digest`, unchecked).

## Outputs
- `component.catalog`: components with story paths, test paths, covered states, prop-type coverage, and the test run result.

## Failure Handling
- `INVALID_INPUT` field `components`: a duplicate component name or repeated prop name; fix the list.
- Component tests fail: the state is not IN_REVIEW; fix, never delete or skip a test (quarantine rules are `a06-frontend--visual-flake-triage`).
- Tests were not run: record `skipped:tool-missing` for them; do not claim a pass.

## Handover
Fields filled: `artifacts` (kind `component.tests` with uri, sha256 digest and producer A06), `verification.checks` (prop coverage, parity, test run, criteria met), `remaining` (criteria unmet). Written with `shared_handover_write` to A08 (`skill://swarm-handover-emit`).

<!-- generated from prompts/skills/a06-frontend/component-catalog.md; edit the source and run scripts/build_agents.py -->
