#!/usr/bin/env python3
"""A01 — autonomous swarm runner: executes a planned task DAG with headless agent sessions.

Each ready task is dispatched to its agent as a headless session, run with the task.assign prompt on stdin:
    claude -p --agent <slug> --output-format json --permission-mode <mode> …
        [--mcp-config <repo>/.mcp.json --strict-mcp-config] --allowedTools … [mcp__substrate]   (--runtime claude)
    grok -p --agent <slug> --output-format json --yolo [--trust] --cwd <repo>               (--runtime grok)
    omp -p --mode json --no-session --no-title --no-extensions -e <agent-swarm>/omp --cwd <repo>
        --approval-mode yolo --tools <frontmatter tools>,yield
        --append-system-prompt $SWARM_DIR/agents/<slug>.md --max-time <n>s                   (--runtime omp)
--runtime auto picks grok when SWARM_RUNTIME=grok or grok is on PATH and claude is not, else claude; it never
picks omp (use --runtime omp or SWARM_RUNTIME=omp). Preflight checks only the selected runtime's binary (and
`claude auth status` for claude). The omp result is the `yield` payload of the terminal agent_end, falling back
to the last fenced json block in the final assistant text.

Agent sessions run without signing keys (SWARM_AGENT_SESSION=1, SWARM_CHILD=1, SWARM_AGENT=<slug>), so a gate
script they run records nothing. For each gate task the runner itself runs the gate script on the gate task's own
id after the session, while the task is still leased; the script records signed verdicts on the gate task's Task
Store gate_for targets (the runner writes no rows itself).
A01 rules (fail-closed gates, bounded rework, escalation) are
applied between rounds by the Task Store.
With SUBSTRATE_URL set (never on --dry-run) each task is worked under a substrate lease the runner holds for its agent:
claimed before dispatch (a denied claim waits), beaten every TTL/3 while the session runs and through review, completed at
DONE, released and re-claimed on CHANGES_REQUESTED, released otherwise; work left in review by an earlier run is claimed
again at start (swarm/substrate_lease.py, docs/substrate-leases.md).
Each boundary that changes the accountable agent (a dependency owned by another agent, a failing gate, an escalation) is
written as a `coord_handoff` packet by its sender, and each assignment carries the task's context rebuilt from the
ledger (swarm/substrate_handoff.py, docs/substrate-handoffs.md).
Each session, each of its lease calls and each packet it sends carries the agent's own SUBSTRATE_TOKEN, from that agent's
env file for the workspace (<repo>); no other SUBSTRATE_TOKEN* reaches the session. The bracketed flags appear when the
install wired the substrate MCP entry into <repo> (swarm/workspace.py, docs/substrate-workspace.md).

  python3 scripts/swarm_run.py                          # run latest plan to completion
  python3 scripts/swarm_run.py --runtime omp            # run it on omp (needs only `omp` on PATH)
  python3 scripts/swarm_run.py --dry-run                # canned agent results; prints each task's invocation to stderr
  python3 scripts/swarm_run.py --once --max-parallel 3  # a single scheduling round
"""
from __future__ import annotations
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from swarm.script_base import AgentScript  # noqa: E402
from swarm.taskstore import TaskStore, TaskState as S  # noqa: E402
from swarm.manifest import get_agent, by_capability  # noqa: E402
from swarm.envelope import build_envelope, sign_envelope  # noqa: E402
from swarm.paths import swarm_dir, latest_correlation  # noqa: E402
from swarm.errors import SwarmError, ErrorCode  # noqa: E402
from swarm.gates import SEVERITIES  # noqa: E402
from swarm.verdicts import GATE_SCRIPTS, simulated_failures  # noqa: E402
from swarm.results import (parse_result, validate_result, apply_result, reconcile, reject,  # noqa: E402
                           agent_failed, agent_findings, agent_verdict)
from swarm import memory as swarm_memory, substrate_client, substrate_handoff, substrate_lease, substrate_tee, workspace  # noqa: E402

# WR-12: agent sessions are untrusted principals. They get no key material and no SWARM_REQUIRE_KEY: they record
# nothing, so a key-less gate-script preview signs with the dev key instead of exiting 2. The runner keeps all
# three; it performs every APPROVED transition and the recorded gate run.
AGENT_SESSION_STRIPPED = ("SWARM_SIGNING_KEY", "SWARM_ED25519_KEY", "SWARM_REQUIRE_KEY")


def upstream_context(store: TaskStore, task: dict) -> str:
    parts = []
    for dep in task["depends_on"]:
        d = store.get(dep)
        outs = "\n".join(f"  - {o['kind']}: {o['uri']} (v{o['version']})" for o in d["outputs"]) or "  - (no registered artifacts)"
        result = (d["notes_json"].get("result") or {})
        parts.append(f"### {dep} — {d['title']} ({d['agent_id']}, {d['state']})\n{outs}\n"
                     + (f"summary: {result.get('summary_md', '')[:1200]}" if result else ""))
    return "\n".join(parts) or "(no upstream tasks)"


_SUBSTRATE_CONTEXT: dict[tuple[str, str], str] = {}
_SUBSTRATE_CONTEXT_LOCK = threading.Lock()


def substrate_memory_section(repo: Path, correlation_id: str) -> str:
    """The `## Substrate memory` block (A01's run-start context), computed once per run and cached; '' when off or down."""
    if not substrate_client.enabled():
        return ""
    key = (str(repo), correlation_id)
    with _SUBSTRATE_CONTEXT_LOCK:
        if key not in _SUBSTRATE_CONTEXT:
            try:
                graph_id = substrate_tee.lookup_graph_id(correlation_id, root=repo)
                brief = swarm_memory.run_start_context(substrate_tee.repo_slug(repo), graph_id)
            except Exception:  # noqa: BLE001 - fail open: memory context never stops a dispatch
                brief = ""
            _SUBSTRATE_CONTEXT[key] = f"## Substrate memory\n{brief}\n\n" if brief else ""
        return _SUBSTRATE_CONTEXT[key]


