# Trae SOLO AgentSwarm

This is a generated **manual registration kit**, not a Trae import API.
Files in `.trae/agents/` do not automatically register agents. XML tags structure
the prompts; they do not execute tool calls. All 15 prompts are below 10,000 characters.

## Register Once

1. In Trae, enter `@` and choose **Create Agent**, then manual creation.
2. For each row in [registration.json](registration.json), use `name`, the content
   of `prompt_file` as the Prompt, and enable **Callable by other agents**.
3. Set **English Identifier** to `english_identifier` exactly, and **When to Call**
   to `when_to_call`. Enable the listed built-in `tools` as needed.
4. In SOLO Agent's configuration, choose **Edit Tools** and enable these 15 custom
   agents as callable. Keep the model configured in Trae; no Claude/Grok login is needed.

The registration JSON is our checklist, not an official import schema. Read/Edit/
Terminal labels refer to UI permissions, not assumed tool API names. Terminal can
write files even for reviewers: prompt ownership rules are not a security sandbox.
Use host permission controls and required worktree isolation for enforcement.

## Run

Open AgentSwarm as the project to expose [commands/swarm.md](commands/swarm.md) as
`/swarm` where project commands are supported. With a different workspace root,
add that command through Trae's custom-command UI or explicitly ask SOLO to read
and follow that file. This generator does not edit global Trae settings or commands.

Example request:

```text
/swarm In /absolute/path/to/app, implement the approved feature described in brief.md.
Use /absolute/path/to/agent-swarm as swarm_root. Delegate through the registered
AgentSwarm agents. Prepare release plans only; do not commit, push or deploy.
```

SOLO calls A01 -> A01 returns a ready batch -> SOLO invokes the specialists ->
SOLO returns their evidence and the ledger to A01 -> repeat.
Always start through **SOLO**, not by opening A01 as a standalone chat.
Every role is accounted for. A full SDLC request includes bounded work for A02-A15;
irrelevant roles are explicitly not applicable for narrower tasks.

If an identifier is unavailable, the flow stops with registration instructions.
Generic agents are not silently substituted. Child contexts are independent;
SOLO passes the ledger and required artifacts on each call.

## Protocol and Limits

- A01 owns logical task state and artifact ownership; SOLO owns native invocations.
- Tasks carry identity, correlation, capability, revision, target, owned files,
  dependencies, inputs, acceptance, risk, budgets and approval constraints.
- Specialists return `task.result`; A01 returns `swarm.dispatch`. Example JSON
  values in the prompts are templates, never evidence that work already succeeded.
- Gate reports target exact revisions. Required missing/skipped checks block
  acceptance. Rework is capped at two cycles, then human escalation.
- This adapter uses native-session handoffs, **not** signed `swarm.v1` messages.
  It does not use the SQLite scheduler or claim cryptographic verification.
  Signed-runtime integration requires separate approval and implementation.
- Existing scripts are optional helpers. Inspect `--help`, supply actual inputs,
  and select only needed checks. Scripts may write `.swarm/` or require signing
  configuration; obtain approval before integrating that stateful runtime.
  Direct repository test/lint commands are suitable evidence in native mode.
- No hooks, headless model processes, auto-registration, automatic deployment,
  background monitoring or external board synchronization are installed.
- This kit is structurally tested. A real callable-agent invocation in your Trae
  UI is still required to validate registration and end-to-end dispatch.

## Maintain

Edit `agents.json` and the role/decision_logic/autonomy sections of `prompts/`
for domain behavior. Edit `scripts/build_trae_agents.py` for the Trae adapter,
handoff protocol and flow command. Do not hand-edit generated output.

```bash
python3 scripts/build_trae_agents.py
python3 scripts/build_trae_agents.py --check
python3 -m pytest tests/test_trae_agents.py -q
```

Regeneration never changes `.claude/`, `.grok/`, skills, hooks or global config.
It refuses oversized prompts rather than silently truncating role rules.

## References

- [Create and manage custom agents](https://docs.trae.ai/ide/agent?_lang=en)
- [SOLO Agent and callable-agent configuration](https://docs.trae.ai/ide/solo-coder?_lang=en)
