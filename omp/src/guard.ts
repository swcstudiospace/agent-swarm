/**
 * The `tool_call` guard decision (Phase 4 HOOK-03/04): a pure function of (event, facts) with no I/O, no bridge,
 * no fs and no process access — index.ts reads the facts (identity, plan mode, active tools, env) and passes them in.
 * Precedence, first match decides: HOOK-04 (A01 depth cap) → HOOK-03 (swarm-state tools) → outside a swarm
 * session nothing else applies → HOOK-02 (the autonomy-ceiling RULES table over bash, D-03/D-04).
 * Reason strings are read by the A01 dispatcher (D-07) and operators: keep them and the rule ids stable.
 */
import { posix } from "node:path";
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
  /** ctx.cwd: relative paths resolve against it; `rm -rf` of an absolute path outside it is universal-destructive. */
  cwd: string;
  /** os.homedir() at call time: expands `~` / `$HOME`. */
  home: string;
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
  // Outside a swarm session nothing else applies (D-01)
  if (!inSwarm(facts)) return undefined;
  // HOOK-02 (D-03): bash only; normalize, then the first matching row in table order decides
  if (event.toolName !== "bash") return undefined;
  const command = typeof event.input === "object" && event.input !== null ? (event.input as { command?: unknown }).command : undefined;
  if (typeof command !== "string" || command.trim() === "") return undefined;
  const hit = matchRule(command, facts);
  return hit === undefined ? undefined : { block: true, reason: ruleReason(hit) };
}

// ---------------------------------------------------------------------------------------------------------------
// HOOK-02 autonomy-ceiling table (D-03/D-04; research FA-5). Ceilings are per capability (agents.json
// autonomy_ceiling, read by humans only — nothing here reads agents.json): every L3/L4 capability blocks for every
// swarm agent, so rows carry no per-agent allow list; L2 cases (kubectl without a prod marker) simply do not match.
// Residual, not shell-visible, enforced by prompts and gates only: scope_change, breaking_contract, brand_change,
// public_docs, contract_deviation, design_deviation, major_bump, waive, accept_risk. Regex evasion (python3 -c,
// base64 | sh, …) is the accepted FA-6 residual.
// ---------------------------------------------------------------------------------------------------------------

/** D-04 prod markers: fixed here, never configured. A release tag (v1.2, 1.2.3-rc1) is a marker too. */
export const PROD_MARKERS: readonly string[] = ["prod", "production", "--prod", "main", "master"];
const RELEASE_TAG = /^v?\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]*)?$/;

/** A token names prod: itself, or any `=:/,_-`-separated part of it, is a marker or a release tag. */
function isProdMarker(token: string): boolean {
  const t = token.toLowerCase();
  if (PROD_MARKERS.includes(t) || RELEASE_TAG.test(t)) return true;
  return t.split(/[=:/,_-]+/).some((part) => part !== "" && (PROD_MARKERS.includes(part) || RELEASE_TAG.test(part)));
}

/** One shell segment after normalize(): its text and whitespace words (quotes stripped, argv[0] basename). */
export interface Segment {
  text: string;
  words: string[];
}
type Matcher = (seg: Segment, facts: GuardFacts, command: string) => boolean;

export interface Rule {
  /** Stable, operator-visible (reason text, AGENTS.md). */
  id: string;
  capability: string;
  /** A regex over the segment text, or a predicate over the parsed segment. */
  pattern: RegExp | Matcher;
  /** Agents the capability is scoped to in agents.json (messaging only, never an allow list). */
  agents?: readonly string[];
  /** Positive example commands: guard.test.ts requires ≥1 and asserts each blocks inside a swarm session. */
  samples: readonly string[];
}

const PREFIX = /^(?:env(?:\s+-\S+)*\s+|sudo(?:\s+-\S+)*\s+|command(?:\s+-[pvV]+)*\s+|[A-Za-z_]\w*=\S*\s+)/;

