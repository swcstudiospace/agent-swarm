/**
 * The `tool_call` guard decision (Phase 4 HOOK-03/04): a pure function of (event, facts) with no I/O, no bridge,
 * no fs and no process access — index.ts reads the facts (identity, plan mode, active tools, env) and passes them in.
 * Precedence, first match decides: HOOK-04 (A01 depth cap) → HOOK-03 (swarm-state tools) → outside a swarm
 * session nothing else applies. Reason strings are read by the A01 dispatcher (D-07): keep them stable.
 */
import type { ToolCallEvent, ToolCallResult } from "./omp-api.ts";

export const ORCHESTRATOR = "a01-orchestrator";

/** The 15 swarm agents (copied from agents.json agents[].slug; guard.test.ts pins the list against agents.json). */
export const SWARM_SLUGS: readonly string[] = [
  "a01-orchestrator", "a02-requirements", "a03-architect", "a04-ux-designer", "a05-backend",
  "a06-frontend", "a07-data", "a08-qa", "a09-reviewer", "a10-security",
  "a11-devops", "a12-release", "a13-observability", "a14-maintenance", "a15-docs",
];

/** Tools that move Task Store state: A01 only, for every caller including the unidentified main session (D-05). */
const SWARM_STATE_TOOLS: Record<string, true> = { swarm_transition: true, swarm_ingest: true };

export const DEPTH_REASON =
  `BLOCKED needs: depth — ${ORCHESTRATOR} is at the task depth cap (no \`task\` tool), so it cannot dispatch. ` +
  `The only permitted call is yield with data {task_id, state: "BLOCKED", needs: "depth"}.`;
export const SWARM_STATE_REASON = "BLOCKED needs: human-approval (swarm-state)";
/** index.ts appends the error message and `)` when the guard itself throws inside a swarm session (D-02). */
export const GUARD_ERROR_PREFIX = "BLOCKED needs: human-approval (guard error: ";

/** Everything the decision needs, pre-read by the handler (D-01 identity, D-06 depth-cap inputs). */
export interface GuardFacts {
  /** callingAgent(ctx): session_init agent, else env SWARM_AGENT when there is no session_init. */
  agent: string | undefined;
  /** session_init.restrictToolNames: a plan-mode child, never at the depth cap. */
  restricted: boolean;
  planMode: boolean;
  /** getActiveTools() contains `task`; read only for a01-orchestrator, true otherwise. */
  hasTask: boolean;
  /** No session_init entry: the operator's main session or a headless `-p` swarm session. */
  topLevel: boolean;
  env: Readonly<Record<string, string | undefined>>;
}

/**
 * The swarm-session marker (D-01): a swarm agent's own session, or a top-level session the swarm runner
 * started (SWARM_AGENT or SWARM_TASK_ID set and non-empty). Generic `task`/`scout` children and the operator's
 * main session are outside.
 */
export function inSwarm(facts: Pick<GuardFacts, "agent" | "topLevel" | "env">): boolean {
  if (facts.agent !== undefined && SWARM_SLUGS.includes(facts.agent)) return true;
  return facts.topLevel && Boolean(facts.env.SWARM_AGENT?.trim() || facts.env.SWARM_TASK_ID?.trim());
}

/** `needs` names depth: a string equal to `depth` after trim + lower-case, an array holding one, or an object with own key `depth`. */
function needsDepth(needs: unknown): boolean {
  const isDepth = (v: unknown) => typeof v === "string" && v.trim().toLowerCase() === "depth";
  if (Array.isArray(needs)) return needs.some(isDepth);
  if (typeof needs === "object" && needs !== null) return Object.hasOwn(needs, "depth");
  return isDepth(needs);
}

/** The one call A01 may make at the depth cap: yield data {state: "BLOCKED", needs: depth}. task_id is never required (Pitfall 4). */
function isDepthYield(event: ToolCallEvent): boolean {
  if (event.toolName !== "yield" || typeof event.input !== "object" || event.input === null) return false;
  const { data } = event.input as { data?: unknown };
  if (typeof data !== "object" || data === null) return false;
  const { state, needs } = data as { state?: unknown; needs?: unknown };
  return state === "BLOCKED" && needsDepth(needs);
}

export function guardToolCall(event: ToolCallEvent, facts: GuardFacts): ToolCallResult | undefined {
  // HOOK-04 (D-06): A01 without `task`, not a restricted child and not in plan mode, can only report BLOCKED/depth
  if (facts.agent === ORCHESTRATOR && !facts.hasTask && !facts.restricted && !facts.planMode && !isDepthYield(event)) {
    return { block: true, reason: DEPTH_REASON };
  }
  // HOOK-03 (D-05): applies everywhere, swarm session or not
  if (Object.hasOwn(SWARM_STATE_TOOLS, event.toolName) && facts.agent !== ORCHESTRATOR) {
    return { block: true, reason: SWARM_STATE_REASON };
  }
  // Outside a swarm session nothing else applies (D-01): the swarm-only rules (D-08, HOOK-02) go below, behind inSwarm.
  return undefined;
}
