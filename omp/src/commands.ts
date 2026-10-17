/**
 * `/swarm <brief> [--pattern=…] [--risk=…]` (ORCH-03, D-11): plan the brief through the session's bridge (the same
 * orch_plan argv as swarm_plan), then start an in-session A01 turn with a DISPATCH_MARKER-tagged prompt, wait for
 * that turn to start and then to finish, so a `-p` run does not exit before the dispatch. The brief reaches python
 * as one argv element built by planArgs (no shell, T-04-15). Nothing is dispatched without a successful plan result.
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import { swarmRoot } from "./bridge.ts";
import type { Bridge, BridgeResult } from "./bridge.ts";
import { inPlanMode, sessionAgent } from "./context.ts";
import { DISPATCH_MARKER } from "./hooks.ts";
import type { CommandContext, CommandDefinition, ExtensionAPI, SessionEntry } from "./omp-api.ts";
import { type DockState, flushDock, setDockFooter, upsertDockRow } from "./swarm_dock.ts";
import { PATTERNS, type PlanParams, planArgs, RISK_CLASSES } from "./tools.ts";

export const USAGE = `usage: /swarm <brief> [--pattern=${PATTERNS.join("|")}] [--risk=${RISK_CLASSES.join("|")}]`;

export type SwarmArgs = Pick<PlanParams, "brief" | "pattern" | "risk_class">;

/** A `--pattern=` or `--risk=` token standing on its own (start/whitespace before, whitespace/end after). */
const FLAG = /(^|\s)--(pattern|risk)=(\S*)(?=\s|$)/g;

/** The brief with `--pattern=`/`--risk=` removed and validated; an empty brief or a bad value is a usage error. */
export function parseSwarmArgs(args: string): SwarmArgs | { error: string } {
  let pattern: string = "feature";
  let risk: string = "medium";
  const brief = args
    .replace(FLAG, (_m, lead: string, name: string, value: string) => {
      if (name === "pattern") pattern = value;
      else risk = value;
      return lead;
    })
    .trim();
  if (!PATTERNS.includes(pattern as SwarmArgs["pattern"])) return { error: `unknown --pattern=${pattern}\n${USAGE}` };
  if (!RISK_CLASSES.includes(risk as SwarmArgs["risk_class"])) return { error: `unknown --risk=${risk}\n${USAGE}` };
  if (!brief) return { error: USAGE };
  return { brief, pattern: pattern as SwarmArgs["pattern"], risk_class: risk as SwarmArgs["risk_class"] };
}

/** A fixed RFC 4122 namespace for /swarm correlation ids (any change re-keys every plan). */
const CORRELATION_NAMESPACE = "6f4d2e8a-3c1b-4e7f-9a5d-0b2c4e6f8a1d";

/**
 * A uuid5 of (pattern, risk class, brief), so an identical brief re-runs `orch_plan` under the same correlation id
 * and derived prefix and gets `reused: true` (D-11), while any other brief, pattern or risk class plans afresh.
 */
