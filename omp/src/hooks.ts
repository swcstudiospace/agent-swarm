/**
 * before_agent_start parts.
 * - HOOK-01 context hook (D-09): a pure classifier plus the transform. No bridge, no fs, no spawn.
 *   hooks/user_prompt_submit.py mirrors classifyPrompt (D-10); tests/fixtures/classifier_prompts.json pins both.
 * - Runtime part (Phase 7 OPEN-3 R1): swarm-slug sessions get the absolute runtime root, resolved in the handler
 *   with swarmRoot() (an fs existence check, never a spawn), so specialists find `scripts/` from any workspace.
 */
import { swarmRoot } from "./bridge.ts";
import { callingAgent, sessionAgent } from "./context.ts";
import { SWARM_SLUGS } from "./guard.ts";
import type { BeforeAgentStartEvent, BeforeAgentStartResult, ExtensionContext } from "./omp-api.ts";

/** Prompts containing these (case-insensitive) are owned by other plugins or explicitly opt out. */
export const NEGATIVE = ["/uplift", "/think", "explain only", "/all-in-one:"] as const;

/** Tags the /swarm dispatch prompt so the hook never re-steers the swarm's own dispatch. */
export const DISPATCH_MARKER = "[agent-swarm:dispatch]";

const QUESTION = /^(what|why|how|when|where|who|which|is|are|can|could|does|do|should|explain|describe)\b/i;
const TRIVIAL = /\btypos?\b|^rename\b/i;
const SDLC =
  /\b(build|implement|add|create|fix|refactor|migrate|deploy|release|ship|write tests?|set up|setup|integrate|scaffold|upgrade)\b/i;

export const SWARM_CONTEXT = `## AgentSwarm

This is SDLC work: route it through the AgentSwarm instead of implementing it in this session.
- Run \`/swarm <brief>\` with the request as the brief, or
- call \`task\` with \`agent: "a01-orchestrator"\` and the request as the brief.
A01 plans the work and dispatches a02–a15 by slug.
`;

/** True only for SDLC-shaped prompts (D-09). */
export function classifyPrompt(prompt: string): boolean {
  const t = prompt.trim();
  if (!t || t.startsWith("/") || t.startsWith("<system-reminder")) return false;
  const low = t.toLowerCase();
  if (low.includes(DISPATCH_MARKER) || NEGATIVE.some((n) => low.includes(n))) return false;
  if (t.endsWith("?") || QUESTION.test(t) || TRIVIAL.test(t)) return false;
  return SDLC.test(t);
}

/** Root elements of an Ultrathink/Prompt-Uplift XML; omp replaces the user's prompt with that XML. */
const UPLIFT_ROOT = /^<(BUILD_PROMPT|FIX_PROMPT|RESEARCH_PROMPT|CHANGE_PROMPT|UPLIFTED_PROMPT|uplifted|ultrathink)\b/i;
const ORIGINAL_EL = /<ORIGINAL\b[^>]*>([\s\S]*?)<\/ORIGINAL>/i;

/** The user's own words: the unescaped <ORIGINAL> of an uplift XML, else the prompt unchanged. */
export function classifiedText(prompt: string): string {
  const t = prompt.trim();
  const m = UPLIFT_ROOT.test(t) ? ORIGINAL_EL.exec(t) : null;
  if (!m) return prompt;
  return m[1]
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}

/**
 * The systemPrompt with SWARM_CONTEXT appended, or undefined in subagents, swarm children, repeats and non-SDLC prompts.
 * An uplift XML prompt is classified by its <ORIGINAL>, so HOOK-01 agrees with the headless kickoff (WR-01).
 */
export function swarmContext(
  event: BeforeAgentStartEvent,
  ctx: Pick<ExtensionContext, "sessionManager">,
  env: Record<string, string | undefined>,
  classify: (prompt: string) => boolean = classifyPrompt,
): BeforeAgentStartResult | undefined {
  const { agent, restricted } = sessionAgent(ctx);
  if (agent !== undefined || restricted) return undefined;
  if (env.SWARM_CHILD === "1" || env.SWARM_AGENT?.trim()) return undefined;
  const prior = event.systemPrompt ?? [];
  if (prior.includes(SWARM_CONTEXT)) return undefined;
  try {
    if (!classify(classifiedText(event.prompt))) return undefined;
  } catch {
    return undefined;
  }
  return { systemPrompt: [...prior, SWARM_CONTEXT] };
}

export const RUNTIME_HEADING = "## AgentSwarm runtime";

/** The label of the runtime part's root line; the omp preamble tells specialists to read that line. */
export const RUNTIME_ROOT_LABEL = "Runtime root:";

/** The runtime part for an absolute runtime root; the omp preamble reads its RUNTIME_ROOT_LABEL line. */
export function runtimePart(root: string): string {
  return `${RUNTIME_HEADING}

${RUNTIME_ROOT_LABEL} ${root}
Run swarm scripts as \`python3 ${root}/scripts/<script>.py … --root <repo> --json\`, where \`<repo>\` is the git toplevel of your working directory.
`;
}

/**
 * The systemPrompt with the runtime part appended, for sessions whose calling agent is a swarm slug (session_init
 * agent, else SWARM_AGENT in a headless `-p` session, SWARM_CHILD included). Undefined for every other session,
 * when a runtime part is already present, and on any error (fail-open: the prompt stays untouched).
 */
export function runtimeContext(
  event: BeforeAgentStartEvent,
  ctx: Pick<ExtensionContext, "sessionManager">,
  root: () => string = swarmRoot,
): BeforeAgentStartResult | undefined {
  try {
    const agent = callingAgent(ctx);
    if (agent === undefined || !SWARM_SLUGS.includes(agent)) return undefined;
    const prior = event.systemPrompt ?? [];
    if (prior.some((p) => p.startsWith(`${RUNTIME_HEADING}\n`))) return undefined;
    return { systemPrompt: [...prior, runtimePart(root())] };
  } catch {
    return undefined;
  }
}
