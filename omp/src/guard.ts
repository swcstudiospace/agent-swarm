/**
 * The `tool_call` guard decision (Phase 4 HOOK-03/04): a pure function of (event, facts) with no I/O, no bridge,
 * no fs and no process access — index.ts reads the facts (identity, plan mode, active tools, env) and passes them in.
 * Precedence, first match decides: HOOK-04 (A01 depth cap) → HOOK-03 (swarm-state tools) → outside a swarm
 * session nothing else applies → D-08 (eval, xd://run_code and xd://debug writes, protected write/edit paths) → HOOK-02 (the RULES table over bash:
 * D-05 shell twin and gate scripts, D-08 shell writes, D-03/D-04 autonomy ceiling).
 * Reason strings are read by the A01 dispatcher (D-07) and operators: keep them and the rule ids stable.
 */
import { posix } from "node:path";
import { GATE_AGENTS } from "./context.ts";
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
  /** os.tmpdir() at call time ($TMPDIR honoured): its subtree is the specialists' scratch space, inside like cwd (IN-01). */
  tmp: string;
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

/** P4-UF-01: write path is omp's raw-code device (`xd://run_code` / `xd://debug`), any case, optional query or subpath. */
const RAW_CODE_DEVICE = /^xd:\/\/(?:run_code|debug)(?:[/?#]|$)/i;

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
  // D-08: eval and the raw-code devices run code past tool_call, so they are closed outright; config/state paths are not writable
  if (event.toolName === "eval") return { block: true, reason: reason("eval", "eval-in-swarm") };
  if (event.toolName === "write" && typeof event.input === "object" && event.input !== null) {
    const { path, file_path } = event.input as { path?: unknown; file_path?: unknown };
    if (
      (typeof path === "string" && RAW_CODE_DEVICE.test(path.trim())) ||
      (typeof file_path === "string" && RAW_CODE_DEVICE.test(file_path.trim()))
    ) {
      return { block: true, reason: reason("eval", "xd-device") };
    }
  }
  if (event.toolName === "write" || event.toolName === "edit") {
    if (editTargets(event.input).some((p) => isProtectedPath(p, facts))) {
      return { block: true, reason: reason("protected_path", "protected-path-write") };
    }
    return undefined;
  }
  // HOOK-02 (D-03): bash only; normalize, then the first matching row in table order decides
  if (event.toolName !== "bash") return undefined;
  const command = typeof event.input === "object" && event.input !== null ? (event.input as { command?: unknown }).command : undefined;
  if (typeof command !== "string" || command.trim() === "") return undefined;
  const hit = matchRule(command, facts);
  return hit === undefined ? undefined : { block: true, reason: ruleReason(hit) };
}

/** A hashline section header `[path]` / `[path#TAG]` (the path may hold spaces; the trailing tag is 4 hex digits). */
const HASHLINE_HEADER = /^\[([^\]\n#]+?)(?:#[0-9A-Fa-f]{4})?\]\s*$/gm;
/** A hashline `MV DEST` file op and the apply_patch file directives. */
const HASHLINE_MOVE = /^\s*MV\s+(\S.*?)\s*$/gm;
const APPLY_PATCH_FILE = /^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$/gm;

/**
 * Every file a write/edit call names, over omp's parameter shapes: `path`/`file_path` (write, edit replace/patch),
 * the hashline / apply_patch / sloppy `{input}` text (section headers, `MV`, `*** Update File:` …), and the
 * `xd://ast_edit` device (a write whose content is JSON with `paths`).
 */
export function editTargets(input: unknown): string[] {
  if (typeof input !== "object" || input === null) return [];
  const { path, file_path, input: text, content } = input as { path?: unknown; file_path?: unknown; input?: unknown; content?: unknown };
  const out: string[] = [];
  const add = (p: unknown) => {
    if (typeof p === "string" && p.trim() !== "") out.push(p.replace(/^(['"])(.*)\1$/, "$2").trim());
  };
  add(path);
  add(file_path);
  if (typeof text === "string") {
    for (const re of [HASHLINE_HEADER, HASHLINE_MOVE, APPLY_PATCH_FILE]) for (const m of text.matchAll(re)) add(m[1]);
  }
  if (typeof path === "string" && /^xd:\/\/ast_edit(?:[/?#]|$)/.test(path.trim()) && typeof content === "string") {
    let args: unknown;
    try {
      args = JSON.parse(content);
    } catch {
      return out;
    }
    const paths = (args as { paths?: unknown } | null)?.paths;
    if (Array.isArray(paths)) for (const p of paths) add(p);
  }
  return out;
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

/** `--dry-run`, `--dry-run=client|server|none`: the command renders and never changes the cluster. */
const isDryRun = (w: string) => /^--dry-run(?:=(?:client|server|none))?$/.test(w);
/** kubectl flags whose value names where the change lands (D-04 markers apply to these values). */
const KUBECTL_TARGET_FLAG = /^(?:-n|--namespace|--context|--cluster|--kubeconfig)$/;
/** kubectl flags whose value is a file, selector or format, never a prod marker. */
const KUBECTL_VALUE_FLAG = /^(?:-f|--filename|-k|--kustomize|-l|--selector|-o|--output|-p|--patch|--field-selector|--template)$/;

/**
 * `kubectl apply|delete|rollout|scale` that lands in prod (D-04): a marker in the value of `-n`/`--namespace`/
 * `--context`/`--cluster`/`--kubeconfig`, or — when no such flag names the destination — in a positional word
 * (resource, name). File, selector and output values never count, and read-only forms (`--dry-run`,
 * `rollout status|history`) never match (WR-08).
 */
function kubectlProdChange({ words }: Segment): boolean {
  if (words[0] !== "kubectl" || words.some(isDryRun)) return false;
  const verb = words.findIndex((w, i) => i > 0 && ["apply", "delete", "rollout", "scale"].includes(w));
  if (verb === -1 || (words[verb] === "rollout" && (words[verb + 1] === "status" || words[verb + 1] === "history"))) return false;
  const targets: string[] = [];
  const positional: string[] = [];
  for (let i = 1; i < words.length; i++) {
    const w = words[i];
    const eq = w.indexOf("=");
    if (w.startsWith("-")) {
      const flag = eq === -1 ? w : w.slice(0, eq);
      if (KUBECTL_TARGET_FLAG.test(flag)) {
        if (eq !== -1) targets.push(w.slice(eq + 1));
        else if (words[i + 1] !== undefined) targets.push(words[++i]);
      } else if (KUBECTL_VALUE_FLAG.test(flag) && eq === -1) i++;
    } else positional.push(w);
  }
  return (targets.length > 0 ? targets : positional).some(isProdMarker);
}

/** One shell segment after normalize(): its text and whitespace words (quotes stripped, argv[0] basename). */
export interface Segment {
  text: string;
  words: string[];
  /** The first word as written (path kept): `./scripts/orch_plan.py` where words[0] is `orch_plan.py`. */
  argv0: string;
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
  /** The block reason when it is not the `(<capability>: <id>)` form (the swarm-state shell twin). */
  reason?: string;
  /** Positive example commands: guard.test.ts requires ≥1 and asserts each blocks inside a swarm session. */
  samples: readonly string[];
}

/** A shell word as an assignment value: quoted runs (with spaces), escapes and bare characters, up to whitespace. */
const VALUE = String.raw`(?:"[^"]*"|'[^']*'|\\.|[^\s"'\\])*`;
/** sudo/doas flags that take the next word (`-u root`, `-g wheel`, `-C 3`, `-D dir`, `-h host`, `-p prompt`, `-r role`, `-t type`, `-U user`, `-T secs`). */
const SUDO_VALUE_FLAG = String.raw`-[ugCDhprtUT]\s+\S+|--(?:user|group|host|prompt|role|type|chdir|close-from|other-user|command-timeout)\s+\S+`;
/**
 * Wrappers that run the rest of the line unchanged; a flag that takes a value takes it along (`nice -n 5`,
 * `timeout -s KILL 5m`, `xargs -n 1`), and `timeout` also drops its duration.
 */
const WRAPPER = String.raw`(?:time(?:\s+-p)?|nohup|exec|builtin|eval|nice(?:\s+(?:-n\s+\S+|-\S+))*|ionice(?:\s+(?:-[cn]\s+\S+|-\S+))*|stdbuf(?:\s+-\S+)+|timeout(?:\s+(?:-[sk]\s+\S+|-\S+))*\s+\S+|xargs(?:\s+(?:-[nIPdaLsE]\s+\S+|-\S+))*)\s+`;
/**
 * Leading words that do not change what runs: `env [-i] [-u NAME] [-C DIR] …`, `sudo`/`doas` with their flags,
 * `command [-pvV]`, `NAME=value` assignments (quoted values may hold spaces) and the WRAPPER set.
 */
const PREFIX = new RegExp(
  String.raw`(?:env(?:\s+(?:-[uCS]\s+\S+|-\S+))*\s+|(?:sudo|doas)(?:\s+(?:${SUDO_VALUE_FLAG}|-\S+))*\s+|command(?:\s+-[pvV]+)*\s+|[A-Za-z_]\w*=${VALUE}\s+|${WRAPPER})`,
  "y",
);
/** `sh -c`, `bash -ec`, `/bin/zsh -x -c` …: the next word is a command line of its own. */
const SHELL_C = /^(?:\S*\/)?(?:ba|z|da|k|a)?sh(?:\s+-\S+)*\s+-[A-Za-z]*c\s+/;
/** Nesting cap for `sh -c "sh -c '…'"` recursion. */
const MAX_LITERAL_DEPTH = 3;

/** The content of the quoted string starting at `text[at]` (bash unescaping inside `"…"`); undefined when unterminated. */
function quotedLiteral(text: string, at: number): string | undefined {
  const q = text[at];
  if (q !== '"' && q !== "'") return undefined;
  let out = "";
  for (let i = at + 1; i < text.length; i++) {
    const c = text[i];
    if (c === q) return out;
    if (c === "\\" && q === '"' && i + 1 < text.length && /["\\$`\n]/.test(text[i + 1])) {
      out += text[++i];
      continue;
    }
    out += c;
  }
  return undefined;
}

/** A heredoc operator and its delimiter word (`<<EOF`, `<<-'EOF'`, `<< "EOF"`); sticky, positioned by splitTopLevel. */
const HEREDOC = /<<-?[ \t]*(?:"([^"\n]*)"|'([^'\n]*)'|([^\s<>|&;()]+))/y;

/**
 * Split on `;`, `&&`, `||`, `|`, newlines and `(`/`)`/`{ `/` }` grouping outside quotes. Heredoc bodies and `#`
 * comments are data, not commands: they are skipped without quote tracking (an apostrophe in them must not
 * swallow the commands after them). An unbalanced quote at the end re-splits its tail with quotes off.
 */
function splitTopLevel(text: string, quotesOn = true): string[] {
  const out: string[] = [];
  let cur = "";
  let blank = true; // cur holds only whitespace
  let quote: string | undefined;
  let quoteAt = 0;
  let quoteOut = 0;
  let quoteCur = "";
  let heredoc: string | undefined;
  const boundary = () => {
    out.push(cur);
    cur = "";
    blank = true;
  };
  /** The index of the newline that ends the heredoc delimiter line at or after `from` (text.length when absent). */
  const heredocEnd = (from: number): number => {
    for (let j = from; j < text.length; ) {
      const nl = text.indexOf("\n", j);
      const end = nl === -1 ? text.length : nl;
      if (text.slice(j, end).replace(/^\t+/, "") === heredoc) return end;
      j = end + 1;
    }
    return text.length;
  };
  const heredocAt = (at: number): RegExpExecArray | null => {
    HEREDOC.lastIndex = at;
    return HEREDOC.exec(text);
  };
  let m: RegExpExecArray | null;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote !== undefined) {
      if (c === quote) quote = undefined;
      else if (c === "\\" && quote === '"' && i + 1 < text.length) cur += text[i++];
      cur += c;
      continue;
    }
    if (quotesOn && (c === "'" || c === '"')) {
      quote = c;
      quoteAt = i;
      quoteOut = out.length;
      quoteCur = cur;
      cur += c;
    } else if (c === "\\" && i + 1 < text.length) {
      cur += c + text[++i];
      blank = false;
    } else if (c === "#" && (blank || /\s/.test(text[i - 1]))) {
      // a comment runs to the end of the line; the newline itself is the segment boundary
      const nl = text.indexOf("\n", i);
      i = (nl === -1 ? text.length : nl) - 1;
    } else if (c === "<" && text[i + 1] === "<" && text[i + 2] !== "<" && (m = heredocAt(i)) !== null) {
      heredoc = m[1] ?? m[2] ?? m[3];
      cur += m[0];
      blank = false;
      i += m[0].length - 1;
    } else if (c === "\n") {
      boundary();
      if (heredoc !== undefined) {
        i = heredocEnd(i + 1);
        heredoc = undefined;
      }
    } else if (c === ";" || c === "|" || (c === "&" && text[i + 1] === "&")) {
      if (c !== ";" && text[i + 1] === c) i++;
      boundary();
    } else if (c === "(" || c === ")") {
      // a subshell `( … )`: its body is its own segment list (`$(…)` was cut out before the split)
      boundary();
    } else if (c === "{" && blank && (i + 1 === text.length || /\s/.test(text[i + 1]))) {
      // a brace group `{ …; }` opener as a word of its own (`${VAR}` and `{a,b}` are glued, never split)
      boundary();
    } else if (c === "}" && (i === 0 || /[\s;]/.test(text[i - 1])) && (i + 1 === text.length || /[\s;&|)]/.test(text[i + 1]))) {
      boundary();
    } else {
      cur += c;
      if (blank && !/\s/.test(c)) blank = false;
    }
  }
  out.push(cur);
  if (quote !== undefined) {
    // bash would reject an unterminated quote; a heredoc or comment the scan missed is the likelier reading
    const tail = splitTopLevel(text.slice(quoteAt + 1), false);
    out.length = quoteOut;
    tail[0] = `${quoteCur}${quote}${tail[0]}`;
    out.push(...tail);
  }
  return out;
}

/**
 * D-03 normalization: the inner text of every `$(…)` / backtick substitution becomes its own command, the rest is
 * split on `;`, `&&`, `||`, `|` (outside quotes), and each segment loses leading `env X=…`, `X=…`, `sudo`, `command`
 * and wrapper words. A literal `sh -c "…"` / `eval "…"` argument is normalized in turn and its segments appended
 * (the outer segment stays too); `bash -c "$VAR"` is opaque by design (the documented residual).
 */
export function normalize(command: string, depth = 0): string[] {
  const parts: string[] = [];
  // a backslash-newline continues the line: `git \` ⏎ `push --force` is one command
  let rest = command.replace(/\\\r?\n/g, " ");
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
      // sticky: each match starts where the last one ended, so k prefixes cost O(n), not O(k·n) (WR-09)
      let at = 0;
      for (PREFIX.lastIndex = 0; PREFIX.test(seg); at = PREFIX.lastIndex);
      seg = seg.slice(at);
      if (seg === "") continue;
      segments.push(seg);
      if (depth < MAX_LITERAL_DEPTH) {
        // `sh -c "<literal>"`, or the quoted line left behind by a stripped `eval`
        const literal = quotedLiteral(seg, SHELL_C.exec(seg)?.[0].length ?? 0);
        if (literal !== undefined) segments.push(...normalize(literal, depth + 1));
      }
    }
  }
  return segments;
}

function parseSegment(text: string): Segment {
  const words = text.split(/\s+/).filter((w) => w !== "").map((w) => w.replace(/^(['"])(.*)\1$/, "$2"));
  const argv0 = words[0] ?? "";
  if (words.length > 0) words[0] = posix.basename(words[0]);
  return { text, words, argv0 };
}

/** git global options that take the next word as their value when written without `=`. */
const GIT_VALUE_OPTION = /^(?:-C|-c|--work-tree|--git-dir|--namespace|--exec-path|--super-prefix|--config-env|--list-cmds|--attr-source)$/;

/** The git subcommand and its arguments, skipping global options (`-C dir`, `-c k=v`, `--work-tree x`, `--no-pager`). */
function git(words: string[]): { sub: string | undefined; args: string[] } {
  if (words[0] !== "git") return { sub: undefined, args: [] };
  let i = 1;
  while (i < words.length && words[i].startsWith("-")) i += GIT_VALUE_OPTION.test(words[i]) ? 2 : 1;
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

/** A repo-state segment (`.git`, `.swarm`, `.omp`) anywhere in the target path, before any resolution. */
const repoStateSegment = (target: string) => target.split("/").some((s) => s === ".git" || s === ".swarm" || s === ".omp");

/** An `rm -rf` target that is `/`, `~`, cwd or the tmp dir itself, or outside both subtrees (incl. `..`). */
function outsideCwd(target: string, facts: GuardFacts): boolean {
  const path = resolvePath(target, facts);
  return !within(path, posix.resolve(facts.cwd || "/")) && !within(path, facts.tmp === "" ? "" : posix.resolve(facts.tmp));
}

/**
 * `rm` that is universal-destructive: `-r -f` of `/`, `~`, cwd, the tmp dir or anything outside those two subtrees;
 * `-r` alone of a `.git`, `.swarm` or `.omp` path; and any `rm` of a `.swarm`/`.omp` path (the D-08 state dirs),
 * whatever the flags.
 */
function rmProtected({ words }: Segment, facts: GuardFacts): boolean {
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
  return targets.some((t) => isProtectedPath(t, facts) || (recursive && (repoStateSegment(t) || (force && outsideCwd(t, facts)))));
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

/** D-08: a path inside `.swarm/` or `.omp/` (cwd's, ~/.omp, or any other) after `~` expansion and cwd resolution. */
export function isProtectedPath(path: string, facts: Pick<GuardFacts, "cwd" | "home">): boolean {
  return resolvePath(path, facts).split("/").some((s) => s === ".swarm" || s === ".omp");
}

/** Files a segment writes, as far as detectable: `>`/`>>` targets, tee operands, cp/mv/install destinations. */
function shellWriteTargets({ text, words }: Segment): string[] {
  const targets: string[] = [];
  for (const m of text.matchAll(/(?:^|[^<>])\d*&?>>?\|?\s*("[^"]*"|'[^']*'|[^\s<>|&;]+)/g)) {
    targets.push(m[1].replace(/^(['"])(.*)\1$/, "$2"));
  }
  const args = operands(words.slice(1));
  if (words[0] === "tee") targets.push(...args);
  if (words[0] === "cp" || words[0] === "mv" || words[0] === "install") {
    const t = words.findIndex((w) => w === "-t" || w === "--target-directory");
    if (t > 0 && words[t + 1] !== undefined) targets.push(words[t + 1]);
    for (const w of words) if (w.startsWith("--target-directory=")) targets.push(w.slice("--target-directory=".length));
    if (args.length > 1) targets.push(args[args.length - 1]);
  }
  return targets;
}

/**
 * Files a segment mutates in place, as far as detectable: the operands of rmdir/unlink/shred/truncate/sqlite3/chmod/
 * chown/chgrp/touch/mkdir, the destination of ln/rsync, `sed -i` file operands, `dd of=`, `tar -C`/`--directory`,
 * `unzip -d`. `$SWARM_DIR`/symlink spellings stay the documented residual.
 */
function shellMutateTargets({ words }: Segment): string[] {
  const cmd = words[0] ?? "";
  const args = operands(words.slice(1));
  if (/^(?:rmdir|unlink|shred|truncate|sqlite3|chmod|chown|chgrp|touch|mkdir|mkfifo)$/.test(cmd)) return args;
  // ln and rsync read their sources and write the last operand
  if (cmd === "ln" || cmd === "rsync") return args.length > 1 ? [args[args.length - 1]] : [];
  if (cmd === "sed") {
    if (!words.some((w) => /^-[A-Za-z]*i|^--in-place/.test(w))) return [];
    // the first operand is the script unless one was given with -e/-f
    const scripted = words.some((w) => /^(?:-[A-Za-z]*[ef]|--expression|--file)/.test(w));
    return scripted ? args : args.slice(1);
  }
  if (cmd === "dd") return words.flatMap((w) => (w.startsWith("of=") ? [w.slice(3)] : []));
  const valueOf = (flags: string[], long?: string): string[] => {
    const out: string[] = [];
    for (let i = 1; i < words.length; i++) {
      if (flags.includes(words[i]) && words[i + 1] !== undefined) out.push(words[i + 1]);
      else if (long !== undefined && words[i].startsWith(`${long}=`)) out.push(words[i].slice(long.length + 1));
    }
    return out;
  };
  if (cmd === "tar") return valueOf(["-C", "--directory"], "--directory");
  if (cmd === "unzip") return valueOf(["-d"]);
  return [];
}

/** `sqlite3` opened on a path under `.swarm/` (the Task Store): a swarm-state write for anyone but A01 (WR-06). */
const sqliteOnSwarm = ({ words }: Segment, facts: GuardFacts) =>
  words[0] === "sqlite3" && operands(words.slice(1)).some((t) => resolvePath(t, facts).split("/").includes(".swarm"));

/** Gate script stem → its gate (swarm_gate's GATE_SCRIPTS, plus the gate names themselves). */
const GATE_STEMS: Record<string, keyof typeof GATE_AGENTS> = {
  qa_gate: "quality", quality_gate: "quality", rev_gate: "review", review_gate: "review",
  sec_gate: "security", security_gate: "security", rel_plan: "release", release_gate: "release",
};

const INTERPRETER = /^(?:python3?|bun|node|deno|uv|pipx)$/;

/**
 * The repo script a segment executes (WR-07): argv[0] itself, or the first word after `python`/`python3`/`bun`/
 * `node`/`deno`/`uv`/`pipx` and their `run`/flags, when it lives under `scripts/` or `scripts/ts/`. A script named
 * anywhere else (`cat scripts/x.py`, `grep … scripts/ts/x.ts`, `pytest tests/test_x_gate.py`) is not a run.
 */
function executedScript({ words, argv0 }: Segment): { stem: string; args: string[] } | undefined {
  let i = 0;
  while (i < words.length && (INTERPRETER.test(words[i]) || words[i] === "run" || (i > 0 && words[i].startsWith("-")))) {
    if (/^-[XW]$/.test(words[i])) i++; // `python -X dev`, `-W error` take a value
    i++;
  }
  const word = i === 0 ? argv0 : words[i];
  const m = /(?:^|\/)scripts\/(?:ts\/)?([A-Za-z0-9_-]+)\.(?:py|ts)$/.exec(word ?? "");
  return m === null ? undefined : { stem: m[1], args: words.slice(i + 1) };
}

/** A direct run of a gate script (qa/quality/rev/review/sec/security/release_gate, rel_plan) by anyone but that gate's owner (D-05). */
function foreignGateScript(seg: Segment, facts: GuardFacts): boolean {
  const run = executedScript(seg);
  if (run === undefined || !Object.hasOwn(GATE_STEMS, run.stem)) return false;
  return facts.agent !== GATE_AGENTS[GATE_STEMS[run.stem]];
}

/** A run of `scripts/orch_plan.py|ts`, or of `scripts/orch_status.py|ts` with `--ingest`/`--transition`. */
function orchStateScript(seg: Segment): boolean {
  const run = executedScript(seg);
  if (run === undefined) return false;
  return run.stem === "orch_plan" || (run.stem === "orch_status" && run.args.some((a) => /^--(?:ingest|transition)(?:=|$)/.test(a)));
}

/** The ordered table: the first row whose pattern matches any segment decides. */
export const RULES: readonly Rule[] = [
  // HOOK-03 shell twin (D-05): Task Store mutation through the scripts is A01's alone, like the swarm-state tools
  {
    id: "orch-state-shell",
    capability: "swarm-state",
    reason: SWARM_STATE_REASON,
    pattern: (seg, facts) => facts.agent !== ORCHESTRATOR && (orchStateScript(seg) || sqliteOnSwarm(seg, facts)),
    samples: [
      "python3 scripts/orch_status.py --ingest r.json",
      "python3 scripts/orch_status.py --transition T-1 DONE",
      "python3 scripts/orch_plan.py --brief-text x",
      "bun scripts/ts/orch_plan.ts",
      "scripts/ts/orch_status.ts --ingest x",
      "sqlite3 .swarm/tasks.db \"UPDATE tasks SET state='DONE'\"",
    ],
  },
  // gate scripts are identity-bound in bash as in swarm_gate (Phase 3 WR-03)
  {
    id: "gate-script-foreign",
    capability: "gate",
    agents: Object.values(GATE_AGENTS),
    pattern: foreignGateScript,
    samples: ["python3 scripts/rev_gate.py --task T-1", "bun scripts/ts/sec_gate.ts", "python3 scripts/review_gate.py x"],
  },
  // D-08: shell writes into .swarm/, .omp/ or ~/.omp
  {
    id: "protected-path-shell",
    capability: "protected_path",
    pattern: (seg, facts) => shellWriteTargets(seg).some((t) => isProtectedPath(t, facts)),
    samples: ["echo x > .swarm/a", "tee -a .omp/config.yml", "cp f ~/.omp/x", "mv a .swarm/b", "echo x >>~/.omp/agent/config.yml"],
  },
  // D-08: in-place mutation of .swarm/, .omp/ or ~/.omp through other tools (rm is the destructive row below)
  {
    id: "protected-path-mutate",
    capability: "protected_path",
    pattern: (seg, facts) => shellMutateTargets(seg).some((t) => isProtectedPath(t, facts)),
    samples: [
      "truncate -s0 .swarm/tasks.db",
      "sed -i 's/a/b/' .omp/config.yml",
      "sqlite3 ~/.omp/x.db 'DELETE FROM t'",
      "chmod 600 .swarm/keys",
      "rmdir .swarm/plans",
      "unlink .omp/config.yml",
      "shred ~/.omp/agent/config.yml",
      "ln -s /tmp/x .swarm/tasks.db",
      "rsync -a src/ .swarm/",
      "dd if=/dev/zero of=.swarm/tasks.db",
      "tar -xf a.tar -C .swarm",
      "unzip a.zip -d ~/.omp",
      "touch .swarm/x",
      "mkdir -p .omp/extensions",
    ],
  },
  // universal destructive (every swarm agent, treated as L4)
  {
    id: "git-force-push",
    capability: "destructive",
    pattern: (seg) => {
      const { sub, args } = git(seg.words);
      // `+refspec` forces that ref exactly like --force (the first operand is the remote)
      return sub === "push" && (args.some((a) => a.startsWith("--force") || shortFlag(a, /f/)) || operands(args).slice(1).some((r) => r.startsWith("+")));
    },
    samples: ["git push --force", "git push -f origin feat/x", "git push --force-with-lease", "git push origin +feat/x", "git --work-tree /x push --force"],
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
    pattern: (seg) => {
      const { sub, args } = git(seg.words);
      if (sub !== "branch") return false;
      if (args.some((a) => shortFlag(a, /D/))) return true;
      return args.some((a) => a === "--delete" || shortFlag(a, /d/)) && args.some((a) => a === "--force" || shortFlag(a, /f/));
    },
    samples: ["git branch -D feature", "git branch --delete --force feature", "git branch -d -f feature", "git branch -df feature"],
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
  {
    id: "rm-rf-protected",
    capability: "destructive",
    pattern: rmProtected,
    samples: ["rm -rf /", "rm -rf ~", "rm -rf .git", "rm -r .git", "rm .swarm/tasks.db", "rm -f .omp/config.yml", "rm -r .swarm"],
  },
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
    pattern: ({ words }) =>
      words[0] === "helm" && !words.some(isDryRun) && words.some((w) => ["install", "upgrade", "uninstall", "delete", "rollback"].includes(w)),
    samples: ["helm upgrade app ./chart", "helm install app ./chart"],
  },
  {
    id: "kubectl-prod-change",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: kubectlProdChange,
    samples: ["kubectl apply -n production -f k.yaml", "kubectl --context=prod rollout restart deploy/api", "kubectl scale deploy/api --replicas=0 --namespace=prod"],
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

export const ruleReason = (rule: Rule): string => rule.reason ?? reason(rule.capability, rule.id);
