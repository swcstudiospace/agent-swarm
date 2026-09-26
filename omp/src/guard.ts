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
  /**
   * The agent-swarm runtime roots, as spelled (env SWARM_ROOT and the extension package's repo; index.ts, no I/O):
   * write targets inside one are protected unless the session's cwd is inside it too (isProtectedPath).
   */
  runtimeRoots: readonly string[];
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
/** T-06-07: omp mints MCP tool names `mcp__<server>_<tool>` (also reachable as their `xd://` alias), any case. */
const MCP_TOOL = /^(?:xd:\/\/)?mcp__/i;
/** T-06-07: write path is an MCP tool's `xd://mcp__…` device, any case, optional query or subpath. */
const MCP_DEVICE = /^xd:\/\/mcp__/i;

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
  // T-06-07: MCP tools act with the operator's credentials past every other row, so they are closed outright too
  if (MCP_TOOL.test(event.toolName)) return { block: true, reason: reason("mcp", event.toolName) };
  if (event.toolName === "write" && typeof event.input === "object" && event.input !== null) {
    const { path, file_path } = event.input as { path?: unknown; file_path?: unknown };
    const devices = [path, file_path].filter((p): p is string => typeof p === "string").map((p) => p.trim());
    if (devices.some((p) => RAW_CODE_DEVICE.test(p))) return { block: true, reason: reason("eval", "xd-device") };
    const mcp = devices.find((p) => MCP_DEVICE.test(p));
    if (mcp !== undefined) return { block: true, reason: reason("mcp", mcp) };
  }
  if (event.toolName === "write" || event.toolName === "edit") {
    if (editTargets(event.input).some((p) => isProtectedPath(p, facts))) {
      return { block: true, reason: reason("protected_path", "protected-path-write") };
    }
    return undefined;
  }
  // HOOK-02 (D-03): bash only; normalize, then the first matching row in table order decides
  if (event.toolName !== "bash") return undefined;
  const input: object = typeof event.input === "object" && event.input !== null ? event.input : {};
  const command = "command" in input ? input.command : undefined;
  if (typeof command !== "string" || command.trim() === "") return undefined;
  // the bash tool's own `env` reaches the shell: GLOBIGNORE/BASHOPTS turn dotglob on, HOME and CDPATH move paths
  const env = new Map<string, unknown>("env" in input && typeof input.env === "object" && input.env !== null ? Object.entries(input.env) : []);
  if (env.has("GLOBIGNORE") || env.has("BASHOPTS")) return { block: true, reason: reason("protected_path", "glob-dotfiles") };
  const home = env.get("HOME");
  const cdpath = env.get("CDPATH");
  const shellFacts: GuardFacts = {
    ...facts,
    home: typeof home === "string" ? home : facts.home,
    env: typeof cdpath === "string" ? { ...facts.env, CDPATH: cdpath } : facts.env,
  };
  // the bash tool's `cwd` is where the command starts; an internal URL (`local://…`) is a directory the guard cannot place
  const cwd = "cwd" in input && typeof input.cwd === "string" ? input.cwd.trim() : "";
  const start = cwd === "" ? facts.cwd : /^[a-z][\w+.-]*:\/\//i.test(cwd) ? undefined : resolvePath(cwd, shellFacts);
  const hit = matchRule(command, shellFacts, start);
  return hit === undefined ? undefined : { block: true, reason: ruleReason(hit) };
}

