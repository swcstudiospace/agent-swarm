"""Substrate step of `build_agents.py --install-workspace` (ADR 0001 S4; INST-01..04). Import-only; no CLI.

Two halves, planned together before anything is written, so a refusal leaves the workspace and the config dir as they were:

1. **MCP entries.** For each executing runtime, the `substrate` server in the project file that runtime reads, from the
   spec agent-substrate's projector emits (`swarm/substrate_mcp.json`, see swarm/workspace.py). The projector's rules
   apply unchanged: the entry is added where it is missing, every other entry and key is kept, an identical entry is
   left alone, and a differing `substrate` entry, unparsable JSON or TOML, or a symlink on the way is refused rather
   than replaced. A file that changes is first copied to `<file>.substrate-backup`. The entry names
   `${SUBSTRATE_TOKEN}`; no token value is ever written under the workspace.

2. **Agent env files.** One per agent, 0600, outside the workspace and every git checkout, holding exactly
   `SUBSTRATE_TOKEN=<that agent's token>`. The value comes from `SUBSTRATE_TOKEN_<SURFACE>` in the installer's own
   environment, the name the server reads the same secret under. With that variable unset, a file that already holds a
   usable token is kept, so an operator can create the files by hand and re-run. When neither is there the install
   prints what the operator must create and exits 2: an install that wires the config without the token would 401 on
   every call, and the brief and the trail fail open, so that agent would look almost healthy. The server's
   `/etc/substrate/substrate.env` is never read.

Every runtime the install wires must be able to execute a node, and a node is leased over MCP only (there is no REST
`graph_claim`), so a runtime without an entry in the spec is refused as an executing agent (ADR 0001 S4 criterion 6).
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, TextIO

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from _install_omp import unsafe_destinations  # noqa: E402 - the same symlink rule as every other destination
from swarm import workspace  # noqa: E402
from swarm.manifest import load_manifest  # noqa: E402
from swarm.substrate_tee import AGENT_SURFACES  # noqa: E402

SPEC = workspace.SPEC
SERVER = workspace.SERVER
RUNTIMES: tuple[str, ...] = tuple(SPEC["runtimes"])


class SubstrateInstallError(Exception):
    """A workspace or config state the substrate step will not write: exit 2, nothing written."""


@dataclass
class FilePlan:
    path: Path
    rel: str
    fmt: str
    runtimes: list[str]
    status: str  # create | append | current
    before: str | None
    after: str


@dataclass
class EnvPlan:
    agent_id: str
    surface: str
    var: str  # SUBSTRATE_TOKEN_<SURFACE>: where the installer reads the value
    path: Path
    status: str  # write | current | keep | missing | invalid
    token: str | None = field(default=None, repr=False)  # never printed
    problem: str = ""


@dataclass
class Plan:
    files: list[FilePlan]
    envs: list[EnvPlan]
    env_dir: Path


# ---------------------------------------------------------------- MCP entries (the projector's rules)

def _same(a: object, b: object) -> bool:
    """Key-order-insensitive, so a hand-written identical entry counts as ours."""
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _plan_json(path: Path, before: str | None) -> tuple[str, str]:
    entry = SPEC["json"]
    if before is None:
        return "create", json.dumps({"mcpServers": {SERVER: entry}}, indent=2, ensure_ascii=False) + "\n"
    try:
        doc = json.loads(before)
    except json.JSONDecodeError as e:
        raise SubstrateInstallError(f"{path} is not valid JSON, refusing to touch it: {e}") from None
    if not isinstance(doc, dict):
        raise SubstrateInstallError(f"{path} is not a JSON object, refusing to touch it")
    servers = doc.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise SubstrateInstallError(f"{path} has an mcpServers that is not an object")
    if SERVER in servers:
        if _same(servers[SERVER], entry):
            return "current", before
        raise SubstrateInstallError(
            f"{path} already has an mcpServers.{SERVER} entry that differs from the projection; the installer never "
            "replaces an entry it did not write identically. Remove it and re-run")
    doc["mcpServers"] = {**servers, SERVER: entry}  # appended last, everything else in its original order
    return "append", json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _without_ours(doc: dict, had_servers: bool) -> dict:
    copy = dict(doc)
    if isinstance(copy.get("mcp_servers"), dict):
        rest = {k: v for k, v in copy["mcp_servers"].items() if k != SERVER}
        if rest or had_servers:
            copy["mcp_servers"] = rest
        else:
            del copy["mcp_servers"]
    return copy


def _plan_toml(path: Path, before: str | None) -> tuple[str, str]:
    block, entry = SPEC["toml"]["block"], SPEC["toml"]["entry"]
    if before is None:
        return "create", block
    try:
        doc = tomllib.loads(before)
    except tomllib.TOMLDecodeError as e:
        raise SubstrateInstallError(f"{path} is not valid TOML, refusing to touch it: {e}") from None
    servers = doc.get("mcp_servers")
    if servers is not None and not isinstance(servers, dict):
        raise SubstrateInstallError(f"{path} has an mcp_servers that is not a table")
    if servers and SERVER in servers:
        if _same(servers[SERVER], entry):
            return "current", before
        raise SubstrateInstallError(
            f"{path} already has an [mcp_servers.{SERVER}] entry that differs from the projection; the installer never "
            "replaces an entry it did not write identically. Remove it and re-run")
    # Appended as text and read back: it must parse, hold exactly our entry and leave every other key as it was. An
    # inline `mcp_servers = { … }` table, for one, cannot be extended this way and is refused rather than rewritten.
    body = before if not before or before.endswith("\n") else before + "\n"
    after = body + ("\n" if body else "") + block
    try:
        reread = tomllib.loads(after)
    except tomllib.TOMLDecodeError as e:
        raise SubstrateInstallError(f"appending to {path} would not parse, so nothing was written: {e}") from None
    ours = (reread.get("mcp_servers") or {}).get(SERVER)
    if not _same(ours, entry) or not _same(_without_ours(reread, servers is not None), doc):
        raise SubstrateInstallError(f"appending [mcp_servers.{SERVER}] to {path} would change other config; add it by hand")
    return "append", after


def plan_mcp(ws: Path, runtimes: list[str]) -> list[FilePlan]:
    """The MCP files to write, grouped by path (Claude and omp share `.mcp.json`). Reads only."""
    by_path: dict[str, FilePlan] = {}
    for runtime in runtimes:
        rel = SPEC["runtimes"][runtime]["path"]
        if rel in by_path:
            by_path[rel].runtimes.append(runtime)
            continue
        path = ws / rel
        unsafe = unsafe_destinations(ws, [path])
        if unsafe:
            raise SubstrateInstallError("; ".join(unsafe) + "; the installer never writes through a symlink")
        before = path.read_text(encoding="utf-8") if path.exists() else None
        fmt = SPEC["runtimes"][runtime]["format"]
        status, after = (_plan_json if fmt == "json" else _plan_toml)(path, before)
        by_path[rel] = FilePlan(path, rel, fmt, [runtime], status, before, after)
    return list(by_path.values())


def describe_mcp(files: list[FilePlan]) -> list[str]:
    """The same lines `swarm-workspace.ts --plan` prints: per file, what is added."""
    lines = []
    for f in files:
        who = ", ".join(f.runtimes)
        if f.status == "current":
            lines.append(f"= {f.rel}: {SERVER} already present ({who})")
            continue
        lines.append(f"{'+ create' if f.status == 'create' else '+ append to'} {f.rel} ({who}):")
        added = (SPEC["toml"]["block"].rstrip("\n") if f.fmt == "toml"
                 else json.dumps({"mcpServers": {SERVER: SPEC["json"]}}, indent=2))
        lines += [f"    {line}" for line in added.split("\n")]
    return lines


# ---------------------------------------------------------------- agent env files

def location_problem(ws: Path, directory: Path) -> str | None:
    """Why the env files must not live in `directory`, or None. Checked on the real path, so a config dir that is a
    symlink into a dotfiles checkout is caught too."""
    real = directory.resolve()
    if real.is_relative_to(ws.resolve()):
        return f"{directory} is inside the workspace {ws}"
    if real.is_relative_to(ROOT.resolve()):
        return f"{directory} is inside the agent-swarm checkout {ROOT}"
    for parent in (real, *real.parents):
        if (parent / ".git").exists():
            return f"{directory} is inside the git checkout {parent}"
    return None


def plan_env(ws: Path, env: Mapping[str, str]) -> list[EnvPlan]:
    plans = []
    for agent in load_manifest():
        surface = AGENT_SURFACES[agent["id"]]
        var = workspace.server_var(surface)
        path = workspace.env_file(ws, agent["slug"], env)
        given = (env.get(var) or "").strip()
        try:
            held: str | None = workspace.read_token(path)
            problem = ""
        except (workspace.EnvFileProblem, OSError) as e:
            held, problem = None, str(e)
        if given and not workspace.valid_token(given):
            plans.append(EnvPlan(agent["id"], surface, var, path, "invalid",
                                 problem=f"{var} holds whitespace or control characters"))
        elif given:
            plans.append(EnvPlan(agent["id"], surface, var, path, "current" if held == given else "write", given))
        elif held is not None:
            plans.append(EnvPlan(agent["id"], surface, var, path, "keep"))
        else:
            missing = not os.path.lexists(path)
            plans.append(EnvPlan(agent["id"], surface, var, path, "missing" if missing else "invalid", problem=problem))
    return plans


def operator_instructions(plans: list[EnvPlan], directory: Path) -> str:
    """What the operator must create when the install cannot deliver every agent's token (INST-03)."""
    todo = [p for p in plans if p.status in ("missing", "invalid")]
    rows = "\n".join(f"  {p.var:<34} -> {p.path}" + (f"  ({p.problem})" if p.status == "invalid" else "")
                     for p in todo)
    return (
        f"error: the substrate install needs each agent's own token and has no usable one for {len(todo)} agent(s); "
        "nothing was written.\n"
        "For each agent below, either export the variable with the value the server holds for that surface (the same "
        "secret it reads as SUBSTRATE_TOKEN_<SURFACE>; this installer never reads /etc/substrate/substrate.env), or "
        "create the file yourself: mode 0600, owned by the user that runs swarm_run.py, holding exactly one line "
        "SUBSTRATE_TOKEN=<that token>. Then re-run. To install without the substrate, pass --no-substrate.\n"
        f"{rows}\n"
        f"(the files live in {directory}, outside the workspace and every git checkout; XDG_CONFIG_HOME moves it, for "
        "the install and swarm_run.py alike)"
    )


