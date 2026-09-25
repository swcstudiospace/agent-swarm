/**
 * Session-log readers (D-03, research FA-2): omp's ExtensionContext has no agent or plan-mode field,
 * so identity and plan mode come from ctx.sessionManager entries. Reused by the Phase 4 guard (HOOK-03/04).
 * callingAgent is the single identity resolver (Phase 4 D-01); it alone also reads env SWARM_AGENT.
 */
import type { ExtensionContext } from "./omp-api.ts";

type SessionCtx = Pick<ExtensionContext, "sessionManager">;

/**
 * Gate → the only agent (omp slug, agents.json) allowed to run it (D-12 separation of gate duties). Read by swarm_gate
 * (tools.ts) and by the guard's gate-script shell rule (guard.ts), which must not import tools.ts or the bridge.
 */
export const GATE_AGENTS = { quality: "a08-qa", review: "a09-reviewer", security: "a10-security", release: "a12-release" } as const;

/** The calling agent (session_init.agent; undefined = the top-level "main" session) and whether its tools are restricted. */
export function sessionAgent(ctx: SessionCtx): { agent: string | undefined; restricted: boolean } {
  const init = ctx.sessionManager.getEntries().find((e) => e.type === "session_init");
  return { agent: typeof init?.agent === "string" ? init.agent : undefined, restricted: init?.restrictToolNames === true };
}

/**
 * The calling agent: the session's session_init agent, else env SWARM_AGENT (headless `-p` sessions).
 * process.env is shared by in-process sessions, so the env is consulted only when no session_init exists (D-01).
 */
export function callingAgent(ctx: SessionCtx): string | undefined {
  return sessionAgent(ctx).agent ?? (process.env.SWARM_AGENT?.trim() || undefined);
}

/**
 * Top-level plan mode, walking the current branch backwards:
 * 1. a `mode_change` entry decides (`mode === "plan"`);
 * 2. a `plan-mode-context` custom_message before the latest user message (a mid-turn steer), or inside the
 *    contiguous custom_message run just before it (the per-prompt batch), means plan mode;
 * 3. any other entry once that user message has been crossed means not plan mode.
 * Fail-closed: after a mid-turn plan approval the rest of that turn may still read as plan mode (research A1).
 * Plan-mode subagents never see extension tools at all (omp-native restrictToolNames).
 */
export function inPlanMode(ctx: SessionCtx): boolean {
  const branch = ctx.sessionManager.getBranch();
  let crossedUser = false;
  for (let i = branch.length - 1; i >= 0; i--) {
    const e = branch[i];
    if (e.type === "mode_change") return e.mode === "plan";
    if (e.type === "custom_message" && e.customType === "plan-mode-context") return true;
    if (e.type === "message" && (e.message as { role?: unknown } | undefined)?.role === "user") {
      if (crossedUser) return false;
      crossedUser = true;
    } else if (crossedUser && e.type !== "custom_message") return false;
  }
  return false;
}
