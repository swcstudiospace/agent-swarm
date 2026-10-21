---
name: "a03-architect--blueprint"
description: "Use when a design.blueprint task arrives with allocated NFRs and a C4 blueprint with fitness checks must be authored from scratch."
disable-model-invocation: true
---

## When to Use
- A `design.blueprint` task is assigned and the NFR bindings exist (`skill://a03-architect--nfr-allocation`).
- You are the single writer of `architecture.blueprint`. This skill authors it. To re-audit an existing blueprint after a CI violation use `skill://a03-architect--fitness-run`.
- Decision order (agents/A03 section 4): catalog pattern first, contract before task references, NFR allocation, fitness functions, compatibility, boundaries.

## Procedure
1. Call `shared_handover_read` with `{task_id}`. Take `requirements.spec`, NFR bindings, `infra.constraints` and `security.policy` from `artifacts`. Missing bindings: run `skill://a03-architect--nfr-allocation` first.
2. Use `read` on the catalog `infra.constraints` names and pick the approved pattern for the use-case class. A catalog miss goes through `skill://a03-architect--adr-record`.
3. Use `write` to draft the C4 model (Structurizr DSL or Mermaid): containers, relations, module-to-layer assignments and `layer_order` listed from the outermost layer to the innermost (a module may depend on its own layer or a later one).
4. Call `arch_c4_fanout` with `{containers, relations, fan_out_budget}` (1 to 100; unique container names; every relation endpoint must be a listed container). Split each `over_budget` entry (`name=count`); review `isolated` ones.
5. Call `arch_layer_violations` with `{layer_order, assignments, edges}` from the model. Fix every `violations` entry; modules in `unassigned` are not judged, so give them a layer.
6. Call `arch_cycle_check` with the same `edges`. Break each `cyclic_components` entry by inverting or extracting a dependency.
7. Map every violation, cycle and over-budget container to `major` (blocks) or `minor` (does not) and call `shared_verdict` with `{severities: [...]}` (values `info|minor|major|critical|blocker`; `major` or higher gives `fail`). On `fail` edit the model and repeat steps 4 to 7, at most 2 loops; then de-scope or call `shared_escalate` with `reason_code` `blocked`.
8. Use `write` to embed the supply-chain rows (SBOM requirement, dependency allowlist, licence matrix) and the fitness functions CI will run.
9. Call `shared_artifact_digest` with the blueprint `path`, then `shared_handover_validate` (`draft` true) and `shared_handover_write`.

## Outputs
- `architecture.blueprint` file with C4 views, layer order, fan-out budget and fitness functions.
- Fitness result: counts per check and the `shared_verdict` verdict.

## Failure Handling
- `INVALID_INPUT` names the field (unknown container, duplicate name, layer not in `layer_order`): correct the model, not the check input.
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry at most twice; a missing check is `skipped:tool-missing`, never a pass.
- Two fix loops used and still `fail`: de-scope the container or escalate; never waive the check.

## Handover
Fill `goal`, `completed` (views drawn, checks passed), `decisions` (pattern and rationale), `artifacts` (blueprint, digest, `producer` A03), `constraints` (layer order, fan-out budget), `open_questions`, `blockers` and `verification` (`status`, `confidence`, `checks` = the tool names). Once the verdict is `pass` write once each to A05, A06 and A07 (`to_agent`; the receiver's downstream `task_id` when the plan names it, else yours).

<!-- generated from prompts/skills/a03-architect/blueprint.md; edit the source and run scripts/build_agents.py -->
