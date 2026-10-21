# Agent Swarm Enhancements

> **Dated analysis, committed 2026-10-20.** Sections 1 to 5 and the acceptance table below describe the 15-agent state at the 2.0.0 prompt rewrite and are kept as written. Section 6 and the current status record the v2.3 resolutions; see [Current status (v2.3)](#current-status-v23).

## 1. Scope and status

- Enhancements 1 to 6 are applied inside the rewritten prompts (`prompts/A01.md` through `prompts/A15.md`).
- Enhancement 7 and all infrastructure (registry fields, runner receipts or events, checkers, the external repair round, and any eval harness) are proposal-only.
- Creating new tools or subagents is out of scope. The enhancement brief sizes that work separately (Section 5).
- The request maps to no existing `.planning/` phase, so no planning file was written (Section 3).

## 2. Seven ranked enhancements

Ranked in this order. Each entry gives Problem, Change, Prompt surfaces touched, Acceptance check, Risk, and Status.

### 1. TOOL_MANIFEST_SYNC

**Problem.** No prompt mentions `swarm.tool_registry`, and every `<tools>` block lists only `<script>` entries, so subagents never see their registered tools. No check today compares prompt tool names with the registry. `python3 -m swarm.tool_registry check` validates only the registry itself.

**Change.** Each prompt gets a `<TOOL_INVENTORY count="N">` element inside `<tools>`, after the existing `<script>` entries. It holds one `<TOOL name="EXACT_REGISTRY_NAME" owner="...">` per registry grant for that agent (lane tools plus shared tools the agent is permitted to call). `N` equals the "Total registry grants" value in Section 4. The `owner` attribute is the registry owner value. Each TOOL contains, in order: `<PURPOSE>`, `<WHEN_TO_USE>`, `<WHEN_NOT_TO_USE>`, `<INPUTS>`, `<OUTPUTS>`, `<SIDE_EFFECT_TIER>`, `<IDEMPOTENT>`, `<TIMEOUT_S>`, `<FAILURE_HANDLING>`, and `<EXAMPLE>`. `<IDEMPOTENT>` and `<TIMEOUT_S>` are copied from the registry. Scripts stay as `<script>` entries and are never TOOL entries. A sync check that compares TOOL names and owners with `agents.json` is proposal-only.

**Prompt surfaces touched.** `<tools>` in all 15 prompts (new `<TOOL_INVENTORY>` and its TOOL children). `<EXAMPLE>` inside each TOOL.

**Acceptance check.** Applied part, runnable now: `python3 -c "import sys, xml.etree.ElementTree as ET; ET.parse(sys.argv[1])" prompts/A01.md` exits 0 for each of the 15 prompts, and each file's `count` equals its TOOL child count and its grant total in Section 4. Proposal part: on a fixture copy of `agents.json` and one prompt, extract the `(name, owner)` pairs from `TOOL_INVENTORY` and compare them with that agent's grants. The unmodified fixture passes. A fixture with one TOOL name changed fails and names the drifted tool.

**Risk.** Medium. TOOL entries are copied by hand and no sync check exists yet, so they can drift from `agents.json`. The renderer exact-match table (`scripts/build_agents.py` lines 185-252) requires each matched sentence to stay byte-for-byte. Changing one makes the build raise.

**Status.** applied-in-prompts for the TOOL_INVENTORY text. The sync check is proposal-only.

### 2. OUTPUT_CONTRACT_GATE

**Problem.** The registry's terminal states are SUCCESS, PARTIAL_SUCCESS, INVALID_INPUT, UNAUTHORIZED, DEPENDENCY_UNAVAILABLE, and TIMEOUT. Task states are a different set. `orch_status --ingest` accepts only IN_PROGRESS, IN_REVIEW, FAILED, and BLOCKED, and the final fenced json block's `state` must be IN_REVIEW, BLOCKED, or FAILED. The brief's PARTIAL and SUCCESS statuses would break ingest if emitted as task states.

**Change.**
- Registry SUCCESS maps to task state IN_REVIEW.
- Registry PARTIAL_SUCCESS maps to BLOCKED, with `needs` naming the gap and `completed_fraction` (in [0,1]) recorded.
- FAILED maps to FAILED, with `error` naming an E-code: E-INPUT, E-TIMEOUT, E-DEP, E-CAPACITY, E-CONTRACT, or E-INTERNAL.
- Autonomy violations use `"state": "BLOCKED", "needs": "human-approval: <capability>"`.
- `<OUTPUT_CONTRACT>` lists the three allowed states. `<REPAIR_PROTOCOL>` requires a self-check: before the final message, validate the agent's own json block against `<OUTPUT_CONTRACT>` and fix it once.
- An external one-shot repair round run by the orchestrator needs an orchestrator change. That change is out of scope and proposal-only.

**Prompt surfaces touched.** `<OUTPUT_CONTRACT>` and `<REPAIR_PROTOCOL>`. The existing `<output_format>` element is kept.

**Acceptance check.** Fixture: one final json block per case. SUCCESS maps to IN_REVIEW. PARTIAL_SUCCESS (`completed_fraction` 0.4) maps to BLOCKED with `needs`. FAILED with `error` E-TIMEOUT. An autonomy violation maps to BLOCKED with `needs` "human-approval: <capability>". Add one negative block with `state` SUCCESS. Run each block through `orch_status --ingest` on the orchestrator side. The four mapped blocks are accepted, and the negative block is not.

**Risk.** Medium. A state string outside the four-value set fails ingest. The self-check runs inside the model with no external repair round, so a malformed block is not repaired automatically.

**Status.** applied-in-prompts for the mapping and the self-check. The external repair round is proposal-only.

### 3. TIERED_TOOL_PERMISSIONS

**Problem.** Registry entries carry no side-effect field. Their fields are name, owner, purpose, input_schema, output_schema, errors, timeout_s, idempotency, permitted_callers, entrypoint, handler, and examples. A non-permitted caller gets INVALID_INPUT or UNAUTHORIZED. Once a caller is permitted, the registry does not distinguish a read from a destructive call.

**Change.** Each TOOL carries `<SIDE_EFFECT_TIER>` set to exactly one of READ, WRITE, DESTRUCTIVE, or EXTERNAL. It is followed by one evidence line naming the handler function, which is the function part of the registry `handler` field (under `swarm/tools/`). If unsure, use the higher tier. `<GUARDRAILS>` states the agent's autonomy ceiling from `agents.json` and the highest side-effect tier the agent may invoke. The tiers are prompt-level labels. The runner does not enforce them, and runner enforcement is proposal-only.

**Prompt surfaces touched.** `<SIDE_EFFECT_TIER>` in each TOOL. `<GUARDRAILS>`.

**Acceptance check.** Fixture prompt with one TOOL whose `<SIDE_EFFECT_TIER>` is removed: the checker fails it. A second fixture with the tier set to a value outside the four fails. A third fixture whose evidence line names a function other than the handler function in the fixture `agents.json` fails. The unmodified fixture passes.

**Risk.** Medium. The tiers are text labels that the runner does not enforce, so a DESTRUCTIVE tool mislabeled as READ is not blocked. The higher-tier default limits that error.

**Status.** applied-in-prompts for the labels and the GUARDRAILS text. Runner enforcement is proposal-only.

### 4. EVIDENCE_LINKED_FINDINGS

**Problem.** `call_tool` and `_finish` in `swarm/tool_registry.py` emit no receipt or event, so a registry call cannot be tied to a recorded result. Findings in a subagent's output have no required link to the tool call or file read that produced them.

**Change.** `<OUTPUT_CONTRACT>` requires every factual claim to carry a source tag: `source: tool <NAME>` for a registry call, or `source: <path>:<start>-<end>` for a file read. A claim without a source goes under a heading named UNVERIFIED in the same output. The `output_format` fields are unchanged.

**Prompt surfaces touched.** `<OUTPUT_CONTRACT>`.

**Acceptance check.** Fixture output with three findings. Two carry source tags. One is uncited and sits outside UNVERIFIED. A checker that scans the finding bullets fails it. Moving the uncited bullet under UNVERIFIED makes it pass. The checker is proposal-only. Until it exists, the fixture review is a manual step.

**Risk.** Low. The change adds text and no runtime path. The main failure mode is over-marking claims as UNVERIFIED, which is visible in the output.

**Status.** applied-in-prompts for the source-tag rule. A checker and stored receipts are proposal-only.

### 5. RUN_BUDGETS_AND_STOP_RULES

**Problem.** A `task.assign` carries a budget (`max_wall_s`, `max_tokens`, `max_cost_usd`), and each registry tool has a `timeout_s`. These are the existing limits. Without a phase exit condition tied to them, a run ends when the budget runs out rather than at a defined step.

**Change.** `<OPERATING_PROCEDURE>` numbers the phases. Each phase names the tools it uses, its exit condition, and the budget references it draws on: `budget.max_wall_s`, `budget.max_tokens`, and `budget.max_cost_usd` from `task.assign`, plus the `timeout_s` of each tool it calls. When the agent judges that the budget cannot cover the next phase, it returns BLOCKED with `needs` naming the budget. No new counters are added.

**Prompt surfaces touched.** `<OPERATING_PROCEDURE>`.

**Acceptance check.** Fixture prompt, plus a fixture `task.assign` that sets all three budget fields. A checker lists the numbered phases and fails any phase that lacks an exit condition or a reference to a budget field or a tool `timeout_s`. The unmodified fixture passes. A fixture with one phase's exit condition removed fails.

**Risk.** Medium. No counter is added, so the stop rule depends on the agent's own estimate. Overruns are caught only if the orchestrator enforces `task.assign` budget, which this document does not add.

**Status.** applied-in-prompts.

### 6. TOOL_FAILURE_PLAYBOOKS

**Problem.** The registry defines six terminal states. A subagent given a failure with no per-tool action may retry, proceed without the result, or invent output. Retry safety depends on the `idempotency` field. All 27 entries declare `idempotent`, and nothing in the runner reads that field.

**Change.** Each TOOL's `<FAILURE_HANDLING>` has one line per applicable state, using the registry's exact names:
- SUCCESS: use the output.
- PARTIAL_SUCCESS: record `completed_fraction` and the gap.
- INVALID_INPUT: correct the named field once, else BLOCKED with `needs=input`.
- UNAUTHORIZED: stop, do not retry, report.
- DEPENDENCY_UNAVAILABLE: retry the same input at most twice, then report the dependent finding as `skipped:tool-missing` or FAILED under the agent's existing `error_handling`.
- TIMEOUT: retry at most once, then E-TIMEOUT.

`<DEGRADED_MODE>` reports a missing or persistently failing tool and forbids fabricated results.

**Prompt surfaces touched.** `<FAILURE_HANDLING>` in each TOOL. `<DEGRADED_MODE>`. `<ESCALATION>`. The failure-recovery sequence in `<EXAMPLES>`.

**Acceptance check.** For each TOOL in a fixture prompt, every `<FAILURE_HANDLING>` line begins with one of the six registry state names, and each state name in that tool's registry `errors` field has a line. The unmodified fixture passes. A fixture line that begins with an invented name such as RATE_LIMITED fails.

**Risk.** Medium. The retry limits assume the declared `idempotent` value is true. Nothing enforces it. If a handler is not actually idempotent, a retry repeats its side effect.

**Status.** applied-in-prompts.

### 7. PROMPT_EVAL_AND_VERSIONING

**Problem.** The rewrite changes all 15 prompts together. No evaluation harness is part of this change, so behavior drift cannot be measured before the rewritten prompts are used.

**Change.** Proposal only. Keep one golden fixture per agent: a `task.assign` input, the expected final state, and the expected TOOL names. Run each fixture against the pre-rewrite prompt from version control and against the 2.0.0 prompt, then compare states and tool names. Bump `<PROMPT_VERSION>` on each prompt change and record the version with each run. In this rewrite, `<PROMPT_VERSION>` is a label only, with the text "2.0.0: tool-grounded XML rewrite; mission and authority unchanged."

**Prompt surfaces touched.** `<PROMPT_VERSION>` as a label in all 15 prompts. No eval wiring is applied.

**Acceptance check.** Proposed, not run. For each agent fixture, the 2.0.0 run's final json state equals the expected state, and every tool name the run calls appears in that agent's `TOOL_INVENTORY`.

**Risk.** High. Fifteen prompts change at once, and this change includes no measured baseline for behavior. A state-mapping error would reach dispatch unseen.

**Status.** proposal-only.

## 3. Evidence

- **Size and well-formedness.** All 15 `prompts/A*.md` files are 13.7 to 18.4 KB. None parses as XML today. `prompts/A01.md`, `prompts/A05.md`, and `prompts/A06.md` have mismatched tags. The other twelve contain invalid tokens such as bare ampersands or angle brackets.
- **Tools block.** Every prompt's `<tools>` block lists only `<script>` entries, 1 to 3 per agent. No prompt mentions `swarm.tool_registry`.
- **Registry totals.** `agents.json` holds 27 entries: 23 lane tools and 4 shared tools (`shared_verdict`, `shared_toolchain`, `shared_check_plan`, `shared_git_head`). Grants (lane plus shared-permitted) total 37. Per agent: A01 has 7; A11 has 4; A03, A05, A06, and A12 have 3 each; A02, A04, A08, A09, and A10 have 2 each; A07, A13, A14, and A15 have 1 each. The registry supports 1 to 7 grants per agent.
- **Idempotency.** All 27 entries in `agents.json` declare `idempotency` as `idempotent`. Nothing in the runner reads that field.
- **No receipts.** `call_tool` and `_finish` in `swarm/tool_registry.py` emit no receipt or event.
- **Lane test bound.** `tests/test_lane_tools.py` asserts `1 <= len(tools) <= 8` per agent.
- **Stale size comment.** `scripts/build_agents.py` line 70 says `tests/test_omp_agents.py` caps omp A01 at 24 KB. That test contains no size check, so the comment is stale and no size cap exists.
- **Renderer table.** `scripts/build_agents.py` lines 185-252 (`SHARED_BODY_SUBSTITUTIONS` and `_CURSOR_ONLY_SUBSTITUTIONS`) hold exact-match sentences. Each old string must occur exactly once in its agent's prompt, so a rewrite that changes one makes the build raise.
- **Test pins.** `prompts/A05` and `prompts/A06` contain `swarm/<task_id>`. `prompts/A09` contains `auto-fix/*`. `prompts/A02` contains the `change_state` proposed|approved|rejected JSON text and must not contain a `state` variant of it. Every prompt has `<agent id="Axx"`, the 20 tag pairs listed in `AGENTS.md`, a `<script path=` entry whose body contains `python3 scripts/` and `scripts/ts/`, and ends with `</agent>`.
- **No planning phase.** `.planning/STATE.md` says the milestone is awaiting the next milestone. `.planning/ROADMAP.md` says no milestone is in progress. The request therefore maps to no existing phase, and `.planning` was not written.

## 4. Tool inventory

Source: the verified registry manifest. "Lane tools" and "Shared tools permitted" list the registry names. "Total registry grants" is lane plus shared-permitted. "Scripts" counts the `<script>` entries in each prompt's `<tools>` block. "Gap to 10" is measured against a target of 10 registry tools.

| Agent | Slug | Lane tools | Shared tools permitted | Total registry grants | Scripts | Gap to 10 |
|---|---|---|---|---|---|---|
| A01 | a01-orchestrator | 7 (orch_registry_ping, orch_dag_order, orch_budget_class, orch_plan, orch_run, orch_reconcile, orch_assign) | none | 7 | 3 | 3 |
| A02 | a02-requirements | 1 (req_gwt_check) | shared_verdict | 2 | 1 | 8 |
| A03 | a03-architect | 2 (arch_adr_slug, arch_contract_ops) | shared_verdict | 3 | 2 | 7 |
| A04 | a04-ux-designer | 1 (ux_contrast) | shared_verdict | 2 | 1 | 8 |
| A05 | a05-backend | 1 (be_route_norm) | shared_toolchain, shared_check_plan | 3 | 2 | 7 |
| A06 | a06-frontend | 1 (fe_a11y_scan) | shared_toolchain, shared_check_plan | 3 | 2 | 7 |
| A07 | a07-data | 1 (data_down_present) | none | 1 | 1 | 9 |
| A08 | a08-qa | 1 (qa_runner_detect) | shared_verdict | 2 | 1 | 8 |
| A09 | a09-reviewer | 1 (rev_diff_size) | shared_verdict | 2 | 1 | 8 |
| A10 | a10-security | 1 (sec_line_entropy) | shared_verdict | 2 | 1 | 8 |
| A11 | a11-devops | 2 (devops_ci_kind, devops_build_systems) | shared_verdict, shared_git_head | 4 | 2 | 6 |
| A12 | a12-release | 1 (rel_canary_steps) | shared_verdict, shared_git_head | 3 | 1 | 7 |
| A13 | a13-observability | 1 (obs_error_burn) | none | 1 | 1 | 9 |
| A14 | a14-maintenance | 1 (maint_spec_pin) | none | 1 | 1 | 9 |
| A15 | a15-docs | 1 (docs_heading_order) | none | 1 | 1 | 9 |

All 15 agents are flagged as under-equipped against the target of 10.

## 5. Conflicts and decisions

- **Tool count.** The user expects 10 to 12 unique tools per subagent. Registry grants (lane plus shared-permitted) are 1 to 7 per agent, 37 in total. Counting registry grants only, reaching 10 per agent needs 113 new grants and reaching 12 needs 143 (the Gap column sums to 113; 15 x 12 - 37 = 143). Counting scripts as tools too, the requirement is 106 (target 10) and 136 (target 12). These figures assume each new tool serves one agent; a new shared tool could serve several and lower the count. Decision applied: rewrite with real tools only, flag all 15 as under-equipped, and treat tool creation as a separate approved milestone.
- **Root element.** The brief's root element `SUBAGENT_PROMPT` conflicts with the pinned root `<agent id=...>`. Decision: keep `<agent>`.
- **Tag case.** The brief's SCREAMING_SNAKE tags conflict with the pinned lowercase tags used by `test_swarm.py` and `build_agents.py`. Decision: new sections use SCREAMING_SNAKE, and existing pinned tags keep their names.
- **Grant sentence.** The brief's per-agent sentence reads "A03 and A11 4 (A03 has 3; A11 has 4)". The manifest rows give A03 = 3 and A11 = 4. This document uses the rows.
- **Ultrathink config.** `/root/.config/ultrathink/config.json` sets `ship.enabled`, `autoMerge`, and `deleteBranch` to true. Ship was not invoked. This needs a user decision.
- **Dispatch frequency.** Dispatch frequency per agent was not measured from runs. Ordering follows capability coverage rather than dispatch counts. Section 4 is ordered by agent ID.

## 6. Open decisions for the user

- **(a)** Build the missing tools as a separate milestone, or accept under-equipped prompts. Section 4 flags all 15 agents until one of these is chosen.
  - Resolved in v2.3: each agent holds 10 to 15 registered tools and 10 to 15 skill sources (`capability_policy`, enforced). No agent is flagged under-equipped.
- **(b)** Whether to ship. The ultrathink config has `autoMerge` set to true, and the worktree mixes uncommitted changes from several sources. Ship stays uninvoked until this is decided.
  - Resolved in v2.3.

## Current status (v2.3)

- **Roster.** 30 agents in `agents.json`: A01 to A15 (SDLC), A16 to A20 (cloud-native), A21 to A25 (cluster) and A26 to A30 (Web3). Each has a prompt in `prompts/`.
- **Tools and skills.** Every agent is inside the 10 to 15 range set by `capability_policy` (`enforce: true`). Lane-owned registered tools are 12 or 13 per agent; skill sources are 10 to 14 per agent. The shared baseline sits on top and is not counted.
- **Decisions.** (a) Tools: resolved in v2.3. The tools exist as registry tools with handlers in `swarm/tools/` and per-agent test suites in `tests/`. (b) Ship: resolved in v2.3. Section 6 records both.
- The figures in Sections 2 to 5 and the table below describe the 15-agent state at the 2.0.0 rewrite.

## Status at the 2.0.0 rewrite (dated analysis)

Checks were run by the orchestrator after the prompt rewrites. Results are for the working tree, which also holds uncommitted work from other sources.

| Criterion | Result |
|---|---|
| Seven ranked proposals, each with all six fields | Met (Section 2) |
| Every prompts/A*.md parses as one well-formed XML document | Met: 15 of 15 |
| TOOL names equal the registry's permitted set, per agent | Met: 15 of 15, empty set difference; 37 of 37 TOOL entries |
| Every TOOL entry has the required children | Met: 37 of 37 |
| Each agent has 10 to 12 unique tools | Not met. All 15 are flagged under-equipped (1 to 7 registry grants). Open user decision. |
| Renderer-matched sentences present exactly once | Met: `build_agents.py` rendered all 15 and `--check` reports up-to-date |
| Pinned prompt invariants (tests/test_swarm.py, test_cursor_agents.py, test_omp_agents.py, test_render_rules.py) | Met: none of these tests fails |
| No references to the retired runtime outside historical .planning records and caches | Met |
| No E-POLICY token in prompts or generated agents | Met |
| Peer references limited to declared inputs, outputs, or A01 routing | Met for the static check; no runtime subset run was executed |
| Single-subagent dry run stays within the declared inventory | Not run. It needs a live agent session, which this environment cannot start. |
| Forced-fail fixture returns PARTIAL with the failed tool named | Not run. Requires a live session. |
| LENGTH_FIT against the target model's context | Not verified. The repository holds no token-limit configuration. Prompt sizes are 23.8 KB to 40.2 KB. |

Repository gates on the working tree: TypeScript root suite 1 of 1 passed; omp package suite 1,621 of 1,621 passed. Python: 70 test failures, all traced to uncommitted changes outside this work (see the next paragraph). Ruff and the registry check fail on the same uncommitted changes. HEAD passes all of these.

Failing tests come from uncommitted work by others. `scripts/swarm_run.py` adds top-level `from swarm.*` imports above its `sys.path` insert, which breaks subprocess runs (most of the failures). `swarm/errors.py` raises a NameError (`ErrorHandler`) that breaks test_substrate_handoff. `swarm/envelope.py` changes private-reference lookup (test_signing). The agents.json example for `orch_run` and `orch_reconcile` fails validation (test_tool_registry; `swarm.tool_registry check`). No failing test checks prompt content or the retired runtime.

Other findings from the rewrite:
- `data_down_present` always passes the fixed name `migration.py`, so the file-pair markers never fire. Only text markers count.
- `orch_assign` defaults `agent_id` to `a02-requirements` when omitted. The handler does not check the value against agents.json.
- The A01 prompt lists four script entries; agents.json lists three. HEAD already had four in the prompt.
- Rendered prompts carry XML entities literally (for example `&lt;`), because the renderer reads raw file text. The model sees the entity form.