# ---------------------------------------------------------------- plan, preflight, install

def runtime_problem(runtimes: list[str]) -> str | None:
    unknown = [r for r in runtimes if r not in RUNTIMES]
    if not unknown:
        return None
    return (f"error: refusing to wire {', '.join(unknown)} as an executing agent: it has no substrate-mcp entry, and a "
            f"swarm node is leased over MCP only (no REST graph_claim; ADR 0001 S4 criterion 6). Executing runtimes: "
            f"{', '.join(RUNTIMES)}. Nothing was written.")


def plan(ws: Path, runtimes: list[str], env: Mapping[str, str]) -> Plan:
    """Everything the step would write. Reads only; SubstrateInstallError for a state it will not write."""
    ws = Path(ws).resolve()
    problem = runtime_problem(runtimes)
    if problem:
        raise SubstrateInstallError(problem)
    directory = workspace.env_dir(ws, env)
    where = location_problem(ws, directory)
    if where:
        raise SubstrateInstallError(
            f"error: cannot keep the agent env files in {directory}: {where}. Point XDG_CONFIG_HOME, for the install and "
            "for swarm_run.py alike, at a directory outside every git checkout and outside the workspace, and re-run. "
            "Nothing was written.")
    try:
        files = plan_mcp(ws, runtimes)
    except SubstrateInstallError as e:
        raise SubstrateInstallError(f"error: cannot add the {SERVER} MCP entry in {ws}: {e}. Nothing was written.") from None
    return Plan(files, plan_env(ws, env), directory)


