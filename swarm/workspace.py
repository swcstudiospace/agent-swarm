"""What an installed swarm workspace holds for the substrate (ADR 0001 S4; INST-01..04), shared by the installer
(`scripts/_install_substrate.py`), the runner (`scripts/swarm_run.py`) and the lease bridge (`swarm/substrate_lease.py`).

Two things, one per side of the token:

1. **The MCP entry.** `substrate_mcp.json` is the spec agent-substrate's projector emits
   (`bun packages/projector/src/swarm-workspace.ts --spec`), vendored because this repository is public and installs
   without that private checkout. It names the file each runtime reads and the entry, which spells the token as
   `${SUBSTRATE_TOKEN}`; each runtime expands it from its own environment.

2. **The agent's env file.** One per workspace and agent, outside the workspace and every git checkout:

       ${XDG_CONFIG_HOME:-~/.config}/agent-swarm/agents/<workspace key>/<slug>.env     (dir 0700, file 0600)

   holding exactly `SUBSTRATE_TOKEN=<that agent's token>`. The client name is `SUBSTRATE_TOKEN`; the server knows the
   same secret as `SUBSTRATE_TOKEN_<SURFACE>` (docs/substrate-workspace.md). The runner starts each agent session with
   this token and no other, and holds that agent's lease with it.

0600 separates OS accounts, not processes of one account: every agent `swarm_run.py` starts runs as the same user and
could read its siblings' files. The identities are attributable, not unforgeable, among agents sharing an account.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Mapping

from .manifest import get_agent

SPEC = json.loads((Path(__file__).resolve().parent / "substrate_mcp.json").read_text(encoding="utf-8"))
SERVER: str = SPEC["server"]
# Claude Code's name for every tool of the server; it goes in an agent's `tools:` and in `--allowedTools`.
CLAUDE_TOOLS = f"mcp__{SERVER}"
TOKEN = "SUBSTRATE_TOKEN"


class EnvFileProblem(ValueError):
    """An agent env file that cannot be used, with what the operator has to fix."""


def carries_token(key: str) -> bool:
    """Whether an environment variable can hand a process a token that is not its own: the client's
    `SUBSTRATE_TOKEN`, the server-side `SUBSTRATE_TOKEN_<SURFACE>` names, and the operator list."""
    return key.startswith(TOKEN) or key == "SUBSTRATE_OPERATOR_TOKENS"


def server_var(surface: str) -> str:
    """`swarm-a05-be` -> `SUBSTRATE_TOKEN_SWARM_A05_BE`: where the server, and the installer, read that agent's secret."""
    return f"{TOKEN}_{surface.upper().replace('-', '_')}"


def mcp_config(workspace: str | Path, runtime: str) -> Path:
    """The project file `runtime` reads the substrate entry from."""
    return Path(workspace) / SPEC["runtimes"][runtime]["path"]


def env_dir(workspace: str | Path, env: Mapping[str, str] | None = None) -> Path:
    """Where the workspace's agent env files live. Keyed by a hash of the workspace's real path, so installing a second
    workspace, which may talk to another substrate, never overwrites the first one's tokens."""
    e = os.environ if env is None else env
    xdg = (e.get("XDG_CONFIG_HOME") or "").strip()
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path(e.get("HOME") or Path.home()) / ".config"
    key = hashlib.sha256(str(Path(workspace).resolve()).encode("utf-8")).hexdigest()[:16]
    return base / "agent-swarm" / "agents" / key


def env_file(workspace: str | Path, slug: str, env: Mapping[str, str] | None = None) -> Path:
    return env_dir(workspace, env) / f"{slug}.env"


def valid_token(value: str) -> bool:
    """A token goes into one env-file line and one HTTP header, so it may hold no whitespace or control characters."""
    return bool(value) and all(c.isprintable() and not c.isspace() for c in value)


def read_token(path: Path) -> str:
    """The token in an agent env file. EnvFileProblem when it is missing, not a regular file of this user, readable by
    anyone else, or anything but the one line `SUBSTRATE_TOKEN=<token>`."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise EnvFileProblem(f"{path} does not exist") from None
    if not stat.S_ISREG(st.st_mode):
        raise EnvFileProblem(f"{path} is not a regular file")
    if st.st_uid != os.getuid():
        raise EnvFileProblem(f"{path} is not owned by this user")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise EnvFileProblem(f"{path} is mode {stat.S_IMODE(st.st_mode):04o}; it must be 0600")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    value = lines[0].removeprefix(f"{TOKEN}=") if len(lines) == 1 and lines[0].startswith(f"{TOKEN}=") else ""
    if not valid_token(value):
        raise EnvFileProblem(f"{path} must hold exactly one line {TOKEN}=<token> and nothing else")
    return value


def write_token(path: Path, token: str) -> None:
    """Write `SUBSTRATE_TOKEN=<token>` at `path`, 0600 from the first byte. The file is created beside the target and
    renamed over it, so a symlink planted at `path` is replaced rather than followed, and a reader never sees half."""
    if not valid_token(token):
        raise EnvFileProblem(f"refusing to write an empty or multi-line token to {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)  # the umask can only narrow the mode; this pins it
        os.write(fd, f"{TOKEN}={token}\n".encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def credential(agent_id: str | None, workspace: str | Path | None,
               env: Mapping[str, str] | None = None) -> tuple[str | None, str, str]:
    """(token, source, problem): THE lookup of an agent's own token, shared by its session (`child_env`), the leases the
    runner holds for it, its handoffs and the event tee, so all four always speak as the same identity.

    1. Its env file for `workspace`, when that file exists. A file that exists but is unusable (mode, owner, content)
       refuses: the operator put it there and it is wrong, so nothing else is tried.
    2. Else `SUBSTRATE_TOKEN_<SURFACE>` in `env`: the same secret under the server's name, deliberately given to this
       process (a pre-S4 runner holding every agent's token).
    3. Else nothing. Never `SUBSTRATE_TOKEN`: in the runner that is A01's, and a claim or a run-log row of another
       agent sent with it would be misattributed, or refused for naming another surface."""
    from .substrate_tee import AGENT_SURFACES  # lazy: the tee imports this module

    e = os.environ if env is None else env
    surface = AGENT_SURFACES.get(agent_id or "")
    if surface is None:
        return None, "", f"no substrate surface for agent {agent_id!r}"
    path = None
    if workspace is not None:
        try:
            path = env_file(workspace, get_agent(agent_id)["slug"], e)
        except KeyError:
            return None, "", f"no agent {agent_id!r} in the manifest"
        if os.path.lexists(path):
            try:
                return read_token(path), str(path), ""
            except (EnvFileProblem, OSError) as exc:
                return None, str(path), str(exc)
    var = server_var(surface)
    given = (e.get(var) or "").strip()
    if valid_token(given):
        return given, f"${var}", ""
    return None, "", f"{path or 'no workspace'}{' does not exist' if path else ''} and {var} is unset"


def child_env(base: Mapping[str, str], workspace: str | Path, agent_id: str) -> tuple[dict[str, str], str, str]:
    """(env, token source, problem) for one agent process: `base` without any variable that carries a token, plus that
    agent's own `SUBSTRATE_TOKEN` from `credential` when it has one."""
    env = {k: v for k, v in base.items() if not carries_token(k)}
    token, source, problem = credential(agent_id, workspace, base)
    if token is not None:
        env[TOKEN] = token
    return env, source, problem