def assignment_prompt(store: TaskStore, task: dict, agent: dict, repo: Path, lease_s: int | None = None,
                      handoffs: str = "") -> str:
    """The session's stdin. `lease_s` is the granted TTL of the task's substrate lease (LEASE-05); a task dispatched
    unleased keeps the old meaning, its wall-clock budget. `handoffs` is the task's context rebuilt from the substrate
    ledger (substrate_handoff.render), after the Task Store's upstream section; '' leaves the prompt as it was."""
    assign = {"task_id": task["task_id"], "correlation_id": task["correlation_id"], "agent_id": agent["id"],
              "capability": task["capability"], "title": task["title"],
              "lease_s": lease_s if lease_s is not None else task["budget"].get("max_wall_s", 1800),
              "inputs": task["inputs"], "acceptance": task["acceptance"], "budget": task["budget"],
              "risk_class": task["risk_class"], "priority": task["priority"], "attempt": task["attempt"],
              "rework_loop": task["rework_loops"]}
    notes = task["notes_json"]
    if notes.get("gate"):
        assign["gate"] = notes["gate"]
        assign["gate_for"] = notes["gate_for"]
    env = sign_envelope(build_envelope(source="A01@runner", target=agent["id"], msg_type="task.assign", payload=assign,
                                       correlation_id=task["correlation_id"], priority=task["priority"],
                                       risk_class=task["risk_class"]))
    rework = ""
    if task["rework_loops"] and notes.get("feedback"):
        rework = "\n## Rework feedback (CHANGES_REQUESTED)\nAddress every finding below before reporting IN_REVIEW:\n" \
                 + json.dumps(notes["feedback"], indent=2)
    gate_note = ""
    if notes.get("gate"):
        gate_note = (f"\n## Gate instructions\nYou are issuing the **{notes['gate']}** gate for tasks {notes['gate_for']}. "
                     f"Run your gate script with `--task-id {task['task_id']}` (this gate task's own id) to see its findings; "
                     f"in this headless session it records nothing. After the session the runner re-runs it with the "
                     f"signing key and records the verdict on each gate_for target. Then report one JSON with "
                     f"`\"gate\": \"{notes['gate']}\"` and `\"verdicts\": {{\"<target_task_id>\": {{\"verdict\": \"pass|fail\", \"findings\": [...]}}}}` "
                     f"(advisory: fail findings become rework feedback, review findings are passed to the review gate "
                     f"script; only script-written verdicts count).")
    return substrate_memory_section(repo, task["correlation_id"]) + f"""# task.assign (signed envelope)
```json
{json.dumps(env, indent=2)}
```

## Brief
{notes.get('brief_excerpt', '')}

## Working repository
{repo}
Swarm runtime lives at {ROOT} (scripts: `python3 {ROOT}/scripts/<script>.py --root {repo} --task-id {task['task_id']} --correlation-id {task['correlation_id']} --json`).

## Upstream artifacts
{upstream_context(store, task)}{handoffs}
{gate_note}{rework}

Execute the task per your agent instructions and finish with your summary and the single ```json result block."""


