/**
 * `/swarm <brief> [--pattern=…] [--risk=…]` (ORCH-03, D-11): plan the brief through the session's bridge (the same
 * orch_plan argv as swarm_plan), then start an in-session A01 turn with a DISPATCH_MARKER-tagged prompt and wait
 * for it, so a `-p` run does not exit before the dispatch. The brief reaches python as one argv element built by
 * planArgs (no shell, T-04-15). Nothing is dispatched without a successful plan result.
 */
import { createHash } from "node:crypto";
import type { Bridge, BridgeResult } from "./bridge.ts";
import { inPlanMode } from "./context.ts";
import { DISPATCH_MARKER } from "./hooks.ts";
import type { CommandContext, CommandDefinition, ExtensionAPI } from "./omp-api.ts";
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

/** Ready = the plan's tasks with an empty depends_on list (the only definition; no Task Store re-read). */
function readyTaskIds(json: Record<string, unknown>): string[] {
  const tasks = Array.isArray(json.tasks) ? json.tasks : [];
  const ids: string[] = [];
  for (const t of tasks) {
    if (typeof t !== "object" || t === null) continue;
    const { task_id, depends_on } = t as { task_id?: unknown; depends_on?: unknown };
    if (typeof task_id === "string" && Array.isArray(depends_on) && depends_on.length === 0) ids.push(task_id);
  }
  return ids;
}

/** The marker-tagged prompt that makes the session call `task` once with A01 and the task.assign payload. */
function dispatchPrompt(res: BridgeResult, parsed: SwarmArgs): string {
  const { json } = res;
  const corr = typeof json.correlation_id === "string" ? json.correlation_id : "";
  const count = Array.isArray(json.tasks) ? json.tasks.length : 0;
  const assign = { correlation_id: corr, capability: "plan.execute", ready_tasks: readyTaskIds(json) };
  const summary = typeof json.summary === "string" ? json.summary : JSON.stringify(json);
  return [
    `${DISPATCH_MARKER} AgentSwarm plan ${corr} is ${json.reused === true ? "reused" : "ready"}: ${count} tasks ` +
      `(pattern ${parsed.pattern}, risk ${parsed.risk_class}).`,
    'Call the `task` tool exactly once with agent "a01-orchestrator" and this task.assign payload as the task text:',
    JSON.stringify(assign),
    "A01 executes the plan (dispatching a02–a15 by slug) and reports back; do not implement the brief in this session.",
    "",
    "Plan summary:",
    summary,
  ].join("\n");
}

function notify(ctx: CommandContext, message: string, level: "info" | "warning" | "error"): void {
  if (ctx.hasUI) ctx.ui?.notify(message, level);
}

export function swarmCommand(pi: Pick<ExtensionAPI, "sendUserMessage">, bridge: Bridge): CommandDefinition {
  return {
    description: "Plan a brief with the AgentSwarm and dispatch a01-orchestrator in this session",
    async handler(args, ctx) {
      const parsed = parseSwarmArgs(args);
      if ("error" in parsed) return notify(ctx, parsed.error, "warning");
      // D-03 parity (T-04-16): refused before any bridge call
      if (inPlanMode(ctx)) {
        return notify(ctx, "E-POLICY: /swarm refused: omp is in plan mode (read-only), so nothing was planned; leave plan mode first", "warning");
      }
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
      pi.sendUserMessage(dispatchPrompt(res, parsed));
      // T-04-17: a `-p` run exits before the A01 turn without this
      await ctx.waitForIdle();
      const corr = typeof res.json.correlation_id === "string" ? res.json.correlation_id : "?";
      notify(ctx, `/swarm: plan ${corr} dispatched to a01-orchestrator (${readyTaskIds(res.json).length} ready tasks)`, "info");
    },
  };
}
