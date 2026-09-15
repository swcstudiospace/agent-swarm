#!/usr/bin/env python3
"""A01 — autonomous swarm runner: executes a planned task DAG with Claude Code subagents.

Each ready task is dispatched to its agent as a headless session
    claude -p --agent <slug> --output-format json --permission-mode <mode> "<task.assign prompt>"
run from --repo (the codebase being worked on
defaults to cwd). Gate tasks record
verdicts on their targets
A01 rules (fail-closed gates, bounded rework, escalation) are
applied between rounds by the Task Store.

  python3 scripts/swarm_run.py                          # run latest plan to completion
  python3 scripts/swarm_run.py --dry-run                # simulate with canned agent results (no claude)
  python3 scripts/swarm_run.py --once --max-parallel 3  # a single scheduling round
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from swarm.script_base import AgentScript  # noqa: E402
from swarm.taskstore import TaskStore, TaskState as S  # noqa: E402
from swarm.manifest import get_agent, by_capability  # noqa: E402
from swarm.envelope import build_envelope, sign_envelope  # noqa: E402
from swarm.runlog import SWARM_DIR  # noqa: E402
from swarm.errors import SwarmError, ErrorCode  # noqa: E402

JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def latest_correlation() -> str | None:
    f = SWARM_DIR / "latest_correlation"
    return f.read_text().strip() if f.exists() else None


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
                     f"Run your gate script with `--task-id <target>` for EACH target, then report one JSON with "
                     f"`\"gate\": \"{notes['gate']}\"` and `\"verdicts\": {{\"<target_task_id>\": {{\"verdict\": \"pass|fail\", \"findings\": [...]}}}}`.")
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


def resolve_runtime(explicit: str) -> str:
    """auto: grok when SWARM_RUNTIME=grok or (grok on PATH and claude is not)."""
    if explicit in ("claude", "grok"):
        return explicit
    if os.environ.get("SWARM_RUNTIME") == "grok":
        return "grok"
    if shutil.which("grok") and not shutil.which("claude"):
        return "grok"
    return "claude"


def run_agent_headless(agent: dict, prompt: str, repo: Path, args) -> tuple[str, dict]:
    runtime = resolve_runtime(getattr(args, "runtime", "auto"))
    env = dict(os.environ, SWARM_DIR=str(SWARM_DIR.resolve()))
    if runtime == "grok":
        grok_bin = getattr(args, "grok_bin", "grok")
        cmd = [grok_bin, "-p", "--agent", agent["slug"], "--output-format", "json",
               "--yolo", "--cwd", str(repo)]
        if args.model:
            cmd += ["--model", args.model]
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=args.task_timeout, cwd=str(repo), env=env)
    else:
        cmd = [args.claude_bin, "-p", "--agent", agent["slug"], "--output-format", "json",
               "--permission-mode", args.permission_mode, "--max-turns", str(args.max_turns),
               "--add-dir", str(ROOT)]
        if args.model:
            cmd += ["--model", args.model]
        if args.allowed_tools:
            cmd += ["--allowedTools", *[t.strip() for t in args.allowed_tools.split(",") if t.strip()]]
        cmd += ["--add-dir", str(repo)]
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=args.task_timeout, cwd=ROOT, env=env)
    text, meta = proc.stdout, {"returncode": proc.returncode, "stderr": proc.stderr[-2000:]}
    try:
        data = json.loads(text)
        meta.update({k: data.get(k) for k in ("total_cost_usd", "duration_ms", "num_turns", "is_error", "session_id")})
        text = data.get("result", "") if isinstance(data, dict) else text
    except json.JSONDecodeError:
        pass
    return text, meta


def _simulated_failures() -> set[str]:
    """SWARM_DRYRUN_FAIL='T-be:quality,T-fe:review' makes those dry-run gate verdicts fail every time."""
    return {x.strip() for x in os.environ.get("SWARM_DRYRUN_FAIL", "").split(",") if x.strip()}


def canned_result(task: dict, agent: dict) -> str:
    notes = task["notes_json"]
    if notes.get("gate"):
        fails = _simulated_failures()
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


def parse_result(text: str) -> dict | None:
    blocks = JSON_BLOCK.findall(text or "")
    for raw in reversed(blocks):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return None


def apply_result(store: TaskStore, task: dict, agent: dict, result: dict | None, meta: dict, ctx) -> str:
    tid = task["task_id"]
    ctx.emit("task.result.raw", {"task_id": tid, "agent": agent["id"], "meta": meta, "parsed": bool(result)})
    if result is None:
        store.transition(tid, S.FAILED, reason="no JSON result block from agent")
        return "FAILED"
    store.set_notes(tid, result=result, meta=meta)
    state = str(result.get("state", "IN_REVIEW")).upper()
    for o in result.get("outputs", []) or []:
        store.add_artifact(tid, kind=o.get("kind", "artifact"), uri=o.get("uri", ""), version=str(o.get("version", "1")),
                           digest=o.get("digest", ""), producer=agent["id"])
    if state == "BLOCKED":
        store.transition(tid, S.BLOCKED, reason=str(result.get("needs", "blocked"))[:500])
        return "BLOCKED"
    if state == "FAILED":
        store.transition(tid, S.FAILED, reason=json.dumps(result.get("error", {}))[:500])
        return "FAILED"
    # gate task: record verdicts on targets
    gate = task["notes_json"].get("gate")
    if gate:
        verdicts = result.get("verdicts") or {}
        if not verdicts and result.get("verdict"):
            verdicts = {t: {"verdict": result["verdict"], "findings": result.get("findings", [])} for t in task["notes_json"]["gate_for"]}
        for target, v in verdicts.items():
            verdict = v.get("verdict", "fail")
            if verdict == "waive" and not v.get("waived_by"):
                verdict = "fail"  # self-waive is E-POLICY
            store.record_verdict(target, gate, verdict, agent["id"], v.get("findings", []))
            if verdict == "fail":
                fb = store.get(target)["notes_json"].get("feedback", [])
                fb.append({"gate": gate, "findings": v.get("findings", [])})
                store.set_notes(target, feedback=fb)
    store.transition(tid, S.IN_REVIEW, reason="agent reported IN_REVIEW")
    return "IN_REVIEW"


def reconcile(store: TaskStore, corr: str, ctx) -> list[str]:
    """Apply A01 gate/rework rules to IN_REVIEW tasks; reopen gate tasks after rework."""
    notes_log = []
    for t in store.list(correlation_id=corr, state=S.IN_REVIEW.value):
        tid = t["task_id"]
        latest = store.latest_verdicts(tid)
        failing = [g for g in store.required_gates(tid) if latest.get(g, {}).get("verdict") == "fail"]
        if failing:
            before = t["rework_loops"]
            nt = store.transition(tid, S.CHANGES_REQUESTED, reason=f"gates failed: {failing}")
            if nt["state"] == S.ESCALATED.value:
                ctx.emit("escalation.request", {"task_id": tid, "reason_code": "E-CONTRACT", "evidence": failing,
                                                "options": ["human review", "cancel", "waive gate (L3)"]})
                notes_log.append(f"{tid}: ESCALATED after {before} rework loops")
            else:
                store.transition(tid, S.IN_PROGRESS, reason="rework loop")
                notes_log.append(f"{tid}: CHANGES_REQUESTED → rework #{nt['rework_loops']} ({failing})")
                # reopen gate tasks that target this task so they re-run after rework
                for g in store.list(correlation_id=corr):
                    if tid in g["notes_json"].get("gate_for", []) and g["state"] in (S.DONE.value, S.IN_REVIEW.value, S.APPROVED.value):
                        new_id = f"{g['task_id']}.r{nt['rework_loops']}"
                        try:
                            store.get(new_id)
                        except SwarmError:
                            store.create(task_id=new_id, correlation_id=corr, capability=g["capability"], title=g["title"] + " (rerun)",
                                         agent_id=g["agent_id"], dag_depth=g["dag_depth"], depends_on=g["depends_on"],
                                         acceptance=g["acceptance"], budget=g["budget"], risk_class=g["risk_class"],
                                         priority=g["priority"], notes={k: v for k, v in g["notes_json"].items() if k not in ("result", "meta")})
                            store.transition(new_id, S.VALIDATED)
                            store.transition(new_id, S.PLANNED, reason="gate rerun")
                            # downstream of the old gate must now wait for the rerun too
                            for d in store.list(correlation_id=corr):
                                if g["task_id"] in d["depends_on"] and new_id not in d["depends_on"] and d["state"] not in (S.DONE.value,):
                                    store.update(d["task_id"], depends_on=d["depends_on"] + [new_id])
            continue
        if not store.missing_gates(tid):
            store.transition(tid, S.APPROVED, reason="all required gates pass")
            store.transition(tid, S.DONE, reason="approved")
            notes_log.append(f"{tid}: DONE")
    # rework: a task in IN_PROGRESS from rework must become ready again — handled by dispatch (state IN_PROGRESS w/ rework)
    return notes_log


def dispatchable(store: TaskStore, corr: str) -> list[dict]:
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
    return ready


def execute_one(store_path, task, agent, args, ctx, repo):
    store = TaskStore(store_path)  # sqlite: one connection per thread
    tid = task["task_id"]
    if task["state"] != S.IN_PROGRESS.value:
        store.transition(tid, S.CLAIMED, reason=f"awarded to {agent['id']}")
        store.transition(tid, S.IN_PROGRESS, reason="lease started")
    store.set_notes(tid, running=time.time())
    try:
        task = store.get(tid)
        prompt = assignment_prompt(store, task, agent, repo)
        (SWARM_DIR / "assignments").mkdir(parents=True, exist_ok=True)
        (SWARM_DIR / "assignments" / f"{tid}.a{task['attempt']}.md").write_text(prompt)
        if args.dry_run:
            text, meta = canned_result(task, agent), {"dry_run": True}
        else:
            text, meta = run_agent_headless(agent, prompt, repo, args)
        (SWARM_DIR / "results").mkdir(parents=True, exist_ok=True)
        (SWARM_DIR / "results" / f"{tid}.a{task['attempt']}.md").write_text(text or "")
        outcome = apply_result(store, task, agent, parse_result(text), meta, ctx)
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
    corr = ctx.correlation_id or latest_correlation()
    if not corr:
        raise SwarmError(ErrorCode.E_INPUT, "no plan found — run scripts/orch_plan.py first")
    ctx.correlation_id = corr
    if not args.dry_run and not shutil.which(args.claude_bin):
        raise SwarmError(ErrorCode.E_DEP, f"{args.claude_bin} not on PATH (use --dry-run to simulate)")
    if not args.dry_run:
        preflight_auth(args.claude_bin)
    repo = Path(args.repo).resolve()
    store = TaskStore()
    store_path = store.path
    log, rounds = [], 0
    while True:
        rounds += 1
        log += reconcile(store, corr, ctx)
        ready = dispatchable(store, corr)
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
    log += reconcile(store, corr, ctx)
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
    p.add_argument("--runtime", choices=["auto", "claude", "grok"], default="auto",
                   help="headless runner: claude -p --agent, grok -p --agent --yolo, or auto")
    p.add_argument("--allowed-tools", default="Bash(python3:*),Bash(git diff:*),Bash(git log:*),Bash(git status:*),Bash(ls:*),Read,Grep,Glob",
                   help="comma list passed to claude --allowedTools so headless agents can run their scripts unattended")


if __name__ == "__main__":
    sys.exit(AgentScript("A01", "swarm_run", run, description=__doc__, add_args=add_args).main())
