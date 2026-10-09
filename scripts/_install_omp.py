"""omp step of `build_agents.py --install-workspace` (Phase 7, PKG-01..03). Import-only; no CLI.

link (default, D-01..D-04): merge the realpath of this checkout's `omp/` package into `<ws>/.omp/config.yml`
`extensions:` line by line; every other line stays byte-for-byte. copy (D-06): copy the generated agents and
skills into `<ws>/.omp/` and leave the config alone. Both modes print one WARNING per same-name agent or skill that
shadows the package (D-05); warnings never change the exit code. Copy mode additionally refuses when a strict
ancestor of the workspace shadows the package (T-07-20): the copies would run guard-less with shadowed skills;
pass `allow_shadowed_copy=True` to proceed with the warnings. Nothing here writes into this repo or `~/.omp`.
"""
from __future__ import annotations

import difflib
import errno
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, TextIO

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PKG = (ROOT / "omp").resolve()

COPY_WARNING = (
    "WARNING copy mode: agents and skills only — no tools (swarm_*), no guard (tool_call), no /swarm command, "
    "no context hook; re-run after regenerating"
)
NOTE_CWD = "note: start omp at {ws} (project config is read from the cwd only; subdirectories load nothing)"
NOTE_FRESH = "note: start a new omp session to pick up the install (extensions and agents load at session start)"
NOTE_DEPTH = (
    "note: task.maxRecursionDepth 2 (default) fits session → a01-orchestrator → specialists; "
    "set 3 only when A01 is spawned by another subagent"
)


class InstallError(Exception):
    """Unsupported workspace state: exit 2, nothing written."""


class UnsafeDestination(InstallError):
    """A destination the installer would reach through a symlink, hardlink, or swapped path: exit 2, nothing written (CR-01, T-07-05)."""


@dataclass(frozen=True)
class Shadow:
    """A same-name agent or skill that omp resolves ahead of the package."""
    kind: str  # "agent" | "skill"
    name: str
    path: Path
    level: str  # "project" | "user" | "extension"
    custom: bool = field(default=False, compare=False)  # found through skills.customDirectories

    def line(self) -> str:
        return f"WARNING shadow: {self.kind} {self.name} at {self.path} ({self.level}) shadows the agent-swarm package"


# ---------------------------------------------------------------- minimal YAML (stdlib; PyYAML validates only)

def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _skip(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def _strip_comment(s: str) -> str:
    return re.split(r"(?:^|\s)#", s, maxsplit=1)[0]


def _scalar(raw: str) -> str | None:
    """A plain or quoted YAML string scalar (trailing comment allowed); None for anything else."""
    raw = raw.strip()
    if raw.startswith('"'):
        m = re.fullmatch(r'"((?:[^"\\]|\\.)*)"\s*(?:#.*)?', raw)
        if not m:
            return None
        try:
            return json.loads(f'"{m.group(1)}"')
        except json.JSONDecodeError:
            return None
    if raw.startswith("'"):
        m = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", raw)
        return m.group(1).replace("''", "'") if m else None
    v = _strip_comment(raw).strip()
    if not v or v[0] in "[]{}|>&*!%@`,?" or re.search(r":(\s|$)", v) or v in ("~", "null", "Null", "NULL"):
        return None
    return v


def _flow(rest: str) -> list[str] | None:
    rest = _strip_comment(rest).strip() if not rest.lstrip().startswith(("'", '"')) else rest.strip()
    if not (rest.startswith("[") and rest.endswith("]")):
        return None
    inner = rest[1:-1].strip()
    if not inner:
        return []
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|\'(?:[^\']|\'\')*\'|[^,]+', inner)
    items = [_scalar(t) for t in tokens if t.strip()]
    return None if any(i is None for i in items) else items  # type: ignore[return-value]


_BOM = "\ufeff"


def _key_re(key: str) -> str:
    """A YAML mapping key, plain or quoted (`extensions`, `"extensions"`, `'extensions'`)."""
    k = re.escape(key)
    return rf"(?:{k}|\"{k}\"|'{k}')"


def _read_list(text: str, *keys: str) -> list[str] | None:
    """Lenient reader for a (nested) YAML string list, block or flow style; None when absent or unreadable."""
    lines = text.removeprefix(_BOM).splitlines()
    start, depth = 0, -1
    for n, key in enumerate(keys):
        found = None
        for i in range(start, len(lines)):
            raw = lines[i]
            if _skip(raw):
                continue
            w = _indent(raw)
            if w <= depth:
                break
            m = re.match(rf"^\s*{_key_re(key)}\s*:(.*)$", raw)
            if m and (n > 0 or w == 0):
                found = (i, w, m.group(1))
                break
        if not found:
            return None
        i, w, rest = found
        if n < len(keys) - 1:
            if _strip_comment(rest).strip():
                return None
            start, depth = i + 1, w
            continue
        if _strip_comment(rest).strip():
            return _flow(rest)
        items: list[str] = []
        for raw in lines[i + 1:]:
            if _skip(raw):
                continue
            m = re.match(r"^\s*-\s+(.*)$", raw)
            if m and _indent(raw) >= w:
                item = _scalar(m.group(1))
                if item is None:
                    return None
                items.append(item)
                continue
            if _indent(raw) <= w:
                break
            return None
        return items
    return None


@dataclass
class _Block:
    items: list[str]
    after: int  # line index to insert a new item at
    prefix: str  # item indentation


_KEY = re.compile(rf"^{_key_re('extensions')}\s*:(.*)$")
_ANY_KEY = re.compile(r"^(?!\s)\W*extensions\W*\s*:")  # a top-level spelling of the key the strict reader cannot edit