def preflight_auth(claude_bin: str) -> None:
    """Fail fast (E-DEP) when the CLI has no credentials instead of burning task attempts."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        proc = subprocess.run([claude_bin, "auth", "status"], capture_output=True, text=True, timeout=30)
        status = json.loads(proc.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return  # older CLI without `auth status`; let the first task surface any problem
    if status.get("loggedIn") is False:
        raise SwarmError(ErrorCode.E_DEP, "claude CLI is not logged in — run `claude login` (or `claude setup-token` for headless use) "
                                          "or export ANTHROPIC_API_KEY; use --dry-run to simulate without credentials")


RUNTIMES = ("claude", "grok", "omp")
# the child-env deltas every runtime gets; --dry-run prints exactly these (never key material)
CHILD_ENV_KEYS = ("SWARM_DIR", "SWARM_CHILD", "SWARM_AGENT_SESSION", "SWARM_AGENT", "AIO_UPLIFT", "AIO_SWARM")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
FRONTMATTER_TOOLS = re.compile(r"^tools:[ \t]*(.*?)[ \t]*$", re.M)


def resolve_runtime(explicit: str) -> str:
    """claude|grok|omp as given; auto: SWARM_RUNTIME=grok|omp, else grok when grok is on PATH and claude is not,
    else claude. auto never picks omp from PATH (OPEN-3)."""
    if explicit in RUNTIMES:
        return explicit
    if os.environ.get("SWARM_RUNTIME") in ("grok", "omp"):
        return os.environ["SWARM_RUNTIME"]
    if shutil.which("grok") and not shutil.which("claude"):
        return "grok"
    return "claude"


def runtime_bin(runtime: str, args) -> str:
    return {"claude": getattr(args, "claude_bin", "claude"), "grok": getattr(args, "grok_bin", "grok"),
            "omp": getattr(args, "omp_bin", "omp")}[runtime]


def omp_agent(slug: str, sdir: Path) -> tuple[str, Path]:
    """(--tools CSV incl. yield, body file): omp/agents/<slug>.md's frontmatter tools, and its body with the
    frontmatter stripped, written to $SWARM_DIR/agents/<slug>.md (a missing file would be appended as literal text)."""
    src = ROOT / "omp" / "agents" / f"{slug}.md"
    try:
        raw = src.read_text()
    except OSError as e:
        raise SwarmError(ErrorCode.E_DEP, f"omp agent body {src} unreadable ({e.strerror}); run scripts/build_agents.py") from e
    fm = FRONTMATTER.match(raw)
    tools_m = FRONTMATTER_TOOLS.search(fm.group(1)) if fm else None
    if fm is None or tools_m is None:
        raise SwarmError(ErrorCode.E_DEP, f"omp agent body {src} has no frontmatter tools: line")
    tools = [t.strip() for t in tools_m.group(1).strip("\"'").split(",") if t.strip()]
    if "yield" not in tools:
        tools.append("yield")
    body = sdir / "agents" / f"{slug}.md"
    body.parent.mkdir(parents=True, exist_ok=True)
    # atomic: parallel tasks of one agent rewrite the same file while an earlier child may be reading it
    tmp = body.with_name(f".{slug}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(raw[fm.end():])
    os.replace(tmp, body)
    return ",".join(tools), body


def headless_command(runtime: str, agent: dict, repo: Path, sdir: Path, args) -> tuple[list[str], dict, Path]:
    """(argv, env, cwd) of one agent session; the prompt goes on stdin. Shared by the live path and --dry-run.

    `repo` is the installed workspace (ADR 0001 S4). The child's environment holds no token but its own: every
    SUBSTRATE_TOKEN* the runner has is dropped, and SUBSTRATE_TOKEN is set from the agent's env file, the one the lease
    calls use too (swarm/workspace.py). Each runtime reads the `substrate` MCP entry the install wrote:
    - claude runs in this checkout, where a workspace `.mcp.json` is never read, so it is passed as --mcp-config
      (strict: the agent's tools list admits no other server anyway) and its tools are added to --allowedTools;
    - grok reads `.grok/config.toml` only in a trusted folder, so it gets --trust, which a --yolo session implies;
    - omp reads `.mcp.json` from its --cwd, and its --tools list does not hide MCP tools (they are xd:// devices)."""
    slug = agent["slug"]
    # Child sessions are full CLI sessions: their brief fires UserPromptSubmit. Mark them so
    # Prompt Uplift (20 min/agent) and the swarm kickoff hooks stay off — uplift runs once, on the user's prompt.
    env, _source, _problem = workspace.child_env(
        {k: v for k, v in os.environ.items() if k not in AGENT_SESSION_STRIPPED}, repo, agent["id"])
    env.pop("SWARM_SUBSTRATE_AGENT", None)  # never inherit another process's MCP authorization marker
    # SWARM_AGENT: the session's agent identity; the omp swarm_gate tool runs only that agent's gate (WR-03)
    env.update(SWARM_DIR=str(sdir), SWARM_CHILD="1", SWARM_AGENT_SESSION="1", SWARM_AGENT=slug,
               AIO_UPLIFT="0", AIO_SWARM="0")
    model = ["--model", args.model] if args.model else []
    binary = runtime_bin(runtime, args)
    disabled = (env.get("SUBSTRATE_DISABLED") or "").strip() == "1"
    wired = (not disabled) and workspace.projected_mcp(repo, runtime)
    if disabled:
        env.pop(workspace.TOKEN, None)
        env.pop("SUBSTRATE_URL", None)
        env.pop("SWARM_SUBSTRATE_AGENT", None)
    if runtime == "grok":
        trust = ["--trust"] if wired else []
        return [binary, "-p", "--agent", slug, "--output-format", "json", "--yolo", *trust, "--cwd", str(repo),
                *model], env, repo
    if runtime == "omp":
        if wired and env.get(workspace.TOKEN):
            env["SWARM_SUBSTRATE_AGENT"] = slug
        tools, body = omp_agent(slug, sdir)
        # omp aborts cleanly before the python timeout: a 10% margin of 5–60 s, never below 1 s (WR-03)
        margin = min(60, max(5, args.task_timeout // 10))
        # -e spelled as omp's absolute root path: omp dedups extension roots by that string, not realpath (D-01)
        return [binary, "-p", "--mode", "json", "--no-session", "--no-title", "--no-extensions",
                "-e", str((ROOT / "omp").resolve()), "--cwd", str(repo), "--approval-mode", "yolo",
                "--tools", tools, "--append-system-prompt", str(body),
                "--max-time", f"{max(1, args.task_timeout - margin)}s", *model], env, repo
    cmd = [binary, "-p", "--agent", slug, "--output-format", "json",
           "--permission-mode", args.permission_mode, "--max-turns", str(args.max_turns),
           "--add-dir", str(ROOT), *model]
    allowed = [t.strip() for t in (args.allowed_tools or "").split(",") if t.strip()]
    if wired:
        cmd += ["--mcp-config", str(workspace.mcp_config(repo, runtime)), "--strict-mcp-config"]
        allowed.append(workspace.CLAUDE_TOOLS)
    if allowed:
        cmd += ["--allowedTools", *allowed]
    return cmd + ["--add-dir", str(repo)], env, ROOT  # cwd ROOT: .claude/agents resolves


def omp_stream_text(stdout: str) -> tuple[str, dict]:
    """Normalise an `omp -p --mode json` stream to (final text, meta). The text is the last assistant message's
    text parts from the last terminal agent_end, plus — when the session yielded successfully — a trailing fenced
    json block of the yield data, so parse_result (last block wins) takes the yield payload first."""
    session_id, turns, end = None, 0, None
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "session":
            session_id = ev.get("id")
        elif ev.get("type") == "turn_end":
            turns += 1
        elif ev.get("type") == "agent_end" and ev.get("isTerminal") is not False:
            end = ev
    meta = {"session_id": session_id, "total_cost_usd": None, "num_turns": turns, "is_error": True}
    if end is None:
        return "", meta
    messages = [m for m in end.get("messages") or [] if isinstance(m, dict)]
    assistants = [m for m in messages if m.get("role") == "assistant"]
    last = assistants[-1] if assistants else {}
    content = last.get("content") or []
    text = content if isinstance(content, str) else "\n".join(
        p.get("text") or "" for p in content if isinstance(p, dict) and p.get("type") == "text")
    meta["total_cost_usd"] = sum(((m.get("usage") or {}).get("cost") or {}).get("total") or 0 for m in assistants)
    meta["is_error"] = last.get("stopReason") in ("error", "aborted")
    for m in reversed(messages):
        if m.get("role") != "toolResult" or m.get("toolName") != "yield":
            continue
        details = m.get("details")
        details = details if isinstance(details, dict) else {}
        if m.get("isError") is False and details.get("status") == "success":
            if isinstance(details.get("data"), dict):
                text += f"\n```json\n{json.dumps(details['data'])}\n```"
        else:
            # D-02: an error yield fails the session, whatever json block the assistant text carries (WR-02)
            meta["yield_error"] = details.get("error") or f"yield status={details.get('status')} isError={m.get('isError')}"
            meta["is_error"] = True
        break
    return text, meta


class AgentTimeout(subprocess.TimeoutExpired):
    """The session outlived --task-timeout and its process group was killed; text/meta hold its partial output."""

    def __init__(self, cmd, timeout, text: str, meta: dict):
        super().__init__(cmd, timeout)
        self.text, self.meta = text, meta


class LeaseStopped(Exception):
    """The lease keeper stopped the dispatch: the substrate says the runner no longer holds the task's node (LEASE-06),
    or the task moved on in another process. `reason` is the FAILED reason from substrate_lease's refusal table (or
    substrate_lease.moved_on_reason); text/meta hold the session's partial output."""

    def __init__(self, reason: str, text: str, meta: dict):
        super().__init__(reason)
        self.reason, self.text, self.meta = reason, text, meta


class DispatchWatch:
    """The lease keeper's handle on one dispatch (LEASE-06), attached before the session spawns and detached once the
    result is applied. `stop(reason)` records the first reason and stops whichever child works the task at that moment:
    the agent session, then the runner's gate script. A child that attaches after a reason was recorded is stopped at
    once, and `applying` refuses the result, so a lost lease never gets a verdict recorded or a result accepted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reason: str | None = None
        self._child: Callable[[str], None] | None = None

    def stop(self, reason: str) -> None:
        with self._lock:  # waits while a result is being applied: that result was written under a held lease
            if self.reason is None:
                self.reason = reason
            child = self._child
        if child is not None:
            child(self.reason)

    def attach(self, child: Callable[[str], None] | None) -> None:
        """`on_session` / `on_process`: the running child's stop, or None once it has ended."""
        with self._lock:
            self._child = child
            reason = self.reason
        if child is not None and reason is not None:
            child(reason)

    def check(self, text: str, meta: dict) -> None:
        if self.reason is not None:
            raise LeaseStopped(self.reason, text, {**meta, "lease_stopped": self.reason})

    @contextmanager
    def applying(self, text: str, meta: dict):
        """Write the result only while no stop has been recorded, and hold the keeper's stop off until it is written."""
        with self._lock:
            self.check(text, meta)
            yield


# process groups of the running children (agent sessions, the runner's gate scripts): they run in their own session,
# so a signal to the runner's group misses them
_SESSIONS: dict[int, ChildGroup] = {}
_SESSIONS_LOCK = threading.Lock()
_STOPPING = threading.Event()  # the runner is ending its sessions: a session that registers now is killed at once
_FORWARDED = (signal.SIGTERM, signal.SIGHUP)
STOP_GRACE_S = 10  # a stopped or timed-out child group gets this long between SIGTERM and SIGKILL


def kill_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _end_group(pgid: int, deadline: float) -> None:
    """Return once no process is left in the group, SIGKILLing what is still there at `deadline` (monotonic). The
    leader's exit proves nothing: a tool with its own pipes can outlive SIGTERM after its parent has gone."""
    while _group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _group_alive(pgid):
        kill_group(pgid, signal.SIGKILL)
        settle = time.monotonic() + 2  # SIGKILL cannot be caught; what is left is the init process reaping orphans
        while _group_alive(pgid) and time.monotonic() < settle:
            time.sleep(0.05)


def kill_sessions(grace: float = 5) -> None:
    """SIGTERM every session group, so omp disposes its session and tool processes; SIGKILL what outlives `grace` s
    (WR-09). The workers still reap their sessions and record the killed tasks as rejected results, clearing
    notes.running, so the next run retries them."""
    with _SESSIONS_LOCK:
        _STOPPING.set()
        sessions = list(_SESSIONS.values())
    for group in sessions:
        group.stop("E-TIMEOUT: runner shutdown", grace=grace)
    groups = [group.pgid for group in sessions]
    deadline = time.monotonic() + grace
    while groups and time.monotonic() < deadline:
        time.sleep(0.1)
        groups = [g for g in groups if _group_alive(g)]
    for pgid in groups:
        kill_group(pgid, signal.SIGKILL)


def _terminate(signum, _frame) -> None:
    """SIGTERM/SIGHUP (GNU timeout, a terminal hangup, `kill -- -PGID`): raise into run()'s BaseException path, which
    ends the sessions (T-06-12). A repeat while that teardown runs is ignored, so it cannot cut kill_sessions short."""
    if _STOPPING.is_set():
        return
    _STOPPING.set()
    raise SystemExit(128 + signum)


def reap_group(proc: subprocess.Popen) -> tuple[str, str]:
    """SIGTERM the child's whole process group, SIGKILL it after STOP_GRACE_S; the partial (stdout, stderr). Returns
    once every process in the group is gone, not only the leader."""
    deadline = time.monotonic() + STOP_GRACE_S
    for sig in (signal.SIGTERM, signal.SIGKILL):
        kill_group(proc.pid, sig)
        try:
            out = proc.communicate(timeout=STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            continue
        _end_group(proc.pid, deadline)
        return out
    proc.kill()  # a descendant that left the group still holds the pipes
    proc.wait()
    return "", ""


class ChildGroup:
    """A child in its own session and process group (WR-04), registered for kill_sessions (WR-09). `stop(reason)` is the
    lease keeper's handle: SIGTERM the group and SIGKILL what is left STOP_GRACE_S later. `close()` forgets the group only
    once every process in a stopped group has exited, so a stopped session leaves no tool running (LEASE-06)."""

    def __init__(self, cmd: list[str], *, cwd, env: dict, stdin=subprocess.PIPE):
        self.proc = subprocess.Popen(cmd, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     cwd=cwd, env=env, start_new_session=True)
        self.pgid = self.proc.pid
        self.stopped: list[str] = []
        self._lock = threading.Lock()
        self._deadline: float | None = None
        self._killer: threading.Timer | None = None
        self._closed = False
        with _SESSIONS_LOCK:
            _SESSIONS[self.pgid] = self
            stopping = _STOPPING.is_set()
        if stopping:  # spawned after kill_sessions took its snapshot (WR-09)
            self.stop("E-TIMEOUT: runner shutdown", grace=0)

    def stop(self, reason: str, *, grace: float | None = None) -> None:
        grace = STOP_GRACE_S if grace is None else grace
        with self._lock:
            self.stopped.append(reason)
            if self._deadline is not None or self._closed:  # already stopping, or already gone and forgotten
                return
            self._deadline = time.monotonic() + grace
            kill_group(self.pgid, signal.SIGTERM)
            # a process that ignores SIGTERM is killed after the grace even when it no longer holds the child's pipes
            self._killer = threading.Timer(grace, kill_group, (self.pgid, signal.SIGKILL))
            self._killer.daemon = True
            self._killer.start()

    def close(self) -> None:
        """After the leader has been reaped: wait out a stopped group (SIGKILL at the grace), then forget it."""
        with self._lock:
            self._closed = True
            deadline, killer = self._deadline, self._killer
        try:
            if deadline is not None:
                _end_group(self.pgid, deadline)
        finally:
            if killer is not None:
                killer.cancel()
            with _SESSIONS_LOCK:
                _SESSIONS.pop(self.pgid, None)


# omp 18.3.1 `-e`: a package that fails to load is only this stderr line (main.ts formatExtensionLoadNotifications),
# rc 0, and the session runs on without it — for ours, without the swarm guard, in yolo (T-06-06)
OMP_EXTENSION_LOAD_ERROR = re.compile(r"^(?:\x1b\[[0-9;]*m)*(Failed to load extension .*?)(?:\x1b\[[0-9;]*m)*$", re.M)


def session_output(runtime: str, stdout: str, stderr: str, returncode) -> tuple[str, dict]:
    meta = {"returncode": returncode, "stderr": (stderr or "")[-2000:]}
    if runtime == "omp":
        text, stream_meta = omp_stream_text(stdout or "")
        meta.update(stream_meta)
        load_error = OMP_EXTENSION_LOAD_ERROR.search(stderr or "")
        if load_error:
            meta.update(extension_error=load_error.group(1)[:500], is_error=True)
        return text, meta
    text = stdout or ""
    try:
        data = json.loads(text)
        meta.update({k: data.get(k) for k in ("total_cost_usd", "duration_ms", "num_turns", "is_error", "session_id")})
        text = data.get("result", "") if isinstance(data, dict) else text
    except json.JSONDecodeError:
        pass
    return text, meta


def run_agent_headless(agent: dict, prompt: str, repo: Path, args, *, on_session=None) -> tuple[str, dict]:
    """Run one agent session. `on_session(stop)` is called once the session is spawned and `on_session(None)` once it
    has ended; `stop(reason)` ends the session's process group and makes this raise LeaseStopped (the lease keeper's
    handle on a session whose node the runner no longer holds). A stopped session returns only once its whole process
    group is gone."""
    runtime = resolve_runtime(getattr(args, "runtime", "auto"))
    cmd, env, cwd = headless_command(runtime, agent, repo, swarm_dir(repo), args)
    try:
        # own session: a timeout kills the whole group, omp's tool processes and MCP servers included (WR-04)
        group = ChildGroup(cmd, cwd=cwd, env=env)
    except OSError as e:
        raise SwarmError(ErrorCode.E_DEP, f"cannot spawn {cmd[0]} ({e.strerror or e})") from e
    proc = group.proc
    try:
        if on_session is not None:
            on_session(group.stop)
        try:
            out, err = proc.communicate(prompt, timeout=args.task_timeout)
        except subprocess.TimeoutExpired:
            text, meta = session_output(runtime, *reap_group(proc), proc.returncode)
            raise AgentTimeout(cmd, args.task_timeout, text, {**meta, "timed_out": True}) from None
    finally:
        if on_session is not None:
            on_session(None)
        group.close()
    text, meta = session_output(runtime, out, err, proc.returncode)
    if group.stopped:  # a result finished under a lease the runner no longer holds is never applied
        raise LeaseStopped(group.stopped[0], text, {**meta, "lease_stopped": group.stopped[0]})
    return text, meta


def dry_run_invocation(task: dict, agent: dict, repo: Path, sdir: Path, args) -> dict:
    """--dry-run: print the exact session invocation (key-var unsets, env deltas, argv, stdin file) to stderr as a
    replayable `env -u … K=V … argv < file` line, then where the session's SUBSTRATE_TOKEN comes from; spawn nothing.
    The token's value is never printed: a replay sources the agent's env file for it."""
    runtime = resolve_runtime(getattr(args, "runtime", "auto"))
    cmd, env, _cwd = headless_command(runtime, agent, repo, sdir, args)
    slug = agent["slug"]
    # names only: the live child env drops these, so a replay from the runner's shell must too (WR-06, INST-03).
    # SUBSTRATE_TOKEN is not unset: the operator sources the agent's env file for it before replay.
    stripped = [*AGENT_SESSION_STRIPPED, *sorted(
        k for k in os.environ
        if workspace.carries_token(k) and k != workspace.TOKEN
    )]
    unset = " ".join(f"-u {k}" for k in stripped)
    clean_base = {k: v for k, v in os.environ.items() if not workspace.carries_token(k)}
    token, source, problem = workspace.credential(agent["id"], repo, clean_base)
    disabled = (env.get("SUBSTRATE_DISABLED") or "").strip() == "1"
    if runtime == "omp" and not disabled and workspace.projected_mcp(repo, runtime) and token is not None:
        env["SWARM_SUBSTRATE_AGENT"] = slug
    keys = [*CHILD_ENV_KEYS, *(["SWARM_SUBSTRATE_AGENT"] if "SWARM_SUBSTRATE_AGENT" in env else [])]
    deltas = " ".join(f"{k}={shlex.quote(env[k])}" for k in keys if k in env)
    stdin = sdir / "assignments" / f"{task['task_id']}.a{task['attempt']}.md"
    print(f"dry-run {task['task_id']} [{agent['id']}]: env {unset} {deltas} {shlex.join(cmd)} < {shlex.quote(str(stdin))}",
          file=sys.stderr, flush=True)
    source_str = f"from {source}" if token is not None else f"none ({problem})"
    print(f"dry-run {task['task_id']} [{agent['id']}] SUBSTRATE_TOKEN: {source_str}", file=sys.stderr, flush=True)
    return {"dry_run": True, "runtime": runtime, "argv": cmd}


def canned_result(task: dict, agent: dict) -> str:
    notes = task["notes_json"]
    if notes.get("gate"):
        fails = simulated_failures()
        verdicts = {}
        for t in notes["gate_for"]:
            if f"{t}:{notes['gate']}" in fails:
                verdicts[t] = {"verdict": "fail", "findings": [{"id": "SIM-1", "severity": "major", "kind": "functional",
                                                                "summary": "simulated gate failure"}]}
            else:
                verdicts[t] = {"verdict": "pass", "findings": []}
        payload = {"gate": notes["gate"], "task_id": task["task_id"], "state": "IN_REVIEW",
                   "verdicts": verdicts, "summary_md": f"dry-run {notes['gate']} gate"}
    else:
        payload = {"task_id": task["task_id"], "state": "IN_REVIEW",
                   "outputs": [{"kind": task["capability"], "uri": f"dry://{task['task_id']}", "version": "1", "digest": ""}],
                   "metrics": {}, "summary_md": f"dry-run output of {agent['id']} for {task['title']}"}
    return f"dry-run\n```json\n{json.dumps(payload)}\n```"


def run_gate_script(task, repo, sdir, *, dry_run, per_target_findings=None, timeout=300, on_process=None) -> None:
    """Run the gate task's real gate script on its own id with the runner's keys (D-12/D-13): with --dry-run in a
    runner dry-run, otherwise after the agent session while the gate task is still leased. The script derives and
    records the verdict; per_target_findings adds the agent's findings as gate input. `on_process` works as
    run_agent_headless's `on_session`: the lease keeper stops a gate script whose task lost its lease, and this raises
    LeaseStopped."""
    gate = task["notes_json"]["gate"]
    script = ROOT / "scripts" / f"{GATE_SCRIPTS[gate]}.py"
    cmd = [sys.executable, str(script), *(["--dry-run"] if dry_run else []), "--task-id", task["task_id"],
           "--correlation-id", task["correlation_id"], "--root", str(repo), "--json"]
    if per_target_findings is not None:
        cmd += ["--per-target-findings", str(per_target_findings)]
    # the autonomous hook starts this runner with SWARM_CHILD=1; the runner's own gate run must still record
    env = {k: v for k, v in os.environ.items() if k not in ("SWARM_AGENT_SESSION", "SWARM_CHILD")}
    env["SWARM_DIR"] = str(Path(sdir).resolve())
    group = ChildGroup(cmd, cwd=None, env=env, stdin=subprocess.DEVNULL)
    try:
        if on_process is not None:
            on_process(group.stop)
        try:
            out, err = group.proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            reap_group(group.proc)
            raise
    finally:
        if on_process is not None:
            on_process(None)
        group.close()
    if group.stopped:  # verdicts it wrote before the stop never approve anything: this gate task's result is refused
        raise LeaseStopped(group.stopped[0], "", {"lease_stopped": group.stopped[0]})
    rc = group.proc.returncode
    if rc == 2 or rc < 0 or rc >= 129:  # 1 means findings; signals must never accept a partially written release plan
        raise SwarmError(ErrorCode.E_CONTRACT, f"{script.name} failed (exit {rc}): {(out or err)[-400:]}",
                         task_id=task["task_id"])


FINDINGS_GATES = ("review", "quality", "security")


def gate_findings_file(task: dict, text: str, sdir: Path, emit) -> Path | None:
    """Review, quality and security gates: write {target: [findings]} — what the agent reported under verdicts{}
    for each gate_for target — to results/<tid>.a<N>.findings.json for the gate script's --per-target-findings, so
    an agent-reported failure reaches the recorded verdict and fails only its own target (WR-14). Normalization fails closed and reports every change (WR-16/WR-17):
    - an entry fails its target unless its verdict is in results.PASS_VERDICTS (pass, A09's legacy approve;
      case-insensitive — request_changes, block, waive, unknown and missing verdicts fail); a failing entry
      without a finding of major or worse gets one synthesized major finding (results.agent_findings, IN-15),
      reported in gate.findings.synthesized;
    - a severity outside SEVERITIES (case-insensitive) or a non-object finding counts as major;
    - a missing severity counts as major under a failing entry, else minor;
    - findings under a key that is not a gate_for id cannot be attributed, so they apply to every target.
    Changes emit one gate.findings.coerced event; unattributed keys emit gate.findings.unattributed.
    None for the release gate or when the session left no parseable result. The agent's verdicts never count:
    the gate script still derives each verdict."""
    notes = task["notes_json"]
    if notes.get("gate") not in FINDINGS_GATES:
        return None
    result = parse_result(text or "")
    if result is None:
        return None
    verdicts = result.get("verdicts")
    verdicts = verdicts if isinstance(verdicts, dict) else {}
    gate_for = list(notes.get("gate_for", []))
    coerced: list[dict] = []
    synthesized: list[dict] = []

    def entries(key: str, applied_to: list[str]) -> list[tuple[object, bool]]:
        if key not in verdicts:
            return []
        v = verdicts[key]
        items, synth = agent_findings(v)
        if synth:
            synthesized.append({"key": key, "verdict": agent_verdict(v), "applied_to": applied_to})
        failed = agent_failed(v)
        return [(f, failed) for f in items]

    def normalize(f, failed: bool) -> dict:
        if not isinstance(f, dict):
            coerced.append({"from": "non-object", "to": "major"})
            return {"severity": "major", "kind": "semantic", "summary": str(f)[:300]}
        sev = f.get("severity")
        if isinstance(sev, str) and sev.lower() in SEVERITIES:
            return {**f, "severity": sev.lower()}
        to = "major" if sev is not None or failed else "minor"
        coerced.append({"from": "missing" if sev is None else str(sev), "to": to})
        return {**f, "severity": to, "evidence": f"{f.get('evidence') or ''} [agent severity {sev!r}]".strip()}

    stray = sorted(k for k in verdicts if k not in gate_for)
    unattributed = [normalize(f, failed) for k in stray for f, failed in entries(k, gate_for)]
    per_target = {}
    for target in gate_for:
        findings, seen = [], set()
        for f in [normalize(f, failed) for f, failed in entries(target, [target])] + unattributed:
            key = json.dumps(f, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                findings.append(f)
        per_target[target] = findings
    if coerced:
        emit("gate.findings.coerced", {"task_id": task["task_id"], "coerced": coerced})
    if synthesized:
        emit("gate.findings.synthesized", {"task_id": task["task_id"], "synthesized": synthesized})
    if stray:
        emit("gate.findings.unattributed", {"task_id": task["task_id"], "keys": stray, "applied_to": gate_for,
                                            "findings": len(unattributed)})
    path = Path(sdir) / "results" / f"{task['task_id']}.a{task['attempt']}.findings.json"
    path.write_text(json.dumps(per_target, indent=2))
    return path


def dispatchable(store: TaskStore, corr: str, emit, handoffs=None) -> list[dict]:
    ready = store.ready(corr)
    # rework tasks sit in IN_PROGRESS with no running session; treat them as ready too
    for t in store.list(correlation_id=corr, state=S.IN_PROGRESS.value):
        if t["notes_json"].get("running") is None and store.deps_satisfied(t):
            ready.append(t)
    for t in store.list(correlation_id=corr, state=S.FAILED.value):
        if t["attempt"] < t["max_attempts"]:
            store.transition(t["task_id"], S.RETRY, reason="auto-retry")
            ready.append(store.get(t["task_id"]))
        else:
            tid = t["task_id"]
            leases = handoffs.leases if handoffs is not None else None
            # FAILED normally released the lease already. If one remains, keep it until the escalation packet lands.
            with leases.transition(tid) if leases is not None else nullcontext():
                store.transition(tid, S.ESCALATED, reason="max_attempts reached")
                emit("escalation.request", {"task_id": tid, "reason_code": "E-CONTRACT", "evidence": ["max_attempts reached"],
                                            "options": ["human review", "cancel", "re-plan"]})
                if handoffs is not None:
                    failed = [h["reason"] for h in store.history(tid) if h["to_state"] == S.FAILED.value]
                    handoffs.escalated(tid, reason="max_attempts reached"
                                       + (f"; last failure: {failed[-1]}" if failed and failed[-1] else ""))
                if leases is not None:
                    leases.settle(tid)
    return ready


def select_batch(store: TaskStore, ready: list[dict], max_parallel: int, leases) -> tuple[list[tuple[dict, dict]], list[str]]:
    """(task, agent) pairs to dispatch this round, and the tasks left waiting on a lease. The first `max_parallel` ready
    tasks fill the round, as before; with leases on, each must first hold its node (or run unleased when the substrate
    cannot answer). A denied, refused or busy claim leaves the task where it is (PLANNED/RETRY, or IN_PROGRESS for a rework)
    without taking a slot, for a later round or run (LEASE-05)."""
    batch, waiting, slots = [], [], max_parallel
    for t in ready:
        if slots <= 0:
            break
        try:
            agent = get_agent(t["agent_id"]) if t["agent_id"] else by_capability(t["capability"])[0]
        except (KeyError, IndexError):
            store.transition(t["task_id"], S.BLOCKED, reason="no agent for capability")
            slots -= 1
            continue
        if leases is not None:
            claim = leases.acquire(t, agent["id"])
            if not claim.dispatch:
                holder = (claim.holder or {}).get("session_id")
                waiting.append(f"{t['task_id']}: {claim.status}" + (f" (held by {holder})" if holder else ""))
                continue
        batch.append((t, agent))
        slots -= 1
    return batch, waiting


def execute_one(store_path, task, agent, args, ctx, repo, leases=None, handoffs=None):
    """Dispatch one task to its agent and apply the result. With leases on, the dispatch is watched by the lease keeper
    and the task's lease is settled from its final state, whatever happened on the way (LEASE-07): only after the
    session's and the gate script's process groups are gone. With handoffs on, the task's dependency packets are written
    first and its assignment carries the context rebuilt from them (HAND-01/02)."""
    try:
        return _execute_one(store_path, task, agent, args, ctx, repo, leases, handoffs)
    finally:
        if leases is not None:
            leases.settle(task["task_id"])


def _execute_one(store_path, task, agent, args, ctx, repo, leases, handoffs):
    store = TaskStore(store_path)  # sqlite: one connection per thread
    sdir = store.path.parent
    tid = task["task_id"]
    if task["state"] != S.IN_PROGRESS.value:
        store.transition(tid, S.CLAIMED, reason=f"awarded to {agent['id']}")
        store.transition(tid, S.IN_PROGRESS, reason="lease started")
    # A01 is the only writer of notes.dry_run: dry-run gate rows record and count only on flagged tasks,
    # and a real dispatch clears a flag left by an earlier dry-run
    store.set_notes(tid, running=time.time(), dry_run=bool(args.dry_run))
    # LEASE-06: the keeper watches the whole dispatch, from before the session spawns until its result is written, so
    # a lease lost (or a task moved on elsewhere) at any point stops the session or gate script and rejects the result
    watch = DispatchWatch()
    if leases is not None:
        leases.watch(tid, watch.stop)
    text, meta, raw_recorded = "", {}, False
    try:
        task = store.get(tid)
        context = ""
        if handoffs is not None:  # sent before the session starts, so its own context already holds them
            handoffs.dispatched(task, agent["id"])
            context = handoffs.context(tid, agent["id"])
        prompt = assignment_prompt(store, task, agent, repo, lease_s=leases.lease_s(tid) if leases is not None else None,
                                   handoffs=context)
        (sdir / "assignments").mkdir(parents=True, exist_ok=True)
        (sdir / "assignments" / f"{tid}.a{task['attempt']}.md").write_text(prompt)
        if args.dry_run:
            meta = dry_run_invocation(task, agent, repo, sdir, args)
            if task["notes_json"].get("gate"):
                run_gate_script(task, repo, sdir, dry_run=True)
            text = canned_result(task, agent)
        else:
            watch.check(text, meta)  # lost before the session spawned: it never starts
            text, meta = run_agent_headless(agent, prompt, repo, args, on_session=watch.attach)
        (sdir / "results").mkdir(parents=True, exist_ok=True)
        (sdir / "results" / f"{tid}.a{task['attempt']}.md").write_text(text or "")
        result, err = None, None
        try:
            result = validate_result(parse_result(text), task_id=tid)
        except SwarmError as e:
            err = e
        if meta.get("yield_error"):  # D-02: an error yield is E-CONTRACT, whatever json the text carries (WR-02)
            result, err = None, SwarmError(ErrorCode.E_CONTRACT, f"agent yielded an error: {meta['yield_error']}"[:500],
                                           task_id=tid)
        if meta.get("extension_error"):  # T-06-06: an unguarded session's result is never applied, nor its gate recorded
            result, err = None, SwarmError(ErrorCode.E_DEP, "omp ran the session without the swarm guard: "
                                           f"{meta['extension_error']}"[:500], task_id=tid)
        # CR-04: record a gate only for a session that completed and asked to finish it — a crashed, errored or
        # BLOCKED/FAILED gate session gets no script run, so its targets keep the gate absent
        if (not args.dry_run and task["notes_json"].get("gate") and result is not None
                and result["state"] == S.IN_REVIEW.value and not meta.get("is_error") and not meta.get("returncode")):
            # WR-12: the key-holding runner, not the agent, records this gate — once per dispatch, still leased
            run_gate_script(task, repo, sdir, dry_run=False, timeout=args.task_timeout,
                            per_target_findings=gate_findings_file(task, text, sdir, ctx.emit), on_process=watch.attach)
        ctx.emit("task.result.raw", {"task_id": tid, "agent": agent["id"], "meta": meta})
        store.set_notes(tid, meta=meta)
        raw_recorded = True
        with watch.applying(text, meta):
            if result is None:
                outcome = reject(store, tid, reason=str(err), mode="headless", emit=ctx.emit)
            else:
                outcome = apply_result(store, task, agent_id=agent["id"], result=result, meta=meta, emit=ctx.emit,
                                       mode="headless")
    except AgentTimeout as e:
        # WR-04: keep what the session wrote before its process group was killed
        (sdir / "results").mkdir(parents=True, exist_ok=True)
        (sdir / "results" / f"{tid}.a{task['attempt']}.md").write_text(e.text or "")
        ctx.emit("task.result.raw", {"task_id": tid, "agent": agent["id"], "meta": e.meta})
        store.set_notes(tid, meta=e.meta)
        store.transition(tid, S.FAILED, reason="E-TIMEOUT: task_timeout exceeded")
        outcome = "FAILED"
    except LeaseStopped as e:
        # LEASE-06: the substrate says another session (or nobody) holds the node, or the task moved on elsewhere, so
        # this result is never applied, whether the stop came during the session, the gate script or before the write
        text, meta = e.text or text, {**meta, **e.meta}
        (sdir / "results").mkdir(parents=True, exist_ok=True)
        (sdir / "results" / f"{tid}.a{task['attempt']}.md").write_text(text or "")
        if not raw_recorded:
            ctx.emit("task.result.raw", {"task_id": tid, "agent": agent["id"], "meta": meta})
        store.set_notes(tid, meta=meta)
        state = store.get(tid)["state"]
        if state in substrate_lease.LET_GO:  # moved on in another process (CANCELLED): that transition stands
            outcome = state
        else:
            store.transition(tid, S.FAILED, reason=e.reason[:500])
            outcome = "FAILED"
    except subprocess.TimeoutExpired:
        store.transition(tid, S.FAILED, reason="E-TIMEOUT: task_timeout exceeded")
        outcome = "FAILED"
    except SwarmError as e:
        store.transition(tid, S.FAILED, reason=str(e)[:500])
        outcome = "FAILED"
    finally:
        if leases is not None:
            leases.watch(tid, None)
        store.set_notes(tid, running=None)
    return tid, agent["id"], outcome


def lease_bridge(args, store_path: Path, corr: str, repo: Path, ctx):
    """The run's substrate leases (ADR 0001 S2), or None: --dry-run never touches the substrate, and off means off."""
    if args.dry_run or not substrate_client.enabled():
        return None
    try:
        return substrate_lease.LeaseBridge(store_path, corr, root=repo, emit=ctx.emit)
    except ValueError as e:  # SWARM_REPLICA or SWARM_LEASE_TTL_S: refused at start, not at every claim
        raise SwarmError(ErrorCode.E_INPUT, str(e)) from e


def handoff_bridge(store_path: Path, corr: str, repo: Path, ctx, leases):
    """The run's handoff packets (ADR 0001 S2 criteria 7-9), on exactly when its leases are: never on --dry-run, off
    means off."""
    if leases is None:
        return None
    return substrate_handoff.HandoffBridge(store_path, corr, root=repo, emit=ctx.emit, env=leases.env, leases=leases)


def run(args, ctx) -> dict:
    repo = Path(args.repo).resolve()
    # the runner's records belong to the workspace it works: their repo slug, and the agent env files the event tee
    # takes each record's token from (workspace.credential), are the workspace's, not those of the cwd it started in
    ctx.root = repo
    sdir = swarm_dir(repo, create=True)
    # one absolute state dir for this process and every child it spawns (D-10)
    os.environ["SWARM_DIR"] = str(sdir)
    store_path = sdir / "tasks.db"
    store = TaskStore(store_path)
    corr = ctx.correlation_id
    if corr is None:
        terminal = {"DONE", "CANCELLED", "ESCALATED"}
        live = sorted({t["correlation_id"] for t in store.list() if t["state"] not in terminal})
        if len(live) > 1:
            raise SwarmError(ErrorCode.E_INPUT, f"{len(live)} non-terminal plans {live}; pass --correlation-id")
        corr = live[0] if live else latest_correlation(repo)
    if not corr:
        raise SwarmError(ErrorCode.E_INPUT, "no plan found — run scripts/orch_plan.py first")
    ctx.correlation_id = corr
    if not store.list(correlation_id=corr):
        raise SwarmError(ErrorCode.E_INPUT, f"no tasks for correlation {corr} in {store_path}; plan with "
                         "orch_plan.py --repo <same repo> (or export one SWARM_DIR)")
    args.runtime = resolve_runtime(args.runtime)  # once per run: auto cannot flip mid-run
    if not args.dry_run:
        name = runtime_bin(args.runtime, args)
        binary = shutil.which(name)
        if not binary:
            raise SwarmError(ErrorCode.E_DEP, f"{name} not on PATH (--runtime {args.runtime}; use --dry-run to simulate)")
        # absolute: sessions spawn with cwd=repo (omp, grok) or ROOT (claude), where a relative path breaks (WR-05)
        binary = os.path.abspath(binary)
        setattr(args, f"{args.runtime}_bin", binary)
        if args.runtime == "claude":
            preflight_auth(binary)
    leases = lease_bridge(args, store_path, corr, repo, ctx)
    handoffs = handoff_bridge(store_path, corr, repo, ctx, leases)
    log, rounds = [], 0
    # T-06-12: a SIGTERM/SIGHUP to the runner's group ends the sessions too, then the previous handlers come back.
    # WR-10: a signal inherited as ignored (nohup, a parent's SIG_IGN) stays ignored.
    _STOPPING.clear()
    previous = ({s: signal.signal(s, _terminate) for s in _FORWARDED if signal.getsignal(s) is not signal.SIG_IGN}
                if threading.current_thread() is threading.main_thread() else {})
    try:
        if leases is not None:
            # work an earlier process left in review is held (and beaten) again before reconcile or any gate dispatch
            leases.resume()
            leases.start()
        while True:
            rounds += 1
            log += reconcile(store, corr, ctx.emit, leases=leases, handoffs=handoffs)
            if handoffs is not None:
                handoffs.flush()  # what the substrate did not take last round
            ready = dispatchable(store, corr, ctx.emit, handoffs=handoffs)
            if not ready:
                remaining = [t for t in store.list(correlation_id=corr) if t["state"] not in (S.DONE.value, S.CANCELLED.value, S.ESCALATED.value)]
                if remaining:
                    log.append(f"stalled: {[(t['task_id'], t['state']) for t in remaining]}")
                break
            batch, waiting = select_batch(store, ready, args.max_parallel, leases)
            if not batch and waiting:  # every candidate's node is held elsewhere: asking again at once would only spin
                log.append(f"waiting on leases: {waiting}")
                break
            with ThreadPoolExecutor(max_workers=max(1, args.max_parallel)) as pool:
                try:
                    futs = [pool.submit(execute_one, store_path, t, a, args, ctx, repo, leases, handoffs) for t, a in batch]
                    for f in as_completed(futs):
                        tid, aid, outcome = f.result()
                        log.append(f"round {rounds}: {tid} [{aid}] → {outcome}")
                        print(log[-1], file=sys.stderr, flush=True)  # progress on stderr keeps --json stdout clean
                except BaseException:  # Ctrl-C, SIGTERM, SIGHUP: sessions run in their own session, so it missed them
                    kill_sessions()
                    raise
            if args.once or rounds >= args.max_rounds:
                break
    finally:
        for s, handler in previous.items():
            signal.signal(s, signal.SIG_DFL if handler is None else handler)
        if leases is not None:
            leases.close()  # the keeper stops; settling below needs no thread
    log += reconcile(store, corr, ctx.emit, leases=leases, handoffs=handoffs)
    if handoffs is not None:
        handoffs.flush()
    tasks = store.list(correlation_id=corr)
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    complete = all(t["state"] in (S.DONE.value, S.CANCELLED.value) for t in tasks)
    escalated = [t["task_id"] for t in tasks if t["state"] == S.ESCALATED.value]
    return {"status": "ok" if complete else "fail", "correlation_id": corr, "rounds": rounds, "counts": counts,
            "escalated": escalated, "complete": complete, "log": log,
            "summary": f"correlation {corr}: {counts} complete={complete} escalated={escalated or 'none'}"}


def add_args(p):
    p.add_argument("--repo", default=".", help="codebase the agents work on")
    p.add_argument("--max-parallel", type=int, default=3)
    p.add_argument("--max-rounds", type=int, default=50)
    p.add_argument("--once", action="store_true", help="single scheduling round")
    p.add_argument("--max-turns", type=int, default=40)
    p.add_argument("--task-timeout", type=int, default=1800)
    p.add_argument("--permission-mode", default="acceptEdits", choices=["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk"])
    p.add_argument("--model", help="override model for all agents")
    p.add_argument("--claude-bin", default="claude")
    p.add_argument("--grok-bin", default="grok")
    p.add_argument("--omp-bin", default="omp")
    p.add_argument("--runtime", choices=["auto", *RUNTIMES], default="auto",
                   help="headless runner: claude -p --agent, grok -p --agent --yolo, omp -p --mode json, or auto "
                        "(claude/grok; omp only when explicit or SWARM_RUNTIME=omp)")
    p.add_argument("--allowed-tools", default="Bash(python3:*),Bash(git diff:*),Bash(git log:*),Bash(git status:*),Bash(ls:*),Read,Grep,Glob",
                   help="comma list passed to claude --allowedTools so headless agents can run their scripts unattended")


if __name__ == "__main__":
    sys.exit(AgentScript("A01", "swarm_run", run, description=__doc__, add_args=add_args).main())