/** A hashline section header `[path]` / `[path#TAG]` (the path may hold spaces; the trailing tag is 4 hex digits). */
const HASHLINE_HEADER = /^\[([^\]\n#]+?)(?:#[0-9A-Fa-f]{4})?\]\s*$/gm;
/** A hashline `MV DEST` file op and the apply_patch file directives. */
const HASHLINE_MOVE = /^\s*MV\s+(\S.*?)\s*$/gm;
const APPLY_PATCH_FILE = /^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$/gm;
/** omp's `xd://ast_edit` device, any case, optional query or subpath (like RAW_CODE_DEVICE). */
const AST_EDIT_DEVICE = /^xd:\/\/ast_edit(?:[/?#]|$)/i;

/**
 * Every file a write/edit call names, over omp's parameter shapes: `path`/`file_path` (write, edit replace/patch),
 * the hashline / apply_patch / sloppy `{input}` text (section headers, `MV`, `*** Update File:` …), and the
 * `xd://ast_edit` device (a write whose `path` or `file_path` is the device and whose content is JSON with `paths`).
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
  const astEdit = [path, file_path].some((p) => typeof p === "string" && AST_EDIT_DEVICE.test(p.trim()));
  if (astEdit && typeof content === "string") {
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

/**
 * `--dry-run`, `--dry-run=client|server|true`: the command renders and never changes the cluster or release.
 * `--dry-run=none`/`=false` (and any other value) run for real.
 */
const isDryRun = (w: string) => /^--dry-run(?:=(?:client|server|true))?$/.test(w);
/** kubectl flags whose value names where the change lands (D-04 markers apply to these values). */
const KUBECTL_TARGET_FLAG = /^(?:-n|--namespace|--context|--cluster|--kubeconfig|-s|--server)$/;
/** kubectl flags whose value is a file, selector, format, container, identity or count, never a prod marker. */
const KUBECTL_VALUE_FLAG =
  /^(?:-f|--filename|-k|--kustomize|-l|--selector|-o|--output|-p|--patch|--field-selector|--template|-c|--container|--as|--as-group|--as-uid|--user|--token|--certificate-authority|--client-certificate|--client-key|--request-timeout|--tls-server-name|--cache-dir|-v|--v|--vmodule|--image|--type|--timeout|--grace-period|--replicas|--port|--target-port|--name|--overrides|--env|--from|--from-file|--from-literal|--for|--subresource|--field-manager)$/;
/** kubectl (and `oc`) commands that change a cluster; see kubectlProdChange for the read-only subcommands. */
const KUBECTL_CHANGE: Record<string, true> = Object.fromEntries(
  ["create", "apply", "replace", "patch", "edit", "delete", "set", "label", "annotate", "expose", "autoscale", "scale", "drain", "cordon",
    "uncordon", "taint", "run", "cp", "exec", "attach", "debug", "certificate", "rollout"].map((v) => [v, true]),
);
/** Every kubectl command word: the verb is the first positional word that is one (a flag value never is, here). */
const KUBECTL_VERB: Record<string, true> = {
  ...KUBECTL_CHANGE,
  ...Object.fromEntries(
    ["get", "describe", "logs", "top", "explain", "diff", "wait", "port-forward", "proxy", "config", "version", "api-resources",
      "api-versions", "cluster-info", "auth", "plugin", "completion", "events", "kustomize", "alpha"].map((v) => [v, true]),
  ),
};

/**
 * A kubectl/oc change that lands in prod (D-04): a mutating verb (create, apply, replace, patch, edit, delete, set,
 * label, annotate, expose, autoscale, scale, drain, cordon, uncordon, taint, run, cp, exec, attach, debug,
 * `certificate approve|deny`, `rollout` but `status|history`, `apply` but `view-last-applied`), flags before or after
 * it, with a marker in the value of `-n`/`--namespace`/`--context`/`--cluster`/`--kubeconfig`/`--server` (`-n prod`,
 * `-nprod`, `-n=prod`, `--namespace=prod`) or a `KUBECONFIG=` prefix, or — when nothing names the destination — in a
 * positional word after the verb (resource, name; `k=v` words such as `set image` and label values aside). File,
 * selector and output values never count, and `--dry-run[=client|server]` never matches (WR-08).
 */
function kubectlProdChange({ words, marked }: Segment): boolean {
  const k = words.findIndex((w) => w === "kubectl" || w === "oc" || w === "kubecolor");
  if (!(k === 0 || (k === 1 && /^(?:microk8s|k3s|minikube)$/.test(words[0]))) || words.some(isDryRun)) return false;
  const targets: string[] = [];
  const positional: string[] = [];
  for (let i = k + 1; i < words.length; i++) {
    const w = words[i];
    if (w === "--") break; // the command `exec`/`debug` run in the pod
    if (!w.startsWith("-") || w === "-") {
      positional.push(w);
      continue;
    }
    const long = /^(--[^=]+)(?:=(.*))?$/.exec(w);
    const flag = long === null ? w.slice(0, 2) : long[1];
    let value = long === null ? (w.length > 2 ? w.slice(2).replace(/^=/, "") : undefined) : long[2];
    const target = KUBECTL_TARGET_FLAG.test(flag);
    if (value === undefined && (target || KUBECTL_VALUE_FLAG.test(flag))) value = words[++i];
    if (target && value !== undefined) targets.push(value);
  }
  const at = positional.findIndex((w) => Object.hasOwn(KUBECTL_VERB, w));
  if (at === -1 || !Object.hasOwn(KUBECTL_CHANGE, positional[at])) return false;
  const [verb, sub] = [positional[at], positional[at + 1]];
  if (verb === "rollout" && (sub === "status" || sub === "history")) return false;
  if (verb === "apply" && sub === "view-last-applied") return false;
  if (verb === "certificate" && sub !== "approve" && sub !== "deny") return false;
  const kubeconfig = /(?:^|\s)KUBECONFIG=(\S+)/.exec(marked)?.[1];
  if (kubeconfig !== undefined) targets.push(unquote(kubeconfig));
  return (targets.length > 0 ? targets : positional.slice(at + 1).filter((w) => !w.includes("="))).some(isProdMarker);
}

/**
 * One shell segment after normalization, as the rows read it, and the directory its relative paths resolve in
 * (matchRule's directory tracking; undefined when it cannot be known).
 */
export interface Segment {
  /** The command text, a cut `$(…)`/backtick substitution read as a blank (the regex rows match it). */
  text: string;
  /** Its shell words, unquoted and unescaped, argv[0] as its basename; a word that was only a substitution is dropped. */
  words: string[];
  /** The first word as written, unquoted (path kept): `./scripts/orch_plan.py` where words[0] is `orch_plan.py`. */
  argv0: string;
  /**
   * The words the write/mutate checks read: `words`, but a cut substitution stays (as CUT), and operands that exist
   * only at run time (`xargs`, `parallel`, `find -exec … {}`) are CUT too, so they are judged unknowable, never absent.
   */
  targetWords: string[];
  /** The text with CUT kept (the redirection scan reads it). */
  marked: string;
  /** The command itself cannot be known (named by an expansion or glob): every operand is a mutate target. */
  opaque: boolean;
  cwd: string | undefined;
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

/**
 * A shell word as an assignment value: quoted runs (with spaces), escapes and bare runs, up to whitespace. The bare
 * run is `+` inside `*`: use it only atomically (PREFIX's lookahead + backreference), never where it can backtrack.
 */
const VALUE = String.raw`(?:"[^"]*"|'[^']*'|\\.|[^\s"'\\]+)*`;
/** sudo/doas flags that take the next word (`-u root`, `-g wheel`, `-C 3`, `-D dir`, `-h host`, `-p prompt`, `-r role`, `-t type`, `-U user`, `-T secs`). */
const SUDO_VALUE_FLAG = String.raw`-[ugCDhprtUT]\s+\S+|--(?:user|group|host|prompt|role|type|chdir|close-from|other-user|command-timeout)\s+\S+`;
/**
 * Wrappers that run the rest of the line unchanged; a flag that takes a value takes it along (`nice -n 5`,
 * `timeout -s KILL 5m`, `xargs -n 1`), and `timeout` also drops its duration. `busybox`/`toybox` followed by an
 * applet name run that applet; `exec [-a NAME]`, `parallel`, `setsid`, `fakeroot`, `chrt PRIO`, `taskset MASK`,
 * `unshare`, `chroot DIR`, `flock FILE` and `watch` run the command after them (prefixInfo keeps what they imply).
 */
const WRAPPER = String.raw`(?:time(?:\s+-p)?|nohup|exec(?:\s+(?:-a\s+\S+|-[cl]+))*|builtin|eval|(?:busybox|toybox)(?=\s+[A-Za-z_])|nice(?:\s+(?:-n\s+\S+|-\S+))*|ionice(?:\s+(?:-[cn]\s+\S+|-\S+))*|stdbuf(?:\s+-\S+)+|timeout(?:\s+(?:-[sk]\s+\S+|-\S+))*\s+\S+|xargs(?:\s+(?:-[nIPdaLsE]\s+\S+|-\S+))*|parallel(?:\s+(?:-[jSN]\s+\S+|-\S+))*|setsid(?:\s+-\S+)*|fakeroot(?:\s+(?:-[is]\s+\S+|-\S+))*|chrt(?:\s+-\S+)*\s+\d+|taskset(?:\s+-\S+)*\s+\S+|unshare(?:\s+(?:-[SGRw]\s+\S+|-\S+))*|chroot(?:\s+-\S+)*\s+\S+|flock(?:\s+(?:-[wE]\s+\S+|-\S+))*\s+\S+|watch(?:\s+(?:-[nd]\s+\S+|-\S+))*)\s+`;
/** Where normalization cut a `$(…)`/backtick substitution out of the text: a non-blank placeholder no check can resolve. */
const CUT = "\u0001";
/** A redirection operator at the start of a shell word (`>`, `2>>`, `&>`, `<>`, `<<<`, `<<-`, `>|`, `>&`). */
const REDIRECT_OP = String.raw`\d*(?:>>?\|?|<>|<<<|<<-?|<|&>>?)&?`;
/** A redirection written before the command word (`2>/dev/null rm …`): PREFIX strips it, the redirection scan still reads it. */
const LEAD_REDIRECT = String.raw`${REDIRECT_OP}\s*(?:"[^"]*"|'[^']*'|\\.|[^\s<>|&;()"'\\])+\s+`;
/**
 * Leading words that do not change what runs: `env [-i] [-u NAME] [-C DIR] …` (up to an `-S` line), `sudo`/`doas`
 * with their flags, `command [-pvV]`, `NAME=value` assignments (quoted values may hold spaces; `GLOBIGNORE=` stays,
 * the glob-dotfiles row reads it), the WRAPPER set, a leading cut substitution and the shell keywords that put a
 * command in a compound (`if`, `then`, `else`, `elif`, `do`, `while`, `until`, `!`, `coproc`). Every wrapper word may
 * be spelled as a path (`/usr/bin/env`, `/usr/bin/sudo`, `…/timeout`), like argv[0]'s basename (T-05-24).
 */
const PREFIX = new RegExp(
  // the assignment value is matched atomically (lookahead + backreference): giving characters back can never reach
  // the whitespace after it, and backtracking through a 1 MB value would cost seconds (WR-09)
  String.raw`(?:(?:\S*\/)?(?:env(?:\s+(?!-[A-Za-z]*S|--split-string)(?:-[A-Za-z]*[uC]\s+\S+|--(?:unset|chdir)\s+\S+|-\S+))*\s+|(?:sudo|doas)(?:\s+(?:${SUDO_VALUE_FLAG}|-\S+))*\s+|pkexec(?:\s+(?:--user\s+\S+|-\S+))*\s+|command(?:\s+-[pvV]+)*\s+|${WRAPPER})|(?:if|then|else|elif|do|while|until|!|coproc)\s+|(?:${CUT}[\uE000-\uF8FF])+(?:\s+|$)|${LEAD_REDIRECT}|(?!GLOBIGNORE=)[A-Za-z_]\w*=(?=(?<value>${VALUE}))\k<value>\s+)`,
  "y",
);
/** `sh -c`, `bash -ec`, `/bin/zsh -x -o pipefail -c`, `fish -c` …: the next word is a command line of its own. */
const SHELL_C = /^(?:\S*\/)?(?:[a-z]*sh|fish)(?:\s+(?:[-+][oO]\s+\S+|--(?:rcfile|init-file)\s+\S+|[-+]\S+))*\s+-[A-Za-z]*c(?:\s+--)?\s+/;
/** A word that runs the rest of its line through the shell again, all its arguments joined (`eval`, `watch`, `parallel`, `sudo -s|-i`). */
const JOINS_ARGS = /^\s*(?:(?:\S*\/)?(?:eval|watch|parallel)\b|(?:\S*\/)?sudo\b.*\s(?:-[A-Za-z]*[si]|--shell|--login)(?:\s|$))/;
/** `env -S LINE` / `--split-string=LINE` once the `env` word was stripped: the rest of the line is the command. */
const SPLIT_STRING = /^(?:-[A-Za-z]*S\s*|--split-string(?:=|\s+))/;
/** Nesting cap for `sh -c "sh -c '…'"` recursion. */
const MAX_LITERAL_DEPTH = 3;

/** The value of the `$'…'` escape whose letter is at `word[i]`, and the index of its last character. */
function ansiEscape(word: string, i: number): [string, number] {
  const c = word[i];
  if (Object.hasOwn(ANSI_ESCAPE, c)) return [ANSI_ESCAPE[c], i];
  const code = /^(?:x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})|([0-7]{1,3}))/.exec(word.slice(i, i + 9));
  if (code !== null) {
    const value = Number.parseInt(code[1] ?? code[2] ?? code[3] ?? code[4], code[4] === undefined ? 16 : 8);
    return [String.fromCodePoint(Math.min(value, 0x10ffff)), i + code[0].length - 1];
  }
  if (c === "c" && i + 1 < word.length) return [String.fromCharCode(word.charCodeAt(i + 1) & 31), i + 1];
  return [`\\${c}`, i];
}
const ANSI_ESCAPE: Record<string, string> = {
  a: "\x07", b: "\b", e: "\x1b", E: "\x1b", f: "\f", n: "\n", r: "\r", t: "\t", v: "\v", "\\": "\\", "'": "'", '"': '"', "?": "?",
};

/**
 * A shell word without its quoting (`'…'` literal; `"…"` and `$"…"` where `\` escapes only `$`, backtick, `"`, `\`
 * and newline; `$'…'` with its C escapes decoded; a bare `\x` is x), and whether every quote it opens is closed.
 */
function dequote(word: string): { text: string; closed: boolean } {
  let text = "";
  let quote: string | undefined;
  for (let i = 0; i < word.length; i++) {
    const c = word[i];
    if (quote === "'") {
      if (c === "'") quote = undefined;
      else text += c;
    } else if (quote === "$'") {
      if (c === "'") quote = undefined;
      else if (c === "\\" && i + 1 < word.length) {
        const [value, end] = ansiEscape(word, i + 1);
        text += value;
        i = end;
      } else text += c;
    } else if (c === "\\" && i + 1 < word.length) {
      const next = word[++i];
      text += quote === '"' && !/[$`"\\\n]/.test(next) ? c + next : next;
    } else if (quote === '"') {
      if (c === '"') quote = undefined;
      else text += c;
    } else if (c === "$" && (word[i + 1] === "'" || word[i + 1] === '"')) quote = word[++i] === "'" ? "$'" : '"';
    else if (c === "'" || c === '"') quote = c;
    else text += c;
  }
  return { text, closed: quote === undefined };
}
const unquote = (word: string): string => dequote(word).text;

/**
 * The shell words of a segment, as written: split on blanks outside quotes, a quoted run (with its blanks) or an
 * escape staying inside its word. With a quote left open (bash would reject the line) the blanks split everywhere.
 */
function shellWords(text: string): string[] {
  const words: string[] = [];
  let cur = "";
  let quote: string | undefined;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote !== undefined) {
      if (c === "\\" && quote !== "'" && i + 1 < text.length) cur += c + text[++i];
      else {
        cur += c;
        if (c === (quote === "$'" ? "'" : quote)) quote = undefined;
      }
    } else if (c === " " || c === "\t" || c === "\n" || c === "\r") {
      if (cur !== "") words.push(cur);
      cur = "";
    } else if (c === "$" && text[i + 1] === "'") {
      cur += "$'";
      quote = "$'";
      i++;
    } else {
      cur += c;
      if (c === "'" || c === '"') quote = c;
      else if (c === "\\" && i + 1 < text.length) cur += text[++i];
    }
  }
  if (quote !== undefined) return text.split(/\s+/).filter((w) => w !== "");
  if (cur !== "") words.push(cur);
  return words;
}

/**
 * Every blank-delimited token that is a plain word once unquoted (`"rm"`, `\sudo`, `r""m`, `'-i'`) written plain, so
 * PREFIX, SHELL_C and the regex rows see the command bash runs. A token whose unquoted form holds a blank, quote,
 * operator, expansion, glob, `=`, `#`, `!` or `~`, or that leaves a quote open, keeps its quotes.
 */
const simplifyWords = (text: string): string =>
  text.replace(/\S+/g, (token) => {
    if (!/["'\\]/.test(token)) return token;
    const { text: plain, closed } = dequote(token);
    return closed && plain !== "" && !/[\s"'\\<>|&;()`$*?[\]{}=#!~\u0001]/.test(plain) ? plain : token;
  });

/** The value of a `-c LINE` / `-cLINE` / `--command[=]LINE` option among `words` (unquoted). */
function dashC(words: string[]): string | undefined {
  for (let i = 0; i < words.length; i++) {
    const w = unquote(words[i]);
    if (w === "-c" || w === "--command") return words[i + 1] === undefined ? undefined : unquote(words[i + 1]);
    const m = /^(?:-c|--command=)(.+)$/.exec(w);
    if (m !== null) return m[1];
  }
  return undefined;
}

/**
 * The command lines a segment hands to a shell, now or later: the word after `sh -c` (and kin; the words after it are
 * `$0`, `$1` …), the `-c` value of `su|runuser|script` and of a stripped `flock FILE`, every `alias NAME=LINE` value, the
 * `trap` action, and an `env -S` line (its string split, the remaining words appended). eval/watch/parallel/`sudo -s`,
 * which join all their arguments, are normalizeSegments' (JOINS_ARGS).
 */
function commandLines(seg: string): string[] {
  const shell = SHELL_C.exec(seg);
  if (shell !== null) {
    const word = shellWords(seg.slice(shell[0].length))[0];
    return word === undefined ? [] : [unquote(word)];
  }
  const split = SPLIT_STRING.exec(seg);
  if (split !== null) {
    const [first = "", ...rest] = shellWords(seg.slice(split[0].length));
    return [[unquote(first), ...rest].join(" ")];
  }
  const words = shellWords(seg);
  const first = unquote(words[0] ?? "");
  const cmd = posix.basename(first);
  if (first.startsWith("-c") || first.startsWith("--command") || /^(?:su|runuser|script)$/.test(cmd)) {
    const line = dashC(first.startsWith("-") ? words : words.slice(1));
    return line === undefined ? [] : [line];
  }
  if (cmd === "alias") return words.slice(1).map(unquote).filter((w) => /^[^=\s-][^=\s]*=/.test(w)).map((w) => w.slice(w.indexOf("=") + 1));
  if (cmd === "trap") {
    const action = words.slice(1).find((w) => w !== "--");
    return action === undefined || /^-[lp]$/.test(action) ? [] : [unquote(action)];
  }
  return [];
}

/** A heredoc operator and its delimiter word (`<<EOF`, `<<-'EOF'`, `<< "EOF"`); sticky, positioned by splitTopLevel. */
const HEREDOC = /<<-?[ \t]*(?:"([^"\n]*)"|'([^'\n]*)'|([^\s<>|&;()]+))/y;

/** The shell operator that ends a split piece; a newline reads as `;`, the end of the text as `""`. */
type Op = ";" | "&&" | "||" | "|" | "&" | "(" | ")" | "{" | "}" | "";
/** A split piece, the operator that ends it, and the heredoc body its line opened (fed to it on stdin). */
interface Piece {
  text: string;
  end: Op;
  body?: string;
}

/**
 * Split on `;`, `&&`, `||`, `|`, a lone `&`, newlines and `(`/`)`/`{ `/` }` grouping outside quotes, keeping the
 * operator that ends each piece (matchRule's directory tracking reads the structure). Only an unquoted, unescaped
 * `>`/`<` makes a following `|` or `&` part of a redirection (`>|`, `2>&1`); an extglob group (`@(a|b)`, `x!(y)`)
 * stays inside its word. Heredoc bodies and `#` comments are data, not commands: they are skipped without quote
 * tracking (an apostrophe in them must not swallow the commands after them), and a body is kept on its piece. An
 * unbalanced quote at the end re-splits its tail with quotes off.
 */
function splitTopLevel(text: string, quotesOn = true): Piece[] {
  const out: Piece[] = [];
  let cur = "";
  let blank = true; // cur holds only whitespace
  let quote: string | undefined;
  let quoteAt = 0;
  let quoteOut = 0;
  let quoteCur = "";
  let heredoc: string | undefined;
  let redirect = false; // the previous character is an unquoted, unescaped `<` or `>`
  let plain = ""; // the previous character when it was a plain one (not quoted, escaped or an operator)
  let extglob = 0; // open extglob groups, whose `|` and parentheses belong to the word
  const boundary = (end: Op) => {
    out.push({ text: cur, end });
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
    const afterRedirect = redirect;
    const before = plain;
    redirect = false;
    plain = "";
    if (quote !== undefined) {
      // an escaped character never closes the quote, and stays in the text as written
      if (c === "\\" && quote !== "'" && i + 1 < text.length) cur += c + text[++i];
      else {
        cur += c;
        if (c === (quote === "$'" ? "'" : quote)) quote = undefined;
      }
      continue;
    }
    if (quotesOn && (c === "'" || c === '"')) {
      // `$'…'` (ANSI-C quoting) lets `\'` escape its closing quote
      quote = c === "'" && before === "$" ? "$'" : c;
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
      boundary(";");
      if (heredoc !== undefined) {
        const end = heredocEnd(i + 1);
        out[out.length - 1].body = text.slice(i + 1, end);
        i = end;
        heredoc = undefined;
      }
    } else if (extglob > 0 && (c === "(" || c === ")" || c === "|")) {
      cur += c;
      if (c !== "|") extglob += c === "(" ? 1 : -1;
    } else if (c === "(" && /[@!+*?]/.test(before) && !(before === "!" && cur.trim() === "!")) {
      // an extglob group `@(…)`, `x!(…)`, `*(…)`: part of the word, not a subshell (`! (…)` negates one)
      extglob = 1;
      cur += c;
    } else if (c === "|" && afterRedirect && text[i - 1] === ">") {
      // `>|`, `2>|`, `&>|`: a noclobber-override redirection, not a pipe
      cur += c;
    } else if (c === ";" || c === "|" || (c === "&" && text[i + 1] === "&")) {
      const double = c !== ";" && text[i + 1] === c;
      if (double) i++;
      // `|&` pipes stderr too: one pipe
      else if (c === "|" && text[i + 1] === "&") i++;
      boundary(c === ";" ? ";" : c === "&" ? "&&" : double ? "||" : "|");
    } else if (c === "&" && text[i + 1] !== ">" && !afterRedirect) {
      // a lone `&` runs what precedes it in the background (`>&`, `&>`, `2>&1` are redirections)
      boundary("&");
    } else if (c === "(" || c === ")") {
      // a subshell `( … )`: its body is its own segment list (`$(…)` was cut out before the split)
      boundary(c);
    } else if (c === "{" && blank && (i + 1 === text.length || /\s/.test(text[i + 1]))) {
      // a brace group `{ …; }` opener as a word of its own (`${VAR}` and `{a,b}` are glued, never split)
      boundary("{");
    } else if (c === "}" && (i === 0 || /[\s;]/.test(text[i - 1])) && (i + 1 === text.length || /[\s;&|)]/.test(text[i + 1]))) {
      boundary("}");
    } else {
      cur += c;
      if (blank && !/\s/.test(c)) blank = false;
      redirect = c === "<" || c === ">";
      plain = c;
    }
  }
  boundary("");
  if (quote !== undefined) {
    // bash would reject an unterminated quote; a heredoc or comment the scan missed is the likelier reading
    const tail = splitTopLevel(text.slice(quoteAt + 1), false);
    out.length = quoteOut;
    tail[0] = { ...tail[0], text: `${quoteCur}${quote}${tail[0].text}` };
    out.push(...tail);
  }
  return out;
}

/** Operands a wrapper supplies at run time: `xargs`/`parallel` append them, `-I R`/`{}` put them into words holding R. */
interface RuntimeArgs {
  append: boolean;
  replace: string[];
}

/** One split piece after normalization (`text` is "" for the empty piece before a `(`/`{` or after a `)`/`}`). */
interface Normalized {
  /** The command text, CUT marking where a substitution was cut out. */
  text: string;
  /** The operator that ends the piece. */
  end: Op;
  /** Directories its stripped `env -C`/`sudo -D`/`unshare -w`/`chroot` prefixes run it in, as written (CUT: unknown). */
  chdirs: string[];
  /** A wrapper, assignment or keyword was stripped: a `cd` behind it may not move this shell. */
  prefixed: boolean;
  /** It opens a loop (`while`/`until`/`for`/`select`) or an `if`/`case`, or closes one (`fi`/`done`/`esac`). */
  compound: Compound | "close" | undefined;
  /** What it runs in a child shell: an `sh -c` (and kin) line, `find -exec` commands, a script fed to a shell's stdin. */
  payload: Normalized[];
  runtime: RuntimeArgs | undefined;
  /** The stripped prefix (its redirections, `2>/dev/null rm …`, still write). */
  prefix: string;
  /** The command word was a cut substitution (`$(which rm) -rf x`): what runs cannot be known here. */
  opaque: boolean;
}
type Compound = "loop" | "if" | "case";
/** The compound keyword a piece starts with, before prefix stripping. */
const COMPOUND_WORD = /^(?:(while|until|for|select)|(if)|(case)|(fi|done|esac))(?=[\s;]|$)/;

/**
 * D-03 normalization: the inner text of every `$(…)` / backtick substitution becomes its own command, the rest is
 * split on `;`, `&&`, `||`, `|`, `&` (outside quotes), plain quoted words are unquoted, and each segment loses leading
 * `env X=…`, `X=…`, `sudo`, `command`, wrapper words and shell keywords (`if`, `then`, `do`, `!` …). What a segment
 * runs in a child shell is normalized in turn and its segments appended (the outer segment stays too): a literal
 * `sh -c "…"` / `eval "…"` / `su -c` / `env -S` line, `find -exec` commands, and a heredoc, here-string or `echo`
 * fed to a shell reading its script from stdin. `bash -c "$VAR"` is opaque by design (the documented residual).
 */
export function normalize(command: string): string[] {
  const texts: string[] = [];
  const collect = (items: Normalized[]) => {
    for (const item of items) {
      const text = item.text.replace(CUT_TOKEN, " ").trim();
      if (text !== "") texts.push(text);
      collect(item.payload);
    }
  };
  collect(normalizeSegments(command, 0, []));
  return texts;
}

/** A cut substitution in normalized text: CUT and a private-use character naming its body in the command's cut table. */
const CUT_TOKEN = /\u0001([\uE000-\uF8FF])/g;
/** Substitutions cut per command (the private-use range holds 6400). */
const MAX_CUTS = 4096;

/**
 * Normalize `command` (see normalize). A `$(…)`/backtick substitution is cut out into `cuts` and leaves CUT plus the
 * index of its body in its place; the body becomes a payload (a subshell) of the piece that holds it, so it runs in
 * the directory of that piece and a `cd` inside it moves nothing outside. A body whose place no piece holds (a
 * heredoc body is its piece's; a comment is none) runs in an unknown directory.
 */
function normalizeSegments(command: string, depth: number, cuts: string[]): Normalized[] {
  // a backslash-newline continues the line: `git \` ⏎ `push --force` is one command
  // `${IFS}` / `$IFS` separate words exactly like a blank
  let rest = command.replace(/\\\r?\n/g, " ").replace(/\$\{IFS\}|\$IFS(?!\w)/g, " ");
  const first = cuts.length;
  const sub = /\$\(([^()]*)\)|`([^`]*)`/;
  for (let m = sub.exec(rest), n = 0; m !== null && n < 256 && cuts.length < MAX_CUTS; m = sub.exec(rest), n++) {
    const marker = `${CUT}${String.fromCharCode(0xe000 + cuts.length)}`;
    cuts.push(m[1] ?? m[2] ?? "");
    rest = `${rest.slice(0, m.index)}${marker}${rest.slice(m.index + m[0].length)}`;
  }
  const items: Normalized[] = [];
  const attached = new Set<number>();
  /** The bodies of the substitutions `text` holds, normalized, as payload of `item`. */
  const attach = (item: Normalized, text: string) => {
    for (const m of text.matchAll(CUT_TOKEN)) {
      const n = m[1].charCodeAt(0) - 0xe000;
      attached.add(n);
      if (n < cuts.length) item.payload.push(...normalizeSegments(cuts[n], depth, cuts));
    }
  };
  const built: { item: Normalized; body: string | undefined }[] = [];
  for (const piece of splitTopLevel(rest)) {
    let seg = simplifyWords(piece.text.trim());
    const keyword = COMPOUND_WORD.exec(seg);
    const compound = keyword === null ? undefined : keyword[1] ? "loop" : keyword[2] ? "if" : keyword[3] ? "case" : "close";
    // sticky: each match starts where the last one ended, so k prefixes cost O(n), not O(k·n) (WR-09)
    let at = 0;
    let joinFrom = -1;
    for (PREFIX.lastIndex = 0; PREFIX.test(seg); at = PREFIX.lastIndex) {
      if (joinFrom === -1 && JOINS_ARGS.test(seg.slice(at, PREFIX.lastIndex))) joinFrom = PREFIX.lastIndex;
    }
    // eval (and kin) joins every argument after it, each unquoted (a leading `--` aside), and runs the result as a new command line
    const joinedWords = joinFrom === -1 ? [] : shellWords(seg.slice(joinFrom)).map(unquote);
    const joined = joinFrom === -1 ? undefined : (joinedWords[0] === "--" ? joinedWords.slice(1) : joinedWords).join(" ");
    const prefix = seg.slice(0, at);
    const info = at === 0 ? undefined : prefixInfo(prefix);
    seg = seg.slice(at);
    // `sudo -e FILE` edits FILE
    if (info?.edit === true && seg !== "") seg = `sudoedit ${seg}`;
    let payload: Normalized[] = [];
    if (seg !== "" && depth < MAX_LITERAL_DEPTH) {
      for (const line of [...commandLines(seg), ...(joined === undefined ? [] : [joined])]) payload.push(...normalizeSegments(line, depth + 1, cuts));
      if (/^(?:\S*\/)?find\s/.test(seg)) payload.push(...findCommands(seg, depth, cuts));
    }
    const runtime = info?.runtime;
    if (runtime !== undefined) payload = withReplace(payload, runtime.replace);
    const opaque = /(?:^|\s)(?:\u0001[\uE000-\uF8FF])+(?:\s|$)/.test(prefix);
    const item: Normalized = { text: seg, end: piece.end, chdirs: info?.chdirs ?? [], prefixed: at > 0, compound, payload, runtime, prefix, opaque };
    attach(item, `${prefix} ${seg} ${piece.body ?? ""}`);
    items.push(item);
    built.push({ item, body: piece.body });
  }
  // a script fed to a shell's stdin (`bash <<EOF`, `sh <<< '…'`, `echo … | sh`) runs like `sh -c`
  if (depth < MAX_LITERAL_DEPTH && built.some(({ item }) => readsStdinScript(item.text))) {
    for (const { item, body } of built) {
      const hereString = /<<<\s*((?:"[^"]*"|'[^']*'|\\.|[^\s<>|&;()"'\\])+)/.exec(`${item.prefix} ${item.text}`)?.[1];
      const echoed = /^(?:echo|printf)(?:\s|$)/.test(item.text) ? shellWords(item.text).slice(1).map(unquote).join(" ") : undefined;
      for (const fed of [body, hereString === undefined ? undefined : unquote(hereString), echoed]) {
        if (fed !== undefined) item.payload.push(...normalizeSegments(fed, depth + 1, cuts));
      }
    }
  }
  for (let n = first; n < cuts.length; n++) {
    if (attached.has(n) || cuts.slice(first).some((body) => body.includes(`${CUT}${String.fromCharCode(0xe000 + n)}`))) continue;
    for (const item of normalizeSegments(cuts[n], depth, cuts)) items.unshift({ ...item, chdirs: [CUT, ...item.chdirs] });
  }
  return items;
}

/** A shell that reads its script from stdin: no script operand, `-s`, or `-`/`/dev/stdin`; `source`/`.` of stdin. */
function readsStdinScript(seg: string): boolean {
  if (!/^(?:\S*\/)?(?:[a-z]*sh|fish|source|\.)(?:\s|$)/.test(seg)) return false;
  const words = shellWords(seg).map(unquote);
  const stdin = (w: string | undefined) => w === "-" || w === "/dev/stdin" || w === "/proc/self/fd/0";
  const cmd = posix.basename(words[0] ?? "");
  if (cmd === "source" || cmd === ".") return stdin(words[1]);
  for (let i = 1; i < words.length; i++) {
    const w = words[i];
    // a redirection (`<<EOF`, `<<< 'x'`, `2>/dev/null`) is not a script operand; a bare operator takes the next word
    if (/^\d*(?:<<<|<<-?|<>|<|>>?|&>>?)$/.test(w)) i++;
    else if (/^\d*[<>&]/.test(w)) continue;
    else if (/^(?:[-+][oO]|--rcfile|--init-file)$/.test(w)) i++;
    else if (/^[-+]./.test(w)) {
      if (/^-[A-Za-z]*c/.test(w)) return false;
      if (/^-[A-Za-z]*s/.test(w)) return true;
    } else return stdin(w);
  }
  return true;
}

/** `items` and their payloads with words holding one of `replace` read as run-time operands. */
function withReplace(items: Normalized[], replace: string[]): Normalized[] {
  return items.map((item) => ({
    ...item,
    runtime: { append: item.runtime?.append ?? false, replace: [...(item.runtime?.replace ?? []), ...replace] },
    payload: withReplace(item.payload, replace),
  }));
}

/**
 * The commands `find … -exec|-execdir|-ok|-okdir CMD … ;|+` runs, each found path (`{}`) a run-time operand;
 * `-execdir`/`-okdir` run them in directories that cannot be known here.
 */
function findCommands(seg: string, depth: number, cuts: string[]): Normalized[] {
  const words = shellWords(seg);
  const out: Normalized[] = [];
  for (let i = 1; i < words.length; i++) {
    const action = unquote(words[i]);
    if (!/^-(?:exec|execdir|ok|okdir)$/.test(action)) continue;
    let j = i + 1;
    while (j < words.length && !/^[;+]$/.test(unquote(words[j]))) j++;
    for (const item of withReplace(normalizeSegments(words.slice(i + 1, j).join(" "), depth + 1, cuts), ["{}"])) {
      out.push(action.endsWith("dir") ? { ...item, chdirs: [CUT, ...item.chdirs] } : item);
    }
    i = j;
  }
  return out;
}

/** Per wrapper: its chdir option with the directory attached (`-CDIR`, `--chdir=DIR`) and as a word of its own. */
const CHDIR_OPTION: Record<string, readonly [RegExp, RegExp]> = {
  env: [/^(?:--chdir=|-[A-Za-z]*C(?=.))(.*)$/, /^(?:--chdir|-[A-Za-z]*C)$/],
  sudo: [/^(?:--chdir=|-[A-Za-z]*D(?=.))(.*)$/, /^(?:--chdir|-[A-Za-z]*D)$/],
  doas: [/^(?:--chdir=|-[A-Za-z]*D(?=.))(.*)$/, /^(?:--chdir|-[A-Za-z]*D)$/],
  unshare: [/^(?:--wd=|-w(?=.))(.*)$/, /^(?:--wd|-w)$/],
};

/**
 * What a stripped prefix implies for its command: the directories it runs in, in order (`env -C DIR` / `-CDIR` /
 * `-iC DIR` / `--chdir[=]DIR`, `sudo`/`doas` `-D`/`--chdir`, `unshare -w`/`--wd`; `chroot` and `unshare -R` make it
 * unknown), the operands `xargs` (`-I R`, `-i`, `--replace[=R]`) and `parallel` supply at run time, and a `sudo -e`.
 */
function prefixInfo(prefix: string): { chdirs: string[]; runtime: RuntimeArgs | undefined; edit: boolean } {
  if (!/-|\b(?:xargs|parallel|chroot)\b/.test(prefix)) return { chdirs: [], runtime: undefined, edit: false };
  const words = shellWords(prefix).map(unquote);
  const info: { chdirs: string[]; runtime: RuntimeArgs | undefined; edit: boolean } = { chdirs: [], runtime: undefined, edit: false };
  let wrapper = "";
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    if (!w.startsWith("-")) {
      const name = w.includes("=") ? "" : posix.basename(w);
      if (/^(?:env|sudo|doas|unshare|xargs|parallel|chroot|exec|nice|timeout|nohup|setsid|watch|flock|stdbuf|ionice|chrt|taskset|fakeroot|busybox|toybox|command|builtin|eval|time)$/.test(name)) {
        wrapper = name;
        if (name === "chroot") info.chdirs.push(CUT);
        if (name === "xargs") info.runtime = { append: true, replace: [] };
        if (name === "parallel") info.runtime = { append: true, replace: ["{"] };
      }
      continue;
    }
    const option = Object.hasOwn(CHDIR_OPTION, wrapper) ? CHDIR_OPTION[wrapper] : undefined;
    const attached = option?.[0].exec(w);
    if (attached) info.chdirs.push(attached[1]);
    else if (option?.[1].test(w) && words[i + 1] !== undefined) info.chdirs.push(words[++i]);
    else if ((wrapper === "sudo" || wrapper === "doas") && /^(?:-[A-Za-z]*e|--edit)$/.test(w)) info.edit = true;
    else if (wrapper === "unshare" && /^(?:-R|--root)(?:=|$)/.test(w)) info.chdirs.push(CUT);
    else if (wrapper === "xargs") {
      const replace = w === "-I" ? words[++i] : /^(?:-I|-i|--replace=)(.+)$/.exec(w)?.[1] ?? (w === "-i" || w === "--replace" ? "{}" : undefined);
      if (replace !== undefined && replace !== "") info.runtime = { append: false, replace: [replace] };
    }
  }
  return info;
}

/** A redirection shell word (`2>/dev/null`, `>&2`, `<<EOF`); a bare operator's target is the next word. */
const REDIRECT_WORD = new RegExp(`^${REDIRECT_OP}`);

/**
 * The segment the rows read from a normalized piece: its words unquoted, redirection words dropped (the redirection
 * scan reads their targets from `marked`), unquoted brace lists expanded (`{touch,.swarm/x}`, `cp a{,.bak}`) and
 * run-time operands marked CUT; `cwd` is set by the caller.
 */
function parseSegment(item: Pick<Normalized, "text" | "runtime" | "prefix" | "opaque">): Segment {
  const all: string[] = [];
  const raw = shellWords(item.text);
  for (let i = 0; i < raw.length; i++) {
    const op = REDIRECT_WORD.exec(raw[i]);
    if (op !== null) {
      if (op[0] === raw[i]) i++;
      continue;
    }
    const word = unquote(raw[i]);
    all.push(...(/["'\\]/.test(raw[i]) ? [word] : (braceAlternatives(word) ?? [word])));
  }
  const argv0 = all[0] ?? "";
  if (all.length > 0) all[0] = posix.basename(all[0]);
  const { runtime } = item;
  const targetWords =
    runtime === undefined
      ? all
      : [...all.map((w, i) => (i > 0 && runtime.replace.some((r) => w.includes(r)) ? CUT : w)), ...(runtime.append ? [CUT] : [])];
  const words = all.filter((w, i) => i === 0 || !/^(?:\u0001[\uE000-\uF8FF]?)+$/.test(w));
  // a command named by an expansion or a glob (`$RM`, `/bin/r?`, `$(which rm)`) cannot be known: all its operands count
  const opaque = item.opaque || (argv0 !== "[" && argv0 !== "[[" && /[$`\u0001*?[]/.test(argv0));
  const text = item.text.replace(CUT_TOKEN, " ").trim();
  return { text, words, argv0, targetWords, marked: `${item.prefix} ${item.text}`, opaque, cwd: undefined };
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

/** A target that `~`/`$HOME` expansion makes absolute. */
const HOME_PREFIX = /^(?:~|\$HOME|\$\{HOME\})(?=\/|$)/;

/** Expand `~`/`$HOME` and resolve against cwd. */
export function resolvePath(p: string, facts: Pick<GuardFacts, "cwd" | "home">): string {
  const expanded = p.replace(HOME_PREFIX, facts.home);
  return posix.resolve(facts.cwd || "/", expanded);
}
const within = (path: string, dir: string) => dir !== "" && dir !== "/" && path.startsWith(`${dir}/`);
/** Where a segment's paths resolve: its tracked directory, else the session cwd (protectedTarget fails closed first). */
const segmentBase = (seg: Segment, facts: GuardFacts) => ({ cwd: seg.cwd ?? facts.cwd, home: facts.home });

/** The D-08 state directories (compared in any case: a case-insensitive file system maps `.Swarm` onto `.swarm`). */
const PROTECTED_DIRS = [".swarm", ".omp"];
/** Device sinks a write may always name. */
const SAFE_SINK = /^\/dev\/(?:null|zero|full|stdout|stderr|tty|fd\/\d+)$/;
/** A glob character or an extglob group in a word (brace lists are expanded first, by braceAlternatives). */
const GLOB_CHAR = /[*?[]|[@!+]\(/;
/** More brace alternatives than this in one word leave it unjudgeable. */
const MAX_ALTERNATIVES = 64;

/** The first brace group bash expands in `word`: its bounds and its comma parts (none for a `{a..z}` sequence). */
function braceGroup(word: string): { start: number; end: number; parts: string[] | undefined } | undefined {
  for (let start = word.indexOf("{"); start !== -1; start = word.indexOf("{", start + 1)) {
    if (word[start - 1] === "$") continue;
    const parts: string[] = [];
    let depth = 0;
    let from = start + 1;
    for (let i = start; i < word.length; i++) {
      if (word[i] === "{") depth++;
      else if (word[i] === "," && depth === 1) {
        parts.push(word.slice(from, i));
        from = i + 1;
      } else if (word[i] === "}" && --depth === 0) {
        parts.push(word.slice(from, i));
        if (parts.length > 1) return { start, end: i, parts };
        if (/\.\./.test(parts[0])) return { start, end: i, parts: undefined };
        break;
      }
    }
  }
  return undefined;
}

/** The words brace expansion makes of `word` (a `{a..z}` sequence read as `*`); undefined past MAX_ALTERNATIVES. */
function braceAlternatives(word: string): string[] | undefined {
  const done: string[] = [];
  const todo = [word];
  for (let next = todo.pop(); next !== undefined; next = todo.pop()) {
    const group = braceGroup(next);
    if (group === undefined) done.push(next);
    else {
      const [head, tail] = [next.slice(0, group.start), next.slice(group.end + 1)];
      // pushed last-first, so they come off the stack (and out) in written order, as bash expands them
      for (const part of [...(group.parts ?? ["*"])].reverse()) todo.push(head + part + tail);
    }
    if (done.length + todo.length > MAX_ALTERNATIVES) return undefined;
  }
  return done;
}

/** One path component's glob as a case-insensitive regex; an extglob group matches anything (a superset). */
function globRegex(pattern: string): RegExp {
  let re = "";
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (/[@!+*?]/.test(c) && pattern[i + 1] === "(") {
      let depth = 0;
      for (i++; i < pattern.length; i++) {
        if (pattern[i] === "(") depth++;
        else if (pattern[i] === ")" && --depth === 0) break;
      }
      re += ".*";
    } else if (c === "*") re += ".*";
    else if (c === "?") re += ".";
    else if (c === "[" && pattern.indexOf("]", i + 2) !== -1) {
      const close = pattern.indexOf("]", i + 2);
      const body = pattern.slice(i + 1, close).replace(/^[!^]/, "^").replace(/\\/g, "\\\\");
      re += `[${body}]`;
      i = close;
    } else re += c.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
  }
  try {
    return new RegExp(`^${re}$`, "i");
  } catch {
    return /^.*$/;
  }
}

/**
 * `pattern` may name `name`: a plain component equals it in any case, a glob matches it. A leading dot is matched only
 * by a literal one, a bracket or an extglob group (bash without dotglob, which the glob-dotfiles row keeps off).
 */
function mayName(pattern: string, name: string): boolean {
  if (!GLOB_CHAR.test(pattern)) return pattern.toLowerCase() === name.toLowerCase();
  if (name.startsWith(".") && !/^(?:\.|\[|[@!+*?]\()/.test(pattern)) return false;
  return globRegex(pattern).test(name);
}

/**
 * D-08 over a shell target, resolved in the segment's directory: a protected path (isProtectedPath), including any
 * brace alternative or glob that may name one; and, failing closed, any target that cannot be judged from the text —
 * a variable or cut substitution (`"$f"`, `$(…)/x`; `$HOME`, `$TMPDIR` and `$PWD` prefixes are expanded), `~user`,
 * `~+`, `~-`, a run-time operand (`xargs`, `find -exec {}`), a glob component that may name `..`, a word of more than
 * MAX_ALTERNATIVES alternatives, or a relative target while the directory is unknown (`cd "$DIR"`, `cd -`).
 */
function protectedTarget(target: string, seg: Segment, facts: GuardFacts): boolean {
  if (SAFE_SINK.test(target)) return false;
  let path = target;
  if (facts.tmp !== "") path = path.replace(/^(?:\$TMPDIR|\$\{TMPDIR\})(?=\/|$)/, facts.tmp);
  if (seg.cwd !== undefined) path = path.replace(/^(?:\$PWD|\$\{PWD\})(?=\/|$)/, seg.cwd || "/");
  const rest = path.replace(HOME_PREFIX, "");
  if (/[$`\u0001]/.test(rest) || rest.startsWith("~") || (HOME_PREFIX.test(path) && facts.home.includes(CUT))) return true;
  if (seg.cwd === undefined && !path.startsWith("/") && !HOME_PREFIX.test(path)) return true;
  const alternatives = braceAlternatives(path);
  if (alternatives === undefined) return true;
  const base = segmentBase(seg, facts);
  return alternatives.some((alt) => {
    const resolved = resolvePath(alt, base);
    // `/proc/<pid>/cwd|root|fd/…` reach a directory or file the text does not name
    if (/^\/proc\/[^/]+\/(?:cwd|root|fd|map_files)(?:\/|$)/.test(resolved)) return true;
    if (!GLOB_CHAR.test(alt)) return isProtectedPath(alt, facts, base.cwd);
    const parts = resolved.split("/");
    if (parts.some((p) => GLOB_CHAR.test(p) && mayName(p, ".."))) return true;
    if (parts.some((p) => PROTECTED_DIRS.some((name) => mayName(p, name)))) return true;
    return guardedRoots(facts).some((root) => {
      const names = root.split("/");
      return names.length <= parts.length && names.every((name, i) => mayName(parts[i], name));
    });
  });
}

/** A `.git`, `.swarm` or `.omp` component, in any case or through a glob, anywhere in the path. */
const repoStateSegment = (path: string) => path.split("/").some((p) => [".git", ...PROTECTED_DIRS].some((name) => mayName(p, name)));

/** An `rm -rf` target that is `/`, `~`, the session cwd or the tmp dir itself, or outside both subtrees (incl. `..`). */
function outsideCwd(target: string, seg: Segment, facts: GuardFacts): boolean {
  const path = resolvePath(target, segmentBase(seg, facts));
  return !within(path, posix.resolve(facts.cwd || "/")) && !within(path, facts.tmp === "" ? "" : posix.resolve(facts.tmp));
}

/**
 * `rm` that is universal-destructive: `-r -f` of `/`, `~`, cwd, the tmp dir or anything outside those two subtrees;
 * `-r` alone of a `.git`, `.swarm` or `.omp` path; and any `rm` of a protected path (the D-08 state dirs, a guarded
 * runtime root, or a target that cannot be judged), whatever the flags. Brace alternatives count one by one.
 */
function rmProtected(seg: Segment, facts: GuardFacts): boolean {
  const { targetWords: words } = seg;
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
  return targets.some((t) => {
    if (protectedTarget(t, seg, facts)) return true;
    if (!recursive) return false;
    return (braceAlternatives(t) ?? []).some((alt) => {
      // a repo-state dir as written, or on the way from the session cwd once resolved (`cd .git && rm -r objects`)
      const fromSession = posix.relative(facts.cwd || "/", resolvePath(alt, segmentBase(seg, facts)));
      return repoStateSegment(alt) || repoStateSegment(fromSession) || (force && outsideCwd(alt, seg, facts));
    });
  });
}

/** The index of the first word naming one of `tools` (its basename; `npx vercel@latest` counts), -1 when none. */
function toolAt(words: string[], tools: RegExp): number {
  return words.findIndex((w) => tools.test(posix.basename(w).replace(/@[^/]*$/, "")));
}

/** `gh`'s positional words (the values of `-R`/`--repo`/`--hostname` skipped); undefined when no `gh` runs. */
function ghArgs(words: string[]): string[] | undefined {
  const at = toolAt(words, /^gh$/);
  if (at === -1) return undefined;
  const out: string[] = [];
  for (let i = at + 1; i < words.length; i++) {
    if (/^(?:-R|--repo|--hostname)$/.test(words[i])) i++;
    else if (!words[i].startsWith("-")) out.push(words[i]);
  }
  return out;
}

/**
 * `git push` of tags or of every branch (`--all`, `--branches`, `--mirror`), or to main/master or a release tag (the
 * refspec destination, any case; `+main`, `:main`, `--delete main` included); and merging a pull request (`gh pr merge`,
 * `gh api …/merge`), which moves its base branch.
 */
function pushToProtected(seg: Segment): boolean {
  const gh = ghArgs(seg.words);
  if (gh !== undefined && ((gh[0] === "pr" && gh[1] === "merge") || (gh[0] === "api" && gh.some((a) => /\/pulls\/\d+\/merge$|\/merges$/.test(a))))) return true;
  const { sub, args } = git(seg.words);
  if (sub !== "push") return false;
  if (args.some((a) => /^--(?:tags|follow-tags|all|branches|mirror)$/.test(a))) return true;
  // the first operand is the remote, unless `--repo` named it
  const refs = operands(args).slice(args.some((a) => a.startsWith("--repo")) ? 0 : 1);
  return refs.some((ref) => {
    const dest = (ref.split(":").pop() ?? "").replace(/^\+/, "").replace(/^refs\/(?:heads|tags)\//, "").toLowerCase();
    return dest === "main" || dest === "master" || RELEASE_TAG.test(dest);
  });
}

/** Database clients a DDL statement in the command runs through. */
const DB_CLIENT = /^(?:psql|pgcli|mysql|mariadb|mycli|sqlite3|litecli|duckdb|prisma|mongosh|mongo|redis-cli|cockroach|clickhouse(?:-client)?|usql)$/;
/** Statements that drop or empty data: `DROP <object>`, `TRUNCATE`, Redis `FLUSHALL|FLUSHDB`, Mongo `dropDatabase()`/`.drop()`. */
const DDL =
  /\b(?:DROP\s+(?:TABLE|COLUMN|DATABASE|SCHEMA|INDEX|VIEW|MATERIALIZED\s+VIEW|SEQUENCE|TYPE|FUNCTION|PROCEDURE|TRIGGER|EXTENSION|ROLE|USER|OWNED)|TRUNCATE|FLUSH(?:ALL|DB)|dropDatabase)\b|\.drop\(\)/i;
/** Registry publishing: the tool and the verb after it that uploads (`npm publish`, `twine upload`, `gem push` …). */
const PUBLISH_VERB: Record<string, RegExp> = {
  ...Object.fromEntries(["npm", "pnpm", "yarn", "bun", "lerna", "changeset", "jsr", "deno", "cargo", "poetry", "uv", "hatch", "flit"].map((t) => [t, /^publish$/])),
  twine: /^upload$/,
  gem: /^push$/,
  dotnet: /^push$/,
  mvn: /^deploy(?::deploy)?$/,
  mvnw: /^deploy(?::deploy)?$/,
  gradle: /^publish(?!ToMavenLocal)\w*$/,
  gradlew: /^publish(?!ToMavenLocal)\w*$/,
};
/** A chmod mode that leaves files writable by others: octal with the write bit in its last digit, or `o`/`a`/no-who gaining `w`. */
const worldWritable = (mode: string) => (/^[0-7]{3,4}$/.test(mode) ? /[2367]$/.test(mode) : /(?:^|,)(?:[ugo]*[oa][ugoa]*)?[+=][rwxXst]*w/.test(mode));

const reason = (capability: string, id: string) => `BLOCKED needs: human-approval (${capability}: ${id})`;

/**
 * D-08: a path, after `~` expansion and resolution against `base` (default the session cwd), inside `.swarm/` or
 * `.omp/` (cwd's, ~/.omp, or any other; any case), or inside a runtime root the session's cwd is not in: a swarm
 * session never rewrites the running guard (`omp/`) or the gate scripts the runner later runs with keys (`scripts/`).
 * A session working inside the agent-swarm checkout itself (self-development) is exempt (the AGENTS.md residual).
 */
export function isProtectedPath(path: string, facts: Pick<GuardFacts, "cwd" | "home" | "runtimeRoots">, base = facts.cwd): boolean {
  const resolved = resolvePath(path, { cwd: base, home: facts.home }).toLowerCase();
  if (resolved.split("/").some((s) => PROTECTED_DIRS.includes(s))) return true;
  return guardedRoots(facts).some((root) => resolved === root || resolved.startsWith(`${root}/`));
}

/** The runtime roots the session's cwd is not in (lower-cased; never `/`): the ones D-08 protects. */
function guardedRoots(facts: Pick<GuardFacts, "cwd" | "runtimeRoots">): string[] {
  const cwd = posix.resolve(facts.cwd || "/");
  return facts.runtimeRoots.flatMap((r) => {
    const root = posix.resolve(r);
    return root === "/" || cwd === root || within(cwd, root) ? [] : [root.toLowerCase()];
  });
}

/** The values of `flags` (`-C dir`) and of `long=` (`--directory=dir`) in `words`. */
function optionValues(words: string[], flags: string[], long?: string): string[] {
  const out: string[] = [];
  for (let i = 1; i < words.length; i++) {
    if (flags.includes(words[i]) && words[i + 1] !== undefined) out.push(words[++i]);
    else if (long !== undefined && words[i].startsWith(`${long}=`)) out.push(words[i].slice(long.length + 1));
  }
  return out;
}

/**
 * A redirection and its target word: `>`, `>>`, `>|`, `n>`, `&>`, `<>` (read-write), `>&file`; group 4 is the `&` of
 * `>&`, group 5 the target (quoted runs and escapes included).
 */
const REDIRECT = /(?:^|[^<>&\d])(\d*|&)(>>?|<>)(\|?)(&?)\s*((?:"[^"]*"|'[^']*'|\\.|[^\s<>|&;()"'\\])+)/g;
/** Per command, the options whose value is a file or directory it writes; any other command's `-o` writes nothing (`grep -o`). */
const OUTPUT_FLAGS: Record<string, readonly string[]> = Object.fromEntries([
  ...["gcc", "cc", "c++", "g++", "clang", "clang++", "tcc", "ld", "ld.lld", "as", "nasm", "rustc", "zstd", "lz4"].map((c) => [c, ["-o"]]),
  ...["sort", "shuf", "pandoc", "strace", "ltrace", "gpg"].map((c) => [c, ["-o", "--output"]]),
  ["curl", ["-o", "--output", "--output-dir", "-D", "--dump-header", "-c", "--cookie-jar", "--trace", "--trace-ascii", "--stderr"]],
  ["wget", ["-O", "--output-document", "-o", "--output-file", "-a", "--append-output", "-P", "--directory-prefix", "--save-cookies"]],
  ["go", ["-o", "-coverprofile", "-cpuprofile", "-memprofile"]],
  ["tcpdump", ["-w"]],
  ["openssl", ["-out", "-keyout"]],
  ["ssh-keygen", ["-f"]],
  ["tsc", ["--outFile", "--outDir", "--out"]],
  ["esbuild", ["--outfile", "--outdir"]],
  ["bun", ["--outfile", "--outdir"]],
  ["javac", ["-d"]],
  ["pg_dump", ["-f", "--file"]],
  ["pg_dumpall", ["-f", "--file"]],
  ["mysqldump", ["-r", "--result-file"]],
  ["rsync", ["--log-file", "--write-batch", "--only-write-batch"]],
  ["pytest", ["--junitxml", "--junit-xml", "--basetemp"]],
  ["docker", ["-o", "--output", "--iidfile", "--cidfile", "--metadata-file"]],
  ["podman", ["-o", "--output", "--iidfile", "--cidfile"]],
]);

/**
 * The value `word` (and the word after it) gives `flag`: `--flag VALUE` / `--flag=VALUE`, `-flag VALUE` /
 * `-flag=VALUE` for a one-dash long option (`go -coverprofile`), and for a one-letter flag also a cluster ending in
 * it (`-sSLo FILE`) or an attached value (`-oFILE`, `-qO-`). `consumed` says whether the next word was the value.
 */
function flagValue(word: string, next: string | undefined, flag: string): { value: string; consumed: boolean } | undefined {
  if (word === flag) return next === undefined ? undefined : { value: next, consumed: true };
  if (flag.length > 2) return word.startsWith(`${flag}=`) ? { value: word.slice(flag.length + 1), consumed: false } : undefined;
  if (word.startsWith("--")) return undefined;
  const letter = flag[1];
  // `-oFILE`; a letter cluster ending in the flag (`-sSLo FILE`); a cluster with a value that cannot be a flag (`-qO./x`)
  if (word.startsWith(flag) && word.length > 2) return { value: word.slice(2), consumed: false };
  if (/^-[A-Za-z]+$/.test(word) && word.endsWith(letter)) return next === undefined ? undefined : { value: next, consumed: true };
  const attached = new RegExp(`^-[A-Za-z]*${letter}([^A-Za-z-].*)$`).exec(word);
  if (attached !== null) return { value: attached[1], consumed: false };
  return undefined;
}

/**
 * Files a segment writes, as far as detectable: redirection targets (fd duplications like `2>&1` aside), tee operands,
 * cp/mv/install destinations, the OUTPUT_FLAGS values of the command, `uniq`/`xxd`'s output operand, the last operand of
 * `ffmpeg`/`convert`/`docker cp`, and the file a `curl -O`/`wget` download names after its URL.
 */
function shellWriteTargets({ marked, targetWords: words }: Segment): string[] {
  const targets: string[] = [];
  for (const m of marked.matchAll(REDIRECT)) {
    const target = unquote(m[5]);
    if (m[4] === "&" && /^(?:\d+-?|-)$/.test(target)) continue;
    targets.push(target);
  }
  const cmd = words[0] ?? "";
  const args = operands(words.slice(1));
  if (cmd === "tee") targets.push(...args);
  if (cmd === "cp" || cmd === "mv" || cmd === "install") {
    const t = words.findIndex((w) => w === "-t" || w === "--target-directory");
    if (t > 0 && words[t + 1] !== undefined) targets.push(words[t + 1]);
    const long = words.filter((w) => w.startsWith("--target-directory=")).map((w) => w.slice("--target-directory=".length));
    targets.push(...long);
    // with a target directory every operand is a source
    if (t <= 0 && long.length === 0 && args.length > 1) targets.push(args[args.length - 1]);
  }
  // long options first, so `-coverprofile=x` is never read as a `-o` cluster
  const flags = Object.hasOwn(OUTPUT_FLAGS, cmd) ? [...OUTPUT_FLAGS[cmd]].sort((a, b) => b.length - a.length) : [];
  for (let i = 1; i < words.length && flags.length > 0; i++) {
    for (const flag of flags) {
      const hit = flagValue(words[i], words[i + 1], flag);
      if (hit === undefined) continue;
      targets.push(hit.value);
      if (hit.consumed) i++;
      break;
    }
  }
  if (cmd === "uniq" || cmd === "xxd") targets.push(...args.slice(1, 2));
  if (cmd === "ffmpeg" || cmd === "convert" || cmd === "magick" || ((cmd === "docker" || cmd === "podman") && words[1] === "cp")) {
    targets.push(...args.slice(-1));
  }
  const remoteName =
    (cmd === "curl" && words.some((w) => /^(?:-[A-Za-z]*O[A-Za-z]*|--remote-name(?:-all)?)$/.test(w))) ||
    (cmd === "wget" && !words.some((w) => /^(?:-O|--output-document)/.test(w)));
  if (remoteName) {
    for (const url of args.filter((a) => /^[a-z][\w+.-]*:\/\//i.test(a))) targets.push(posix.basename(url.replace(/[?#].*$/, "")) || "index.html");
  }
  return targets;
}

/** Commands whose every operand is a file they create, change, link or remove. */
const MUTATES_OPERANDS =
  /^(?:rmdir|unlink|link|ln|shred|truncate|fallocate|sqlite3|chmod|chown|chgrp|chattr|setfacl|setfattr|touch|mkdir|mkfifo|mknod|mktemp|sponge|sudoedit|rename|patch|ed|ex|vi|vim|nvim|emacs|trash|trash-put|dos2unix|unix2dos)$/;
/** Compressors that replace their operands (unless writing to stdout, testing or listing). */
const COMPRESSOR = /^(?:gzip|gunzip|bzip2|bunzip2|xz|unxz|lzma|unlzma|zstd|unzstd|lz4|lzip|compress|uncompress)$/;

/**
 * Files a segment mutates in place, as far as detectable: the MUTATES_OPERANDS operands (`ln`/`link` all of them: a
 * link makes its source writable elsewhere), compressor operands, the sources `mv`, `cp -l|-s` and `rsync
 * --remove-source-files` give away, `install -d` directories, the ln/rsync destination, in-place editors (`sed -i`,
 * `perl|ruby -i`, `awk -i inplace`, `yq -i`), `dd of=`, `tar -C` and the archive `tar -c|-r|-u` writes, `unzip -d`,
 * `zip`'s archive, `split`'s prefix, `script`'s log files, `find -delete` (the found paths: unknowable) and
 * `-fprint*`/`-fls` files, and the paths `git checkout|restore|rm|mv|clone|init|worktree` and `git config -f` touch
 * (under `git -C`/`--work-tree`).
 */
function shellMutateTargets({ targetWords: words, opaque }: Segment): string[] {
  const cmd = words[0] ?? "";
  const args = operands(words.slice(1));
  const has = (flag: RegExp) => words.some((w, i) => i > 0 && flag.test(w));
  if (opaque || MUTATES_OPERANDS.test(cmd)) return args;
  if (COMPRESSOR.test(cmd)) return has(/^(?:-[A-Za-z]*[ctl][A-Za-z]*|--(?:stdout|to-stdout|test|list))$/) ? [] : args;
  if (cmd === "mv") {
    // `-t DIR` / `--target-directory[=]DIR` makes every other operand a source; otherwise the last one is the destination
    const t = words.findIndex((w) => w === "-t" || w === "--target-directory");
    if (t > 0) return args.filter((a) => a !== words[t + 1]);
    return words.some((w) => w.startsWith("--target-directory=")) ? args : args.slice(0, -1);
  }
  if (cmd === "cp") return has(/^(?:-[A-Za-z]*[ls][A-Za-z]*|--link|--symbolic-link)$/) ? args : [];
  if (cmd === "install") return has(/^(?:-[A-Za-z]*d[A-Za-z]*|--directory)$/) ? args : [];
  // rsync reads its sources and writes the last operand; --remove-source-files deletes the sources too
  if (cmd === "rsync") return args.length < 2 ? [] : words.includes("--remove-source-files") ? args : [args[args.length - 1]];
  if (cmd === "sed") {
    if (!has(/^-[A-Za-z]*i|^--in-place/)) return [];
    // the first operand is the script unless one was given with -e/-f
    return has(/^(?:-[A-Za-z]*[ef]|--expression|--file)/) ? args : args.slice(1);
  }
  if (cmd === "perl" || cmd === "ruby") return has(/^-[A-Za-z]*i/) ? args : [];
  if (/^[gm]?awk$/.test(cmd)) return words.some((w, i) => /^(?:-i|--include)$/.test(w) && words[i + 1] === "inplace") || has(/^--include=inplace$/) ? args : [];
  if (cmd === "yq") return has(/^(?:-[A-Za-z]*i[A-Za-z]*|--inplace)$/) ? args : [];
  if (cmd === "dd") return words.flatMap((w) => (w.startsWith("of=") ? [w.slice(3)] : []));
  if (cmd === "tar") {
    const out = optionValues(words, ["-C", "--directory"], "--directory");
    const creating = has(/^--(?:create|append|update|concatenate)$/) || /^-?[A-Za-z]*[cru]/.test(words[1] ?? "");
    if (!creating) return out;
    for (let i = 1; i < words.length; i++) {
      if (words[i].startsWith("--file=")) out.push(words[i].slice("--file=".length));
      else if ((words[i] === "--file" || (i === 1 ? /^-?[A-Za-z]*f$/ : /^-[A-Za-z]*f$/).test(words[i])) && words[i + 1] !== undefined) out.push(words[++i]);
    }
    return out;
  }
  if (cmd === "unzip") return optionValues(words, ["-d"]);
  if (cmd === "zip") return args.slice(0, 1);
  if (cmd === "split") return args.slice(1, 2);
  if (cmd === "script") {
    const out: string[] = [];
    for (let i = 1; i < words.length; i++) {
      if (/^(?:-c|--command|-E|--echo|-m|--logging-format)$/.test(words[i])) i++;
      else if (/^(?:-[OBTI]|--log-(?:out|in|io|timing)|--timing)$/.test(words[i]) && words[i + 1] !== undefined) out.push(words[++i]);
      else if (!words[i].startsWith("-")) out.push(words[i]);
    }
    return out;
  }
  if (cmd === "find") {
    const out = has(/^-delete$/) ? [CUT] : [];
    for (let i = 1; i < words.length; i++) if (/^-(?:fprint0?|fprintf|fls)$/.test(words[i]) && words[i + 1] !== undefined) out.push(words[++i]);
    return out;
  }
  if (cmd === "git") return gitPaths(words);
  return [];
}

/** The paths a path-mutating git subcommand touches, joined to `git -C` directories; a `--work-tree` itself. */
function gitPaths(words: string[]): string[] {
  let base = "";
  const out: string[] = [];
  let i = 1;
  for (; i < words.length && words[i].startsWith("-"); i += GIT_VALUE_OPTION.test(words[i]) ? 2 : 1) {
    const w = words[i];
    const value = GIT_VALUE_OPTION.test(w) ? (words[i + 1] ?? "") : w.slice(w.indexOf("=") + 1);
    if (w === "-C") base = value.startsWith("/") || HOME_PREFIX.test(value) ? value : posix.join(base || ".", value);
    else if (/^--work-tree(?:=|$)/.test(w)) out.push(value);
  }
  const sub = words[i] ?? "";
  const args = words.slice(i + 1);
  const at = (p: string) => (base === "" || p.startsWith("/") || HOME_PREFIX.test(p) ? p : posix.join(base, p));
  if (sub === "config") return optionValues(["", ...args], ["-f", "--file"], "--file").map(at);
  if (!/^(?:checkout|restore|rm|mv|clone|init|worktree)$/.test(sub)) return [];
  const paths = operands(args).filter((a) => a !== "add");
  if (sub === "clone" && paths.length === 1) paths.push(posix.basename(paths[0]).replace(/\.git$/, ""));
  return [...out, ...paths].map(at);
}

/**
 * `sqlite3` opened on a path under `.swarm/` (the Task Store), resolved in the segment's directory: a swarm-state
 * write for anyone but A01 (WR-06). A relative operand in an unknown directory is the protected-path-mutate row's.
 */
const sqliteOnSwarm = (seg: Segment, facts: GuardFacts) =>
  seg.words[0] === "sqlite3" &&
  operands(seg.words.slice(1)).some((t) => resolvePath(t, segmentBase(seg, facts)).toLowerCase().split("/").includes(".swarm"));

/** Gate script stems (swarm_gate's GATE_SCRIPTS, plus the gate names themselves). */
const GATE_STEMS: Record<string, true> = {
  qa_gate: true, quality_gate: true, rev_gate: true, review_gate: true,
  sec_gate: true, security_gate: true, rel_plan: true, release_gate: true,
};

/** An interpreter or runner word, versioned pythons (`python3.12`) included. */
const INTERPRETER = /^(?:python(?:3(?:\.\d+)?)?|pypy3?|bun|bunx|node|deno|tsx|ts-node|npx|uv|pipx)$/;
/** python's module-run option: `-m mod`, `-mmod`, or clustered behind no-value flags (`-Bm mod`); group 1 is `mod`. */
const MODULE_OPTION = /^-[bBdEiIOPqsSuvxR]*m(.*)$/;

/**
 * The repo script a segment executes (WR-07, WR-02): argv[0] itself, the first word after `python`/`python3[.N]`/
 * `bun`/`node`/`deno`/`uv`/`pipx` and their `run`/flags, or the module after `-m` (`python3 -m scripts.rev_gate`).
 * The stem is the executed file's basename or the module's last dotted name, so `cd scripts && python3 rev_gate.py`
 * is a run too. A script only named (`cat scripts/x.py`, `grep … scripts/ts/x.ts`, `pytest tests/test_x_gate.py`,
 * `python3 -m pytest scripts/x.py`) is not a run.
 */
function executedScript({ words, argv0 }: Segment): { stem: string; args: string[] } | undefined {
  let i = 0;
  while (i < words.length && (INTERPRETER.test(words[i]) || words[i] === "run" || (i > 0 && words[i].startsWith("-")))) {
    const mod = i > 0 ? MODULE_OPTION.exec(words[i]) : null;
    if (mod !== null) {
      const at = mod[1] === "" ? i + 1 : i; // `-m mod` or `-mmod`
      const name = /(?:^|\.)([A-Za-z0-9_]+)$/.exec(mod[1] === "" ? (words[at] ?? "") : mod[1]);
      return name === null ? undefined : { stem: name[1], args: words.slice(at + 1) };
    }
    if (/^-[XW]$/.test(words[i])) i++; // `python -X dev`, `-W error` take a value
    i++;
  }
  const word = i === 0 ? argv0 : words[i];
  const m = /(?:^|\/)([A-Za-z0-9_-]+)\.(?:py|ts)$/.exec(word ?? "");
  return m === null ? undefined : { stem: m[1], args: words.slice(i + 1) };
}

/**
 * A direct run of a gate script (qa/quality/rev/review/sec/security/release_gate, rel_plan) by any swarm agent, the
 * gate's owner included: a shell run scores whatever tree cwd points at and can overwrite its own verdict, so gates
 * are recorded only through swarm_gate (Phase 5 D-1).
 */
const gateScriptRun = (seg: Segment): boolean => {
  const run = executedScript(seg);
  return run !== undefined && Object.hasOwn(GATE_STEMS, run.stem);
};

/** A run of `orch_plan.py|ts` (or module), or of `orch_status.py|ts` (or module) with `--ingest`/`--transition`. */
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
  // gates are recorded only through swarm_gate, never through the shell, the gate's owner included (Phase 5 D-1)
  {
    id: "gate-script-shell",
    capability: "gate",
    agents: Object.values(GATE_AGENTS),
    pattern: gateScriptRun,
    samples: ["python3 scripts/qa_gate.py --task T-1", "python3 scripts/rev_gate.py --task T-1", "bun scripts/ts/sec_gate.ts", "python3 scripts/review_gate.py x"],
  },
  // D-08: shell writes into .swarm/, .omp/ or ~/.omp
  {
    id: "protected-path-shell",
    capability: "protected_path",
    pattern: (seg, facts) => shellWriteTargets(seg).some((t) => protectedTarget(t, seg, facts)),
    samples: ["echo x > .swarm/a", "tee -a .omp/config.yml", "cp f ~/.omp/x", "mv a .swarm/b", "echo x >>~/.omp/agent/config.yml"],
  },
  // D-08: in-place mutation of .swarm/, .omp/ or ~/.omp through other tools (rm is the destructive row below)
  {
    id: "protected-path-mutate",
    capability: "protected_path",
    pattern: (seg, facts) => shellMutateTargets(seg).some((t) => protectedTarget(t, seg, facts)),
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
      "mv .swarm/tasks.db /tmp/x",
      "rsync -a --remove-source-files .omp/ /tmp/bak/",
      "dd if=/dev/zero of=.swarm/tasks.db",
      "tar -xf a.tar -C .swarm",
      "unzip a.zip -d ~/.omp",
      "touch .swarm/x",
      "mkdir -p .omp/extensions",
    ],
  },
  // D-08: dotglob would let a leading `*`/`?` name `.swarm`/`.omp` (shell targets assume it off, as bash starts)
  {
    id: "glob-dotfiles",
    capability: "protected_path",
    pattern: ({ text, words, targetWords }) =>
      /\b(?:GLOBIGNORE|dotglob|glob_?dots)\b/i.test(text) ||
      ((words[0] === "shopt" || words[0] === "setopt") && targetWords.slice(1).some((w) => /[$`\u0001*?[{]/.test(w))),
    samples: ["shopt -s dotglob", "GLOBIGNORE=x", "setopt globdots", "bash -O dotglob -c 'rm -r *'", 'shopt -s "$OPT"'],
  },
  // universal destructive (every swarm agent, treated as L4)
  {
    id: "git-force-push",
    capability: "destructive",
    pattern: (seg) => {
      const { sub, args } = git(seg.words);
      // `+refspec` forces that ref exactly like --force; `--mirror` force-updates every ref
      return sub === "push" && (args.some((a) => a.startsWith("--force") || a === "--mirror" || shortFlag(a, /f/)) || operands(args).some((r) => r.startsWith("+")));
    },
    samples: ["git push --force", "git push -f origin feat/x", "git push --force-with-lease", "git push origin +feat/x", "git --work-tree /x push --force", "git push -fu origin x", "git push --mirror"],
  },
  {
    id: "git-reset-hard",
    capability: "destructive",
    pattern: (seg) => gitIs(seg, "reset") && git(seg.words).args.includes("--hard"),
    samples: ["git reset --hard HEAD~1", "git -c core.x=y reset --hard"],
  },
  {
    id: "git-clean-force",
    capability: "destructive",
    pattern: (seg) =>
      gitIs(seg, "clean") &&
      (git(seg.words).args.some((a) => a === "--force" || shortFlag(a, /f/)) || seg.words.some((w) => /^clean\.requireforce=(?:false|no|off|0)$/i.test(w))),
    samples: ["git clean -fdx", "git clean -f", "git clean -xfd", "git -c clean.requireForce=false clean -d"],
  },
  {
    id: "git-branch-force-delete",
    capability: "destructive",
    // `-D`, `--delete --force`, and the forced move/copy/reset (`-M`, `-C`, `-f`) that overwrite an existing branch
    pattern: (seg) => {
      const { sub, args } = git(seg.words);
      return sub === "branch" && args.some((a) => a === "--force" || shortFlag(a, /[DMCf]/));
    },
    samples: ["git branch -D feature", "git branch --delete --force feature", "git branch -d -f feature", "git branch -df feature", "git branch -M main", "git branch -f main x"],
  },
  {
    id: "git-discard-repo",
    capability: "destructive",
    pattern: (seg) => {
      const { sub, args } = git(seg.words);
      if ((sub === "checkout" || sub === "switch") && args.some((a) => a === "--force" || a === "--discard-changes" || shortFlag(a, /f/))) return true;
      if (sub === "stash" && /^(?:drop|clear)$/.test(args[0] ?? "")) return true;
      return (sub === "checkout" || sub === "restore") && operands(args).some((a) => [".", "./", ":/", "*", ":/*"].includes(a));
    },
    samples: ["git checkout -- .", "git restore .", "git checkout -f main", "git switch --discard-changes main", "git stash clear"],
  },
  {
    id: "rm-rf-protected",
    capability: "destructive",
    pattern: rmProtected,
    samples: ["rm -rf /", "rm -rf ~", "rm -rf .git", "rm -r .git", "rm .swarm/tasks.db", "rm -f .omp/config.yml", "rm -r .swarm", "rm -Rf /", "rm -r -f ~"],
  },
  {
    id: "chmod-777-recursive",
    capability: "destructive",
    // recursive and leaving files writable by others: `777`, `0777`, `1777`, `666`, `a+rwx`, `o+w`, `+w` …
    pattern: ({ words }) =>
      words[0] === "chmod" && words.some((w) => w === "--recursive" || shortFlag(w, /R/)) && words.slice(1).some((w) => !w.startsWith("-") && worldWritable(w)),
    samples: ["chmod -R 777 .", "chmod -R 0777 dir", "chmod --recursive a+rwx .", "chmod -R o+w src"],
  },
  // A07 destructive_ddl (L4)
  {
    id: "ddl-drop-truncate",
    capability: "destructive_ddl",
    agents: ["a07-data"],
    pattern: ({ words }, _facts, command) =>
      (toolAt(words, DB_CLIENT) !== -1 && DDL.test(command)) || /^(?:dropdb|dropuser)$/.test(words[0] ?? "") || (words[0] === "mysqladmin" && words.includes("drop")),
    samples: ['psql -c "DROP TABLE users"', 'echo "TRUNCATE t" | psql app', "dropdb app", 'redis-cli FLUSHALL', 'npx prisma db execute --stdin <<< "DROP TABLE t"'],
  },
  {
    id: "prisma-migrate-reset",
    capability: "destructive_ddl",
    agents: ["a07-data"],
    // resets that drop every table: prisma, supabase, rails/rake and django
    pattern: ({ words }) => {
      const prisma = toolAt(words, /^prisma$/);
      const migrate = prisma === -1 ? -1 : words.indexOf("migrate", prisma);
      if (migrate !== -1 && words.indexOf("reset", migrate) !== -1) return true;
      if (toolAt(words, /^supabase$/) !== -1 && words.includes("db") && words.includes("reset")) return true;
      if (toolAt(words, /^(?:rails|rake)$/) !== -1 && words.some((w) => /^db:(?:drop|reset|purge|schema:load|structure:load|migrate:reset)$/.test(w))) return true;
      return words.some((w) => /(?:^|\/)manage\.py$/.test(w)) && words.some((w) => w === "flush" || w === "reset_db");
    },
    samples: ["npx prisma migrate reset --force", "prisma --schema x.prisma migrate reset", "supabase db reset", "bin/rails db:drop", "python manage.py flush --noinput"],
  },
  {
    id: "db-push-accept-data-loss",
    capability: "destructive_ddl",
    agents: ["a07-data"],
    pattern: ({ text, words }) =>
      /\bdb\s+push\b.*--(?:accept-data-loss|force-reset)\b/.test(text) || (toolAt(words, /^drizzle-kit$/) !== -1 && words.includes("push") && words.includes("--force")),
    samples: ["prisma db push --accept-data-loss", "prisma db push --force-reset", "npx drizzle-kit push --force"],
  },
  // A11 prod_infra (L3)
  {
    id: "terraform-apply-destroy",
    capability: "prod_infra",
    agents: ["a11-devops"],
    // apply/destroy (also `-chdir=…` first, `apply -destroy`, terragrunt `run-all apply`) and the state-changing commands
    pattern: ({ words }) => {
      const at = toolAt(words, /^(?:terraform|tofu|terragrunt)$/);
      if (at === -1) return false;
      const rest = words.slice(at + 1);
      const after = (w: string) => rest[rest.indexOf(w) + 1] ?? "";
      return (
        rest.some((w) => /^(?:apply|destroy|import|taint|untaint|force-unlock|refresh)$/.test(w)) ||
        (rest.includes("state") && /^(?:rm|mv|push|replace-provider)$/.test(after("state"))) ||
        (rest.includes("workspace") && after("workspace") === "delete")
      );
    },
    samples: ["terraform apply -auto-approve", "terraform destroy", "terraform -chdir=infra apply", "terraform apply -destroy", "terraform state rm x", "terragrunt run-all apply"],
  },
  {
    id: "pulumi-up-destroy",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: ({ words }) => {
      const at = toolAt(words, /^pulumi$/);
      if (at === -1) return false;
      const rest = words.slice(at + 1);
      const after = (w: string) => rest[rest.indexOf(w) + 1] ?? "";
      return (
        rest.some((w) => /^(?:up|update|destroy|import|refresh|cancel)$/.test(w)) ||
        (rest.includes("stack") && after("stack") === "rm") ||
        (rest.includes("state") && /^(?:delete|unprotect|rename|move|edit)$/.test(after("state")))
      );
    },
    samples: ["pulumi up --yes", "pulumi destroy", "pulumi -C infra update", "pulumi stack rm prod"],
  },
  {
    id: "helm-release-change",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: ({ words }) => {
      const at = toolAt(words, /^helm$/);
      const rest = at === -1 ? [] : words.slice(at + 1);
      return !rest.includes("plugin") && !words.some(isDryRun) && rest.some((w) => /^(?:install|upgrade|uninstall|delete|del|un|rollback)$/.test(w));
    },
    samples: ["helm upgrade app ./chart", "helm install app ./chart", "helm --kube-context prod upgrade --install app ./c", "helm uninstall app", "helm rollback app 1"],
  },
  {
    id: "kubectl-prod-change",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: kubectlProdChange,
    samples: [
      "kubectl apply -n production -f k.yaml",
      "kubectl --context=prod rollout restart deploy/api",
      "kubectl scale deploy/api --replicas=0 --namespace=prod",
      "kubectl -nprod apply -f k.yaml",
      "kubectl --context prod set image deploy/api api=img:2",
      "kubectl -n prod patch deploy api -p '{}'",
    ],
  },
  {
    id: "cloud-destructive",
    capability: "prod_infra",
    agents: ["a11-devops"],
    pattern: ({ words }) => {
      const at = toolAt(words, /^(?:aws|gcloud|az|gsutil|doctl)$/);
      if (at === -1) return false;
      const tool = posix.basename(words[at]);
      const rest = words.slice(at + 1);
      if (tool === "aws") {
        return rest.some((w) => /^(?:delete|terminate|remove|deregister|destroy|purge)-/.test(w) || /^(?:rb|rm|mv|deploy)$/.test(w)) || (rest.includes("sync") && rest.includes("--delete"));
      }
      if (tool === "gcloud") return rest.some((w) => /^(?:deploy|delete|destroy)$/.test(w));
      if (tool === "az") return rest.some((w) => /^(?:delete|deploy|purge|up)$/.test(w));
      if (tool === "gsutil") return rest.some((w) => w === "rm" || w === "rb") || (rest.includes("rsync") && rest.some((w) => shortFlag(w, /d/)));
      return rest.some((w) => /^(?:delete|destroy|rm)$/.test(w));
    },
    samples: ["aws ec2 terminate-instances --instance-ids i-1", "gcloud app deploy", "aws --profile prod s3 rm s3://b/x", "aws s3 sync . s3://b --delete", "az group delete -n rg"],
  },
  // A12 prod_high_risk (L4)
  {
    id: "vercel-prod",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: ({ words }) => {
      const at = toolAt(words, /^(?:vercel|vc)$/);
      const rest = at === -1 ? [] : words.slice(at + 1);
      return rest.some((w, i) => /^(?:--prod|--production|--target=production|promote|rollback)$/.test(w) || (w === "--target" && rest[i + 1] === "production"));
    },
    samples: ["vercel --prod", "npx vercel deploy --prod", "vc --prod", "npx vercel@latest deploy --target production", "vercel promote https://x.vercel.app"],
  },
  {
    id: "fly-deploy",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: ({ words }) => {
      const at = toolAt(words, /^(?:fly|flyctl)$/);
      const rest = at === -1 ? [] : words.slice(at + 1);
      return rest.some(
        (w, i) =>
          /^(?:deploy|destroy|scale|restart|rollback)$/.test(w) ||
          (/^(?:secrets|machine|machines|apps|volumes|volume|postgres|pg|certs)$/.test(w) &&
            /^(?:set|unset|import|destroy|remove|rm|delete|update|run|clone|stop|kill|restart)$/.test(rest[i + 1] ?? "")),
      );
    },
    samples: ["fly deploy", "flyctl -a app deploy", "fly apps destroy app", "fly secrets set X=1"],
  },
  {
    id: "gh-release-create",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    // release create/edit (publishes a draft)/delete/upload, also behind `-R owner/repo`, and the same through `gh api`
    pattern: ({ words }) => {
      const gh = ghArgs(words);
      if (gh === undefined) return false;
      if (gh[0] === "release" && /^(?:create|edit|delete|upload|delete-asset)$/.test(gh[1] ?? "")) return true;
      const mutating = words.some(
        (w, i) => (/^(?:-X|--method)$/.test(w) && /^(?:POST|PATCH|PUT|DELETE)$/i.test(words[i + 1] ?? "")) || /^(?:-X|--method=)(?:POST|PATCH|PUT|DELETE)$/i.test(w) || /^(?:-f|-F|--field|--raw-field|--input)$/.test(w),
      );
      return gh[0] === "api" && mutating && gh.some((a) => /\/releases(?:\/|$)/.test(a));
    },
    samples: ["gh release create v1.0.0", "gh -R o/r release create v1", "gh release edit v1 --draft=false", "gh api -X POST repos/o/r/releases -f tag_name=v1"],
  },
  {
    id: "package-publish",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: ({ words }) =>
      !words.some(isDryRun) &&
      words.some((w, i) => {
        const tool = posix.basename(w).replace(/@[^/]*$/, "");
        return Object.hasOwn(PUBLISH_VERB, tool) && words.slice(i + 1).some((v) => PUBLISH_VERB[tool].test(v));
      }),
    samples: ["npm publish", "pnpm publish --access public", "bun publish", "npm --registry https://r publish", "cargo publish", "twine upload dist/*", "poetry publish", "gem push x.gem"],
  },
  {
    id: "docker-push",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    // a push to a registry: `docker|podman|nerdctl|buildah push`, `buildx build --push` / `--output type=registry`, crane/skopeo/regctl copies
    pattern: ({ words }) => {
      const d = toolAt(words, /^(?:docker|podman|nerdctl|buildah)$/);
      if (d !== -1 && words.slice(d + 1).some((w) => w === "push" || w === "--push" || /type=registry|push=true/.test(w))) return true;
      const c = toolAt(words, /^(?:crane|skopeo|regctl)$/);
      return c !== -1 && words.slice(c + 1).some((w) => /^(?:push|copy|cp|sync|mutate|append|tag)$/.test(w));
    },
    samples: ["docker push ghcr.io/org/app:1.0", "docker --context x push img", "docker buildx build --push -t img .", "skopeo copy docker://a docker://b"],
  },
  {
    id: "git-push-protected",
    capability: "prod_high_risk",
    agents: ["a12-release"],
    pattern: pushToProtected,
    samples: ["git push --tags", "git push origin main", "git push origin HEAD:master", "git push origin v1.2.0", "git push origin :main", "git push origin --delete main", "gh pr merge 12 --squash"],
  },
];

/** One possible shell state while walking a command: the directory and the pushd stack (undefined: unknown). */
interface World {
  dir: string | undefined;
  stack: (string | undefined)[] | undefined;
}
/** More worlds than this in one command collapse to one unknown directory (bounds segments × directories). */
const MAX_WORLDS = 16;

/** Deduplicated worlds; past MAX_WORLDS, one unknown world (it blocks every relative write, a superset). */
function distinct(worlds: World[]): World[] {
  const byKey = new Map<string, World>();
  for (const w of worlds) byKey.set(JSON.stringify([w.dir ?? null, w.stack ?? null]), w);
  return byKey.size > MAX_WORLDS ? [{ dir: undefined, stack: undefined }] : [...byKey.values()];
}

/**
 * The directory `cd`/`pushd` with these operands (or an `env -C` prefix) moves to from `from`; undefined when it
 * cannot be known here: `cd -`, `pushd +N`/`-N`, a `-`-led or more than one operand, a `$VAR`/glob/brace/quoted/
 * escaped operand, or a relative operand from an unknown directory or under a non-empty CDPATH. No operand is `~`.
 */
function chdirTo(dirs: string[], from: string | undefined, facts: GuardFacts): string | undefined {
  if (dirs.length > 1) return undefined;
  const dir = dirs[0] ?? "~";
  if (/^[-+]/.test(dir) || /[$`*?[{\\"'\u0001]/.test(dir.replace(HOME_PREFIX, ""))) return undefined;
  const absolute = dir.startsWith("/") || HOME_PREFIX.test(dir);
  if (!absolute && (from === undefined || (Boolean(facts.env.CDPATH) && !/^\.\.?(?:\/|$)/.test(dir)))) return undefined;
  const to = resolvePath(dir, { cwd: from ?? "/", home: facts.home });
  return to.includes(CUT) ? undefined : to;
}

/**
 * The worlds after `cd`/`pushd`/`popd` (`words`) in world `w`. A move that may fail — or may not run at all
 * (`conditional`: after `&&`/`||`, inside an `if`/`case`/loop, behind a wrapper) — keeps the unmoved world too; a
 * move to the directory itself or an ancestor cannot fail. `popd` returns to the pushed directory, stays put on an
 * empty stack (it fails), and yields an unknown directory on an unknown stack; inside a loop (any iteration count)
 * the stack is unknown.
 */
function changeDir(words: string[], w: World, conditional: boolean, loop: boolean, facts: GuardFacts): World[] {
  let i = 1;
  while (/^-[LPe@]+$/.test(words[i] ?? "")) i++;
  if (words[i] === "--") i++;
  const args = words.slice(i);
  const unknown: World = { dir: undefined, stack: undefined };
  const kept = conditional ? [w] : [];
  if (words[0] === "popd") {
    if (args.length > 0 || w.stack === undefined || loop) return [...kept, unknown];
    if (w.stack.length === 0) return [w];
    return [...kept, { dir: w.stack[w.stack.length - 1], stack: w.stack.slice(0, -1) }];
  }
  if (words[0] === "pushd" && args.length === 0) return [...kept, unknown];
  const dir = chdirTo(args, w.dir, facts);
  const from = w.dir === undefined ? undefined : w.dir || "/";
  const sure = dir === "/" || (dir !== undefined && from !== undefined && (from === dir || from.startsWith(`${dir}/`)));
  const stack = words[0] !== "pushd" ? w.stack : loop || w.stack === undefined ? undefined : [...w.stack, w.dir];
  return sure && !conditional ? [{ dir, stack }] : [w, { dir, stack }];
}

/** A `(`/`{` nesting level of the walk (the command itself is level 0). */
interface Level {
  open: Op;
  /** The current member runs only after `&&`/`||`. */
  cond: boolean;
  /** The group itself was opened conditionally or inside a compound. */
  inherited: boolean;
  /** Open `if`/`case`/loop compounds at this level. */
  compounds: Compound[];
  /** Worlds at the start of the current and-or list, pipeline and pipeline member. */
  list: World[];
  pipe: World[] | undefined;
  member: World[];
  /** A `(` subshell: the worlds its close restores. */
  saved: World[] | undefined;
}

/**
 * Push each segment of `items` once per directory it may run in, starting from `start` (D-08). Bash scoping:
 * `cd`/`pushd`/`popd` move the shell for what follows; a `( … )` subshell, every pipeline member and a `&`
 * background list restore the directory afterwards (the last pipeline member's moves are also kept, as zsh and
 * `lastpipe` run it in the shell); `{ …; }` does not restore. `env -C`/`sudo -D` move only their own command and its
 * `sh -c` payload (a child shell with an empty stack); the command does not run where the chdir fails.
 */
function walk(items: Normalized[], start: World[], facts: GuardFacts, out: Segment[]): void {
  let cur = start;
  const levels: Level[] = [
    { open: "", cond: false, inherited: false, compounds: [], list: cur, pipe: undefined, member: cur, saved: undefined },
  ];
  for (const item of items) {
    const level = levels[levels.length - 1];
    if (item.compound === "close") level.compounds.pop();
    else if (item.compound !== undefined) level.compounds.push(item.compound);
    let runIn = cur;
    for (const dir of item.chdirs) runIn = distinct(runIn.map((w) => ({ dir: chdirTo([dir], w.dir, facts), stack: w.stack })));
    // a payload (a substitution body, an `sh -c` line) runs even when nothing is left of the command itself (`$(…)`)
    walk(item.payload, runIn.map((w) => ({ dir: w.dir, stack: [] })), facts, out);
    if (item.text !== "") {
      const seg = parseSegment(item);
      for (const cwd of new Set(runIn.map((w) => w.dir))) out.push({ ...seg, cwd });
      if (item.chdirs.length === 0 && /^(?:cd|pushd|popd)$/.test(seg.words[0] ?? "")) {
        const conditional = item.prefixed || level.cond || level.inherited || level.compounds.length > 0;
        const loop = levels.some((l) => l.compounds.includes("loop"));
        cur = distinct(cur.flatMap((w) => changeDir(seg.targetWords, w, conditional, loop, facts)));
      }
    }
    const { end } = item;
    if (end === "(" || end === "{") {
      const inherited = level.cond || level.inherited || level.compounds.length > 0;
      const saved = end === "(" ? cur : undefined;
      levels.push({ open: end, cond: false, inherited, compounds: [], list: cur, pipe: undefined, member: cur, saved });
      continue;
    }
    const pattern = end === ")" && level.compounds[level.compounds.length - 1] === "case";
    if (!pattern && levels.length > 1 && level.open === (end === ")" ? "(" : end === "}" ? "{" : undefined)) {
      if (level.pipe !== undefined) cur = distinct([...level.pipe, ...cur]);
      levels.pop();
      if (level.saved !== undefined) cur = level.saved;
      continue;
    }
    if (end === "|") {
      level.pipe ??= level.member;
      cur = level.pipe;
    } else if (end === "&") {
      cur = level.list;
      level.pipe = undefined;
      level.cond = false;
    } else {
      // `;`, `&&`, `||`, a newline, the end, a case pattern's `)`, an unmatched `)`/`}`
      if (level.pipe !== undefined) cur = distinct([...level.pipe, ...cur]);
      level.pipe = undefined;
      level.cond = end === "&&" || end === "||";
    }
    level.member = cur;
    if (end !== "&&" && end !== "||" && end !== "|") level.list = cur;
  }
}

/**
 * `command` may give `name` a new value: an assignment word (`NAME=…`, `NAME+=…`, also after `export`/`declare`/`local`/
 * `readonly`/`env` or inside a quoted `eval` line), `unset NAME`, `read … NAME`, `for|select NAME`, `printf -v NAME`,
 * `getopts … NAME`, or a nameref (`declare -n ref=NAME`). A bare mention (`echo HOME`, `grep HOME .env`) is none.
 */
function assignsVariable(command: string, name: string): boolean {
  const start = String.raw`(?:^|[\s;&|(){}!"'\`])`;
  return new RegExp(
    [
      `${start}${name}\\+?=`,
      `${start}unset\\s+(?:-[fvn]+\\s+)*(?:[A-Za-z_]\\w*\\s+)*${name}\\b`,
      `${start}read\\b[^;&|\\n]*\\s${name}\\b`,
      `${start}(?:for|select)\\s+${name}\\b`,
      `${start}printf\\s+-v\\s*${name}\\b`,
      `${start}getopts\\s+\\S+\\s+${name}\\b`,
      `${start}(?:declare|typeset|local)\\s+-\\w*n[^;&|\\n]*\\b${name}\\b`,
    ].join("|"),
  ).test(command);
}

/**
 * The first row (table order) matching any normalized segment of `command`, each segment tried in every directory it
 * may run in from `start` (the session cwd, or the bash tool's `cwd`; undefined: unknown) (D-08, walk). An unknown
 * directory (`cwd` undefined) makes every relative write target protected. A `$(…)`/backtick body runs as a
 * subshell of the piece that holds it (normalizeSegments), so it sees that piece's directory and moves no other.
 */
export function matchRule(command: string, facts: GuardFacts, start: string | undefined): Rule | undefined {
  // a command that assigns HOME, TMPDIR or CDPATH itself changes what `~`, `$TMPDIR` and a relative `cd` mean
  const shellFacts: GuardFacts = {
    ...facts,
    home: assignsVariable(command, "HOME") ? CUT : facts.home,
    tmp: assignsVariable(command, "TMPDIR") ? "" : facts.tmp,
    env: assignsVariable(command, "CDPATH") ? { ...facts.env, CDPATH: CUT } : facts.env,
  };
  const segments: Segment[] = [];
  walk(normalizeSegments(command, 0, []), [{ dir: start, stack: [] }], shellFacts, segments);
  return RULES.find((rule) =>
    segments.some((seg) => (typeof rule.pattern === "function" ? rule.pattern(seg, shellFacts, command) : rule.pattern.test(seg.text))),
  );
}

export const ruleReason = (rule: Rule): string => rule.reason ?? reason(rule.capability, rule.id);