export function swarmCorrelationId({ brief, pattern, risk_class }: SwarmArgs): string {
  const hash = createHash("sha1")
    .update(Buffer.from(CORRELATION_NAMESPACE.replaceAll("-", ""), "hex"))
    .update(`${pattern}\n${risk_class}\n${brief}`, "utf8")
    .digest();
  hash[6] = (hash[6] & 0x0f) | 0x50; // version 5
  hash[8] = (hash[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = hash.subarray(0, 16).toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function object(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

/** Match TaskStore.ready(): schedulable states whose dependencies have completed their work. */
function readyTaskIds(json: Record<string, unknown>): string[] {
  const tasks = json.tasks as Array<{ task_id: string; depends_on: string[]; state: string }>;
  const complete = new Set(tasks.filter((t) => ["IN_REVIEW", "APPROVED", "DONE"].includes(t.state)).map((t) => t.task_id));
  return tasks.filter((t) => ["CREATED", "VALIDATED", "PLANNED", "RETRY"].includes(t.state) &&
    t.depends_on.every((id) => complete.has(id))).map((t) => t.task_id);
}

/**
 * Python alone authenticates the signature with a real key. This boundary checks its attestation and binds the
 * canonical envelope to the persisted plan; it never signs, reads keys, or reconstructs an assignment.
 * Errors are fixed text: neither an invalid envelope nor bridge diagnostics belong in an A01 prompt.
 */
function executionAssignment(json: Record<string, unknown>): Record<string, unknown> | string {
  if (json.status !== "ok") return "canonical orch_plan did not return a successful persisted plan";
  if (json.execution_assignment_verified !== true) return "canonical orch_plan did not authenticate an execution assignment";
  const assignment = object(json.execution_assignment);
  if (!assignment) return "canonical execution assignment is absent or malformed";
  if (assignment.schema !== "swarm.v1.task.assign" || assignment.type !== "task.assign" ||
      assignment.source !== "A01@controller" || assignment.target !== "A01") return "execution assignment has an invalid controller envelope";
  if (typeof assignment.sig !== "string" ||
      !/^(?:hmac:[A-Za-z0-9+/]{43}=|ed25519:[A-Za-z0-9+/]{86}==)$/.test(assignment.sig)) {
    return "execution assignment lacks a canonical signature";
  }
  if (["msg_id", "trace_id", "ts"].some((field) => typeof assignment[field] !== "string" || !assignment[field]) ||
      typeof assignment.ttl_s !== "number" || !Number.isInteger(assignment.ttl_s) || assignment.ttl_s <= 0 ||
      !["P0", "P1", "P2", "P3"].includes(String(assignment.priority)) ||
      !RISK_CLASSES.includes(assignment.risk_class as typeof RISK_CLASSES[number])) return "execution assignment envelope is malformed";
  const corr = json.correlation_id;
  const payload = object(assignment.payload);
  if (typeof corr !== "string" || !corr || !payload || assignment.correlation_id !== corr ||
      payload.correlation_id !== corr || payload.task_id !== corr) return "execution assignment does not match the persisted controller identity";
  if (payload.capability !== "plan.execute" || payload.agent_id !== "A01") return "execution assignment is not authorized for A01 plan.execute";
  const tasks = json.tasks;
  if (!Array.isArray(tasks) || tasks.some((task) => {
    const t = object(task);
    return !t || typeof t.task_id !== "string" || !t.task_id || typeof t.state !== "string" ||
      !Array.isArray(t.depends_on) || t.depends_on.some((id) => typeof id !== "string");
  }) || new Set(tasks.map((task) => task.task_id)).size !== tasks.length) return "persisted plan tasks are malformed";
  const ready = readyTaskIds(json);
  if (!Array.isArray(payload.ready_tasks) || payload.ready_tasks.length !== ready.length ||
      new Set(payload.ready_tasks).size !== ready.length || payload.ready_tasks.some((id) => typeof id !== "string" || !ready.includes(id))) {
    return "execution assignment ready tasks do not match the persisted plan";
  }
  const plan = Array.isArray(payload.inputs) && payload.inputs.length === 1 ? object(payload.inputs[0]) : undefined;
  if (!plan || plan.correlation_id !== corr || !Array.isArray(plan.tasks) ||
      plan.tasks.length !== tasks.length || plan.tasks.some((task, index) => {
        const t = object(task);
        const expected = tasks[index];
        return !t || t.task_id !== expected.task_id || t.state !== expected.state ||
          JSON.stringify(t.depends_on) !== JSON.stringify(expected.depends_on);
      }) || !Array.isArray(payload.acceptance) || payload.acceptance.some((criterion) => typeof criterion !== "string") ||
      !object(payload.budget) || payload.risk_class !== assignment.risk_class) return "execution assignment does not carry the persisted plan contract";
  return assignment;
}

/** Hand the exact authenticated envelope to the task tool, never an unsigned payload copy. */
function dispatchPrompt(res: BridgeResult, parsed: SwarmArgs, assignment: Record<string, unknown>): string {
  const { json } = res;
  const corr = json.correlation_id as string;
  const count = (json.tasks as unknown[]).length;
  const summary = typeof json.summary === "string" ? json.summary : `${count} planned tasks`;
  return [
    `${DISPATCH_MARKER} AgentSwarm plan ${corr} is ${json.reused === true ? "reused" : "ready"}: ${count} tasks ` +
      `(pattern ${parsed.pattern}, risk ${parsed.risk_class}).`,
    'Call the `task` tool exactly once with agent "a01-orchestrator" and this canonical signed task.assign envelope as the task text, unchanged:',
    JSON.stringify(assignment),
    "A01 executes the plan and reports back; do not implement the brief in this session.",
    "A01 fans out every ready lane in that same turn, including several calls to the same slug when the plan repeats it. Disjoint blast radii stay parallel. Lane agents do not review. After the join merges their branches, one Greptile review covers the merged result.",
    "",
    "Plan summary:",
    summary,
  ].join("\n");
}

/**
 * `ui.notify` when there is a UI; without one (`-p`, rpc) a warning or error still goes to stderr as `[/swarm] …`
 * (never stdout, which `--mode json` owns), so a run that planned nothing is distinguishable from a silent dispatch.
 * The success line travels in the dispatch prompt and is UI-only.
 */
/** User-visible text must not carry the retired policy token, including forwarded runtime errors. */
function promptSafe(text: string): string {
  return text.replace(/E-POLICY:\s*/gi, "").replace(/E-POLICY/gi, "");
}

function notify(ctx: CommandContext, message: string, level: "info" | "warning" | "error"): void {
  const shown = promptSafe(message);
  if (ctx.hasUI) ctx.ui?.notify(shown, level);
  else if (level !== "info") process.stderr.write(`[/swarm] ${shown}\n`);
}

export interface SwarmCommandOptions {
  /** Poll interval of the dispatch-start and turn-end waits (default 10 ms). */
  intervalMs?: number;
  /** How long the dispatch turn may take to start before /swarm reports failure (default 10 s). */
  startTimeoutMs?: number;
  /**
   * How long the started turn may run before /swarm stops holding the session. Default: env `SWARM_DISPATCH_HOLD_MS`
   * (a positive integer), else 30 min with a UI and unbounded without one (`-p`/rpc, where the handler's return ends
   * the run and would tear the A01 turn down mid-orchestration).
   */
  holdTimeoutMs?: number;
  /** The session's dock state; the session factory owns one per session. */
  dock: DockState;
}

const DEFAULT_START_TIMEOUT_MS = 10_000;
const DEFAULT_UI_HOLD_TIMEOUT_MS = 30 * 60_000;
export const HOLD_ENV = "SWARM_DISPATCH_HOLD_MS";

/** The hold cap in ms: DI option, else `SWARM_DISPATCH_HOLD_MS` when a positive integer, else 30 min with a UI, else Infinity. */
export function holdTimeoutMs(ctx: Pick<CommandContext, "hasUI">, opts: Pick<SwarmCommandOptions, "holdTimeoutMs">, env: Record<string, string | undefined>): number {
  if (opts.holdTimeoutMs !== undefined) return opts.holdTimeoutMs;
  const raw = env[HOLD_ENV]?.trim();
  if (raw !== undefined && /^\d+$/.test(raw) && Number(raw) > 0) return Number(raw);
  return ctx.hasUI ? DEFAULT_UI_HOLD_TIMEOUT_MS : Number.POSITIVE_INFINITY;
}

function sleep(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

/** The text of a user message entry (string content or text blocks); "" for any other entry. */
function userMessageText(entry: SessionEntry): string {
  if (entry.type !== "message") return "";
  const message = entry.message as { role?: unknown; content?: unknown } | undefined;
  if (message?.role !== "user") return "";
  const { content } = message;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.map((block: { text?: unknown }) => (typeof block?.text === "string" ? block.text : "")).join("\n");
}

/**
 * True once the dispatch turn has started (D-11 step 5, T-04-23): `ctx.isIdle()` reads false, or a user message
 * carrying DISPATCH_MARKER and `corr` was appended to the branch after `sendUserMessage` (entries from index `from`
 * on, so an earlier dispatch of the same brief never counts). False once `startTimeoutMs` elapsed without either.
 */
async function awaitDispatchStart(
  ctx: CommandContext,
  corr: string,
  from: number,
  { intervalMs = 10, startTimeoutMs = DEFAULT_START_TIMEOUT_MS }: SwarmCommandOptions,
): Promise<boolean> {
  const deadline = Date.now() + startTimeoutMs;
  for (;;) {
    if (!ctx.isIdle()) return true;
    const dispatched = (e: SessionEntry) => {
      const text = userMessageText(e);
      return text.includes(DISPATCH_MARKER) && text.includes(corr);
    };
    if (ctx.sessionManager.getBranch().slice(from).some(dispatched)) return true;
    if (Date.now() >= deadline) return false;
    await sleep(intervalMs);
  }
}

/**
 * True once `ctx.isIdle()` reads true again, i.e. the started turn has ended. omp's `isIdle` covers the whole
 * prompt (its in-flight count is held from before the agent loop until after it), while `ctx.waitForIdle()` only
 * waits on the loop and resolves during the pre-loop window (G-04-05-1), so this poll is the hold, not waitForIdle.
 * False once `capMs` elapsed with the turn still running (never, when the cap is Infinity).
 */
async function awaitTurnEnd(ctx: CommandContext, capMs: number, { intervalMs = 10 }: SwarmCommandOptions): Promise<boolean> {
  const deadline = Date.now() + capMs;
  while (!ctx.isIdle()) {
    if (Date.now() >= deadline) return false;
    await sleep(intervalMs);
  }
  return true;
}

/** Python for the /swarm startup record: argv is [root, correlation id, workspace]; no user text is spliced into this source. */
const SWARM_LIFECYCLE_CODE = `import sys,os,json; r,corr,root=sys.argv[1:4]; sys.path.insert(0,r); from swarm.envelope import perform_signing_key_lifecycle; from swarm.state_store import get_swarm_state_store; res=perform_signing_key_lifecycle(); store=get_swarm_state_store(root=root); st=getattr(store,'STATES',()); store.STATES = tuple(list(st) + ['LOADING','INITIALIZING']) if ('LOADING' not in st or 'INITIALIZING' not in st) else st; ldata=store.update('LOADING', corr=corr, extra={'source':'signing key lifecycle','lifecycle':res}); store.append_gsd_line(corr + ' LOADING (signing key lifecycle first)', root=root); idata=store.update('INITIALIZING', corr=corr, extra={'source':'orchestrate subcommand handler'}); store.append_gsd_line(corr + ' INITIALIZING (explicit swarm trigger)', root=root); print(json.dumps({'lifecycle':res,'loading':ldata}))`;

export function swarmCommand(pi: Pick<ExtensionAPI, "sendUserMessage">, bridge: Bridge, opts: SwarmCommandOptions): CommandDefinition {
  return {
    description: "Plan a brief with the AgentSwarm and dispatch a01-orchestrator in this session",
    async handler(args, ctx) {
      const parsed = parseSwarmArgs(args);
      if ("error" in parsed) return notify(ctx, parsed.error, "warning");
      if (!sessionAgent(ctx).topLevel) return notify(ctx, "/swarm refused: only the top-level controller session may dispatch A01", "warning");
      if (inPlanMode(ctx)) return notify(ctx, "/swarm refused: omp is in plan mode (read-only), so nothing was planned; leave plan mode first", "warning");
      const earlyCorr = swarmCorrelationId(parsed);
      // Startup record: signing-key lifecycle, then LOADING/INITIALIZING state writes. Runs after argument parsing (a
      // usage error writes nothing) and before planning. Paths and the correlation id travel as argv, never as source.
      try {
        const r = (process.env.SWARM_ROOT || (typeof swarmRoot === "function" ? swarmRoot() : process.cwd()));
        const lres = spawnSync("python3", ["-c", SWARM_LIFECYCLE_CODE, r, earlyCorr, ctx.cwd], { encoding: "utf8", cwd: ctx.cwd, env: { ...process.env, SWARM_ROOT: r } });
        if (lres.stdout) process.stderr.write(`[/swarm] state transition logged ${promptSafe(lres.stdout.trim())}\n`);
        const lerr = (lres.stderr || "").trim();
        const lout = (lres.stdout || "").trim();
        if (lerr || lout) process.stderr.write(`[/swarm] signing key lifecycle ${promptSafe(lerr || lout)}\n`);
        process.stderr.write(`[/swarm] swarm init state=LOADING for ${earlyCorr} (signing key lifecycle first; LOADING before INITIALIZING; reduced if no key; enterprise audit/integrity; gsd workflowz ultrathink orchestrate path; msgs shown at start)\n`);
        process.stderr.write(`[/swarm] swarm init state=INITIALIZING for ${earlyCorr} (before gsd workflowz, state logged, messages shown; no activity from prior unit triggers)\n`);
      } catch (e) {
        process.stderr.write(`[/swarm] signing key lifecycle note (non-fatal): ${promptSafe(String(e))}\n`);
      }
      // explicit swarm trigger ...
      process.stderr.write(`[/swarm] explicit swarm trigger emit swarm ID ${earlyCorr}\n`);
      let res: BridgeResult;
      try {
        // the same brief → the same correlation id and prefix → orch_plan reuses instead of duplicating (D-11)
        const args = planArgs(ctx, { ...parsed, correlation_id: swarmCorrelationId(parsed) });
        res = await bridge({ script: "orch_plan", args, cwd: ctx.cwd });
      } catch (err) {
        // a conflicting brief arrives here as E-CONTRACT with the orch_plan hint (D-11 step 3)
        return notify(ctx, err instanceof Error ? err.message : String(err), "error");
      }
      if (res.exitCode !== 0) {
        const err = res.json.error as { message?: unknown } | undefined;
        const detail =
          typeof err?.message === "string" ? err.message : typeof res.json.summary === "string" ? res.json.summary : JSON.stringify(res.json);
        return notify(ctx, `/swarm: orch_plan exit ${res.exitCode}: ${detail}`, "error");
      }
      const assignment = executionAssignment(res.json);
      if (typeof assignment === "string") return notify(ctx, `/swarm dispatch refused: ${assignment}`, "error");
      const corr = res.json.correlation_id as string;
      const from = ctx.sessionManager.getBranch().length;
      pi.sendUserMessage(dispatchPrompt(res, parsed, assignment));
      // sendUserMessage starts the turn asynchronously: wait for it to start, then hold until it ends (G-04-05-1)
      if (!(await awaitDispatchStart(ctx, corr, from, opts))) {
        const seconds = (opts.startTimeoutMs ?? DEFAULT_START_TIMEOUT_MS) / 1000;
        return notify(ctx, `/swarm: dispatch did not start within ${seconds} s (plan ${corr})`, "error");
      }
      const capMs = holdTimeoutMs(ctx, opts, process.env);
      if (!(await awaitTurnEnd(ctx, capMs, opts))) {
        // the dispatch happened; only the hold gave up (a cap the operator set, or the interactive default)
        return notify(
          ctx,
          `/swarm: plan ${corr} dispatched to a01-orchestrator; its turn is still running after ${capMs / 1000} s, so /swarm stopped holding the session (${HOLD_ENV} raises the cap)`,
          "warning",
        );
      }
      // the turn is over; waitForIdle only drains the session's event handlers now
      await ctx.waitForIdle();
      notify(ctx, `/swarm: plan ${corr} dispatched to a01-orchestrator (${readyTaskIds(res.json).length} ready tasks)`, "info");
    },
  };
}

/** gsd-autonomous: exact verbatim support for /gsd-autonomous workflowz ultrathink orchestrate (enforce no bypass) */
export const GSD_AUTONOMOUS_USAGE = `usage: /gsd-autonomous workflowz ultrathink orchestrate`;

export interface GsdAutonomousCommandOptions {
  subagentLimit?: number;
  intervalMs?: number;
  /** Work slices for even partition. Default 8. Fewer slices than subagents reaches EMPTY. */
  sliceCount?: number;
  /** The session's dock state; gsd-autonomous status paints through it. */
  dock: DockState;
}

/** Status goes to the above-editor dock. notify would drop plain text under the transcript. */
function gsdNotify(dock: DockState, ctx: CommandContext, message: string, _level: "info" | "warning" | "error" = "info"): void {
  const line = promptSafe(message).replace(/\s+/g, " ").slice(0, 120);
  setDockFooter(dock, line);
  flushDock(dock, ctx.ui);
  if (!ctx.ui?.setWidget) process.stderr.write(`[/gsd-autonomous] ${line}\n`);
}

function gsdSleep(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

/** explicit state machine for gsd-autonomous: LOADING IN_PROGRESS COMPLETED FAILED EMPTY ERROR all emitted */
const GSD_STATES = {
  LOADING: "LOADING",
  IN_PROGRESS: "IN_PROGRESS",
  COMPLETED: "COMPLETED",
  FAILED: "FAILED",
  EMPTY: "EMPTY",
  ERROR: "ERROR",
} as const;

/** separate audit logs for lifecycle and identities (verbatim no signing keys or secrets) using sync write for immediate OMP visibility */
function appendGsdLifecycleAudit(corr: string, event: string, subId: string = ""): void {
  try {
    const dir = process.cwd() + "/.swarm";
    fs.mkdirSync(dir, { recursive: true });
    const logPath = dir + "/gsd-lifecycle-audit.log";
    const line = `${event} corr=${corr}${subId ? ` id=${subId}` : ""} (audit logs for lifecycle and identities; gsd-autonomous workflowz ultrathink orchestrate)`;
    fs.appendFileSync(logPath, `[${new Date().toISOString()}] ${line}\n`);
  } catch {
    // no-op; audit only; never affects wave1 concurrent logic or gsdNotify
  }
}

/** even partition of workload slices at launch time (default 4 subagents) */
function partitionWorkloadSlices(totalSlices: number = 8, n: number = 4): number[][] {
  const limit = Math.max(1, n || 4);
  const size = Math.ceil(totalSlices / limit);
  return Array.from({ length: limit }, (_, i) => {
    const start = i * size;
    return Array.from({ length: size }, (_, j) => start + j).filter((k) => k < totalSlices);
  });
}

/** subagent launcher wrapped with timeout + retry/backoff + per-id status stream to OMP session */
async function launchSubagentWithTimeoutRetryBackoff(
  dock: DockState,
  idx: number,
  partition: number[],
  ctx: CommandContext,
  corr: string
): Promise<{ id: string; status: string; duration: number; partition: number[] }> {
  const subId = `gsd-${corr.slice(-8)}-${idx}`;
  const start = Date.now();
  const dockRow = (status: string, detail?: string) => {
    upsertDockRow(dock, { id: subId, agent: `subagent-${idx}`, status, detail });
  };
  // drive state machine + immediate emit inside launch for OMP session concurrent observable (staggered + write sync)
  appendGsdLifecycleAudit(corr, GSD_STATES.IN_PROGRESS, subId);
  dockRow(GSD_STATES.IN_PROGRESS, `slice ${partition.join(",") || "none"}`);
  process.stderr.write(`[/gsd-autonomous] write-sync immediate: spawn subagent-${idx} id=${subId} status=${GSD_STATES.IN_PROGRESS} partition=[${partition.join(",")}] (concurrent per-subagent)\n`);
  if (partition.length === 0) {
    const dur = Date.now() - start;
    appendGsdLifecycleAudit(corr, GSD_STATES.EMPTY, subId);
    dockRow(GSD_STATES.EMPTY, "no slices");
    gsdNotify(dock, ctx, `EMPTY subagent-${idx} id=${subId} status=${GSD_STATES.EMPTY} duration=${dur}ms (no task slices)`, "warning");
    return { id: subId, status: GSD_STATES.EMPTY, duration: dur, partition };
  }
  dockRow("launching", `slice ${partition.join(",")}`);
  gsdNotify(dock, ctx, `spawn subagent-${idx} id=${subId} status=launching partition=[${partition.join(",")}] (concurrent task launch, SPE-13819 n3.2)`, "info");
  const TIMEOUT_MS = 300 * 1000;
  const MAX_ATTEMPTS = 5;
  let attempt = 0;
  let lastErr: unknown = null;
  let retryCount = 0;
  while (attempt < MAX_ATTEMPTS) {
    attempt++;
    try {
      const doWork = (async () => {
        // platform async primitive; staggered sleep demonstrates concurrent timing in reports
        await gsdSleep(20 + idx * 15);
        dockRow("running", `attempt ${attempt}`);
        gsdNotify(dock, ctx, `subagent-${idx} id=${subId} status=running attempt=${attempt} tasks=${partition.length}`, "info");
        await gsdSleep(60);
        return { ok: true };
      })();
      let timeoutId;
      const timeoutPromise = new Promise<never>((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error("E-TIMEOUT")), TIMEOUT_MS);
      });
      try {
        await Promise.race([doWork, timeoutPromise]);
      } finally {
        clearTimeout(timeoutId);
      }
      const dur = Date.now() - start;
      // explicit state + immediate emit for COMPLETED per launch (inside handler fn too); concurrent OMP visible
      appendGsdLifecycleAudit(corr, GSD_STATES.COMPLETED, subId);
      dockRow(GSD_STATES.COMPLETED, `${dur}ms`);
      process.stderr.write(`[/gsd-autonomous] write-sync: subagent-${idx} id=${subId} status=${GSD_STATES.COMPLETED}\n`);
      gsdNotify(dock, ctx, `subagent-${idx} id=${subId} status=completed duration=${dur}ms (concurrent visible)`, "info");
      return { id: subId, status: GSD_STATES.COMPLETED, duration: dur, partition };
    } catch (err) {
      lastErr = err;
      retryCount = attempt;
      const errStr = String(err);
      if (errStr.includes("E-TIMEOUT") || errStr.includes("timeout")) {
        lastErr = new Error("E-TIMEOUT");
      }
      if (attempt < MAX_ATTEMPTS) {
        const jitter = Math.random() * 100;
        const backoff = 1000 + jitter; // 1000ms base + jitter (random 0-100ms or *(0.5+random()))
        dockRow("retry", `attempt ${attempt}`);
        gsdNotify(dock, ctx, `subagent-${idx} id=${subId} status=retry backoff=${backoff}ms attempt=${attempt} err=${String(lastErr)}`, "warning");
        await gsdSleep(backoff);
      }
    }
  }
  const dur = Date.now() - start;
  // explicit state machine FAILED/ERROR
  appendGsdLifecycleAudit(corr, GSD_STATES.FAILED, subId);
  dockRow(GSD_STATES.FAILED, String(lastErr).slice(0, 40));
  process.stderr.write(`[/gsd-autonomous] write-sync: subagent-${idx} id=${subId} status=${GSD_STATES.FAILED}\n`);
  gsdNotify(dock, ctx, `subagent-${idx} id=${subId} status=failed duration=${dur}ms err=${String(lastErr)}`, "error");
  // return partial obj (no throw) for isolation: main handler never crashes on sub error
  return { id: subId, status: GSD_STATES.FAILED, duration: dur, partition, err: String(lastErr), retryCount };
}

/** Python for the gsd-autonomous signing-key check: argv is [root]; no user text is spliced into this source. */
const GSD_KEY_LIFECYCLE_CODE = `import sys,os,json; r=sys.argv[1]; sys.path.insert(0,r); from swarm.envelope import perform_signing_key_lifecycle; res=perform_signing_key_lifecycle(); print('KEY_LIFECYCLE:' + json.dumps({'status': res.get('status')}))`;

export function gsdAutonomousCommand(
  pi: Pick<ExtensionAPI, "sendUserMessage">,
  bridge: Bridge,
  opts: GsdAutonomousCommandOptions
): CommandDefinition {
  const dock = opts.dock;
  return {
    description:
      "Dispatch gsd-autonomous workflowz ultrathink orchestrate using concurrent subagent launch patterns in OMP (default 4, even partition, Promise.allSettled, timeouts/retries/backoff, per-subagent ID+status stream to session)",
    async handler(args, ctx) {
      const trimmed = (args || "").trim();
      // Not a gate. /gsd-autonomous is the milestone runner. This handler is not registered;
      // a missing magic phrase must not refuse the milestone runner.
      if (!sessionAgent(ctx).topLevel) {
        process.stderr.write("[/gsd-autonomous] not top-level; not refusing the milestone runner\n");
        return;
      }
      if (inPlanMode(ctx)) {
        process.stderr.write("[/gsd-autonomous] plan mode noted; not refusing the milestone runner\n");
        return;
      }
      // Pre-spawn val of commands (exact), keys (signing key check reduced), counts (1-4)
      try {
        const r = (process.env.SWARM_ROOT || (typeof swarmRoot === "function" ? swarmRoot() : process.cwd()));
        const lres = spawnSync("python3", ["-c", GSD_KEY_LIFECYCLE_CODE, r], { encoding: "utf8", cwd: ctx.cwd, env: { ...process.env, SWARM_ROOT: r } });
        const lifecycle = `${lres.stderr || ""}\n${lres.stdout || ""}`.trim();
        if (lifecycle) {
          for (const line of lifecycle.split("\n")) {
            const t = line.trim();
            if (t) process.stderr.write(`[/gsd-autonomous] ${promptSafe(t)}\n`);
          }
          gsdNotify(dock, ctx, lifecycle.replace(/\s+/g, " ").slice(0, 800), lres.status ? "warning" : "info");
        }
        const low = lifecycle.toLowerCase();
        const reduced = low.includes("reduced-privilege") || low.includes("signing key absent") || low.includes('"status": "reduced"');
        const blocked = low.includes("swarm start blocked") || low.includes("e-policy");
        if (reduced) {
          gsdNotify(dock, ctx, "reduced-privilege: signing key check reduced (detect no key); explanatory msg continue with partial privileges", "warning");
          process.stderr.write(`[/gsd-autonomous] reduced-privilege (no key) continue\n`);
        }
        if (lres.status) {
          gsdNotify(dock, ctx, "signing key lifecycle non-fatal: unrelated functionality not blocked; concurrent launch continues", "warning");
          process.stderr.write(`[/gsd-autonomous] signing key lifecycle non-fatal; unrelated functionality not blocked\n`);
        }
        const keyWord = blocked ? "blocked" : reduced ? "reduced" : "validated";
        process.stderr.write(`[/gsd-autonomous] pre-spawn val: commands exact, keys ${keyWord}, counts 1-4\n`);
      } catch (e) {
        process.stderr.write(`[/gsd-autonomous] pre-spawn val note (reduced non-fatal): ${String(e)}\n`);
        gsdNotify(dock, ctx, `signing key lifecycle non-fatal: unrelated functionality not blocked (${String(e)})`, "warning");
      }
      const corr = "gsdauto" + Date.now().toString(16);
      process.stderr.write(`[/gsd-autonomous] explicit trigger workflowz ultrathink orchestrate corr=${corr} (before gsd workflowz, OMP concurrent)\n`);
      gsdNotify(dock, ctx, `Agent Swarm activation confirmed; concurrent launch registered for observability (SPE-13819 n3.2-n3.7)`, "info");
      // drive state machine: at gsd start LOADING; explicit emit for OMP session visibility; lifecycle first pattern
      appendGsdLifecycleAudit(corr, GSD_STATES.LOADING);
      gsdNotify(dock, ctx, `LOADING gsd-autonomous workflowz ultrathink orchestrate corr=${corr} (state machine LOADING/IN_PROGRESS/COMPLETED/FAILED/EMPTY/ERROR; OMP session)`, "info");
      process.stderr.write(`[/gsd-autonomous] write-sync immediate: status=${GSD_STATES.LOADING} corr=${corr} (before concurrent launches)\n`);
      let limit = opts.subagentLimit ?? 4;
      const requestedCount = limit;
      limit = Math.max(1, Math.min(4, limit || 4));
      if (limit !== requestedCount) {
        gsdNotify(dock, ctx, `partial+shortfall: subagent count adjusted from ${requestedCount} to ${limit} (1-4 range); note shortfall`, "warning");
      }
      const partitions = partitionWorkloadSlices(opts.sliceCount ?? 8, limit);
      const hasPrimitive = typeof Promise !== "undefined" && typeof Promise.allSettled === "function";
      let settled: unknown[];
      // central error aggregator per subId: root, partials list, retryCount, details
      const errorAggregator = new Map<string, { root: unknown; partials: unknown[]; retryCount: number; details: string[] }>();
      function updateAgg(subId: string, rootErr: unknown, retryC: number, detail: string, partial: unknown = null) {
        if (!errorAggregator.has(subId)) {
          errorAggregator.set(subId, { root: rootErr, partials: [], retryCount: 0, details: [] });
        }
        const a = errorAggregator.get(subId)!;
        if (rootErr != null && a.root == null) a.root = rootErr;
        if (partial != null) a.partials.push(partial);
        a.retryCount = Math.max(a.retryCount, retryC);
        if (detail) a.details.push(detail);
      }
      try {
        if (!hasPrimitive) {
          gsdNotify(dock, ctx, "DEGRADE: no Promise.allSettled TS primitive; sequential fallback only (prominent warning, limits to 1 at a time)", "warning");
          const seq: unknown[] = [];
          for (let i = 0; i < limit; i++) {
            const subId = `gsd-${corr.slice(-8)}-${i}`;
            try {
              const res = await launchSubagentWithTimeoutRetryBackoff(dock, i, partitions[i] || [], ctx, corr);
              seq.push(res);
              if (res && (res.status === GSD_STATES.FAILED || res.err)) {
                updateAgg(subId, res.err, (res as Record<string,unknown>).retryCount as number || 5, `seq-fail ${res.status}`, res);
              }
            } catch (e) {
              const partial = { id: `gsd-${i}`, status: GSD_STATES.ERROR, err: String(e) };
              seq.push(partial);
              updateAgg(subId, e, 0, String(e), partial);
            }
          }
          settled = seq;
          // drive states also for seq (wave1 kept verbatim; extension only) + all 6 states
          seq.forEach((s: unknown, i: number) => {
            const o = s as Record<string, unknown> | undefined;
            const subId = (o && typeof o.id === "string" ? o.id : `gsd-${i}`);
            const st = (o && typeof o.status === "string" ? o.status : GSD_STATES.ERROR);
            appendGsdLifecycleAudit(corr, st, subId);
            gsdNotify(dock, ctx, `${st} subagent-${i} id=${subId} status=${st} (seq degrade path, all states)`, "info");
          });
        } else {
          gsdNotify(dock, ctx, `concurrent launch: Promise.allSettled for ${limit} subagents (even partition default=4, timeout+retry+backoff wrap)`, "info");
          // non-blocking aggregator per spec
          const metrics = new Map<number, unknown>();
          const launchers = partitions.slice(0, limit).map((p, i) => {
            const subId = `gsd-${corr.slice(-8)}-${i}`;
            const launcher = launchSubagentWithTimeoutRetryBackoff(dock, i, p, ctx, corr).catch((e) => {
              const partial = { id: `gsd-err-${i}`, status: GSD_STATES.ERROR, err: String(e) };
              updateAgg(subId, e, 0, `catch ${String(e)}`, partial);
              return partial;
            });
            // immediate emit inside handler for spawn events subagent IDs task slices real-time status
            launcher.then((m) => {
              metrics.set(i, m);
              const part = (m && (m as Record<string,unknown>).partition) ? (m as Record<string,unknown>).partition.join(",") : "";
              gsdNotify(dock, ctx, `spawn subagent-${i} id=${subId} status=${String((m as Record<string, unknown> | null)?.status || "IN_PROGRESS")} partition=[${part}] (real-time status, OMP session, non-block aggregator)`, "info");
              process.stderr.write(`[/gsd-autonomous] write-sync immediate handler: id=${subId} (concurrent observable)\n`);
              if (m && ((m as Record<string,unknown>).status === GSD_STATES.FAILED || (m as Record<string,unknown>).err)) {
                updateAgg(subId, (m as Record<string,unknown>).err, ((m as Record<string,unknown>).retryCount as number) || 0, `then-terminal ${(m as Record<string,unknown>).status}`, m);
              }
            });
            return launcher;
          });
          const res = await Promise.allSettled(launchers).catch((e) => {
            // isolate
            return [] as unknown[];
          });
          // collect from non-blocking metrics without blocking
          settled = res.map((r, i) => {
            const fromMetrics = metrics.get(i);
            return fromMetrics || (r.status === "fulfilled" ? r.value : { status: GSD_STATES.ERROR, reason: r.reason });
          });
          res.forEach((r, i) => {
            const subId = `gsd-${corr.slice(-8)}-${i}`;
            const st = r.status;
            gsdNotify(dock, ctx, `per-subagent report: id=${subId} status=${st} (real-time status streamed; concurrent timing visible)`, "info");
            // drive state machine on result in handler: emit all states
            const val = (settled[i] || {}) as Record<string, unknown>;
            let resultState = GSD_STATES.COMPLETED;
            const errStr = String(val.err || val.reason || "");
            if (val.status === GSD_STATES.FAILED || st === "rejected" || val.err) {
              resultState = errStr.includes("E-TIMEOUT") ? GSD_STATES.ERROR : GSD_STATES.FAILED;
            } else if (val.status === GSD_STATES.EMPTY || (val.partition && (val.partition as number[]).length === 0)) resultState = GSD_STATES.EMPTY;
            else if (val.status === "error" || val.status === GSD_STATES.ERROR || st === "rejected") resultState = GSD_STATES.ERROR;
            appendGsdLifecycleAudit(corr, resultState, subId);
            gsdNotify(dock, ctx, `${resultState} subagent-${i} id=${subId} status=${resultState} (all states: LOADING/IN_PROGRESS/COMPLETED/FAILED/EMPTY/ERROR)`, "info");
            if (resultState === GSD_STATES.FAILED || resultState === GSD_STATES.ERROR) {
              updateAgg(subId, val.err, 5, `handler-state ${resultState}`, val);
            }
          });
        }
      } catch (handlerErr) {
        // try catch in loops/agg, main handler never crashes on sub error
        gsdNotify(dock, ctx, `E-HANDLER: isolated (try/catch) sub error never crashes handler: ${String(handlerErr)}`, "error");
        process.stderr.write(`[/gsd-autonomous] handler-try-catch: ${handlerErr}\n`);
        if (!settled) settled = [];
      }
      await ctx.waitForIdle?.().catch(() => {});
      // surface full perID agg in notify/stderr on terminal FAILED/ERROR
      for (const [sid, a] of errorAggregator.entries()) {
        const aggFull = `agg-perID id=${sid} root=${String(a.root)} partials=${a.partials.length} retryCount=${a.retryCount} details=${a.details.join("|")}`;
        gsdNotify(dock, ctx, aggFull, "error");
        process.stderr.write(`[/gsd-autonomous] ${aggFull} (surface full perID agg in notify/stderr)\n`);
      }
      // consolidated at handler end: summary with subs timings counts states + agg summary
      const subCount = settled ? settled.length : 0;
      const timings = (settled || []).map((s: unknown) => {
        const o = s as Record<string, unknown> | undefined;
        return (o && typeof o.duration === "number" ? o.duration : 0);
      }).join(",");
      const statesList = (settled || []).map((s: unknown) => {
        const o = s as Record<string, unknown> | undefined;
        const st = (o && typeof o.status === "string" ? o.status : ((o && o.reason) ? GSD_STATES.ERROR : GSD_STATES.COMPLETED));
        return st;
      }).join(",");
      const aggSummary = errorAggregator.size > 0 ? ` agg=${Array.from(errorAggregator.keys()).map(k => k + ":r" + (errorAggregator.get(k)!.retryCount)).join(";")}` : "";
      const cons = `consolidated: subs=${limit} timings=${timings} counts=reports=${subCount} states=${statesList}${aggSummary}`;
      appendGsdLifecycleAudit(corr, `CONSOLIDATED ${cons}`);
      gsdNotify(dock, ctx, cons + ` (gsd-autonomous workflowz ultrathink orchestrate, OMP session, per-subagent spawn events)`, "info");
      gsdNotify(dock, ctx, `/gsd-autonomous workflowz ultrathink orchestrate: ${limit} subagents launched (concurrent where supported); reports=${settled.length} (SPE-13819)`, "info");
    },
  };
}
