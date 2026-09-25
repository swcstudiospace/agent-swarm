"""omp step of `build_agents.py --install-workspace` (Phase 7, PKG-01..03). Import-only; no CLI.

link (default, D-01..D-04): merge the realpath of this checkout's `omp/` package into `<ws>/.omp/config.yml`
`extensions:` line by line; every other line stays byte-for-byte. copy (D-06): copy the generated agents and
skills into `<ws>/.omp/` and leave the config alone. Both modes print one WARNING per same-name agent or skill that
shadows the package (D-05); warnings never change the exit code. Nothing here writes into this repo or `~/.omp`.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
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


def _read_list(text: str, *keys: str) -> list[str] | None:
    """Lenient reader for a (nested) YAML string list, block or flow style; None when absent or unreadable."""
    lines = text.splitlines()
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
            m = re.match(rf"^\s*{re.escape(key)}\s*:(.*)$", raw)
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


_KEY = re.compile(r"^extensions\s*:(.*)$")


def _parse_extensions(text: str) -> _Block | None:
    """Strict reader for the workspace's top-level `extensions:` block list; None when the key is absent."""
    lines = text.splitlines(keepends=True)
    keys = [i for i, line in enumerate(lines) if _KEY.match(line.rstrip("\r\n"))]
    if not keys:
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


def _yaml_item(value: str) -> str:
    plain = re.fullmatch(r"[\w./+@-][\w./+@~-]*", value) and value not in ("null", "true", "false")
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
    """An `extensions:`/directory entry as omp resolves it: `file://`/`@/` shorthands, `~`, relative to `base`."""
    e = entry.strip()
    if e.startswith("file://"):
        e = e[len("file://"):]
    elif e.startswith("@/"):
        e = e[1:]
    if e == "~" or e.startswith("~/"):
        e = str(home) + e[1:]
    p = Path(e)
    return Path(os.path.realpath(p if p.is_absolute() else base / p))