def _parse_extensions(text: str) -> _Block | None:
    """Strict reader for the workspace's top-level `extensions:` block list; None when the key is absent.

    `text` carries no BOM (the caller strips it)."""
    lines = text.splitlines(keepends=True)
    keys = [i for i, line in enumerate(lines) if _KEY.match(line.rstrip("\r\n"))]
    if not keys:
        if any(_ANY_KEY.match(line) for line in lines if not _skip(line)):
            raise InstallError("an `extensions` key this installer cannot edit (flow mapping, complex or escaped key)")
        return None
    if len(keys) > 1:
        raise InstallError("more than one top-level `extensions` key")
    k = keys[0]
    rest = _KEY.match(lines[k].rstrip("\r\n")).group(1)  # type: ignore[union-attr]
    if _strip_comment(rest).strip():
        raise InstallError("`extensions` is flow-style or not a block list")
    items: list[str] = []
    prefix: str | None = None
    last = k
    for i in range(k + 1, len(lines)):
        raw = lines[i].rstrip("\r\n")
        if _skip(raw):
            continue
        m = re.match(r"^([ \t]*)-[ \t]+(.*)$", raw)
        if prefix is None:
            if not m:
                raise InstallError("`extensions` is empty or not a block list")
            prefix = m.group(1)
        if m and m.group(1) == prefix:
            item = _scalar(m.group(2))
            if item is None:
                raise InstallError(f"unsupported `extensions` item: {m.group(2).strip()}")
            items.append(item)
            last = i
            continue
        if _indent(raw) == 0 and not raw.lstrip().startswith("-"):
            break
        raise InstallError("`extensions` has nested or multi-line items")
    if prefix is None:
        raise InstallError("`extensions` is empty or not a block list")
    return _Block(items, last + 1, prefix)


_PLAIN = re.compile(r"[\w/][\w./+@~-]*")
# Plain scalars that decode to something other than a string: YAML 1.2 core (Bun.YAML, what omp reads) ...
_YAML12_NONSTR = re.compile(
    r"~|null|Null|NULL|true|True|TRUE|false|False|FALSE|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
    r"|0x[\da-fA-F]+|0o[0-7]+|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
)
# ... and YAML 1.1 (PyYAML, which validates the result).
_YAML11_NONSTR = re.compile(r"(?i)y|n|yes|no|on|off|true|false|null|[-+]?[\d_]*\.?[\d_]+(?:e[-+]?\d+)?|0b[01_]+|0x[\da-f_]+|\d{4}-\d\d?-\d\d?(?:[t ].*)?")


def _yaml_item(value: str) -> str:
    """`value` as a YAML string scalar: plain when both YAML 1.1 and 1.2 read it back as that string, else quoted."""
    plain = _PLAIN.fullmatch(value) and not (_YAML12_NONSTR.fullmatch(value) or _YAML11_NONSTR.fullmatch(value))
    return value if plain else json.dumps(value)


def _validate(text: str, expected: list[str] | None) -> None:
    """PyYAML, when importable, only checks that `text` decodes to the expected `extensions` list."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InstallError(f"the YAML does not parse ({exc.__class__.__name__})") from None
    if data is None and expected is None:
        return
    if not isinstance(data, dict) or data.get("extensions") != expected:
        raise InstallError("the YAML does not decode to the expected `extensions` list")


# ---------------------------------------------------------------- paths and names

def _resolve_entry(entry: str, base: Path, home: Path) -> Path:
    """An `extensions:`/directory entry as omp's root discovery resolves it (`resolveAgainst`: `expandTilde`, then
    `path.resolve(cwd, …)`). No `@`/`file://` shorthand: those spellings give omp no extension root (WR-06)."""
    e = entry
    if e == "~":
        e = str(home)
    elif e.startswith(("~/", "~\\")):
        e = str(home) + e[1:]
    elif e.startswith("~"):
        e = os.path.join(home, e[1:])
    p = Path(e)
    return Path(os.path.realpath(os.path.normpath(p if p.is_absolute() else base / p)))


def _frontmatter(path: Path) -> dict[str, tuple[str, list[str]]] | None:
    """Top-level frontmatter keys → (inline value, indented continuation lines); None without closed frontmatter."""
    text = _read(path)
    if text is None:
        return None
    lines = text.removeprefix(_BOM).splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    out: dict[str, tuple[str, list[str]]] = {}
    key: str | None = None
    for line in lines[1:]:
        if line.rstrip() == "---":
            return out
        if _skip(line):
            continue
        m = re.match(r"""^("[^"]*"|'[^']*'|[A-Za-z_][\w.-]*)[ \t]*:(?:[ \t]+(.*))?$""", line)
        if m:
            key = k = m.group(1).strip("\"'")
            out[k] = (m.group(2) or "", [])
        elif key is not None and _indent(line) > 0:
            out[key][1].append(line.strip())
        else:
            key = None
    return None


def _fm_string(value: tuple[str, list[str]] | None) -> str | None:
    """The string a frontmatter value decodes to (YAML 1.2, as omp parses it); None for null, bool, number or a collection."""
    if value is None:
        return None
    raw, more = value[0].strip(), value[1]
    if raw[:1] in ("|", ">"):
        return "\n".join(more)
    if not raw:
        if not more or more[0].startswith("-") or re.match(r"""^("[^"]*"|'[^']*'|[^\s'"][^:]*):(\s|$)""", more[0]):
            return None
        raw = " ".join(more)
    elif more and not raw.startswith(("'", '"')):
        raw = " ".join([raw, *more])
    v = _scalar(raw)
    if v is None or (not raw.startswith(("'", '"')) and _YAML12_NONSTR.fullmatch(v)):
        return None
    return v


