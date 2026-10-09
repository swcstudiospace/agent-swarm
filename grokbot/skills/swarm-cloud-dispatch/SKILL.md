---
name: swarm-cloud-dispatch
description: "Desk Lead dispatches a unit to a keyless Cursor cloud agent. The brief is the ultrathink node prompt plus seat, branch and graph id. The agent returns a draft pull request and Ming merges."
---

# Swarm cloud dispatch

This file is the single source for handing a unit to a Cursor cloud agent that runs the 15 AgentSwarm agents as subagents. Use it when Desk Lead has an ultrathink node prompt, a seat, a branch and a graph id.

The box copy is not edited by hand. After Ming merges this file and approves the diff, Desk Lead copies this file over the box copy and records the sha256 of both.

```bash
sha256sum grokbot/skills/swarm-cloud-dispatch/SKILL.md
```

## Trust

Cloud runs are keyless and advisory. A cloud session never receives `SWARM_SIGNING_KEY`, `SWARM_ED25519_KEY`, or a substrate token (`SUBSTRATE_TOKEN`, including any `SUBSTRATE_TOKEN_*` name). Do not place a signing key or a substrate token on the cloud VM.

Finished checks are advisory-complete (IN_REVIEW, never APPROVED) per B2. B2 marks finished quality, review and security checks `IN_REVIEW` so later build, release, observability and docs tasks can proceed in the same draft pull request. Those checks never move to `APPROVED`, and the gate scripts write no verdict rows. Nothing a cloud run records counts as APPROVED. The merge gate is Greptile, run by Desk Quality, and then Ming.

## Brief

The brief is not a fixed wording. It is built from the ultrathink node prompt (the uplift plus that node's chain of thought) plus the seat, the branch and the graph id, via the port's prompts build.

The agent passes that graph id to `scripts/orch_plan.py --graph-id`. The id has the form `ut-<base36>-<8 hex>`.

```bash
python3 scripts/orch_plan.py --root <repo> --brief-text "<unit>" --pattern feature --risk-class low --graph-id <graph-id> --json
```

Use the unit's pattern (`feature`, `hotfix` or `dependency`) and its risk class. The command above is the shape, with `--graph-id` set to the ultrathink graph id.

## Branches

Programming-desk work uses `bot-0N-<seat>/<slug>` for the seat that owns the changed paths. Agent-swarm work uses `grokbot/<slug>`. An existing pull request keeps its branch.

If the base branch moves, bring it in with a merge commit. Do not rebase or force-push.

## Install

Pin an agent-swarm checkout and set `SWARM_ROOT` to that absolute path. The target repo does not vendor the swarm runtime. The usual install target is `swcstudiospace/programming-desk`, which already has its own `scripts/` directory. That directory is not the swarm tools.

Pin agent-swarm main, which carries the keyless advisory export. Installed agents keep pull requests in draft and do not auto-merge. A keyless run is advisory.

Open a pull request on the target repo that installs the agents. From the pinned checkout, dry-run first, then copy:

```bash
python3 scripts/_install_cursor.py --target /path/to/target-repo --dry-run
python3 scripts/_install_cursor.py --target /path/to/target-repo
```

`build_agents.py --install-cursor /path/to/target-repo` is the same copy. It writes only under `.cursor/` (the 15 agents, `.cursor/rules/agent-swarm.mdc`, and a provenance stamp). It does not write MCP config or env files, and it does not wire substrate.

## Dispatch

Brief a Cursor cloud agent on that repo to run `a01-orchestrator` with the brief from the Brief section. Nesting is two levels: the cloud session may spawn `a01-orchestrator`; A01 may spawn `a02-requirements` through `a15-docs`; those specialists must not spawn further.

Do not start an unattended headless runner. Do not grant a permission-bypass mode.

## Greptile loop

Desk Lead runs the Greptile loop, cap 5 rounds. The pull request's own agent fixes every finding.

After the draft pull request is opened, and after every later push, wait for Greptile's review of that head (the summary comment with the n/5 score, plus inline threads). If no Greptile review of the current head appears within 20 minutes of the push, post exactly one pull-request comment `@greptileai please review` for that head. Do not post it more than once per head.

Fix every finding in new commits on the same branch. Reply on each thread with the fixing commit and resolve the thread only when the change addresses it. One round is one push answering one review. Stop at the first of: Greptile 5/5 on the current head with zero unresolved threads and required checks green; cap 5 used without 5/5; or a finding that needs a decision outside the unit's scope. A failed, skipped or missing review is not a pass. Reaching 5/5 does not authorise a merge.

## Receipt

The receipt goes in the PR body. Include the files changed, the red-first run, the full suite results, the sha256 of this skill, the advisory swarm state, and the open risks.

## Status

Desk Lead posts unit status (graph id, unit, pull request, Greptile score, advisory gate results) to the desk gateway under the ultrathink graph id. The cloud agent does not post to the desk gateway and holds no substrate credentials.

## No-merge

The rule is no-merge. Leave the pull request in draft. Never merge, enable auto-merge, or delete the branch from this skill. Ming merges.
