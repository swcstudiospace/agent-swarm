/**
 * agent-swarm omp extension entry (omp/package.json `omp.extensions`).
 * omp calls the factory once per session (subagents included), so it only registers:
 * no fs calls, no spawns, no `pi` action calls, no module-scope state (TOOL-01). Runtime actions such as
 * getActiveTools run only inside handlers.
 */
import { homedir } from "node:os";
import { type Bridge, type Inflight, killInflight, runPy } from "./bridge.ts";
import { callingAgent, inPlanMode, sessionAgent } from "./context.ts";
import { GUARD_ERROR_PREFIX, type GuardFacts, guardToolCall, inSwarm, ORCHESTRATOR } from "./guard.ts";
import { swarmContext } from "./hooks.ts";
import type { ExtensionFactory } from "./omp-api.ts";
import { buildTools } from "./tools.ts";

/** DI seam: tests inject a recording bridge; production uses the single python bridge. */
export function createSwarmExtension({ bridge }: { bridge: Bridge }): ExtensionFactory {
  return (pi) => {
    // WR-01: this session's children only — a sibling session's shutdown never reaches them
    const inflight: Inflight = new Set();
    const scoped: Bridge = (req) => bridge({ ...req, inflight });
    for (const tool of buildTools(scoped)) pi.registerTool(tool);
    // D-07: group-kill this session's in-flight python children when it ends (registration only, no I/O)
    pi.on("session_shutdown", () => killInflight(inflight));
    // HOOK-01 (D-09): SDLC prompts in a top-level session get the AgentSwarm context; fail-open on any error.
    pi.on("before_agent_start", (event, ctx) => {
      try {
        return swarmContext(event, ctx, process.env);
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
        const { agent: initAgent, restricted } = sessionAgent(ctx);
        known = { agent: callingAgent(ctx), topLevel: initAgent === undefined };
        // getActiveTools only for A01: the depth-cap check is the only reader (D-06)
        const hasTask = known.agent === ORCHESTRATOR ? pi.getActiveTools().includes("task") : true;
        return guardToolCall(event, { ...known, restricted, planMode: inPlanMode(ctx), hasTask, env, cwd: ctx.cwd, home: homedir() });
      } catch (err) {
        if (!inSwarm({ ...known, env })) return undefined;
        return { block: true, reason: `${GUARD_ERROR_PREFIX}${err instanceof Error ? err.message : String(err)})` };
      }
    });
  };
}

export default createSwarmExtension({ bridge: runPy });
