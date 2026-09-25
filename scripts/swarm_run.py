#!/usr/bin/env python3
"""A01 — autonomous swarm runner: executes a planned task DAG with headless agent sessions.

Each ready task is dispatched to its agent as a headless session, run with the task.assign prompt on stdin:
    claude -p --agent <slug> --output-format json --permission-mode <mode> …                (--runtime claude)
    grok -p --agent <slug> --output-format json --yolo --cwd <repo>                          (--runtime grok)
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
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

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


def assignment_prompt(store: TaskStore, task: dict, agent: dict, repo: Path) -> str:
    assign = {"task_id": task["task_id"], "correlation_id": task["correlation_id"], "agent_id": agent["id"],
              "capability": task["capability"], "title": task["title"], "lease_s": task["budget"].get("max_wall_s", 1800),
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
    return f"""# task.assign (signed envelope)
```json
{json.dumps(env, indent=2)}
```

## Brief
{notes.get('brief_excerpt', '')}

## Working repository
{repo}
Swarm runtime lives at {ROOT} (scripts: `python3 {ROOT}/scripts/<script>.py --root {repo} --task-id {task['task_id']} --correlation-id {task['correlation_id']} --json`).

## Upstream artifacts
{upstream_context(store, task)}
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
    """(argv, env, cwd) of one agent session; the prompt goes on stdin. Shared by the live path and --dry-run."""
    slug = agent["slug"]
    # Child sessions are full CLI sessions: their brief fires UserPromptSubmit. Mark them so
    # Prompt Uplift (20 min/agent) and the swarm kickoff hooks stay off — uplift runs once, on the user's prompt.
    env = {k: v for k, v in os.environ.items() if k not in AGENT_SESSION_STRIPPED}
    # SWARM_AGENT: the session's agent identity; the omp swarm_gate tool runs only that agent's gate (WR-03)
    env.update(SWARM_DIR=str(sdir), SWARM_CHILD="1", SWARM_AGENT_SESSION="1", SWARM_AGENT=slug,
               AIO_UPLIFT="0", AIO_SWARM="0")
    model = ["--model", args.model] if args.model else []
    binary = runtime_bin(runtime, args)
    if runtime == "grok":
        return [binary, "-p", "--agent", slug, "--output-format", "json", "--yolo", "--cwd", str(repo), *model], env, repo
    if runtime == "omp":
        tools, body = omp_agent(slug, sdir)
        # -e spelled as omp's absolute root path: omp dedups extension roots by that string, not realpath (D-01)
        return [binary, "-p", "--mode", "json", "--no-session", "--no-title", "--no-extensions",
                "-e", str((ROOT / "omp").resolve()), "--cwd", str(repo), "--approval-mode", "yolo",
                "--tools", tools, "--append-system-prompt", str(body),
                "--max-time", f"{max(60, args.task_timeout - 60)}s", *model], env, repo
    cmd = [binary, "-p", "--agent", slug, "--output-format", "json",
           "--permission-mode", args.permission_mode, "--max-turns", str(args.max_turns),
           "--add-dir", str(ROOT), *model]
    if args.allowed_tools:
        cmd += ["--allowedTools", *[t.strip() for t in args.allowed_tools.split(",") if t.strip()]]
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
            meta["yield_error"] = details.get("error") or f"yield status={details.get('status')} isError={m.get('isError')}"
        break
    return text, meta


def run_agent_headless(agent: dict, prompt: str, repo: Path, args) -> tuple[str, dict]:
    runtime = resolve_runtime(getattr(args, "runtime", "auto"))
    cmd, env, cwd = headless_command(runtime, agent, repo, swarm_dir(repo), args)
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=args.task_timeout, cwd=cwd, env=env)
    meta = {"returncode": proc.returncode, "stderr": proc.stderr[-2000:]}
    if runtime == "omp":
        text, stream_meta = omp_stream_text(proc.stdout)
        return text, {**meta, **stream_meta}
    text = proc.stdout
    try:
        data = json.loads(text)
        meta.update({k: data.get(k) for k in ("total_cost_usd", "duration_ms", "num_turns", "is_error", "session_id")})
        text = data.get("result", "") if isinstance(data, dict) else text
    except json.JSONDecodeError:
        pass
    return text, meta


