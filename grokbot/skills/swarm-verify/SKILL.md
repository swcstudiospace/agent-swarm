---
name: swarm-verify
description: >
  Grok Bot lane verify for a08-qa, a09-reviewer, a10-security.
  Use when Desk Lead routes work to this lane of the 15 AgentSwarm roles.
disable-model-invocation: false
---

# Swarm lane verify

Generated from agents.json and the role prompts. Do not edit by hand.
Regenerate with `python3 scripts/build_agents.py`.

## When to Use

- The assignment belongs to lane `verify`.
- The role slug is one of: a08-qa, a09-reviewer, a10-security.

## Procedure

1. Read the role prompt named below. The swarm has exactly these 15 roles.
2. Route the change with `grokbot/swarm/seat-map.json`. A routing rule wins over role path globs. Android paths go to bot-03, iOS paths to bot-04, and desktop shells to bot-02.
3. Run that role's scripts with `python3 scripts/<script>.py --json` (the bun twin is `scripts/ts/<script>.ts`). Stay inside the role's path globs and autonomy ceiling.
4. Verify with the seat's verification tools from the seat map.
5. Finish with a task.result. A keyless cloud session is advisory. Leave the pull request in draft.

## Roles

### A08 QA (a08-qa)

- Prompt: `prompts/A08-qa.md`
- Role: Senior Test Engineer (A08 QA, slug a08-qa) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A08-qa.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for test.suite, test.results, quality gate.verdict.
- Home: `bot-06-quality-security`. Seat: `bot-06-quality-security`.
- Autonomy ceiling: block_merge=L2, run_suites=L2, waive=L3.
- Scripts: `scripts/qa_gate.py`.

### A09 REV (a09-reviewer)

- Prompt: `prompts/A09-reviewer.md`
- Role: Senior Code Reviewer (A09 REV, slug a09-reviewer) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A09-reviewer.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for review.verdict / review gate.verdict (read-only on product code).
- Home: `bot-06-quality-security`. Seat: `bot-06-quality-security`.
- Autonomy ceiling: verdict=L2, waive=L3.
- Scripts: `scripts/rev_gate.py`.

### A10 SEC (a10-security)

- Prompt: `prompts/A10-security.md`
- Role: Senior Security Auditor (A10 SEC, slug a10-security) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A10-security.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for security gate.verdict, vulnerability.report (read-only on product code).
- Home: `bot-06-quality-security`. Seat: `bot-06-quality-security`.
- Autonomy ceiling: accept_risk=L4, block_release=L2, verdict=L2.
- Scripts: `scripts/sec_gate.py`.