def preflight(ws: Path, runtimes: list[str], env: Mapping[str, str] | None = None, *, tokens: bool = True) -> str | None:
    """The error the step would stop on (exit 2), or None. Reads only. `tokens=False` (a dry run) leaves missing tokens
    to the plan it prints."""
    e = os.environ if env is None else env
    try:
        p = plan(ws, runtimes, e)
    except SubstrateInstallError as exc:
        return str(exc)
    if tokens and any(x.status in ("missing", "invalid") for x in p.envs):
        return operator_instructions(p.envs, p.env_dir)
    return None


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.substrate-tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def install_substrate(ws: Path, runtimes: list[str], dry_run: bool, out: TextIO = sys.stdout,
                      env: Mapping[str, str] | None = None) -> int:
    """Write (or, dry, print) the MCP entries and the agent env files. 0 on success, 2 on a refusal or a token the
    operator still has to provide, with nothing written."""
    ws = Path(ws).resolve()
    e = os.environ if env is None else env

    def say(line: str) -> None:
        print(line, file=out)

    try:
        p = plan(ws, runtimes, e)
    except SubstrateInstallError as exc:
        say(str(exc))
        return 2
    short = [x for x in p.envs if x.status in ("missing", "invalid")]
    if dry_run:
        say(f"dry-run: {SERVER} MCP entries (agent-substrate packages/projector, spec v{SPEC['version']}):")
        for line in describe_mcp(p.files):
            say(line)
        say(f"dry-run: agent env files in {p.env_dir} (dir 0700, each file 0600 holding one SUBSTRATE_TOKEN line):")
        for x in p.envs:
            what = {"write": f"would write SUBSTRATE_TOKEN from ${x.var}", "current": f"unchanged (matches ${x.var})",
                    "keep": "kept: already holds a token", "missing": f"MISSING: export {x.var} or create the file",
                    "invalid": f"UNUSABLE: {x.problem}"}[x.status]
            say(f"  {x.path.name:<26} {what}")
        if short:
            say(operator_instructions(p.envs, p.env_dir))
            return 2
        return 0
    if short:
        say(operator_instructions(p.envs, p.env_dir))
        return 2
    # The env files first: they live outside the workspace, so a failure here leaves the workspace untouched.
    written = 0
    for x in p.envs:
        if x.status != "write":
            continue
        try:
            workspace.write_token(x.path, x.token or "")
        except OSError as exc:
            say(f"error: cannot write {x.path}: {exc.strerror or exc}")
            unwritten = [dataclasses.replace(y, status="missing") for y in p.envs[p.envs.index(x):] if y.status == "write"]
            say(operator_instructions(unwritten, p.env_dir))
            return 2
        written += 1
    say(f"substrate: {written} agent env file(s) written in {p.env_dir}, {len(p.envs) - written} unchanged "
        "(0600, one SUBSTRATE_TOKEN each)")
    for f in p.files:
        who = ", ".join(f.runtimes)
        if f.status == "current":
            say(f"substrate: {f.path} already has {SERVER} ({who})")
            continue
        now = f.path.read_text(encoding="utf-8") if f.path.exists() else None
        if now != f.before:
            say(f"error: {f.path} changed while the install ran; re-run")
            return 2
        backup = ""
        if f.before is not None:
            _write_atomic(f.path.with_name(f.path.name + ".substrate-backup"), f.before)
            backup = f"; backup at {f.path}.substrate-backup"
        _write_atomic(f.path, f.after)
        say(f"substrate: {'created' if f.status == 'create' else 'appended ' + SERVER + ' to'} {f.path} ({who}){backup}")
    return 0