def dry_run_invocation(task: dict, agent: dict, repo: Path, sdir: Path, args) -> dict:
    """--dry-run: print the exact session invocation (env deltas, argv, stdin file) to stderr; spawn nothing."""
    runtime = resolve_runtime(getattr(args, "runtime", "auto"))
    cmd, env, _cwd = headless_command(runtime, agent, repo, sdir, args)
    deltas = " ".join(f"{k}={shlex.quote(env[k])}" for k in CHILD_ENV_KEYS)
    stdin = sdir / "assignments" / f"{task['task_id']}.a{task['attempt']}.md"
    print(f"dry-run {task['task_id']} [{agent['id']}]: {deltas} {shlex.join(cmd)} < {shlex.quote(str(stdin))}",
          file=sys.stderr, flush=True)
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


def run_gate_script(task, repo, sdir, *, dry_run, per_target_findings=None, timeout=300) -> None:
    """Run the gate task's real gate script on its own id with the runner's keys (D-12/D-13): with --dry-run in a
    runner dry-run, otherwise after the agent session while the gate task is still leased. The script derives and
    records the verdict; per_target_findings (review gate) only adds the agent's findings as rev_gate input."""
    script = ROOT / "scripts" / f"{GATE_SCRIPTS[task['notes_json']['gate']]}.py"
    cmd = [sys.executable, str(script), *(["--dry-run"] if dry_run else []), "--task-id", task["task_id"],
           "--correlation-id", task["correlation_id"], "--root", str(repo), "--json"]
    if per_target_findings is not None:
        cmd += ["--per-target-findings", str(per_target_findings)]
    # the autonomous hook starts this runner with SWARM_CHILD=1; the runner's own gate run must still record
    env = {k: v for k, v in os.environ.items() if k not in ("SWARM_AGENT_SESSION", "SWARM_CHILD")}
    env["SWARM_DIR"] = str(Path(sdir).resolve())
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode == 2:
        raise SwarmError(ErrorCode.E_CONTRACT, f"{script.name} failed: {(proc.stdout or proc.stderr)[-400:]}",
                         task_id=task["task_id"])


def review_findings_file(task: dict, text: str, sdir: Path, emit) -> Path | None:
    """Review gate: write {target: [findings]} — what the agent reported under verdicts{} for each gate_for target —
    to results/<tid>.a<N>.findings.json for rev_gate --per-target-findings, so a finding fails only its own target
    (WR-14). Normalization fails closed and reports every change (WR-16/WR-17):
    - an entry fails its target unless its verdict is in results.PASS_VERDICTS (pass, A09's legacy approve;
      case-insensitive — request_changes, block, waive, unknown and missing verdicts fail); a failing entry
      without a finding of major or worse gets one synthesized major finding (results.agent_findings, IN-15),
      reported in gate.findings.synthesized;
    - a severity outside SEVERITIES (case-insensitive) or a non-object finding counts as major;
    - a missing severity counts as major under a failing entry, else minor;
    - findings under a key that is not a gate_for id cannot be attributed, so they apply to every target.
    Changes emit one gate.findings.coerced event; unattributed keys emit gate.findings.unattributed.
    None for other gates or when the session left no parseable result. The agent's verdicts never count:
    rev_gate still derives each verdict."""
    notes = task["notes_json"]
    if notes.get("gate") != "review":
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


def dispatchable(store: TaskStore, corr: str, emit) -> list[dict]:
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
            store.transition(t["task_id"], S.ESCALATED, reason="max_attempts reached")
            emit("escalation.request", {"task_id": t["task_id"], "reason_code": "E-CONTRACT", "evidence": ["max_attempts reached"],
                                        "options": ["human review", "cancel", "re-plan"]})
    return ready


