/**
 * agent-swarm omp extension entry (omp/package.json `omp.extensions`).
 * omp calls the factory once per session (subagents included), so it only registers:
 * no fs calls, no spawns, no `pi` action calls, no module-scope state (TOOL-01).
 */
import { type Bridge, type Inflight, killInflight, runPy } from "./bridge.ts";
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
  };
}

export default createSwarmExtension({ bridge: runPy });