def _frontmatter_name(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^name\s*:(.*)$", line)
        if m:
            return _scalar(m.group(1))
    return None


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
    """`ws` and its ancestors up to its git toplevel, else `$HOME` (closest first)."""
    top = next((d for d in (ws, *ws.parents) if (d / ".git").exists()), None)
    stop = top or home
    if not ws.is_relative_to(stop):
        return [ws]
    out = [ws]
    while out[-1] != stop and out[-1].parent != out[-1]:
        out.append(out[-1].parent)
    return out


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def shadow_scan(
    ws: Path,
    home: Path,
    before: tuple[Path, ...] | list[Path] = (),
    env: Mapping[str, str] | None = None,
    agent_names: set[str] | None = None,
    skill_names: set[str] | None = None,
) -> list[Shadow]:
    """Same-name agents and skills that omp resolves ahead of the package (D-05). Reads only.

    `before` holds the extension roots ordered before the package. Agents match on frontmatter `name:` (else the
    file stem); skills on frontmatter `name:`, else the directory name."""
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
            if not f.is_file() or f in seen:
                continue
            fallback = f.stem if kind == "agent" else f.parent.name
            name = _frontmatter_name(f) or fallback
            if name in names:
                seen.add(f)
                found.append(Shadow(kind, name, f, level, custom))

    profile = env.get("OMP_PROFILE")
    profile_dir = home / ".omp" / "profiles" / profile / "agent" if profile else None
    # agents: nearest project dir, user dir (+ profile), earlier extension roots
    project_agents = _nearest(ws, ".omp/agents")
    if project_agents:
        scan(project_agents, "agent", "project")
    scan(home / ".omp" / "agent" / "agents", "agent", "user")
    if profile_dir:
        scan(profile_dir / "agents", "agent", "user")
    for root in before:
        scan(Path(root) / "agents", "agent", "extension")
    # skills: ws and ancestors, the agent dir, earlier extension roots, skills.customDirectories
    for d in _skill_ancestors(ws, home):
        scan(d / ".omp" / "skills", "skill", "project")
    agent_dir = Path(env["PI_CODING_AGENT_DIR"]) if env.get("PI_CODING_AGENT_DIR") else profile_dir or home / ".omp" / "agent"
    scan(agent_dir / "skills", "skill", "user")
    for root in before:
        scan(Path(root) / "skills", "skill", "extension")
    for level, cfg, base in (("project", ws / ".omp" / "config.yml", ws), ("user", home / ".omp" / "agent" / "config.yml", home)):
        text = _read(cfg)
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


def _inherited(ws: Path, home: Path) -> tuple[list[str], Path | None]:
    """The list omp uses in `ws` while its config.yml has no `extensions` key (project JSON, user YAML, user JSON)."""
    for path in (ws / ".omp" / "settings.json", home / ".omp" / "agent" / "config.yml", home / ".omp" / "agent" / "settings.json"):
        text = _read(path)
        if text is None:
            continue
        if path.suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                raise InstallError(f"{path} is not valid JSON; cannot tell which extensions it enables") from None
            if not isinstance(data, dict) or "extensions" not in data:
                continue
            value = data["extensions"]
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise InstallError(f"{path} `extensions` is not a list of strings")
            return list(value), path
        value = _read_list(text, "extensions")
        if value is not None:
            return value, path
        if re.search(r"(?m)^extensions\s*:", text):
            raise InstallError(f"{path} has an `extensions` value this installer cannot read")
    return [], None


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
    if cfg.exists():
        plan.old = _read(cfg)
        if plan.old is None:
            raise InstallError(f"{cfg} is unreadable")
    agents, skills = package_names()
    if mode == "copy":
        plan.copies = [(PKG / "agents" / f"{s}.md", ws / ".omp" / "agents" / f"{s}.md") for s in sorted(agents)]
        plan.copies += [(PKG / "skills" / s / "SKILL.md", ws / ".omp" / "skills" / s / "SKILL.md") for s in sorted(skills)]
        entries = _read_list(plan.old, "extensions") if plan.old else None
        plan.linked = next((e for e in entries or [] if _resolve_entry(e, ws, home) == PKG), None)
        dests = {d for _, d in plan.copies}
        own = ws / ".omp"
        plan.shadows = [
            s for s in shadow_scan(ws, home, (), env, agents, skills)
            if s.custom or (s.path.is_relative_to(own) and s.path not in dests)
        ]
        return plan
    block = _parse_extensions(plan.old or "")
    _validate(plan.old or "", block.items if block else None)
    pkg_item = _yaml_item(str(PKG))
    if block:
        hit = next((i for i, e in enumerate(block.items) if _resolve_entry(e, ws, home) == PKG), None)
        if hit is not None:
            plan.linked = block.items[hit]
            before = block.items[:hit]
        else:
            lines = (plan.old or "").splitlines(keepends=True)
            if not lines[block.after - 1].endswith("\n"):
                lines[block.after - 1] += "\n"
            lines.insert(block.after, f"{block.prefix}- {pkg_item}\n")
            plan.new = "".join(lines)
            before = block.items
            _validate(plan.new, block.items + [str(PKG)])
    else:
        inherited, plan.carried_from = _inherited(ws, home)
        plan.replaced = [e for e in inherited if _resolve_entry(e, ws, home) == PKG]
        plan.carried = [e for e in inherited if e not in plan.replaced]
        old = plan.old or ""
        if old and not old.endswith("\n"):
            old += "\n"
        plan.new = old + "extensions:\n" + "".join(f"  - {_yaml_item(e)}\n" for e in plan.carried + [str(PKG)])
        before = plan.carried
        _validate(plan.new, plan.carried + [str(PKG)])
    roots = tuple(p for p in (_resolve_entry(e, ws, home) for e in before) if p.is_dir())
    plan.shadows = shadow_scan(ws, home, roots, env, agents, skills)
    return plan


def _error(ws: Path, exc: InstallError) -> str:
    cfg = ws / ".omp" / "config.yml"
    return (
        f"error: cannot install the omp package into {cfg}: {exc}; nothing was written.\n"
        f"Add it by hand as a block list item, then re-run to check:\nextensions:\n  - {_yaml_item(str(PKG))}"
    )


def preflight(ws: Path, mode: str, home: Path | None = None, env: Mapping[str, str] | None = None) -> str | None:
    """The error `install_omp` would stop on (exit 2), or None. Reads only."""
    try:
        _plan(Path(ws).resolve(), mode, Path.home() if home is None else Path(home), os.environ if env is None else env)
    except InstallError as exc:
        return _error(Path(ws).resolve(), exc)
    return None


def install_omp(
    ws: Path,
    mode: str,
    dry_run: bool,
    out: TextIO = sys.stdout,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
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
    try:
        plan = _plan(ws, mode, home, env)
    except InstallError as exc:
        say(_error(ws, exc))
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
        say(COPY_WARNING)
        changed = 0
        for src, dest in plan.copies:
            if dry_run:
                say(f"dry-run: would copy {src.relative_to(ROOT)} -> {dest}")
                continue
            if dest.is_file() and dest.read_bytes() == src.read_bytes():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            changed += 1
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
        plan.config.parent.mkdir(parents=True, exist_ok=True)
        plan.config.write_text(plan.new, encoding="utf-8")
        say(f"installed omp package (link) into {plan.config}: {PKG}")
    if mode == "link":
        say(NOTE_CWD.format(ws=ws))
    say(NOTE_FRESH)
    say(NOTE_DEPTH)
    return 0