def execute_one(store_path, task, agent, args, ctx, repo):
    store = TaskStore(store_path)  # sqlite: one connection per thread
    sdir = store.path.parent
    tid = task["task_id"]
    if task["state"] != S.IN_PROGRESS.value:
        store.transition(tid, S.CLAIMED, reason=f"awarded to {agent['id']}")
        store.transition(tid, S.IN_PROGRESS, reason="lease started")
    # A01 is the only writer of notes.dry_run: dry-run gate rows record and count only on flagged tasks,
    # and a real dispatch clears a flag left by an earlier dry-run
    store.set_notes(tid, running=time.time(), dry_run=bool(args.dry_run))
    try:
        task = store.get(tid)
        prompt = assignment_prompt(store, task, agent, repo)
        (sdir / "assignments").mkdir(parents=True, exist_ok=True)
        (sdir / "assignments" / f"{tid}.a{task['attempt']}.md").write_text(prompt)
        if args.dry_run:
            meta = dry_run_invocation(task, agent, repo, sdir, args)
            if task["notes_json"].get("gate"):
                run_gate_script(task, repo, sdir, dry_run=True)
            text = canned_result(task, agent)
        else:
            text, meta = run_agent_headless(agent, prompt, repo, args)
        (sdir / "results").mkdir(parents=True, exist_ok=True)
        (sdir / "results" / f"{tid}.a{task['attempt']}.md").write_text(text or "")
        result, err = None, None
        try:
            result = validate_result(parse_result(text), task_id=tid)
        except SwarmError as e:
            err = e
        # CR-04: record a gate only for a session that completed and asked to finish it — a crashed, errored or
        # BLOCKED/FAILED gate session gets no script run, so its targets keep the gate absent
        if (not args.dry_run and task["notes_json"].get("gate") and result is not None
                and result["state"] == S.IN_REVIEW.value and not meta.get("is_error") and not meta.get("returncode")):
            # WR-12: the key-holding runner, not the agent, records this gate — once per dispatch, still leased
            run_gate_script(task, repo, sdir, dry_run=False, timeout=args.task_timeout,
                            per_target_findings=review_findings_file(task, text, sdir, ctx.emit))
        ctx.emit("task.result.raw", {"task_id": tid, "agent": agent["id"], "meta": meta})
        store.set_notes(tid, meta=meta)
        if result is None:
            outcome = reject(store, tid, reason=str(err), mode="headless", emit=ctx.emit)
        else:
            outcome = apply_result(store, task, agent_id=agent["id"], result=result, meta=meta, emit=ctx.emit, mode="headless")
    except subprocess.TimeoutExpired:
        store.transition(tid, S.FAILED, reason="E-TIMEOUT: task_timeout exceeded")
        outcome = "FAILED"
    except SwarmError as e:
        store.transition(tid, S.FAILED, reason=str(e)[:500])
        outcome = "FAILED"
    finally:
        store.set_notes(tid, running=None)
    return tid, agent["id"], outcome


def run(args, ctx) -> dict:
    repo = Path(args.repo).resolve()
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
        binary = runtime_bin(args.runtime, args)
        if not shutil.which(binary):
            raise SwarmError(ErrorCode.E_DEP, f"{binary} not on PATH (--runtime {args.runtime}; use --dry-run to simulate)")
        if args.runtime == "claude":
            preflight_auth(binary)
    log, rounds = [], 0
    while True:
        rounds += 1
        log += reconcile(store, corr, ctx.emit)
        ready = dispatchable(store, corr, ctx.emit)
        if not ready:
            remaining = [t for t in store.list(correlation_id=corr) if t["state"] not in (S.DONE.value, S.CANCELLED.value, S.ESCALATED.value)]
            if remaining:
                log.append(f"stalled: {[(t['task_id'], t['state']) for t in remaining]}")
            break
        batch = []
        for t in ready[: args.max_parallel]:
            try:
                agent = get_agent(t["agent_id"]) if t["agent_id"] else by_capability(t["capability"])[0]
            except (KeyError, IndexError):
                store.transition(t["task_id"], S.BLOCKED, reason="no agent for capability")
                continue
            batch.append((t, agent))
        with ThreadPoolExecutor(max_workers=max(1, args.max_parallel)) as pool:
            futs = [pool.submit(execute_one, store_path, t, a, args, ctx, repo) for t, a in batch]
            for f in as_completed(futs):
                tid, aid, outcome = f.result()
                log.append(f"round {rounds}: {tid} [{aid}] → {outcome}")
                print(log[-1], file=sys.stderr, flush=True)  # progress on stderr keeps --json stdout clean
        if args.once or rounds >= args.max_rounds:
            break
    log += reconcile(store, corr, ctx.emit)
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