def _fm_truthy(value: tuple[str, list[str]] | None) -> bool:
    """JavaScript truthiness of a frontmatter value (omp's `!frontmatter.description`)."""
    if value is None:
        return False
    s = _fm_string(value)
    if s is not None:
        return s != ""
    raw = _strip_comment(value[0]).strip()
    if not raw:
        return bool(value[1])  # a nested collection is truthy, a bare key is null
    if raw in ("~", "null", "Null", "NULL", "false", "False", "FALSE"):
        return False
    try:
        return float(int(raw, 0) if re.fullmatch(r"0[xo][\da-fA-F]+", raw) else raw) != 0
    except ValueError:
        return True


def _agent_name(path: Path) -> str | None:
    """The name omp registers an agent file under: `name` and `description` strings required, no stem fallback."""
    fm = _frontmatter(path) or {}
    name, description = _fm_string(fm.get("name")), _fm_string(fm.get("description"))
    return name if name and description else None


def _skill_name(path: Path) -> str | None:
    """The name omp registers a SKILL.md under: needs a truthy `description`, skipped on `enabled: false`;
    the trimmed frontmatter `name` string, else the directory name."""
    fm = _frontmatter(path) or {}
    enabled = fm.get("enabled")
    if enabled is not None and _strip_comment(enabled[0]).strip() in ("false", "False", "FALSE") or not _fm_truthy(fm.get("description")):
        return None
    name = _fm_string(fm.get("name"))
    return (name.strip() if name is not None else "") or path.parent.name


def _profile(env: Mapping[str, str]) -> str | None:
    """The active omp profile: `OMP_PROFILE` when set (even empty), else `PI_PROFILE`; "default" means none."""
    raw = env["OMP_PROFILE"] if "OMP_PROFILE" in env else env.get("PI_PROFILE")
    name = (raw or "").strip()
    return name if name and name != "default" else None


def user_dir(home: Path, env: Mapping[str, str]) -> Path:
    """omp's `getAgentDir()`: the profile's agent dir, else `PI_CODING_AGENT_DIR`, else `~/.omp/agent`.
    A named profile ignores `PI_CODING_AGENT_DIR` (pi-utils `DirResolver`)."""
    profile = _profile(env)
    if profile:
        return home / ".omp" / "profiles" / profile / "agent"
    if env.get("PI_CODING_AGENT_DIR"):
        return Path(os.path.abspath(env["PI_CODING_AGENT_DIR"]))
    return home / ".omp" / "agent"


def _user_agents_dir(home: Path, env: Mapping[str, str]) -> Path:
    """omp's one user agents dir (`getConfigDirs("agents", {project: false})[0]`): profile-aware, never
    `PI_CODING_AGENT_DIR`."""
    profile = _profile(env)
    return (home / ".omp" / "profiles" / profile / "agent" if profile else home / ".omp" / "agent") / "agents"


def _user_yaml(home: Path, env: Mapping[str, str]) -> Path | None:
    """The user config omp reads: the first readable of `config.yml`, `config.yaml` in the agent dir."""
    d = user_dir(home, env)
    return next((d / n for n in ("config.yml", "config.yaml") if _read(d / n) is not None), None)


def package_names(pkg: Path = PKG) -> tuple[set[str], set[str]]:
    """(agent names, skill names): the manifest's agent slugs and the skill directories under `omp/skills/`."""
    from swarm.manifest import load_manifest

    agents = {a["slug"] for a in load_manifest()}
    skills = {p.parent.name for p in (pkg / "skills").glob("*/SKILL.md")}
    return agents, skills


def _nearest(start: Path, rel: str) -> Path | None:
    for d in (start, *start.parents):
        if (d / rel).is_dir():
            return d / rel
    return None


def _skill_ancestors(ws: Path, home: Path) -> list[Path]:
    """omp's `getAncestorDirs(cwd, repoRoot ?? home)`: `ws` up to its git toplevel, else `$HOME`, inclusive; to `/`
    when that stop dir is not an ancestor (closest first)."""
    top = next((d for d in (ws, *ws.parents) if (d / ".git").exists()), None)
    stop = top or home
    out = [ws]
    while out[-1] != stop and out[-1].parent != out[-1]:
        out.append(out[-1].parent)
    return out


_READ_LIMIT = 1_048_576  # a config or agent file larger than this is not one this installer will load


