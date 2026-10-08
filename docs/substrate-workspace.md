# A swarm workspace wired to substrate-mcp (ADR 0001, phase S4)

Implements S4 of
[ADR 0001](https://github.com/swcstudiospace/agent-substrate/blob/main/docs/adr/0001-agent-swarm-as-sdlc-mesh-on-substrate.md)
(Linear SPE-5055; INST-01..05): `build_agents.py --install-workspace` alone wires a workspace to the substrate, and each
agent process holds only its own token.

Code: `scripts/_install_substrate.py` (the install step), `swarm/workspace.py` (the env files and the MCP spec, shared by
the installer, the runner and the lease bridge), `swarm/substrate_mcp.json` (the projector's spec), `scripts/swarm_run.py`
(`headless_command`), `swarm/substrate_lease.py` (`_call`). Tests: `tests/test_install_substrate.py`,
`tests/test_agent_token.py`, `tests/test_substrate_lease.py`.

## Install

```bash
export SUBSTRATE_TOKEN_SWARM_A01_ORCH=… … SUBSTRATE_TOKEN_SWARM_A15_DOC=…   # the values the server holds, one per agent
python3 scripts/build_agents.py --install-workspace /path/to/ws --dry-run   # the additions and the env files, nothing written
python3 scripts/build_agents.py --install-workspace /path/to/ws             # [--runtimes claude,grok,omp] [--no-substrate]
```

The install writes two things besides the agents, skills, hooks and omp package it always wrote.

**The `substrate` MCP entry**, once per file, in the project file each runtime reads:

| Runtime | File | Entry |
|---|---|---|
| claude, omp | `<ws>/.mcp.json` | `mcpServers.substrate = {type: http, url: ${SUBSTRATE_URL}/mcp, headers: {Authorization: Bearer ${SUBSTRATE_TOKEN}}}` |
| grok | `<ws>/.grok/config.toml` | `[mcp_servers.substrate]` with the same `url` and `headers` |

The entry is the one agent-substrate's projector emits (`bun packages/projector/src/swarm-workspace.ts --spec`), vendored
in `swarm/substrate_mcp.json` because this repository is public and installs without that private checkout. The rules are
the projector's: `substrate` is added where it is missing; every other server and key stays as it was; an identical entry
is left alone, so a second run changes nothing; a `substrate` entry that differs, unparsable JSON or TOML, or a symlink on
the way is refused (exit 2, nothing written) rather than replaced. A file that changes is first copied to
`<file>.substrate-backup`. Every runtime expands `${VAR}` in `url` and `headers` from its own environment. There is no
default URL: a swarm with `SUBSTRATE_URL` unset is off, and a default would point its agents at a server anyway.

The substrate step is an all-or-nothing transaction for the files it owns: agent credentials, MCP configs and their
`.substrate-backup` files. It stages every replacement and rollback copy before publishing any of them. A later write
failure restores earlier replacements and removes newly created files and empty directories. An intervening operator
edit is preserved rather than overwritten during rollback, and the conflict is reported with exit 2. Staging uses
unpredictable, exclusively created no-follow files; directory descriptors pin writes and cleanup, and destination and
ancestor safety is checked again before each publication. Temporary files are removed on success or failure. This is
handled-error rollback, not a crash-recovery journal or an atomic multi-file view for concurrent readers.

**One env file per agent**, outside the workspace and every git checkout:

```
${XDG_CONFIG_HOME:-~/.config}/agent-swarm/agents/<workspace key>/<slug>.env     dir 0700, file 0600
SUBSTRATE_TOKEN=<that agent's token>                                          the one line it holds
```

`<workspace key>` is the first 16 hex digits of the SHA-256 of the workspace's real path, so a second workspace, possibly
talking to another substrate, never overwrites the first one's tokens. The value comes from `SUBSTRATE_TOKEN_<SURFACE>` in
the installer's environment, e.g. `SUBSTRATE_TOKEN_SWARM_A05_BE` for `a05-backend.env`. With the variable unset, a file
that already holds a usable token (0600, this user's, exactly that one line) is kept, so an operator may create the files
by hand. When an agent has neither, the install prints each missing variable and the file it would go to, and exits 2
with nothing written: an install that wires the config without the token would 401 on every call, while the brief and the
trail fail open, so the agent would look almost healthy. The installer never reads `/etc/substrate/substrate.env`.

It also refuses (exit 2, nothing written):

- a runtime that cannot call MCP as an executing agent (`--runtimes claude,trae`): a node is leased over MCP only, and
  there is no REST `graph_claim` (ADR 0001 S4 criterion 6);
- an env-file directory inside the workspace, inside this checkout or inside any git checkout (a dotfiles repo behind
  `~/.config`, say); point `XDG_CONFIG_HOME` elsewhere for the install and `swarm_run.py` alike;
- an existing env-file directory that is not owned by the current user, grants any group/other permissions, or is
  reached through a symlink, even when every existing token file is valid and mode 0600. Preflight and `--dry-run`
  report the refusal before any write. The operator must fix ownership/permissions (private directory mode 0700) or
  select another `XDG_CONFIG_HOME`; the installer never auto-fixes existing directory permissions. Newly written
  token files, including their staging copies, are mode 0600 from their first byte;
- everything it refused before: a workspace inside this checkout, equal to `$HOME` or inside `~/.omp`, and any
  destination reached through a symlink.

`--no-substrate` installs the rest without the entry or the env files; the swarm then runs with the integration off.

## Run

`swarm_run.py --repo <ws>` treats `<ws>` as the installed workspace. For every agent session it:

- drops every `SUBSTRATE_TOKEN*` and `SUBSTRATE_OPERATOR_TOKENS` from the child's environment and sets `SUBSTRATE_TOKEN`
  to the agent's own token (see *One lookup* below). Without one the session gets no token at all, never the runner's;
- **claude**: runs in this checkout so `.claude/agents` resolves, where a workspace `.mcp.json` is never read, so it passes
  `--mcp-config <ws>/.mcp.json --strict-mcp-config` and adds `mcp__substrate` to `--allowedTools`. The generated agents
  list `mcp__substrate` in their `tools:`: a Claude session sees only the tools its agent names;
- **grok**: passes `--trust` only after parsing the config and finding the exact projected `mcp_servers.substrate`
  entry with `${SUBSTRATE_URL}` and `${SUBSTRATE_TOKEN}` references. An unrelated, malformed, symlinked or differing
  config never grants automatic trust. Grok loads a repo's `[mcp_servers]` (and its hooks) only in a trusted folder,
  and a workspace that is its own git checkout is not covered by a parent's trust. A `--yolo` session already runs
  every tool call unasked; `--trust` records the grant in `~/.grok/trusted_folders.toml`;
- **omp**: runs with `--cwd <ws>`, where it reads `.mcp.json`. Its `--tools` list names built-ins only and does not hide
  MCP tools, which mount as `xd://mcp__substrate_*` devices. The runner sets `SWARM_SUBSTRATE_AGENT` only when it finds
  the exact projected entry and resolves this agent's token. The swarm guard then allows the known substrate API for
  that matching session identity; operator-credential MCP tools and lookalike device names remain blocked. A01's
  depth cap still takes precedence.

Those flags appear only when the workspace has the exact projected entry, not merely a config file. `--dry-run` prints
the variables a replay must unset (names only) and, on a second line, the env file the session's `SUBSTRATE_TOKEN` comes
from; it never prints a token.

### One lookup

An agent's token is found in exactly one place in the code, `swarm/workspace.py` `credential(agent, workspace)`, and
every caller that speaks for that agent uses it: its session's environment, the leases the runner holds for it
([substrate-leases.md](substrate-leases.md)), its handoffs, and the event tee's rows attributed to it
([substrate-tee.md](substrate-tee.md)). So all four always carry the same identity. In order:

1. the agent's env file for the workspace, when the file exists. An unsafe env directory refuses even before file
   lookup. A file that exists but is unusable (not 0600, not this user's, not exactly one `SUBSTRATE_TOKEN=` line)
   also refuses, and nothing else is tried;
2. else `SUBSTRATE_TOKEN_<SURFACE>` in the process's environment (a pre-S4 runner given every agent's token);
3. else nothing: a claim is refused naming the file, a session starts without a token, and a run-log row stays local
   with one stderr note per agent. `SUBSTRATE_TOKEN`, which in the runner is A01's, never stands in.

The runner's own calls (`graph_bind`, `graph_register`, its memory brief) still use its `SUBSTRATE_TOKEN`. Its records are
teed under the workspace (`--repo`), so their repo slug and the env files the tee reads are the workspace's.

Standalone handoff recovery also supplies the installed workspace:
`substrate_handoff.reconstruct(graph_id, node_id, agent="A14", workspace=ws)`, with the same
`XDG_CONFIG_HOME` used at install time. That selects A14's private env-file token; the runner's
`SUBSTRATE_TOKEN` is not a receiver fallback. The workspace identifies credentials only: task context
still comes from exactly `coord_handoff_list` and `events_query`, not a Task Store or local run log
([substrate-handoffs.md](substrate-handoffs.md)).

## Who owns which variable

| Variable | Side | Owner | Where |
|---|---|---|---|
| `SUBSTRATE_TOKEN_<SURFACE>`, one per surface | server | the substrate operator | `/etc/substrate/substrate.env`, the server unit's `EnvironmentFile=`, root-owned 0600 |
| `SUBSTRATE_OPERATOR_TOKENS` | server | the substrate operator | same file; never a swarm token |
| `SUBSTRATE_TOKEN`, exactly one, the agent's own | agent | whoever runs the swarm | that agent's env file, written by the install |
| `SUBSTRATE_URL` | agent and runner | whoever runs the swarm | the runner's environment; every session inherits it |
| `SUBSTRATE_TOKEN_<SURFACE>` (install time) | installer | whoever runs the swarm | the installer's environment only; the same secret the server holds under that name |

One secret, two names, opposite ends: the server maps `SUBSTRATE_TOKEN_SWARM_A05_BE` to `swarm-a05-be`, and A05's process
presents the same value as `SUBSTRATE_TOKEN`. The full split is in agent-substrate `docs/swarm-workspace.md`.

## The shared-account limit

0600 separates OS accounts, not processes of one account. `swarm_run.py` starts every agent as the same user, so each
agent could read its siblings' env files and authenticate as them, and possession of a token is the identity. What S1's
identity work buys is real and bounded: no caller can name another surface over its own token, and accidental
misattribution and shared-holder lease renewal stop. The 15 identities are attributable, not unforgeable, among agents
sharing an account. A separate OS principal per agent, or a broker handing each process only its own short-lived
credential, would make them a security boundary; neither is in S4.
