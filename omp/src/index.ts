/**
 * agent-swarm omp extension entry (omp/package.json `omp.extensions`).
 * omp calls the factory once per session (subagents included), so it only registers:
 * no fs calls, no spawns, no `pi` action calls, no module-scope state (TOOL-01). Runtime actions such as
 * getActiveTools run only inside handlers.
 */
import { homedir, tmpdir } from "node:os";
import { resolve } from "node:path";
import { type Bridge, type Inflight, killInflight, runPy } from "./bridge.ts";
import { swarmCommand } from "./commands.ts";
import { callingAgent, inPlanMode, sessionAgent } from "./context.ts";
import { GUARD_ERROR_PREFIX, type GuardFacts, guardToolCall, inSwarm, ORCHESTRATOR, parseSignerDenyPaths } from "./guard.ts";
import { runtimeContext, swarmContext } from "./hooks.ts";
import type { ExtensionFactory } from "./omp-api.ts";
import { clearToolActivity, createDockState, dockUi, noteToolActivity, publishTaskSpawn, settleTaskResult, stopDock, type TaskResultEvent } from "./swarm_dock.ts";
import { buildTools } from "./tools.ts";

function taskResult(event: unknown): TaskResultEvent | undefined {
  if (!event || typeof event !== "object" || !("toolName" in event) || event.toolName !== "task") return undefined;
  const toolCallId = "toolCallId" in event && typeof event.toolCallId === "string" ? event.toolCallId : undefined;
  const isError = "isError" in event && event.isError === true;
  return { toolName: "task", toolCallId, isError };
}

export function createSwarmExtension({ bridge }: { bridge: Bridge }): ExtensionFactory {
  return (pi) => {
    // WR-01: this session's children only — a sibling session's shutdown never reaches them
    const inflight: Inflight = new Set();
    // the status bar is per session, like inflight: a sibling session never paints this session's rows
    const dock = createDockState();
    const scoped: Bridge = (req) => bridge({ ...req, inflight });
    for (const tool of buildTools(scoped)) pi.registerTool(tool);
    // ORCH-03 (D-11): the same scoped bridge, so session_shutdown also sweeps the command's python child
    pi.registerCommand("swarm", swarmCommand(pi, scoped, { dock }));
    // D-07: group-kill this session's in-flight python children when it ends (registration only, no I/O)
    pi.on("session_shutdown", () => {
      killInflight(inflight);
      stopDock(dock);
    });
    // HOOK-01 (D-09): SDLC prompts in a top-level session get the AgentSwarm context; fail-open on any error.
    // OPEN-3 R1: swarm-slug sessions get the runtime part instead (the two never apply to the same session).
    pi.on("before_agent_start", (event, ctx) => {
      try {
        return swarmContext(event, ctx, process.env) ?? runtimeContext(event, ctx);
      } catch {
        return undefined;
      }
    });
    // HOOK-03/04 guard. omp turns a throwing handler into a block (D-02): an error blocks inside a swarm
    // session and returns undefined outside one, so the operator's own tools never break.
    pi.on("tool_call", (event, ctx) => {
      const env = process.env;
      let known: Pick<GuardFacts, "agent" | "topLevel"> = { agent: undefined, topLevel: false };
      try {
        const { restricted, topLevel } = sessionAgent(ctx);
        known = { agent: callingAgent(ctx), topLevel };
        // getActiveTools only for A01: the depth-cap check is the only reader (D-06)
        const hasTask = known.agent === ORCHESTRATOR ? pi.getActiveTools().includes("task") : true;
        // D-08: the runtime roots bridge.swarmRoot() picks from (env SWARM_ROOT, the package's repo), spelled without fs
        const root = env.SWARM_ROOT?.trim();
        const runtimeRoots = [...(root ? [resolve(root)] : []), resolve(import.meta.dir, "..", "..")];
        const signerReadPaths = parseSignerDenyPaths(env.SWARM_SIGNER_DENY_PATHS);
        const facts = { ...known, restricted, planMode: inPlanMode(ctx), hasTask, env, cwd: ctx.cwd, home: homedir(), tmp: tmpdir(), runtimeRoots, signerReadPaths };
        const decision = guardToolCall(event, facts);
        if (!decision?.block) {
          try {
            if (event.toolName === "task") publishTaskSpawn(dock, dockUi(ctx), event.input, event.toolCallId);
            else noteToolActivity(dock, dockUi(ctx), event.toolName);
          } catch {
            // dock is display-only; a render failure must not change the tool decision
          }
        }
        return decision;
      } catch (err) {
        if (!inSwarm({ ...known, env })) return undefined;
        return { block: true, reason: `${GUARD_ERROR_PREFIX}${err instanceof Error ? err.message : String(err)})` };
      }
    });
    // The bar paints a task spawn as running and any other tool as the current activity.
    // tool_result is the parent-side signal that the call finished; without a repaint the
    // host would keep the last settled "completed" until the next event.
    pi.on("tool_result", (event, ctx) => {
      try {
        const result = taskResult(event);
        if (result) settleTaskResult(dock, dockUi(ctx), result);
        clearToolActivity(dock, dockUi(ctx));
      } catch {
        // dock is display-only; a render failure must not change the tool result
      }
    });
  };
}

export default createSwarmExtension({ bridge: runPy });
