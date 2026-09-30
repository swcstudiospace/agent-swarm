/**
 * Session-log readers (D-03, research FA-2): omp's ExtensionContext has no agent or plan-mode field,
 * so identity and plan mode come from ctx.sessionManager entries (plus omp's own argv for `--plan-yolo`, see
 * planYoloPending). Reused by the Phase 4 guard (HOOK-03/04).
 * callingAgent is the single identity resolver (Phase 4 D-01); it alone also reads env SWARM_AGENT.
 */
import type { ExtensionContext, SessionEntry } from "./omp-api.ts";

type SessionCtx = Pick<ExtensionContext, "sessionManager">;

/**
 * Gate → the only agent (omp slug, agents.json) allowed to run it (D-12 separation of gate duties). Read by swarm_gate
 * (tools.ts) and by the guard's gate-script shell rule (guard.ts), which must not import tools.ts or the bridge.
 */
export const GATE_AGENTS = { quality: "a08-qa", review: "a09-reviewer", security: "a10-security", release: "a12-release" } as const;

/**
 * The calling agent (session_init.agent; undefined = no agent named), whether its tools are restricted, and whether
 * a session_init entry exists at all (`topLevel: false`) — an agent-less session_init is still a child, not the
 * top-level session (IN-05).
 */
export function sessionAgent(ctx: SessionCtx): { agent: string | undefined; restricted: boolean; topLevel: boolean } {
  const init = ctx.sessionManager.getEntries().find((e) => e.type === "session_init");
  return { agent: typeof init?.agent === "string" ? init.agent : undefined, restricted: init?.restrictToolNames === true, topLevel: init === undefined };
}

/**
 * The calling agent: the session's session_init agent, else env SWARM_AGENT (headless `-p` sessions).
 * process.env is shared by in-process sessions, so the env is consulted only when no session_init exists (D-01).
 */
export function callingAgent(ctx: SessionCtx): string | undefined {
  const { agent, topLevel } = sessionAgent(ctx);
  return agent ?? (topLevel ? process.env.SWARM_AGENT?.trim() || undefined : undefined);
}

/**
 * `omp --plan-yolo` (flag or `--plan-yolo=…`, before a `--` end-of-options) is in plan mode from process start
 * until this process's plan-yolo handoff: the handoff is the only way plan-yolo leaves plan mode, and one persisted
 * by an earlier process (a resumed session) does not count. An entry without a parseable timestamp never counts.
 */
function planYoloPending(branch: SessionEntry[]): boolean {
  const argv = process.argv.slice(2); // omp's own args: cli.ts:620 runCli(process.argv.slice(2))
  const end = argv.indexOf("--");
  const flags = end === -1 ? argv : argv.slice(0, end);
  if (!flags.some((a) => a === "--plan-yolo" || a.startsWith("--plan-yolo="))) return false;
  return !branch.some(
    (e) => e.type === "custom_message" && e.customType === "plan-yolo-handoff" && Date.parse(String(e.timestamp)) >= performance.timeOrigin,
  );
}

/**
 * Top-level plan mode. A top-level `--plan-yolo` session is in plan mode while planYolo is pending: omp runs an
 * extension command (/swarm) before it arms plan-yolo, with nothing extension-visible yet (argv is the only signal).
 * Otherwise walk the current branch backwards:
 * 1. a `mode_change` entry decides (`mode === "plan"`);
 * 2. a `plan-mode-context` custom_message before the latest user message (a mid-turn steer), or inside the
 *    contiguous custom_message run just before it (the per-prompt batch), means plan mode;
 * 3. any other entry once that user message has been crossed means not plan mode.
 * Fail-closed: after a mid-turn plan approval the rest of that turn may still read as plan mode (research A1).
 * Plan-mode subagents never see extension tools at all (omp-native restrictToolNames); in-process children share
 * argv, so only the top-level session reads it (like SWARM_AGENT in callingAgent).
 */
export function inPlanMode(ctx: SessionCtx): boolean {
  const branch = ctx.sessionManager.getBranch();
  // omp 18.3.1 session/agent-session.ts:6652 runs extension commands; :7156 arms plan-yolo later (prewalk.ts:311)
  if (sessionAgent(ctx).topLevel && planYoloPending(branch)) return true;
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
