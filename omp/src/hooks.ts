/**
 * HOOK-01 context hook (D-09): a pure classifier plus the before_agent_start transform. No bridge, no fs, no spawn.
 * hooks/user_prompt_submit.py mirrors classifyPrompt (D-10); tests/fixtures/classifier_prompts.json pins both.
 */
import { sessionAgent } from "./context.ts";
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

/** The systemPrompt with SWARM_CONTEXT appended, or undefined in subagents, swarm children, repeats and non-SDLC prompts. */
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
    if (!classify(event.prompt)) return undefined;
  } catch {
    return undefined;
  }
  return { systemPrompt: [...prior, SWARM_CONTEXT] };
}