def _read(path: Path, *, limit: int | None = None) -> str | None:
    """Text of a regular file, or None when it is missing, not a regular file, or over `limit`.

    The open is non-blocking. `read_text` on `/dev/zero` or a FIFO `config.yml` never returns, and a caller that
    treats that as "no config" can then overwrite a real file (T-07-09). Size is taken from `fstat` before the
    read. `os.read` may return short, so the read loops until EOF or the buffer exceeds `limit` (still too big).
    `limit` is read from `_READ_LIMIT` at call time so a test can point the cap at a few bytes."""
    if limit is None:
        limit = _READ_LIMIT
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        parts: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(fd, limit + 1 - total)
            if chunk == b"":
                break
            parts.append(chunk)
            total += len(chunk)
        data = b"".join(parts)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > limit:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _text_or_missing(path: Path) -> str | None:
    """Text of `path`, or None when it is absent.

    A present file that is not a readable regular file, or that `_read` cannot load (larger than `_READ_LIMIT`,
    or a regular file that comes back None), raises `InstallError` naming `path`. A FIFO or device is not read
    and is not treated as an empty config."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    try:
        followed = os.stat(path) if stat.S_ISLNK(info.st_mode) else info
    except OSError:
        raise InstallError(f"{path} is unreadable") from None
    if not stat.S_ISREG(followed.st_mode):
        raise InstallError(f"{path} is unreadable")
    text = _read(path)
    if text is None:
        raise InstallError(f"{path} is unreadable")
    return text


def shadow_scan(
    ws: Path,
    home: Path,
    before: tuple[Path, ...] | list[Path] = (),
    env: Mapping[str, str] | None = None,
    agent_names: set[str] | None = None,
    skill_names: set[str] | None = None,
) -> list[Shadow]:
    """Same-name agents and skills that omp resolves ahead of the package (D-05). Reads only.

    `before` holds the extension roots ordered before the package. Agents and skills are named as omp's loaders
    name them (`_agent_name`, `_skill_name`); files those loaders drop never warn."""
    env = os.environ if env is None else env
    ws, home = Path(ws), Path(home)
    if agent_names is None or skill_names is None:
        agents, skills = package_names()
        agent_names = agents if agent_names is None else agent_names
        skill_names = skills if skill_names is None else skill_names
    found: list[Shadow] = []
    seen: set[Path] = set()

    def scan(d: Path, kind: str, level: str, custom: bool = False) -> None:
        if not d.is_dir():
            return
        pattern, names = ("*.md", agent_names) if kind == "agent" else ("*/SKILL.md", skill_names)
        for f in sorted(d.glob(pattern)):
            if not f.is_file() or f in seen or (kind == "skill" and f.parent.name.startswith(".")):
                continue
            name = _agent_name(f) if kind == "agent" else _skill_name(f)
            if name in names:
                seen.add(f)
                found.append(Shadow(kind, name, f, level, custom))

    # agents: nearest project dir (walks to /), the one user dir, earlier extension roots
    project_agents = _nearest(ws, ".omp/agents")
    if project_agents:
        scan(project_agents, "agent", "project")
    scan(_user_agents_dir(home, env), "agent", "user")
    for root in before:
        scan(Path(root) / "agents", "agent", "extension")
    # skills: ws and ancestors, the agent dir, earlier extension roots, skills.customDirectories
    for d in _skill_ancestors(ws, home):
        scan(d / ".omp" / "skills", "skill", "project")
    scan(user_dir(home, env) / "skills", "skill", "user")
    for root in before:
        scan(Path(root) / "skills", "skill", "extension")
    for level, cfg, base in (("project", ws / ".omp" / "config.yml", ws), ("user", _user_yaml(home, env), home)):
        text = _read(cfg) if cfg else None
        for entry in (_read_list(text, "skills", "customDirectories") or []) if text else []:
            scan(_resolve_entry(entry, base, home), "skill", level, custom=True)
    return found


# ---------------------------------------------------------------- plan and apply

@dataclass
class _Plan:
    config: Path
    old: str | None = None  # current config text; None when the file is absent
    new: str | None = None  # config text to write; None when nothing changes
    linked: str | None = None  # existing entry that already resolves to the package
    carried: list[str] = field(default_factory=list)
    carried_from: Path | None = None
    replaced: list[str] = field(default_factory=list)  # inherited entries spelling the package differently
    copies: list[tuple[Path, Path]] = field(default_factory=list)
    shadows: list[Shadow] = field(default_factory=list)
    copy_blockers: list[Shadow] = field(default_factory=list)  # copy mode only: strict-ancestor shadows (T-07-20)
    dest_state: dict[str, list[tuple[str, tuple[int, int] | None]]] = field(default_factory=dict)


def _json_extensions(path: Path) -> list[str] | None:
    """The `extensions` list of a legacy settings.json; None when the file or the key is absent.

    `_read` returning None used to mean "missing". A present file that cannot be read raises instead, so link
    mode does not write a project list that drops the extensions omp still loads from this file."""
    text = _text_or_missing(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise InstallError(f"{path} is not valid JSON; cannot tell which extensions it enables") from None
    if not isinstance(data, dict) or "extensions" not in data:
        return None
    value = data["extensions"]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InstallError(f"{path} `extensions` is not a list of strings")
    return list(value)


def _inherited(ws: Path, home: Path, env: Mapping[str, str]) -> tuple[list[str], Path | None]:
    """The list omp uses in `ws` while its config.yml has no `extensions` key, in omp's `readConfiguredExtensions`
    order: project settings.json, the user YAML (present without the key: nothing), then user settings.json.

    A missing file stays absent. A present settings.json or user YAML that exists but cannot be read raises;
    skipping it would install a project list without the extensions that source still holds. A FIFO is not an
    empty config: the install fails instead of treating it as absent and reading a later source."""
    project = ws / ".omp" / "settings.json"
    value = _json_extensions(project)
    if value is not None:
        return value, project
    agent = user_dir(home, env)
    user_yaml: Path | None = None
    for name in ("config.yml", "config.yaml"):
        candidate = agent / name
        try:
            os.lstat(candidate)
        except OSError:
            continue
        user_yaml = candidate
        break
    if user_yaml is not None:
        text = _text_or_missing(user_yaml)
        if text is not None:  # removed between the lstat above and the read: fall through
            value = _read_list(text, "extensions")
            if value is not None:
                return value, user_yaml
            if any(_ANY_KEY.match(line) for line in text.removeprefix(_BOM).splitlines() if not _skip(line)):
                raise InstallError(f"{user_yaml} has an `extensions` value this installer cannot read")
            return [], user_yaml  # a present user YAML suppresses the legacy settings.json
    legacy = agent / "settings.json"
    value = _json_extensions(legacy)
    return (value, legacy) if value is not None else ([], None)


def workspace_problem(ws: Path, home: Path) -> str | None:
    """Why `ws` must not be installed into (WR-01), or None: this checkout or inside it, `$HOME`, inside `~/.omp`."""
    ws, home = Path(ws).resolve(), Path(home).resolve()
    where = (
        "is inside the agent-swarm checkout, which wires itself (.omp/config.yml)" if ws.is_relative_to(ROOT)
        else "is $HOME" if ws == home
        else "is inside ~/.omp" if ws.is_relative_to(home / ".omp")
        else None
    )
    if where is None:
        return None
    return f"error: workspace {ws} {where}; install into a workspace outside the agent-swarm checkout, $HOME and ~/.omp"


def unsafe_destinations(ws: Path, dests: list[Path]) -> list[str]:
    """Reasons the installer must not write `dests` (CR-01, T-07-05): a symlink at a destination or at any
    path component between `ws` and it, a hardlink (shared inode) at a regular-file destination, a
    non-directory component, or a destination outside `ws`. Reads only; `ws` must be resolved.

    Directories pass as destinations (sibling installers check export roots); the omp write path
    additionally refuses non-regular destinations at write time (`_stat_dest`)."""
    reasons: list[str] = []
    for dest in dests:
        p = Path(dest)
        while p != ws:
            try:
                st = os.lstat(p)
            except FileNotFoundError:
                pass  # absent: nothing planted here; keep walking toward `ws`
            except OSError as exc:
                reasons.append(f"{p} is unreadable ({exc.strerror})")
                break
            else:
                if stat.S_ISLNK(st.st_mode):
                    reasons.append(f"{p} is a symlink")
                    break
                if p == dest:
                    if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                        reasons.append(f"{dest} is a hardlink (nlink={st.st_nlink})")
                        break
                elif not stat.S_ISDIR(st.st_mode):
                    reasons.append(f"{p} is not a directory")
                    break
            if p.parent == p:
                reasons.append(f"{dest} is outside {ws}")
                break
            p = p.parent
    return list(dict.fromkeys(reasons))


def _check_destinations(ws: Path, dests: list[Path]) -> None:
    reasons = unsafe_destinations(ws, dests)
    if reasons:
        raise UnsafeDestination("; ".join(reasons))


def _record_dest_state(ws: Path, dests: list[Path]) -> dict[str, list[tuple[str, tuple[int, int] | None]]]:
    """Plan-time (dev, ino) record of every component from `ws` to each destination (T-07-05).

    The write path re-resolves each component with `O_NOFOLLOW` and refuses on any difference, so a
    symlink or hardlink swapped in between the plan check and the write, or a replaced directory,
    fails closed instead of winning the race. Call only after `_check_destinations` passed."""
    state: dict[str, list[tuple[str, tuple[int, int] | None]]] = {}
    for dest in dests:
        parts: list[Path] = []
        p = Path(dest)
        while True:
            parts.append(p)
            if p == ws or p.parent == p:
                break
            p = p.parent
        chain: list[tuple[str, tuple[int, int] | None]] = []
        for comp in reversed(parts):
            try:
                st = os.lstat(comp)
            except OSError:
                chain.append((str(comp), None))
            else:
                chain.append((str(comp), (st.st_dev, st.st_ino)))
        state[str(dest)] = chain
    return state


def _revalidate_destinations(
    ws: Path, dests: list[Path], state: dict[str, list[tuple[str, tuple[int, int] | None]]]
) -> tuple[list[str], dict[str, tuple[int, int]]]:
    """Write-time re-validation of every destination (T-07-05). Reads only; writes nothing.

    Each component from `ws` to each destination is re-resolved with `O_NOFOLLOW` (a swapped-in
    symlink fails with `ELOOP` instead of being followed) and its `fstat` (dev, ino) is compared
    against the plan-time record; a regular-file destination with more than one link refuses as a
    hardlink. Returns (reasons, baseline): `reasons` is empty when every destination is unchanged,
    and `baseline` maps each existing component to its fresh (dev, ino) for the write path to pin."""
    reasons: list[str] = []
    baseline: dict[str, tuple[int, int]] = {}
    for dest in dests:
        try:
            dst = os.lstat(dest)
        except OSError:
            pass  # absent/unreadable: the component walk below reports it
        else:
            if stat.S_ISLNK(dst.st_mode):
                reasons.append(f"{dest} is a symlink")
            elif stat.S_ISDIR(dst.st_mode):
                reasons.append(f"{dest} is a directory")
            elif not stat.S_ISREG(dst.st_mode):
                reasons.append(f"{dest} is not a regular file")
            elif dst.st_nlink > 1:
                reasons.append(f"{dest} is a hardlink (nlink={dst.st_nlink})")
        want = dict(state.get(str(dest), []))
        chain: list[Path] = []
        p = Path(dest)
        while True:
            chain.append(p)
            if p == ws or p.parent == p:
                break
            p = p.parent
        if chain[-1] != ws:
            reasons.append(f"{dest} is outside {ws}")
            continue
        for comp in reversed(chain):
            key = str(comp)
            expected = want.get(key)
            try:
                fd = os.open(comp, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | (os.O_DIRECTORY if comp != dest else 0))
            except FileNotFoundError:
                if expected is None and key in want:
                    continue
                reasons.append(f"{comp} disappeared after the plan check")
                break
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    reasons.append(f"{comp} is a symlink")
                elif exc.errno == errno.ENOTDIR:
                    reasons.append(f"{comp} is not a directory")
                else:
                    reasons.append(f"{comp} is unreadable ({exc.strerror})")
                break
            try:
                st = os.fstat(fd)
            finally:
                os.close(fd)
            if comp != dest:
                if not stat.S_ISDIR(st.st_mode):
                    reasons.append(f"{comp} is not a directory")
                    break
            elif stat.S_ISDIR(st.st_mode):
                reasons.append(f"{dest} is a directory")
                break
            elif not stat.S_ISREG(st.st_mode):
                reasons.append(f"{dest} is not a regular file")
                break
            elif st.st_nlink > 1:
                reasons.append(f"{dest} is a hardlink (nlink={st.st_nlink})")
                break
            seen = (st.st_dev, st.st_ino)
            if key not in want:
                reasons.append(f"{comp} changed after the plan check")
                break
            if expected is None:
                reasons.append(f"{comp} appeared after the plan check")
                break
            if expected != seen:
                reasons.append(f"{comp} changed after the plan check")
                break
            baseline[key] = seen
    return list(dict.fromkeys(reasons)), baseline


def _stat_dest(dest: Path) -> os.stat_result | None:
    """`lstat` of a destination file, refusing symlinks, directories, non-regular files and hardlinks
    (T-07-05); None when absent. The caller compares the result against the plan-time record."""
    try:
        st = os.lstat(dest)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UnsafeDestination(f"{dest} is unreadable ({exc.strerror})") from None
    if stat.S_ISLNK(st.st_mode):
        raise UnsafeDestination(f"{dest} is a symlink") from None
    if stat.S_ISDIR(st.st_mode):
        raise UnsafeDestination(f"{dest} is a directory") from None
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeDestination(f"{dest} is not a regular file") from None
    if st.st_nlink > 1:
        raise UnsafeDestination(f"{dest} is a hardlink (nlink={st.st_nlink})") from None
    return st


def _verify_dest_snapshot(
    dest: Path, state: dict[str, list[tuple[str, tuple[int, int] | None]]]
) -> os.stat_result | None:
    """The verified pre-write `lstat` of `dest`: classified by `_stat_dest` and compared by (dev, ino)
    against the plan-time record (T-07-05). Any planted symlink/hardlink or check-write swap raises
    `UnsafeDestination` with a named reason before anything is written."""
    chain = state.get(str(dest))
    if chain is None:
        raise UnsafeDestination(f"{dest} was not recorded at plan time") from None
    expected = dict(chain)[str(dest)]
    before = _stat_dest(dest)
    if expected is None:
        if before is not None:
            raise UnsafeDestination(f"{dest} appeared after the plan check") from None
    elif before is None:
        raise UnsafeDestination(f"{dest} disappeared after the plan check") from None
    elif (before.st_dev, before.st_ino) != expected:
        raise UnsafeDestination(f"{dest} changed after the plan check") from None
    return before


def _safe_ensure_parent(ws: Path, dest: Path, baseline: dict[str, tuple[int, int]]) -> None:
    """Create the missing parents of `dest` below `ws` without following symlinks (T-07-05).

    Every level is `lstat`-verified (symlinks and non-directories refuse) and pinned by (dev, ino):
    levels the write-time record saw must still match it, and levels this install creates are pinned
    at creation — so a level that appears out of nowhere, including an `EEXIST` race against
    `os.mkdir`, refuses as a check-write swap instead of being descended into."""
    try:
        rel = dest.parent.relative_to(ws)
    except ValueError:
        raise UnsafeDestination(f"{dest} is outside {ws}") from None
    cur = ws
    for part in rel.parts:
        nxt = cur / part
        key = str(nxt)
        try:
            st = os.lstat(nxt)
        except FileNotFoundError:
            if key in baseline:
                raise UnsafeDestination(f"{nxt} disappeared after the plan check") from None
            try:
                os.mkdir(nxt)
            except FileExistsError:
                raise UnsafeDestination(f"{nxt} changed after the plan check") from None
            try:
                st = os.lstat(nxt)
            except OSError as exc:
                raise UnsafeDestination(f"{nxt} is unreadable ({exc.strerror})") from None
            baseline[key] = (st.st_dev, st.st_ino)
        except OSError as exc:
            raise UnsafeDestination(f"{nxt} is unreadable ({exc.strerror})") from None
        if stat.S_ISLNK(st.st_mode):
            raise UnsafeDestination(f"{nxt} is a symlink") from None
        if not stat.S_ISDIR(st.st_mode):
            raise UnsafeDestination(f"{nxt} is not a directory") from None
        if baseline.get(key) != (st.st_dev, st.st_ino):
            raise UnsafeDestination(f"{nxt} changed after the plan check") from None
        cur = nxt


def _same_bytes(dest: Path, before: os.stat_result, data: bytes) -> bool:
    """True when `dest` already holds `data` (T-07-05). `before` is the verified pre-write `lstat`: the
    read uses `O_NOFOLLOW` and is accepted only when the opened file is the same inode with one link,
    so a swapped or multiply-linked `dest` never counts as "same" — it proceeds to the write path,
    which refuses."""
    try:
        fd = os.open(dest, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        live = os.fstat(fd)
        if (live.st_dev, live.st_ino) != (before.st_dev, before.st_ino) or live.st_nlink > 1:
            return False
        if live.st_size != len(data):
            return False
        parts: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if chunk == b"":
                break
            parts.append(chunk)
    except OSError:
        return False
    finally:
        os.close(fd)
    return b"".join(parts) == data


def _safe_write_bytes(dest: Path, data: bytes, before: os.stat_result | None, mode: int | None = None) -> None:
    """Write `data` to `dest` without following a symlink and without truncating a swapped file (T-07-05).

    `before` is the verified pre-write `lstat` (`_verify_dest_snapshot`): when it is None the open uses
    `O_EXCL`, so a file raced into place refuses instead of being overwritten; otherwise the opened
    file's `fstat` (dev, ino) must equal it and a hardlink refuses — all before `ftruncate`, so a
    swapped-in file is never truncated. Every refusal raises `UnsafeDestination` with a named reason."""
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW | (0 if before is not None else os.O_EXCL), 0o666)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafeDestination(f"{dest} is a symlink") from None
        if exc.errno == errno.EEXIST:
            raise UnsafeDestination(f"{dest} changed after the plan check") from None
        raise UnsafeDestination(f"{dest} cannot be opened ({exc.strerror})") from None
    try:
        after = os.fstat(fd)
        if stat.S_ISDIR(after.st_mode):
            raise UnsafeDestination(f"{dest} is a directory") from None
        if not stat.S_ISREG(after.st_mode):
            raise UnsafeDestination(f"{dest} is not a regular file") from None
        if after.st_nlink > 1:
            raise UnsafeDestination(f"{dest} is a hardlink (nlink={after.st_nlink})") from None
        if before is not None and (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise UnsafeDestination(f"{dest} changed after the plan check") from None
        if mode is not None:
            os.fchmod(fd, mode)
        os.ftruncate(fd, 0)
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)


def _check_package(pkg: Path) -> None:
    try:
        meta = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise InstallError(f"{pkg}/package.json is missing or invalid") from None
    if not (meta.get("omp") or {}).get("extensions"):
        raise InstallError(f"{pkg}/package.json declares no omp.extensions")


def _plan(ws: Path, mode: str, home: Path, env: Mapping[str, str]) -> _Plan:
    _check_package(PKG)
    cfg = ws / ".omp" / "config.yml"
    plan = _Plan(config=cfg)
    agents, skills = package_names()
    if mode == "copy":
        plan.copies = [(PKG / "agents" / f"{s}.md", ws / ".omp" / "agents" / f"{s}.md") for s in sorted(agents)]
        plan.copies += [(PKG / "skills" / s / "SKILL.md", ws / ".omp" / "skills" / s / "SKILL.md") for s in sorted(skills)]
    targets = [d for _, d in plan.copies] if mode == "copy" else [cfg]
    _check_destinations(ws, targets)
    plan.dest_state = _record_dest_state(ws, targets)
    if cfg.exists():
        plan.old = _read(cfg)
        if plan.old is None:
            raise InstallError(f"{cfg} is unreadable")
    if mode == "copy":
        entries = _read_list(plan.old, "extensions") if plan.old else None
        plan.linked = next((e for e in entries or [] if _resolve_entry(e, ws, home) == PKG), None)
        dests = {d for _, d in plan.copies}
        own = ws / ".omp"
        found = shadow_scan(ws, home, (), env, agents, skills)
        plan.shadows = [s for s in found if s.custom or (s.path.is_relative_to(own) and s.path not in dests)]
        # T-07-20: project-level shadows outside `ws/.omp` live in a strict ancestor. The copies below would
        # resolve ahead of that ancestor's package wiring, so the workspace would run guard-less with shadowed
        # skills while looking installed; the copy path refuses them unless the caller opts in.
        plan.copy_blockers = [
            s for s in found if s.level == "project" and not s.custom and not s.path.is_relative_to(own)
        ]
        return plan
    bom = _BOM if (plan.old or "").startswith(_BOM) else ""
    body = (plan.old or "")[len(bom):]  # parsed and edited without the BOM, which is kept on write
    block = _parse_extensions(body)
    _validate(body, block.items if block else None)
    pkg_item = _yaml_item(str(PKG))
    if block:
        hit = next((i for i, e in enumerate(block.items) if _resolve_entry(e, ws, home) == PKG), None)
        if hit is not None:
            plan.linked = block.items[hit]
            before = block.items[:hit]
        else:
            lines = body.splitlines(keepends=True)
            if not lines[block.after - 1].endswith("\n"):
                lines[block.after - 1] += "\n"
            lines.insert(block.after, f"{block.prefix}- {pkg_item}\n")
            new = "".join(lines)
            before = block.items
            _validate(new, block.items + [str(PKG)])
            plan.new = bom + new
    else:
        inherited, plan.carried_from = _inherited(ws, home, env)
        plan.replaced = [e for e in inherited if _resolve_entry(e, ws, home) == PKG]
        plan.carried = [e for e in inherited if e not in plan.replaced]
        if body and not body.endswith("\n"):
            body += "\n"
        new = body + "extensions:\n" + "".join(f"  - {_yaml_item(e)}\n" for e in plan.carried + [str(PKG)])
        before = plan.carried
        _validate(new, plan.carried + [str(PKG)])
        plan.new = bom + new
    roots = tuple(p for p in (_resolve_entry(e, ws, home) for e in before) if p.is_dir())
    plan.shadows = shadow_scan(ws, home, roots, env, agents, skills)
    return plan


def _error(ws: Path, exc: InstallError) -> str:
    if isinstance(exc, UnsafeDestination):
        return f"error: refusing to install into {ws}: {exc}; the installer never writes through a symlink. Nothing was written."
    cfg = ws / ".omp" / "config.yml"
    return (
        f"error: cannot install the omp package into {cfg}: {exc}; nothing was written.\n"
        f"Add it by hand as a block list item, then re-run to check:\nextensions:\n  - {_yaml_item(str(PKG))}"
    )


def _copy_shadow_error(ws: Path, blockers: list[Shadow]) -> str:
    """Refusal for a copy-install under a shadowing ancestor (T-07-20): names the workspace, the ancestor
    shadows, the guard-less consequence, and the two ways forward. Writes nothing."""
    names = ", ".join(f"{s.kind} {s.name} at {s.path}" for s in blockers)
    return (
        f"error: refusing copy mode into {ws}: {names} shadow the agent-swarm package from an ancestor "
        f"workspace, so the copies in {ws / '.omp'} would run guard-less (no tools (swarm_*), no guard "
        f"(tool_call)) with shadowed skills while looking installed. Nothing was written. "
        f"Re-run with --allow-shadowed-copy to proceed anyway, or use link mode to keep tools and guard."
    )


def preflight(
    ws: Path,
    mode: str,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    allow_shadowed_copy: bool = False,
) -> str | None:
    """The error `install_omp` would stop on (exit 2), or None. Reads only."""
    ws, home = Path(ws).resolve(), Path.home() if home is None else Path(home)
    problem = workspace_problem(ws, home)
    if problem:
        return problem
    try:
        plan = _plan(ws, mode, home, os.environ if env is None else env)
    except InstallError as exc:
        return _error(ws, exc)
    if mode == "copy" and plan.copy_blockers and not allow_shadowed_copy:
        return _copy_shadow_error(ws, plan.copy_blockers)
    return None


def install_omp(
    ws: Path,
    mode: str,
    dry_run: bool,
    out: TextIO = sys.stdout,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    allow_shadowed_copy: bool = False,
) -> int:
    """Link (default) or copy the omp package into `ws`; 0 on success, 2 on an unsupported workspace state."""
    ws = Path(ws).resolve()
    home = Path.home() if home is None else Path(home)
    env = os.environ if env is None else env

    def say(line: str) -> None:
        print(line, file=out)

    if mode not in ("link", "copy"):
        say(f"error: unknown omp mode {mode!r} (link|copy)")
        return 2
    if not ws.is_dir():
        say(f"error: workspace {ws} is not an existing directory")
        return 2
    problem = workspace_problem(ws, home)
    if problem:
        say(problem)
        return 2
    try:
        plan = _plan(ws, mode, home, env)
    except InstallError as exc:
        say(_error(ws, exc))
        return 2
    if mode == "copy" and plan.copy_blockers and not allow_shadowed_copy:
        say(_copy_shadow_error(ws, plan.copy_blockers))
        return 2
    if plan.carried or plan.replaced:
        what = ", ".join(plan.carried) or "(none)"
        extra = f"; replaced {', '.join(plan.replaced)} with the package realpath" if plan.replaced else ""
        say(
            f"WARNING carry-over: a workspace extensions list replaces the one from {plan.carried_from}, "
            f"so {plan.config} starts with its entries: {what}{extra}"
        )
    if mode == "copy" and plan.linked:
        say(f"WARNING copy mode: {plan.config} also links the package ({plan.linked}); the copies in {ws / '.omp'} shadow it")
    for shadow in plan.shadows:
        say(shadow.line())
    if mode == "copy":
        for shadow in plan.copy_blockers:
            say(shadow.line())
    if mode == "copy":
        say(COPY_WARNING)
        changed = 0
        if dry_run:
            for src, dest in plan.copies:
                say(f"dry-run: would copy {src.relative_to(ROOT)} -> {dest}")
        else:
            try:
                reasons, baseline = _revalidate_destinations(ws, [d for _, d in plan.copies], plan.dest_state)
                if reasons:
                    raise UnsafeDestination("; ".join(reasons))
                pending: list[tuple[Path, Path, bytes, os.stat_result | None]] = []
                for src, dest in plan.copies:
                    _safe_ensure_parent(ws, dest, baseline)
                    before = _verify_dest_snapshot(dest, plan.dest_state)
                    data = src.read_bytes()
                    if before is not None and _same_bytes(dest, before, data):
                        continue
                    pending.append((src, dest, data, before))
                for src, dest, data, before in pending:
                    _safe_write_bytes(dest, data, before, stat.S_IMODE(os.stat(src).st_mode))
                    changed += 1
            except InstallError as exc:
                say(_error(ws, exc))
                return 2
        n_agents = sum(1 for _, d in plan.copies if d.suffix == ".md" and d.name != "SKILL.md")
        if not dry_run:
            say(
                f"copied omp package (copy): {n_agents} agent(s) and {len(plan.copies) - n_agents} skill(s) "
                f"into {ws / '.omp'} ({changed} changed)"
            )
    elif plan.new is None:
        say(f"omp package (link) already in {plan.config}: {plan.linked}")
    elif dry_run:
        say(f"dry-run: would write {plan.config}:")
        diff = difflib.unified_diff(
            (plan.old or "").splitlines(keepends=True), plan.new.splitlines(keepends=True),
            fromfile=str(plan.config) if plan.old is not None else "/dev/null", tofile=str(plan.config),
        )
        for line in diff:
            out.write(line if line.endswith("\n") else line + "\n")
    else:
        try:
            reasons, baseline = _revalidate_destinations(ws, [plan.config], plan.dest_state)
            if reasons:
                raise UnsafeDestination("; ".join(reasons))
            _safe_ensure_parent(ws, plan.config, baseline)
            before = _verify_dest_snapshot(plan.config, plan.dest_state)
            _safe_write_bytes(plan.config, plan.new.encode("utf-8"), before)
        except InstallError as exc:
            say(_error(ws, exc))
            return 2
        say(f"installed omp package (link) into {plan.config}: {PKG}")
    if mode == "link":
        say(NOTE_CWD.format(ws=ws))
    say(NOTE_FRESH)
    say(NOTE_DEPTH)
    return 0