/** Split on `;`, `&&`, `||`, `|` and newlines outside quotes. */
function splitTopLevel(text: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quote: string | undefined;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote !== undefined) {
      if (c === quote) quote = undefined;
      else if (c === "\\" && quote === '"' && i + 1 < text.length) cur += text[i++];
      cur += c;
      continue;
    }
    if (c === "'" || c === '"') {
      quote = c;
      cur += c;
    } else if (c === "\\" && i + 1 < text.length) {
      cur += c + text[++i];
    } else if (c === ";" || c === "\n" || c === "|" || (c === "&" && text[i + 1] === "&")) {
      if (c !== ";" && c !== "\n" && text[i + 1] === c) i++;
      out.push(cur);
      cur = "";
    } else cur += c;
  }
  out.push(cur);
  return out;
}

/**
 * D-03 normalization: the inner text of every `$(…)` / backtick substitution becomes its own command, the rest is
 * split on `;`, `&&`, `||`, `|` (outside quotes), and each segment loses leading `env X=…`, `X=…`, `sudo`, `command`.
 */
export function normalize(command: string): string[] {
  const parts: string[] = [];
  let rest = command;
  const sub = /\$\(([^()]*)\)|`([^`]*)`/;
  for (let m = sub.exec(rest), n = 0; m !== null && n < 256; m = sub.exec(rest), n++) {
    parts.push(m[1] ?? m[2] ?? "");
    rest = `${rest.slice(0, m.index)} ${rest.slice(m.index + m[0].length)}`;
  }
  parts.push(rest);
  const segments: string[] = [];
  for (const part of parts) {
    for (let seg of splitTopLevel(part)) {
      seg = seg.trim();
      for (let prev = ""; prev !== seg; ) {
        prev = seg;
        seg = seg.replace(PREFIX, "").trim();
      }
      if (seg !== "") segments.push(seg);
    }
  }
  return segments;
}

function parseSegment(text: string): Segment {
  const words = text.split(/\s+/).filter((w) => w !== "").map((w) => w.replace(/^(['"])(.*)\1$/, "$2"));
  if (words.length > 0) words[0] = posix.basename(words[0]);
  return { text, words };
}

/** The git subcommand and its arguments, skipping global options (`-C dir`, `-c k=v`, `--no-pager`). */
function git(words: string[]): { sub: string | undefined; args: string[] } {
  if (words[0] !== "git") return { sub: undefined, args: [] };
  let i = 1;
  while (i < words.length && words[i].startsWith("-")) i += words[i] === "-C" || words[i] === "-c" ? 2 : 1;
  return { sub: words[i], args: words.slice(i + 1) };
}
const gitIs = (seg: Segment, sub: string) => git(seg.words).sub === sub;
const shortFlag = (w: string, letters: RegExp) => /^-[A-Za-z]+$/.test(w) && letters.test(w);
const operands = (args: string[]) => args.filter((a) => !a.startsWith("-"));

/** Expand `~`/`$HOME` and resolve against cwd. */
export function resolvePath(p: string, facts: Pick<GuardFacts, "cwd" | "home">): string {
  const expanded = p.replace(/^(?:~|\$HOME|\$\{HOME\})(?=\/|$)/, facts.home);
  return posix.resolve(facts.cwd || "/", expanded);
}
const within = (path: string, dir: string) => dir !== "" && dir !== "/" && path.startsWith(`${dir}/`);

/** An `rm -rf` target that is `/`, `~`, outside cwd (incl. `..`), cwd itself, or any `.git`/`.swarm` path. */
function dangerousRmTarget(target: string, facts: GuardFacts): boolean {
  if (target.split("/").some((s) => s === ".git" || s === ".swarm")) return true;
  return !within(resolvePath(target, facts), posix.resolve(facts.cwd || "/"));
}

function rmRecursiveForce({ words }: Segment, facts: GuardFacts): boolean {
  if (words[0] !== "rm") return false;
  let recursive = false;
  let force = false;
  let endOfOptions = false;
  const targets: string[] = [];
  for (const w of words.slice(1)) {
    if (!endOfOptions && w === "--") endOfOptions = true;
    else if (!endOfOptions && w.startsWith("--")) {
      recursive ||= w === "--recursive";
      force ||= w === "--force";
    } else if (!endOfOptions && /^-[A-Za-z]+$/.test(w)) {
      recursive ||= /[rR]/.test(w);
      force ||= w.includes("f");
    } else targets.push(w);
  }
  return recursive && force && targets.some((t) => dangerousRmTarget(t, facts));
}

/** `git push` of tags, or to main/master or a release tag (refspec destination). */
function pushToProtected(seg: Segment): boolean {
  const { sub, args } = git(seg.words);
  if (sub !== "push") return false;
  if (args.includes("--tags") || args.includes("--follow-tags")) return true;
  const refs = operands(args).slice(1);
  return refs.some((ref) => {
    const dest = (ref.split(":").pop() ?? "").replace(/^\+/, "").replace(/^refs\/(?:heads|tags)\//, "");
    return dest === "main" || dest === "master" || RELEASE_TAG.test(dest);
  });
}

const DB_CLIENT = /^(?:psql|mysql|mariadb|sqlite3|prisma)$/;
const DDL = /\b(?:DROP\s+(?:TABLE|COLUMN|DATABASE|SCHEMA)|TRUNCATE)\b/i;

const reason = (capability: string, id: string) => `BLOCKED needs: human-approval (${capability}: ${id})`;

/** The ordered table: the first row whose pattern matches any segment decides. */
export const RULES: readonly Rule[] = [
  // universal destructive (every swarm agent, treated as L4)
  {
    id: "git-force-push",
    capability: "destructive",
    pattern: (seg) => gitIs(seg, "push") && git(seg.words).args.some((a) => a.startsWith("--force") || shortFlag(a, /f/)),
    samples: ["git push --force", "git push -f origin feat/x", "git push --force-with-lease"],
  },
  {
    id: "git-reset-hard",
    capability: "destructive",
    pattern: (seg) => gitIs(seg, "reset") && git(seg.words).args.includes("--hard"),
    samples: ["git reset --hard HEAD~1"],
  },
  {
    id: "git-clean-force",
    capability: "destructive",
    pattern: (seg) => gitIs(seg, "clean") && git(seg.words).args.some((a) => a === "--force" || shortFlag(a, /f/)),
    samples: ["git clean -fdx", "git clean -f"],
  },
  {
    id: "git-branch-force-delete",
    capability: "destructive",
    pattern: (seg) => gitIs(seg, "branch") && git(seg.words).args.some((a) => shortFlag(a, /D/)),
    samples: ["git branch -D feature"],
  },
  {
    id: "git-discard-repo",
    capability: "destructive",
    pattern: (seg) => {
      const { sub, args } = git(seg.words);
      return (sub === "checkout" || sub === "restore") && operands(args).some((a) => [".", "./", ":/", "*"].includes(a));
    },
    samples: ["git checkout -- .", "git restore ."],
  },
  { id: "rm-rf-protected", capability: "destructive", pattern: rmRecursiveForce, samples: ["rm -rf /", "rm -rf ~", "rm -rf .git"] },
  {
    id: "chmod-777-recursive",
    capability: "destructive",
    pattern: ({ words }) =>
      words[0] === "chmod" && words.some((w) => w === "--recursive" || shortFlag(w, /R/)) &&
      words.some((w) => w === "777" || /^(?:a|ugo)\+rwx$/.test(w)),
    samples: ["chmod -R 777 ."],
  },
  // A07 destructive_ddl (L4)
  {
    id: "ddl-drop-truncate",
    capability: "destructive_ddl",
    agents: ["a07-data"],
    pattern: ({ words }, _facts, command) => DB_CLIENT.test(words[0] ?? "") && DDL.test(command),
    samples: ['psql -c "DROP TABLE users"', 'echo "TRUNCATE t" | psql app'],
  },
  {
    id: "prisma-migrate-reset",
    capability: "destructive_ddl",
    agents: ["a07-data"],
    pattern: /\bprisma\s+migrate\s+reset\b/,
    samples: ["npx prisma migrate reset --force"],
  },
  {
    id: "db-push-accept-data-loss",
    capability: "destructive_ddl",
    agents: ["a07-data"],
    pattern: /\bdb\s+push\b.*--accept-data-loss\b/,
    samples: ["prisma db push --accept-data-loss"],
  },
  // A11 prod_infra (L3)
  {
    id: "terraform-apply-destroy",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: ({ words }) => (words[0] === "terraform" || words[0] === "tofu") && words.some((w) => w === "apply" || w === "destroy"),
    samples: ["terraform apply -auto-approve", "terraform destroy"],
  },
  {
    id: "pulumi-up-destroy",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: ({ words }) => words[0] === "pulumi" && words.some((w) => w === "up" || w === "destroy"),
    samples: ["pulumi up --yes", "pulumi destroy"],
  },
  {
    id: "helm-release-change",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: ({ words }) => words[0] === "helm" && words.some((w) => ["install", "upgrade", "uninstall", "delete", "rollback"].includes(w)),
    samples: ["helm upgrade app ./chart", "helm install app ./chart"],
  },
  {
    id: "kubectl-prod-change",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: ({ words }) =>
      words[0] === "kubectl" && words.some((w) => ["apply", "delete", "rollout", "scale"].includes(w)) && words.slice(1).some(isProdMarker),
    samples: ["kubectl apply -n production -f k.yaml", "kubectl --context=prod rollout restart deploy/api"],
  },
  {
    id: "cloud-destructive",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: ({ words }) =>
      (words[0] === "aws" && words.slice(1).some((w) => /^(?:delete|terminate|remove|deregister|destroy)-/.test(w) || w === "rb" || w === "rm")) ||
      (words[0] === "gcloud" && words.slice(1).some((w) => w === "deploy" || w === "delete")),
    samples: ["aws ec2 terminate-instances --instance-ids i-1", "gcloud app deploy"],
  },
  // A12 prod_high_risk (L4)
  {
    id: "vercel-prod",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: ({ words }) => words.includes("vercel") && words.some((w) => w === "--prod" || w === "--production"),
    samples: ["vercel --prod", "npx vercel deploy --prod"],
  },
  {
    id: "fly-deploy",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: ({ words }) => (words[0] === "fly" || words[0] === "flyctl") && words.includes("deploy"),
    samples: ["fly deploy"],
  },
  {
    id: "gh-release-create",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: ({ words }) => words[0] === "gh" && words[1] === "release" && words[2] === "create",
    samples: ["gh release create v1.0.0"],
  },
  {
    id: "package-publish",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: ({ words }) => ["npm", "pnpm", "bun", "yarn"].includes(words[0] ?? "") && words.slice(1).includes("publish"),
    samples: ["npm publish", "pnpm publish --access public", "bun publish"],
  },
  {
    id: "docker-push",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: ({ words }) => (words[0] === "docker" || words[0] === "podman") && words.includes("push"),
    samples: ["docker push ghcr.io/org/app:1.0"],
  },
  {
    id: "git-push-protected",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: pushToProtected,
    samples: ["git push --tags", "git push origin main", "git push origin HEAD:master", "git push origin v1.2.0"],
  },
];

/** The first row (table order) matching any normalized segment of `command`. */
export function matchRule(command: string, facts: GuardFacts): Rule | undefined {
  const segments = normalize(command).map(parseSegment);
  return RULES.find((rule) =>
    segments.some((seg) => (typeof rule.pattern === "function" ? rule.pattern(seg, facts, command) : rule.pattern.test(seg.text))),
  );
}

export const ruleReason = (rule: Rule): string => reason(rule.capability, rule.id);
