# Operations runbook

Commands assume the repo root. The registry command is `python3 -m swarm.tool_registry`. Docs and contract tests are generated; do not hand-edit `docs/tools/` or `tests/generated/test_tool_contracts.py`.

CI (`.github/workflows/ci.yml`) runs `check`, `docs --check`, and `gen-tests --check` on every push and pull request. `.pre-commit-config.yaml` runs the same three checks before a commit when the hook is installed. Both checks are read-only: drift fails instead of silently regenerating files. CI remains the enforced branch gate.

## Enable the commit-time drift gate

Install the optional `pre-commit` development tool, then run:

```bash
pre-commit install
pre-commit run --all-files
```

The hooks use the existing `python3` runtime, pass no filenames, and run even when the staged files are unrelated to the registry. At commit time, pre-commit temporarily stashes unstaged changes, so the checks see the staged snapshot. If a hook fails, regenerate the docs and contract tests using the commands below, stage the generated files with the registry change, and retry.

Without pre-commit, run `python3 -m swarm.tool_registry check`, `docs --check`, and `gen-tests --check` locally before pushing. The swarm runtime does not depend on pre-commit, and the existing CI checks remain active.

## Add a lane tool

1. Pick the owner agent and the prefix from the manifest (`orch_`, `req_`, `arch_`, `ux_`, `be_`, `fe_`, `data_`, `qa_`, `rev_`, `sec_`, `devops_`, `rel_`, `obs_`, `maint_`, `docs_`). The name must be unique.
2. Add a handler `call`-style function under `swarm/tools/`. The registry imports it only through `call`.
3. Append a `swarm.tool.v1` object to that agent's `registered_tools` in `agents.json`: purpose, input and output schemas, the five error states, `timeout_s`, idempotency, `permitted_callers`, `entrypoint`, `handler`, and examples for success, invalid input, an unauthorized caller, dependency, timeout, and partial success.
4. `python3 -m swarm.tool_registry check`
5. `python3 -m swarm.tool_registry docs` and `python3 -m swarm.tool_registry gen-tests`
6. `python3 -m pytest -o addopts='' tests/generated/test_tool_contracts.py tests/test_lane_tools.py`

Counts below 8 are normal. Do not invent tools to fill the table. Write the reason in the phase dedup notes.

## Add a shared tool

Same contract, with three differences:

- `owner` is `shared` and the name starts with `shared_`.
- The object goes in top-level `shared_tools`, not on an agent.
- `permitted_callers` lists at least two agent ids that already perform this operation. A one-caller helper stays a lane tool.

Then regenerate docs and contract tests the same way. The catalog groups the new row under `### shared` and under each permitted agent.

## Change ACP permissions

Permissions are the manifest, not a second file.

- To allow a message, add the type to the sender's `produces` and the recipient's `consumes`. Both sides are required. Removing either side denies the message.
- To allow a tool, add the agent id to that tool's `permitted_callers`.
- `python3 -m swarm.tool_registry check` after the edit. `routing_table()` in `swarm/acp.py` picks up the new rows on the next process start. There is no peer list to update.

A denied message or tool returns `UNAUTHORIZED` and an audit entry `{caller, target, reason}`.

## Diagnose a dead or timing-out peer

1. Read the bus record `state`, `attempts`, `reason`, and `history`.
2. `DEAD_LETTERED` means the retry budget ran out. Open `Bus.dead` and read the stored envelope. The payload is intact. Compare `correlation_id` with the sender's log.
3. `reason: circuit open` means this peer already failed `breaker_threshold` times. Later calls do not wait for `timeout_hint_s`. Fix or restart the peer handler, then construct a new `Bus` (the breaker is in-memory).
4. Confirm the sender's own tool still works: `python3 -m swarm.tool_registry call --name <lane tool> --caller <agent> --input '<json>'`. A local SUCCESS next to a dead peer is expected.
5. `INVALID_INPUT` and `UNAUTHORIZED` are not peer health. Fix the envelope or the matrix. They do not move the breaker.

## Delivery states

| State | Operator action |
| --- | --- |
| `RESPONDED` | None. Match `correlation_id` to the request. |
| `INVALID_INPUT` | Read `errors` or `reason`. Fix the sender. Do not retry by hand more than once; the bus will not. |
| `UNAUTHORIZED` | Read the audit reason. Change `produces` / `consumes` or `permitted_callers` if the call should be legal. |
| `TIMED_OUT` | Intermediate. The bus already schedules the next attempt. |
| `FAILED` | Peer missing, handler crash, or circuit open. Read `reason`. |
| `DEAD_LETTERED` | Retry budget finished. Replay only after the peer is healthy, using the retained envelope. |

## Regenerate docs

```bash
python3 -m swarm.tool_registry check
python3 -m swarm.tool_registry docs
python3 -m swarm.tool_registry gen-tests
python3 -m swarm.tool_registry docs --check
python3 -m swarm.tool_registry gen-tests --check
```

`--check` exits 1 and prints the drifted paths. Commit the regenerated catalog, agent pages, and contract tests together. The pages always contain identity, handoffs, lane tools, shared tools, the ACP profile, and degradation. Empty lanes say so in a sentence; they do not use placeholder headings.
