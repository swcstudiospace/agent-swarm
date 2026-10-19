/**
 * Agent Swarm bar: the third aboveEditor widget (key `agent-swarm`) plus a compact `setStatus("swarm", …)` text.
 * Display-only (v2.2 DESIGN section 6, D4); nothing here may throw into the host. Three parts:
 *   1. pure text: `formatElapsed`, `sanitizeText`, the width-safe `renderSwarmBar` (<= 2 rows);
 *   2. a model reducer merging three sources by run id: the omp bus (`task:subagent:progress|lifecycle`), `<swarmDir>/live.json`
 *      (`swarm.live.v1`) and the parent's own `task` tool_call/tool_result rows (last resort). Elapsed is extrapolated with the
 *      consumer's monotonic clock (`performance.now()`), never `Date.now()`;
 *   3. `createSwarmBar`: the per-session controller (mount once, deferred double-tick remount, 1 Hz ticker only while something
 *      is live (an unref'd mtime-only idle watcher of live.json otherwise), headless stderr lines, full teardown). No module-scope state; no fs, spawn or env access before the first event.
 */
import { readFileSync, type Stats, statSync } from "node:fs";
import { join } from "node:path";
import { sessionAgent } from "./context.ts";
import type {
  EventBus,
  ExtensionContext,
  ExtensionUIContext,
  Theme,
  WidgetFactory,
  WidgetHost,
} from "./omp-api.ts";
import { swarmDir as resolveSwarmDir } from "./paths.ts";
import { band, type BarTheme, clipToWidth, displayWidth, stripAnsi } from "./swarm_dock.ts";

export const SWARM_BAR_KEY = "agent-swarm";
export const SWARM_STATUS_KEY = "swarm";
export const PROGRESS_CHANNEL = "task:subagent:progress";
export const LIFECYCLE_CHANNEL = "task:subagent:lifecycle";
export const LIVE_SCHEMA = "swarm.live.v1";
/** Events after which the bar re-mounts itself last (DESIGN 6); `agent_start` and `turn_end` are the ones Ultrathink also remounts on. */
export const REMOUNT_EVENTS = [
  "session_start",
  "agent_start",
  "turn_start",
  "turn_end",
  "before_agent_start",
  "tool_execution_start",
  "tool_execution_end",
  "agent_end",
  "session_switch",
] as const;

// ---------------------------------------------------------------------------------------------------------------------
// 1. Text: elapsed format, sanitization, redaction
// ---------------------------------------------------------------------------------------------------------------------

/** `59s`, `4m12s`, `1h05m`: the TypeScript twin of `swarm/clock.py format_elapsed` (parity: tests/fixtures/elapsed_cases.json). */
export function formatElapsed(seconds: number): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "0s";
  const total = Math.floor(seconds);
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m${String(total % 60).padStart(2, "0")}s`;
  return `${Math.floor(total / 3600)}h${String(Math.floor((total % 3600) / 60)).padStart(2, "0")}m`;
}

const REDACTED = "[REDACTED]";

// Faithful TS port of `swarm/redact.py` (DESIGN 4.3). Both implementations run the shared vectors in
// tests/fixtures/redact_cases.json; change a pattern here and there together, or those tests fail.
/** Static word table: a `Record` keyed by the words (read it with `=== true`, so inherited names such as `constructor` never match). */
const words = (list: string): Record<string, true> => Object.fromEntries(list.split(" ").map((w) => [w, true]));
const SENSITIVE_TOKENS = words(
  "token secret password passwd passphrase credential credentials signing private cookie cookies auth oauth authorization " +
    "authorisation authentication apikey accesskey secretkey privatekey signingkey sshkey masterkey encryptionkey bearer",
);
/** `<prefix>_key` / `<prefix>_seed` is a credential only after these prefixes (`SWARM_ED25519_KEY`, `signing_seed`); a bare `key`/`seed`, `sort_key`, `random_seed` stay readable. */
const KEY_PREFIX_LIST =
  "api access secret private signing ssh encryption master auth session license licence client ed25519 hmac jwt handoff signer";
const KEY_PREFIXES = words(KEY_PREFIX_LIST);
const KEY_WORDS = words("key seed");
/** Plurals are counts/labels (`tokens: 1200`, `max_tokens`) unless right after one of these (`SUBSTRATE_OPERATOR_TOKENS`, `api_keys`). */
const PLURAL_WORDS = words("tokens secrets passwords keys");
const PLURAL_CONTEXT = words("operator api access bearer auth session");
/** A name whose LAST token is one of these holds a path to the secret, not the secret (`SWARM_ED25519_KEY_FILE`). */
const PATH_TOKENS = words("file path");
const SENSITIVE_SUFFIXES: readonly string[] = [
  "password", "passwd", "passphrase", "token", "secret", "credential", "credentials", "apikey", "accesskey",
  "secretkey", "privatekey", "signingkey", "sshkey", "masterkey", "encryptionkey",
  ...KEY_PREFIX_LIST.split(" ").flatMap((p) => [`${p}key`, `${p}seed`]),
];
const PASS_TOKENS = words("pass pwd");
const PASS_CONTEXT = words(
  "db database sql mysql pg postgres postgresql mongo mongodb redis smtp imap ftp sftp ssh ldap amqp rabbitmq user admin root login mail auth master",
);
/** Whole-word names too generic for the prose-prone `name: value` and `--flag value` forms; `=` and JSON pairs keep them. */
const WEAK_TOKENS = words("auth oauth private signing cookie cookies authentication authorization authorisation bearer");
const NAME_CAP = 256;
const CAMEL_SPLIT = /(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/;

function nameTokens(name: string): string[] {
  const out: string[] = [];
  for (const part of name.slice(0, NAME_CAP).split(/[^A-Za-z0-9]+/)) {
    for (const t of part.split(CAMEL_SPLIT)) if (t) out.push(t.toLowerCase());
  }
  return out;
}

function sensitiveTokens(toks: string[], strict: boolean): boolean {
  if (toks.length > 1 && PATH_TOKENS[toks[toks.length - 1]!] === true) return false;
  for (let i = 0; i < toks.length; i++) {
    const t = toks[i]!;
    if (SENSITIVE_TOKENS[t] === true) {
      if (!(strict && WEAK_TOKENS[t] === true)) return true;
    } else if (SENSITIVE_SUFFIXES.some((suffix) => t.endsWith(suffix))) {
      return true;
    } else if (i > 0) {
      const prev = toks[i - 1]!;
      if (KEY_WORDS[t] === true && KEY_PREFIXES[prev] === true) return true;
      if (PASS_TOKENS[t] === true && PASS_CONTEXT[prev] === true) return true;
      if (PLURAL_WORDS[t] === true && PLURAL_CONTEXT[prev] === true) return true;
    }
  }
  return false;
}

/** Verdicts are memoized per name (a dense `a=b c=d ...` line repeats a few names thousands of times); the cache is bounded. */
const VERDICTS = { loose: new Map<string, boolean>(), strict: new Map<string, boolean>() };
const VERDICT_CAP = 1024;
function keyVerdict(cache: Map<string, boolean>, name: string, strict: boolean): boolean {
  let hit = cache.get(name);
  if (hit === undefined) {
    hit = sensitiveTokens(nameTokens(name), strict);
    if (cache.size >= VERDICT_CAP) cache.clear();
    cache.set(name, hit);
  }
  return hit;
}
const isSensitiveKey = (name: string): boolean => keyVerdict(VERDICTS.loose, name, false);
const isStrictKey = (name: string): boolean => keyVerdict(VERDICTS.strict, name, true);

const PEM = /-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----|$)/g;
const JWT = /(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g;
const TOKENS = [
  /(?<![A-Za-z0-9_])(?:gh[opsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})/g,
  /(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}/g,
  /(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])/g,
  /(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}/g,
];
const BEARER = /(?<![A-Za-z0-9])([Bb]earer\s{1,16})[A-Za-z0-9._~+/=-]{16,}/g;
/** `Authorization: Basic <base64>` (and Proxy-Authorization); a bare "basic <word>" in prose is not touched. */
const BASIC = /(?<![A-Za-z0-9])((?:Proxy-)?Authorization[ \t]{0,3}[:=][ \t]{0,3}["']?Basic[ \t]{1,16})[A-Za-z0-9+/_-]{4,}={0,2}/gi;
/** Any other `Cookie:` / `Set-Cookie:` / `Authorization:` header: the rest of the line up to a quote is the credential (Bearer/Basic keep their scheme). */
const HEADER = /(?<![A-Za-z0-9_-])((?:Set-|Proxy-)?(?:Cookie|Authorization)[ \t]{0,3}:(?![ \t]{0,3}(?:Bearer|Basic)\b)[ \t]{0,3})[^\s"'][^\r\n"']{0,511}/gi;
/** scheme://user:password@host: the password runs to the LAST '@' before the first '/' or whitespace. */
const USERINFO = /(\b[A-Za-z][A-Za-z0-9+.-]{0,31}:\/\/)[^/\s@:]{0,256}:[^/\s]{0,256}@/g;
// The loops below are written unrolled (`[^x]*(?:\\(?!")[^x]*)*`, not `(?:[^x]|\\(?!"))*`): same language as the Python
// patterns, but a megabyte-long value no longer exhausts the regex engine's backtrack stack (which skips the match, i.e. leaks).
const VALUE = String.raw`(\\"[^"\\\n]*(?:\\(?!")[^"\\\n]*)*\\"|"[^"\n]*"|'[^'\n]*'|(?!//)(?=[^\s&;"'\\]|\\(?!"))[^\s&;"'\\]*(?:\\(?!")[^\s&;"'\\]*)*)`;
/** NAME=value / NAME = value (any sensitive name) and NAME: value (strict names only; prose-safe). */
const NAME_VALUE = new RegExp(
  String.raw`(?<![A-Za-z0-9_])(?<![A-Za-z0-9_][.-])([A-Za-z_][A-Za-z0-9_.-]{0,127})([ \t]{0,3}[=:][ \t]{0,3})` + VALUE,
  "g",
);
/** `--password hunter2`, `-db-password hunter2`: a sensitive flag followed by a separate value that is not another flag. */
const FLAG = new RegExp(
  String.raw`(?<![A-Za-z0-9_-])(--?[A-Za-z][A-Za-z0-9-]{0,63})([ \t]{1,8})(?![-\s])` +
    String.raw`(\\"[^"\\\n]*(?:\\(?!")[^"\\\n]*)*\\"|"[^"\n]*"|'[^'\n]*'|(?=[^\s"'\\]|\\(?!"))[^\s"'\\]*(?:\\(?!")[^\s"'\\]*)*)`,
  "g",
);
/** "name": "value", 'name': 'value', "name": 'value' (JSON, Python repr, JS). */
const PAIR = /(?<lead>(?<q>["'])(?<name>[^"'\\\n]{1,80})\k<q>[ \t]{0,8}:[ \t]{0,8})(?<v>"[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*')/g;
/** JSON that was serialised again into a string: {\"password\": \"x\"} */
const ESC_PAIR = /(\\"(?<name>[^"'\\\n]{1,80})\\"[ \t]{0,8}:[ \t]{0,8}\\")[^"\\]*(?:\\(?!")[^"\\]*)*(\\")/g;

/** TS port of `swarm/redact.py redact_text`: every secret-looking span becomes `[REDACTED]`. */
export function redactText(text: string): string {
  if (text.length < 6) return text;
  let s = text.replace(PEM, REDACTED).replace(JWT, REDACTED);
  for (const rx of TOKENS) s = s.replace(rx, REDACTED);
  s = s.replace(BEARER, (_m, lead: string) => lead + REDACTED);
  s = s.replace(BASIC, (_m, lead: string) => lead + REDACTED);
  s = s.replace(USERINFO, (_m, scheme: string) => `${scheme}${REDACTED}@`);
  s = s.replace(HEADER, (_m, lead: string) => lead + REDACTED);
  s = s.replace(NAME_VALUE, (m, name: string, sep: string) => {
    const hit = sep.includes("=") ? isSensitiveKey(name) : isStrictKey(name);
    return hit ? `${name}${sep}${REDACTED}` : m;
  });
  s = s.replace(FLAG, (m, flag: string, sep: string) => (isStrictKey(flag) ? `${flag}${sep}${REDACTED}` : m));
  s = s.replace(PAIR, (...args) => {
    const g = args[args.length - 1] as { lead: string; name: string; v: string };
    return isSensitiveKey(g.name) ? `${g.lead}${g.v[0]}${REDACTED}${g.v[0]}` : (args[0] as string);
  });
  return s.replace(ESC_PAIR, (...args) => {
    const g = args[args.length - 1] as { name: string };
    return isSensitiveKey(g.name) ? `${args[1] as string}${REDACTED}${args[2] as string}` : (args[0] as string);
  });
}

/** Controls, line separators and bidi overrides (display spoofing), replaced by a space or dropped. */
const CONTROLS = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g;
const BIDI = /[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;

/**
 * Agent-supplied text made safe for a status row: ANSI and control characters stripped, secrets redacted (before clipping,
 * so a cut cannot hide half a token), whitespace collapsed, clipped to `max` columns. Non-strings give "".
 */
export function sanitizeText(input: unknown, max = 120): string {
  if (typeof input !== "string") return "";
  const redacted = redactText(stripAnsi(boundInput(input)));
  const flat = redacted.replace(BIDI, "").replace(CONTROLS, " ").replace(/\s+/g, " ").trim();
  return clipToWidth(flat, max);
}

/** Longest input masked (the visible 120 columns come from the head; the URL-credential pattern is quadratic on long text). */
const MAX_SANITIZE_INPUT = 4096;

/** `input` cut to `MAX_SANITIZE_INPUT`, backed up to a whitespace boundary so a cut never leaves half a secret unmasked. */
function boundInput(input: string): string {
  if (input.length <= MAX_SANITIZE_INPUT) return input;
  const cut = input.slice(0, MAX_SANITIZE_INPUT);
  const space = Math.max(cut.lastIndexOf(" "), cut.lastIndexOf("\n"), cut.lastIndexOf("\t"));
  return space > MAX_SANITIZE_INPUT - 256 ? cut.slice(0, space) : cut;
}

// ---------------------------------------------------------------------------------------------------------------------
// 2. Model and reducer
// ---------------------------------------------------------------------------------------------------------------------

export type RunState =
  | "waiting"
  | "queued"
  | "starting"
  | "running"
  | "handing_off"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out";
export type SourceName = "bus" | "live" | "task";
export type BarState = "idle" | "loading" | "active" | "overflow" | "completed" | "failed" | "error";

const RUN_STATES: ReadonlySet<string> = new Set([
  "waiting", "queued", "starting", "running", "handing_off", "completed", "failed", "cancelled", "timed_out",
]);
const SOURCE_ORDER: readonly SourceName[] = ["bus", "live", "task"];

export function isTerminal(state: RunState): boolean {
  return state === "completed" || state === "failed" || state === "cancelled" || state === "timed_out";
}

/**
 * The canonical identity of an agent across sources: the slot `aNN`, lower-case, parsed from either spelling (the Task Store and
 * the derived live.json say `A02`, omp's nested progress and the scheduler say `a02-requirements`). An agent with no slot (an
 * omp built-in such as `explore`) is its lower-cased trimmed name.
 */
export function agentKey(agent: string): string {
  const raw = agent.trim().toLowerCase();
  const slot = /^a(\d{2})(?:$|[^a-z0-9])/.exec(raw);
  return slot ? `a${slot[1]}` : raw;
}

/** Elapsed seconds `baseS` valid at consumer-monotonic `atMono` (ms, `performance.now()` scale). */
export interface Timer {
  baseS: number;
  atMono: number;
}

function timerAt(timer: Timer, nowMono: number): number {
  return timer.baseS + Math.max(0, nowMono - timer.atMono) / 1000;
}

/** What one source last said about a run. */
interface Part {
  state?: RunState;
  stalled?: boolean;
  timer?: Timer;
  activity?: string;
  toolCalls?: number;
  agent?: string;
  detached?: boolean;
  /** live.json `run_id` of the attempt this part describes. */
  runId?: string;
  /** live.json `task_id` of the run this part describes (the join key for in-session specialists). */
  taskId?: string;
  /** Nested (in-session) bus parts: consumer-monotonic start (first sight minus the producer duration, earliest estimate wins). */
  startMono?: number;
  /** Nested bus parts: fingerprint of the snapshot; a repeat snapshot changes nothing (and so is not "fresh" for stall logic). */
  sig?: string;
  updatedMono: number;
}

export interface BarRun {
  key: string;
  /** Creation order: stable identity of one run generation (a reused id after a settle is a new generation). */
  seq: number;
  /** `task` tool call that spawned the run, when known. */
  callId?: string;
  /** Key of the row whose in-flight `task` call spawned this one (an in-session specialist under A01). */
  parentKey?: string;
  /** Swarm task ids (`T<hex>-<suffix>`) the nested assignment text names: the join hint for live.json rows. */
  hints?: string[];
  parts: Partial<Record<SourceName, Part>>;
  // merged view (recomputed on every ingest)
  agent: string;
  state: RunState;
  stalled: boolean;
  timer: Timer;
  activity: string;
  toolCalls?: number;
  /** Consumer-monotonic moment the run was first seen terminal; its elapsed freezes there. */
  endedMono?: number;
  frozenS?: number;
  /** A nested row whose `task` call returned (or spawned background jobs) so no frame will settle it: shown running with a `~`, settled only by evidence. */
  detached?: boolean;
  /** Nested rows: consumer-monotonic moment of the last frame or live.json read that named the row (the detached timeout counts from it). */
  evidenceMono?: number;
}

export interface SwarmBarModel {
  runs: Map<string, BarRun>;
  /** alias id -> run key (bus ids, `<toolCallId>#<index>`, task ids, live run ids). */
  aliases: Map<string, string>;
  seq: number;
  anon: number;
  /** Slot limit of the live swarm (`effective_parallel` or `max_parallel`). */
  slotLimit?: number;
  /** Swarm-wide elapsed from live.json, extrapolated while any run is live. */
  swarmTimer?: Timer;
  /** Why telemetry is unavailable (source unreadable, malformed or stale). */
  error?: string;
  lingerMs: number;
  failLingerMs: number;
  /** How long a detached nested row survives without any evidence before it is dropped. */
  detachedMs: number;
  reducedMotion: boolean;
}

export interface ModelOptions {
  lingerMs?: number;
  failLingerMs?: number;
  detachedMs?: number;
  reducedMotion?: boolean;
}

export function createModel(options: ModelOptions = {}): SwarmBarModel {
  return {
    runs: new Map(),
    aliases: new Map(),
    seq: 0,
    anon: 0,
    lingerMs: options.lingerMs ?? 20_000,
    failLingerMs: options.failLingerMs ?? 600_000,
    detachedMs: options.detachedMs ?? 900_000,
    reducedMotion: options.reducedMotion ?? false,
  };
}

const MAX_TEXT = 120;
const MAX_AGENT = 40;

function lingerFor(model: SwarmBarModel, state: RunState): number {
  return state === "failed" || state === "timed_out" ? model.failLingerMs : model.lingerMs;
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** The run `ids` already name (any alias), if any. */
function resolveKey(model: SwarmBarModel, ids: readonly string[]): string | undefined {
  for (const id of ids) {
    const alias = model.aliases.get(id);
    if (alias !== undefined && model.runs.has(alias)) return alias;
    if (model.runs.has(id)) return id;
  }
  return undefined;
}

function dropRun(model: SwarmBarModel, key: string): void {
  model.runs.delete(key);
  for (const [alias, target] of model.aliases) if (target === key) model.aliases.delete(alias);
  // nested rows go with their parent's linger: settled specialists nothing else describes do not outlive the row that spawned them
  for (const [childKey, child] of [...model.runs]) {
    if (child.parentKey === key && isTerminal(child.state) && !child.parts.live) dropRun(model, childKey);
  }
}

/** A new wave starts when a live run appears while nothing else is live: the previous wave's settled rows go away. */
function pruneSettled(model: SwarmBarModel, keep: ReadonlySet<string>): void {
  for (const [key, run] of [...model.runs]) if (isTerminal(run.state) && !keep.has(key)) dropRun(model, key);
}

export function hasLive(model: SwarmBarModel): boolean {
  for (const run of model.runs.values()) if (!isTerminal(run.state)) return true;
  return false;
}

/**
 * Recompute the merged view of one run from its parts. A terminal part beats everything; otherwise the freshest bus/live part
 * wins, and the parent's own `task` row speaks only when no richer source has said anything.
 */
function remerge(run: BarRun, nowMono: number, ageMs = 0): void {
  const present = SOURCE_ORDER.flatMap((name) => {
    const part = run.parts[name];
    return part ? [part] : [];
  });
  const terminal = present.find((part) => part.state !== undefined && isTerminal(part.state));
  const rich = present.filter((part) => part.state !== undefined && part !== run.parts.task);
  const stated = rich.length > 0 ? rich : present.filter((part) => part.state !== undefined);
  const freshest = stated.reduce<Part | undefined>((best, part) => (!best || part.updatedMono > best.updatedMono ? part : best), undefined);
  const wasTerminal = isTerminal(run.state);
  // an in-session specialist joined to its Task Store row: the store's state is the identity (waiting, handing off, timed out); the nested snapshot is stale-prone
  const authority = run.parentKey !== undefined && run.parts.live?.state !== undefined ? run.parts.live : freshest;
  // terminal states have no exits: a row seen settled never leaves its state because a part went away (file deleted, join changed)
  if (!wasTerminal || terminal !== undefined) run.state = (terminal ?? authority)?.state ?? run.state;
  const live = run.parts.live;
  const bus = run.parts.bus;
  run.stalled = run.state === "running" && live?.stalled === true && (bus?.updatedMono ?? -Infinity) <= live.updatedMono;
  run.agent = present.find((part) => part.agent)?.agent ?? run.agent;
  run.activity = present.find((part) => part.activity)?.activity ?? "";
  run.toolCalls = present.find((part) => part.toolCalls !== undefined)?.toolCalls ?? run.toolCalls;
  // elapsed: the terminal part's final value, else the producer-measured live value, then the bus duration, then the parent's own
  // stopwatch; it never goes backwards when the preferred source changes
  const source = [terminal, live, bus, run.parts.task].find((part) => part?.timer !== undefined);
  const candidate = source?.timer ? timerAt(source.timer, nowMono) : undefined;
  const current = timerAt(run.timer, nowMono);
  // a specialist's settled snapshot carries its exact final duration: it replaces the extrapolation the row showed until then
  const exact = !wasTerminal && terminal !== undefined && terminal === source && terminal.sig !== undefined && candidate !== undefined;
  run.timer = { baseS: candidate !== undefined && (exact || candidate > current) ? candidate : current, atMono: nowMono };
  if (!isTerminal(run.state)) {
    run.endedMono = undefined;
    run.frozenS = undefined;
  } else if (!wasTerminal) {
    run.endedMono = nowMono - ageMs;
    run.frozenS = timerAt(run.timer, nowMono);
  }
}

function emptyRun(model: SwarmBarModel, key: string, nowMono: number): BarRun {
  return {
    key,
    seq: ++model.seq,
    parts: {},
    agent: "task",
    state: "starting",
    stalled: false,
    timer: { baseS: 0, atMono: nowMono },
    activity: "",
  };
}

interface UpsertOptions {
  /** Ids naming this run, most specific first; registered as aliases. */
  ids: readonly string[];
  /** A frame that definitively starts a (new) run: a settled run of the same id is replaced. */
  fresh?: boolean;
  callId?: string;
  /** Age of the information in ms (a live.json first read); only settle times use it. */
  ageMs?: number;
  /** A nested (in-session) row: it never adopts a run of the same agent by slug, because the join pass owns that decision. */
  parentKey?: string;
}

/** Merge `partial` into the run `opts.ids` name (creating it, or adopting an unmatched run of the same agent). Undefined when the frame is stale. */
function upsertPart(model: SwarmBarModel, source: SourceName, partial: Omit<Part, "updatedMono">, nowMono: number, opts: UpsertOptions): BarRun | undefined {
  const key = resolveKey(model, opts.ids);
  let run = key === undefined ? undefined : model.runs.get(key);
  if (run && isTerminal(run.state) && partial.state !== undefined && !isTerminal(partial.state)) {
    // terminal states have no exits: a live frame for a settled id is a new run only when the frame says so, or (bus) its clock restarted
    const restarted = source === "bus" && partial.timer !== undefined && partial.timer.baseS + 1 < (run.frozenS ?? 0);
    if (opts.fresh !== true && !restarted) return undefined;
    dropRun(model, run.key);
    run = undefined;
  }
  if (!run) {
    // the same agent already shown from another source (ids that never met) is the same run: one row, not two
    run = partial.agent === undefined || opts.parentKey !== undefined ? undefined : findTwin(model, source, partial.agent);
    if (!run) {
      run = emptyRun(model, opts.ids[0] ?? `run-${model.seq + 1}`, nowMono);
      model.runs.set(run.key, run);
    }
  }
  for (const id of opts.ids) model.aliases.set(id, run.key);
  if (opts.parentKey !== undefined) run.parentKey = opts.parentKey;
  if (opts.callId !== undefined) run.callId = opts.callId;
  const defined = Object.fromEntries(Object.entries(partial).filter(([, value]) => value !== undefined)) as Omit<Part, "updatedMono">;
  run.parts[source] = { ...run.parts[source], ...defined, updatedMono: nowMono };
  remerge(run, nowMono, opts.ageMs ?? 0);
  return run;
}

/** An unsettled run of `agent` that `source` has not reported yet: the same work seen through another channel. */
function findTwin(model: SwarmBarModel, source: SourceName, agent: string): BarRun | undefined {
  if (source === "task") return undefined;
  for (const run of model.runs.values()) {
    if (isTerminal(run.state) || run.parentKey !== undefined || agentKey(run.agent) !== agentKey(agent) || run.parts[source]) continue;
    if (source === "live" ? run.parts.bus || run.parts.task : run.parts.live || run.parts.task) return run;
  }
  return undefined;
}

function agentName(value: unknown): string | undefined {
  const name = sanitizeText(value, MAX_AGENT);
  return name.length > 0 ? name : undefined;
}

const BUS_STATE: Record<string, RunState> = {
  pending: "queued",
  running: "running",
  completed: "completed",
  failed: "failed",
  aborted: "cancelled",
};

function busActivity(progress: Record<string, unknown>): string {
  const failure = record(progress.retryFailure);
  if (failure) return sanitizeText(`blocked: ${str(failure.errorMessage) ?? "retries exhausted"}`, MAX_TEXT);
  const retry = record(progress.retryState);
  if (retry) return sanitizeText(`retry ${num(retry.attempt) ?? "?"}/${num(retry.maxAttempts) ?? "?"}`, MAX_TEXT);
  // each piece is cut before the join: a megabyte of arguments must not be flattened every frame (sanitizeText bounds it again)
  const tool = str(progress.currentTool)?.slice(0, 256);
  const args = str(progress.currentToolArgs)?.slice(0, MAX_SANITIZE_INPUT);
  const intent = str(progress.currentToolIntent) ?? (tool ? (args ? `${tool} ${args}` : tool) : undefined) ?? str(progress.lastIntent);
  return sanitizeText(intent, MAX_TEXT);
}

/**
 * A new wave starts when a live run appears while nothing else is live: the previous wave's settled rows go away
 * (failed rows linger until then). `before` is `hasLive` read before the frame was merged.
 */
function startWave(model: SwarmBarModel, before: boolean, keep: Iterable<string>): void {
  if (before) return;
  const kept = new Set(keep);
  if ([...kept].some((key) => model.runs.has(key) && !isTerminal(model.runs.get(key)?.state ?? "completed"))) pruneSettled(model, kept);
}

/** Merge a `task:subagent:progress` frame. Returns false when the payload is not one. */
export function ingestProgress(model: SwarmBarModel, payload: unknown, nowMono: number): boolean {
  const frame = record(payload);
  const progress = record(frame?.progress);
  const state = progress ? BUS_STATE[String(progress.status)] : undefined;
  if (!frame || !progress || state === undefined) return false;
  const index = num(frame.index) ?? num(progress.index);
  const call = str(frame.parentToolCallId);
  const ids = [str(progress.id), call !== undefined && index !== undefined ? `${call}#${index}` : undefined].filter((id): id is string => id !== undefined);
  if (ids.length === 0) return false;
  const duration = num(progress.durationMs);
  const before = hasLive(model);
  const run = upsertPart(
    model,
    "bus",
    {
      state,
      timer: duration === undefined ? undefined : { baseS: Math.max(0, duration) / 1000, atMono: nowMono },
      activity: busActivity(progress),
      toolCalls: num(progress.toolCount),
      agent: agentName(frame.agent) ?? agentName(progress.agent),
      detached: frame.detached === true,
    },
    nowMono,
    { ids, callId: call },
  );
  if (run) {
    const details = record(progress.inflightTaskDetails);
    const keys = [run.key];
    if (details !== undefined && !isTerminal(run.state)) keys.push(...ingestNested(model, run, details, nowMono));
    // a frame listing other rows, or the parent's `task` call ended or was replaced (omp clears the in-flight snapshot): what it
    // no longer lists was handed to a background job or lost; it is detached, never settled for lack of news
    const tasking = !isTerminal(run.state) && progress.currentTool === "task";
    if (!isTerminal(run.state) && (details !== undefined || !tasking)) detachChildren(model, run.key, new Set(keys));
    settleChildren(model, nowMono);
    startWave(model, before, keys);
  }
  return true;
}

// ---- in-session specialists: rows for the `task` calls a subagent (A01) runs, read from its in-flight `TaskToolDetails` ----

/** Most nested entries (progress and results together) one frame may add; the rest of an oversized payload is ignored. */
export const MAX_NESTED = 64;
const MAX_HINTS = 8;
const MAX_HINT_SCAN = 2048;
/** The swarm task-id shape (`scripts/orch_plan.py`: `T<hex>-<node>`, a split adds `-aNN`; a run id adds `.aN`, which is not part of the task id). */
const TASK_TOKEN = /(?<![A-Za-z0-9_])T[0-9a-f]{4,8}-[A-Za-z0-9][A-Za-z0-9_-]*/g;

/** One nested entry, normalised from either an `AgentProgress` or a settled `SingleResult`. */
interface Nested {
  id: string;
  agent?: string;
  state: RunState;
  durationMs?: number;
  activity: string;
  toolCalls?: number;
  hints: string[];
}

function taskTokens(values: readonly unknown[]): string[] {
  const found: string[] = [];
  for (const value of values) {
    if (typeof value !== "string") continue;
    for (const match of value.slice(0, MAX_HINT_SCAN).matchAll(TASK_TOKEN)) {
      const token = match[0].replace(/[-_]+$/, "");
      if (!found.includes(token)) found.push(token);
      if (found.length >= MAX_HINTS) return found;
    }
  }
  return found;
}

function nestedFromProgress(entry: unknown): Nested | undefined {
  const item = record(entry);
  const index = num(item?.index);
  const id = str(item?.id) ?? (index !== undefined ? `#${index}` : undefined);
  const state = item ? BUS_STATE[String(item.status)] : undefined;
  if (!item || id === undefined || state === undefined) return undefined;
  return {
    id: sanitizeText(id, 80),
    agent: agentName(item.agent),
    state,
    durationMs: num(item.durationMs),
    activity: busActivity(item),
    toolCalls: num(item.toolCount),
    hints: taskTokens([item.assignment, item.task, item.description]),
  };
}

function nestedFromResult(entry: unknown): Nested | undefined {
  const item = record(entry);
  const index = num(item?.index);
  const id = str(item?.id) ?? (index !== undefined ? `#${index}` : undefined);
  if (!item || id === undefined) return undefined;
  const failed = item.retryFailure !== undefined || (num(item.exitCode) ?? 0) !== 0 || str(item.error) !== undefined;
  return {
    id: sanitizeText(id, 80),
    agent: agentName(item.agent),
    state: item.aborted === true ? "cancelled" : failed ? "failed" : "completed",
    durationMs: num(item.durationMs),
    activity: record(item.retryFailure) ? busActivity(item) : sanitizeText(item.error, MAX_TEXT),
    hints: taskTokens([item.assignment, item.task, item.description]),
  };
}

/** At most `MAX_NESTED` entries of `details.progress` then `details.results`; a settled result replaces the progress entry of the same id. */
function nestedEntries(details: Record<string, unknown>): Nested[] {
  const byId = new Map<string, Nested>();
  const progress = Array.isArray(details.progress) ? details.progress.slice(0, MAX_NESTED) : [];
  for (const entry of progress) {
    const nested = nestedFromProgress(entry);
    if (nested && !byId.has(nested.id)) byId.set(nested.id, nested);
  }
  const results = Array.isArray(details.results) ? details.results.slice(0, MAX_NESTED) : [];
  for (const entry of results) {
    const settled = nestedFromResult(entry);
    if (!settled) continue;
    const known = byId.get(settled.id);
    if (known) byId.set(settled.id, { ...known, ...settled, agent: settled.agent ?? known.agent, toolCalls: known.toolCalls, hints: known.hints.length > 0 ? known.hints : settled.hints });
    else if (byId.size < MAX_NESTED) byId.set(settled.id, settled);
  }
  return [...byId.values()];
}

/**
 * Rows for the specialists `parent` spawned in this session. The subagent's own bus carries no `task:subagent:*` frames to us, but
 * its in-flight `task` details ride on the parent's frames. Each entry is one row (parent = `parent`), keyed by parent and entry id.
 * Elapsed is "first sight minus the producer's duration" on the consumer clock (the earliest estimate wins, so a repeated stale
 * snapshot never shortens it); a snapshot identical to the last one changes nothing. Returns the keys of the rows named.
 */
function ingestNested(model: SwarmBarModel, parent: BarRun, details: Record<string, unknown>, nowMono: number): string[] {
  const keys: string[] = [];
  // omp marks a call that started background jobs with `details.async`: those specialists outlive the call
  const background = record(details.async) !== undefined;
  for (const nested of nestedEntries(details)) {
    const alias = `${parent.key}>${nested.id}`;
    const known = resolveKey(model, [alias]);
    const existing = known === undefined ? undefined : model.runs.get(known);
    const settledNow = isTerminal(nested.state);
    let fresh = false;
    if (existing && isTerminal(existing.state) && !settledNow) {
      // a new call of the parent reuses the id only when its clock restarted; anything else is a stale snapshot
      if (nested.durationMs === undefined || nested.durationMs / 1000 + 1 >= (existing.frozenS ?? 0)) {
        keys.push(existing.key);
        continue;
      }
      fresh = true;
    }
    const previous = fresh ? undefined : existing?.parts.bus;
    const sig = [nested.state, nested.durationMs ?? "", nested.toolCalls ?? "", nested.activity, nested.agent ?? ""].join("|");
    if (previous !== undefined && previous.sig === sig) {
      if (existing) feed(existing, background, nowMono);
      keys.push(existing?.key ?? alias);
      continue;
    }
    const guess = nested.durationMs === undefined ? nowMono : nowMono - Math.max(0, nested.durationMs);
    const startMono = Math.min(previous?.startMono ?? guess, guess);
    const timer: Timer =
      settledNow && nested.durationMs !== undefined
        ? { baseS: Math.max(0, nested.durationMs) / 1000, atMono: nowMono }
        : { baseS: Math.max(0, nowMono - startMono) / 1000, atMono: nowMono };
    const run = upsertPart(
      model,
      "bus",
      { state: nested.state, timer, activity: nested.activity, toolCalls: nested.toolCalls, agent: nested.agent, startMono, sig },
      nowMono,
      { ids: [alias], fresh, parentKey: parent.key },
    );
    if (!run) continue;
    feed(run, background, nowMono);
    if (nested.hints.length > 0) run.hints = nested.hints;
    keys.push(run.key);
  }
  joinNested(model, nowMono);
  return keys;
}

/** A frame named this nested row: fresh evidence, and a background (detached) row when the call that listed it started jobs. */
function feed(run: BarRun, background: boolean, nowMono: number): void {
  run.evidenceMono = nowMono;
  run.detached = background && !isTerminal(run.state);
}

/** `parent`'s unsettled nested rows its latest frame did not name: nothing will update them from the frame any more, so they are detached. */
function detachChildren(model: SwarmBarModel, parent: string, named: ReadonlySet<string>): void {
  for (const run of model.runs.values()) {
    if (run.parentKey === parent && !isTerminal(run.state) && !named.has(run.key)) run.detached = true;
  }
}

/** Whether the row is shown as detached (`~`): a background specialist no live.json row vouches for. */
export function isDetached(run: BarRun): boolean {
  return run.detached === true && !run.parts.live && !isTerminal(run.state);
}

/** Fold the live.json row `live` into the nested row `nested`: one row, the Task Store's ids and state, the bus' activity and timer. */
function adopt(model: SwarmBarModel, nested: BarRun, live: BarRun, nowMono: number): void {
  nested.parts.live = live.parts.live;
  for (const [alias, target] of model.aliases) if (target === live.key) model.aliases.set(alias, nested.key);
  model.runs.delete(live.key);
  remerge(nested, nowMono);
}

/**
 * Join nested rows with live.json rows nothing else describes. First by task id (a `T<hex>-<node>` token the nested assignment
 * names that is the live row's `task_id`, same agent); then by agent slug where exactly one unmatched row of that agent is
 * left on each side among rows that are both live or both settled. Anything else stays two rows (the summary counts them once).
 * Order independent: runs after both a nested frame and a live.json read.
 */
function joinNested(model: SwarmBarModel, nowMono: number): void {
  const nested: BarRun[] = [];
  const pool = new Set<BarRun>();
  for (const run of model.runs.values()) {
    if (run.parentKey !== undefined && !run.parts.live) nested.push(run);
    else if (run.parentKey === undefined && run.parts.live && !run.parts.bus && !run.parts.task) pool.add(run);
  }
  if (nested.length === 0 || pool.size === 0) return;
  const left: BarRun[] = [];
  for (const row of nested) {
    let match: BarRun | undefined;
    for (const token of row.hints ?? []) {
      const target = model.runs.get(model.aliases.get(token) ?? "");
      if (target && pool.has(target) && (row.agent === "task" || agentKey(row.agent) === agentKey(target.agent))) {
        match = target;
        break;
      }
    }
    if (match) {
      pool.delete(match);
      adopt(model, row, match, nowMono);
    } else left.push(row);
  }
  const group = (run: BarRun): string => `${agentKey(run.agent)}|${isTerminal(run.state) ? 1 : 0}`;
  const sides = new Map<string, { rows: BarRun[]; lives: BarRun[] }>();
  for (const row of left) {
    const side = sides.get(group(row)) ?? { rows: [], lives: [] };
    side.rows.push(row);
    sides.set(group(row), side);
  }
  for (const live of pool) sides.get(group(live))?.lives.push(live);
  for (const { rows, lives } of sides.values()) {
    if (rows.length !== 1 || lives.length !== 1) continue;
    const [row] = rows;
    const [target] = lives;
    const taskId = target.parts.live?.taskId;
    // the nested text names another task of that agent: two lanes of one agent, not this pair
    if (row.hints !== undefined && row.hints.length > 0 && taskId !== undefined && !row.hints.includes(taskId)) continue;
    adopt(model, row, target, nowMono);
  }
}

/**
 * Nested rows whose parent row settled and no live.json row owns. A parent that failed or was cancelled took its specialists down
 * with it: `cancelled`. A parent that completed says nothing about them: the rows are dropped (a state is never invented), and
 * a row of a parent that is gone goes with it. While the parent runs nothing here settles a row: see `detachChildren`.
 */
function settleChildren(model: SwarmBarModel, nowMono: number): void {
  for (const run of [...model.runs.values()]) {
    if (run.parentKey === undefined || isTerminal(run.state) || run.parts.live) continue;
    const owner = model.runs.get(run.parentKey);
    if (owner !== undefined && !isTerminal(owner.state)) continue;
    if (owner !== undefined && owner.state !== "completed") upsertPart(model, "bus", { state: "cancelled" }, nowMono, { ids: [run.key], parentKey: run.parentKey });
    else dropRun(model, run.key);
  }
}

const LIFECYCLE_STATE: Record<string, RunState> = { started: "starting", completed: "completed", failed: "failed", aborted: "cancelled" };

/** Merge a `task:subagent:lifecycle` frame. Returns false when the payload is not one. */
export function ingestLifecycle(model: SwarmBarModel, payload: unknown, nowMono: number): boolean {
  const frame = record(payload);
  const state = frame ? LIFECYCLE_STATE[String(frame.status)] : undefined;
  const id = str(frame?.id);
  if (!frame || state === undefined || id === undefined) return false;
  const index = num(frame.index);
  const call = str(frame.parentToolCallId);
  const ids = [id, call !== undefined && index !== undefined ? `${call}#${index}` : undefined].filter((x): x is string => x !== undefined);
  const before = hasLive(model);
  const run = upsertPart(model, "bus", { state, agent: agentName(frame.agent), detached: frame.detached === true }, nowMono, {
    ids,
    callId: call,
    fresh: state === "starting",
  });
  if (run) {
    startWave(model, before, [run.key]);
    settleChildren(model, nowMono);
  }
  return true;
}

/** Rows for a `task` tool call, shown the moment it is seen (loading). Ids the input names (omp's `name`, or an `id`) alias the row for the bus. */
export function ingestTaskSpawn(model: SwarmBarModel, input: unknown, toolCallId: string | undefined, nowMono: number): number {
  const tasks = record(input)?.tasks;
  if (!Array.isArray(tasks)) return 0;
  const call = toolCallId ?? `anon-${model.anon++}`;
  const before = hasLive(model);
  const keys: string[] = [];
  tasks.forEach((item, i) => {
    const task = record(item);
    if (!task) return;
    const own = [str(task.name), str(task.id)].filter((id): id is string => id !== undefined);
    const run = upsertPart(model, "task", { state: "starting", agent: agentName(task.agent) ?? "task", timer: { baseS: 0, atMono: nowMono } }, nowMono, {
      ids: [`${call}#${i}`, ...own],
      callId: call,
      fresh: true,
    });
    if (run) keys.push(run.key);
  });
  startWave(model, before, keys);
  return keys.length;
}

/**
 * A `task` call returned: settle its rows that live.json does not own (the bus is authoritative while it reports, a detached
 * spawn outlives the call). An error for a row no subagent ever reported on means the call never ran (blocked by another
 * extension, denied, aborted): that row is `cancelled`, not a failure of the swarm.
 */
export function ingestTaskResult(model: SwarmBarModel, toolCallId: string | undefined, isError: boolean, nowMono: number): void {
  if (toolCallId === undefined) return;
  for (const run of [...model.runs.values()]) {
    if (run.callId !== toolCallId || isTerminal(run.state) || run.parts.live || run.parts.bus?.detached === true) continue;
    const state: RunState = !isError ? "completed" : run.parts.bus ? "failed" : "cancelled";
    upsertPart(model, "task", { state }, nowMono, { ids: [run.key] });
  }
  settleChildren(model, nowMono);
}

/**
 * The agent loop ended: a row only the parent's own `task` call ever described (no bus frame, no live.json entry) can never
 * settle any more, because omp emits no `tool_result` for a call something blocked after our guard. Returns whether any row changed.
 */
export function settleOrphanedSpawns(model: SwarmBarModel, nowMono: number): boolean {
  let changed = false;
  for (const run of [...model.runs.values()]) {
    if (isTerminal(run.state) || run.parts.bus || run.parts.live) continue;
    upsertPart(model, "task", { state: "cancelled" }, nowMono, { ids: [run.key] });
    changed = true;
  }
  return changed;
}

/** True when a decoded live.json has the `swarm.live.v1` shape this reader understands. */
export function isLiveSnapshot(doc: unknown): doc is { schema: string; runs: unknown[]; swarm?: unknown } {
  const body = record(doc);
  return body !== undefined && body.schema === LIVE_SCHEMA && Array.isArray(body.runs);
}

/** Whether a valid snapshot lists a run that has not settled. */
export function snapshotHasLive(doc: { runs: unknown[] }): boolean {
  return doc.runs.some((entry) => {
    const state = str(record(entry)?.state);
    return state !== undefined && RUN_STATES.has(state) && !isTerminal(state as RunState);
  });
}

/** `T.a2` -> [2, 0]; `T.a1.r2` (rework 2 of attempt 1) -> [1, 2]. Falls back to the row's own `attempt` field, then [0, 0]. */
function attemptOrder(run: Record<string, unknown>, runId: string): [number, number] {
  const m = /\.a(\d+)(?:\.r(\d+))?$/.exec(runId);
  if (m) return [Number(m[1]), m[2] === undefined ? 0 : Number(m[2])];
  return [num(run.attempt) ?? 0, 0];
}

/**
 * The producer keeps every attempt of a task in live.json (`T.a1` completed, `T.a1.r1` rework running, `T.a2` ...) and sorts
 * running rows before completed ones. One task is one row on the bar, so per task (`task_id`, else the run id) only the NEWEST
 * attempt is merged: higher attempt, then higher rework index, then the earlier row (the producer's own order: active first).
 * An older settled attempt must never overwrite, or settle, the row of the attempt that is running now.
 */
function newestAttemptRows(runs: unknown[]): Set<unknown> {
  const best = new Map<string, { entry: unknown; order: [number, number] }>();
  for (const entry of runs) {
    const run = record(entry);
    const runId = str(run?.run_id);
    const state = str(run?.state);
    if (!run || !runId || !state || !RUN_STATES.has(state)) continue;
    const group = str(run.task_id) ?? runId;
    const order = attemptOrder(run, runId);
    const held = best.get(group);
    if (!held || order[0] > held.order[0] || (order[0] === held.order[0] && order[1] > held.order[1])) best.set(group, { entry, order });
  }
  return new Set([...best.values()].map((held) => held.entry));
}

/**
 * Merge a `swarm.live.v1` snapshot read at consumer-monotonic `nowMono`. Producer elapsed values are as of the read, so they
 * are extrapolated from `nowMono`. `ageMs` is how old the file was at the first read: settled runs older than their linger
 * are never shown. A task listed with several attempts contributes its newest one only (`newestAttemptRows`); a new `run_id`
 * for a known `task_id` is a retry: a new run. Runs the snapshot no longer lists lose their live part.
 */
export function ingestLive(model: SwarmBarModel, doc: unknown, nowMono: number, ageMs = 0): boolean {
  if (!isLiveSnapshot(doc)) return false;
  const seen = new Set<string>();
  const before = hasLive(model);
  const swarm = record(doc.swarm);
  const limit = num(swarm?.effective_parallel) ?? num(swarm?.max_parallel);
  model.slotLimit = limit !== undefined && limit > 0 ? Math.floor(limit) : undefined;
  const swarmElapsed = num(swarm?.elapsed_s);
  model.swarmTimer = swarmElapsed === undefined ? undefined : { baseS: Math.max(0, swarmElapsed), atMono: nowMono };
  const newest = newestAttemptRows(doc.runs);
  for (const entry of doc.runs) {
    if (!newest.has(entry)) continue;
    const run = record(entry);
    const runId = str(run?.run_id);
    const state = str(run?.state);
    if (!run || !runId || !state || !RUN_STATES.has(state)) continue;
    const taskId = str(run.task_id);
    const ids = taskId !== undefined && taskId !== runId ? [runId, taskId] : [runId];
    const known = resolveKey(model, ids);
    const runState = state as RunState;
    if (known === undefined && isTerminal(runState) && ageMs >= lingerFor(model, runState)) continue;
    const previous = known === undefined ? undefined : model.runs.get(known)?.parts.live?.runId;
    const elapsed = num(run.elapsed_s);
    const merged = upsertPart(
      model,
      "live",
      {
        state: runState,
        stalled: run.stalled === true,
        timer: elapsed === undefined ? undefined : { baseS: Math.max(0, elapsed), atMono: nowMono },
        activity: sanitizeText(str(run.error) ?? run.activity, MAX_TEXT),
        toolCalls: num(run.tool_calls),
        agent: agentName(run.agent),
        runId,
        taskId,
      },
      nowMono,
      { ids, fresh: previous !== undefined && previous !== runId, ageMs: isTerminal(runState) ? ageMs : 0 },
    );
    if (merged) seen.add(merged.key);
  }
  for (const [key, run] of [...model.runs]) {
    if (!run.parts.live || seen.has(key)) continue;
    delete run.parts.live;
    if (Object.keys(run.parts).length === 0) dropRun(model, key);
    else remerge(run, nowMono);
  }
  joinNested(model, nowMono);
  // a row the file lists is evidence for it (a detached row it names is vouched for, and counts from here if the file later loses it)
  for (const run of model.runs.values()) if (run.parts.live && run.parentKey !== undefined) run.evidenceMono = nowMono;
  settleChildren(model, nowMono);
  startWave(model, before, [...model.runs.values()].filter((run) => run.parts.live).map((run) => run.key));
  return true;
}

/** Forget everything the live source said (file gone, malformed or stale); other sources keep their runs. */
export function dropLive(model: SwarmBarModel, nowMono: number): void {
  model.slotLimit = undefined;
  model.swarmTimer = undefined;
  for (const [key, run] of [...model.runs]) {
    if (!run.parts.live) continue;
    delete run.parts.live;
    if (Object.keys(run.parts).length === 0) dropRun(model, key);
    else remerge(run, nowMono);
  }
}

/** Remove settled runs past their linger. Returns whether anything went away. */
export function pruneExpired(model: SwarmBarModel, nowMono: number): boolean {
  let changed = false;
  for (const [key, run] of [...model.runs]) {
    if (isTerminal(run.state) && nowMono - (run.endedMono ?? nowMono) >= lingerFor(model, run.state)) {
      dropRun(model, key);
      changed = true;
    } else if (isDetached(run) && model.runs.has(key) && nowMono - (run.evidenceMono ?? nowMono) >= model.detachedMs) {
      // no frame and no live.json row has named it for the whole window: it is lost, not done
      dropRun(model, key);
      changed = true;
    }
  }
  return changed;
}

/** Milliseconds until the first settled run expires, or undefined when none lingers. */
export function nextExpiryMs(model: SwarmBarModel, nowMono: number): number | undefined {
  let soonest: number | undefined;
  for (const run of model.runs.values()) {
    if (!isTerminal(run.state)) continue;
    const left = (run.endedMono ?? nowMono) + lingerFor(model, run.state) - nowMono;
    if (soonest === undefined || left < soonest) soonest = left;
  }
  return soonest === undefined ? undefined : Math.max(0, soonest);
}

/** Elapsed seconds of one run at `nowMono`: frozen once settled. */
export function runElapsedS(run: BarRun, nowMono: number): number {
  return run.frozenS ?? timerAt(run.timer, nowMono);
}

/** Runs still worth showing at `nowMono` (live ones, and settled ones inside their linger), in display order. */
export function visibleRuns(model: SwarmBarModel, nowMono: number): BarRun[] {
  const shown = [...model.runs.values()].filter((run) => !isTerminal(run.state) || nowMono - (run.endedMono ?? nowMono) < lingerFor(model, run.state));
  return shown.sort((a, b) => rank(a) - rank(b) || runElapsedS(b, nowMono) - runElapsedS(a, nowMono) || a.seq - b.seq);
}

/** Display priority: failed, timed-out, stalled, running, waiting, queued, then settled. */
function rank(run: BarRun): number {
  if (run.state === "failed") return 0;
  if (run.state === "timed_out") return 1;
  if (run.stalled) return 2;
  if (run.state === "running" || run.state === "starting" || run.state === "handing_off") return 3;
  if (run.state === "waiting") return 4;
  if (run.state === "queued") return 5;
  if (run.state === "cancelled") return 6;
  return 7;
}

/** The bar's state before width is known (`overflow` is decided by the renderer). */
export function barState(model: SwarmBarModel, nowMono: number): Exclude<BarState, "overflow"> {
  const rows = visibleRuns(model, nowMono);
  if (rows.length === 0) return model.error ? "error" : "idle";
  const live = rows.filter((run) => !isTerminal(run.state));
  if (live.length > 0) return live.every((run) => run.state === "starting") ? "loading" : "active";
  return rows.some((run) => run.state === "failed" || run.state === "timed_out") ? "failed" : "completed";
}

// ---------------------------------------------------------------------------------------------------------------------
// 3. Rendering
// ---------------------------------------------------------------------------------------------------------------------

interface Seg {
  /** Plain text: width math and the no-color fallback. */
  plain: string;
  /** The same text with theme colors. */
  painted: string;
}

type Color = "accent" | "error" | "success" | "warning" | "dim" | "muted";

function paint(theme: BarTheme | undefined, color: Color, text: string): string {
  try {
    return theme?.fg?.(color, text) ?? text;
  } catch {
    return text;
  }
}

function bold(theme: BarTheme | undefined, text: string): string {
  try {
    return theme?.bold?.(text) ?? text;
  } catch {
    return text;
  }
}

function seg(theme: BarTheme | undefined, color: Color, text: string, strong = false): Seg {
  const colored = paint(theme, color, text);
  return { plain: text, painted: strong ? bold(theme, colored) : colored };
}

const DEFAULT_SPINNER = "●";

function spinner(model: SwarmBarModel, theme: BarTheme | undefined, nowMono: number): string {
  if (model.reducedMotion) return DEFAULT_SPINNER;
  try {
    const frames = theme?.getSpinnerFrames?.("activity");
    if (Array.isArray(frames) && frames.length > 0) {
      const frame = frames[Math.floor(nowMono / 1000) % frames.length];
      if (typeof frame === "string" && frame.length > 0 && displayWidth(frame) === 1) return stripAnsi(frame);
    }
  } catch {
    // plain mark
  }
  return DEFAULT_SPINNER;
}

interface Label {
  glyph: string;
  word: string;
  color: Color;
  /** The glyph is the animated spinner; the plain fallback shows `●`. */
  spinning?: boolean;
}

function labelOf(run: BarRun, spin: string): Label {
  switch (run.state) {
    case "failed":
      return { glyph: "✕", word: "FAIL", color: "error" };
    case "timed_out":
      return { glyph: "⏱", word: "TIMEOUT", color: "error" };
    case "cancelled":
      return { glyph: "⊘", word: "cancelled", color: "muted" };
    case "completed":
      return { glyph: "✓", word: "", color: "success" };
    case "queued":
      return { glyph: "○", word: "queued", color: "dim" };
    case "waiting":
      return { glyph: "◌", word: "wait", color: "dim" };
    case "handing_off":
      return { glyph: "⇄", word: "handoff", color: "accent" };
    case "starting":
      return { glyph: spin, word: "start", color: "accent", spinning: true };
    default:
      return run.stalled ? { glyph: "!", word: "STALL", color: "warning" } : { glyph: spin, word: "", color: "accent", spinning: true };
  }
}

const ELAPSED_COLUMNS = 6;
const AGENT_COLUMNS = 18;

function rowSegment(run: BarRun, theme: BarTheme | undefined, spin: string, nowMono: number, activityCap: number): Seg {
  const label = labelOf(run, spin);
  const word = label.word ? ` ${label.word}` : "";
  const agent = clipToWidth(run.agent, AGENT_COLUMNS);
  // `~` after the elapsed: a background specialist no source vouches for; its clock keeps running on the consumer's
  const elapsed = `${formatElapsed(runElapsedS(run, nowMono))}${isDetached(run) ? "~" : ""}`.padStart(ELAPSED_COLUMNS);
  const activity = activityCap > 0 && run.activity ? ` ${clipToWidth(run.activity, activityCap)}` : "";
  return {
    plain: `${label.spinning ? DEFAULT_SPINNER : label.glyph}${word} ${agent} ${elapsed}${activity}`,
    painted: `${paint(theme, label.color, `${label.glyph}${word}`)} ${agent} ${paint(theme, "dim", elapsed)}${activity ? paint(theme, "muted", activity) : ""}`,
  };
}

/** `parts` as one band when it fits `width`; otherwise undefined. */
function fitBand(theme: BarTheme | undefined, parts: readonly Seg[], width: number): string | undefined {
  const line = band(theme, parts.map((part) => part.painted), " | ");
  return displayWidth(line) <= width ? line : undefined;
}

function plainLine(parts: readonly Seg[], width: number): string {
  return clipToWidth(parts.map((part) => part.plain).join(" | "), width);
}

export interface BarFrame {
  lines: string[];
  state: BarState;
  /** Rows drawn / rows left out (`+N more`). */
  shown: number;
  hidden: number;
}

const MAX_CANDIDATES = 12;

/**
 * The rows the summary counts. A nested specialist and a live.json row of the same agent that could not be joined (several lanes,
 * no task id in the text) may be one specialist seen twice: per agent, nested rows with no live part count only beyond the number of
 * unjoined live.json rows, the least prominent first, so the swarm never reports more specialists than the larger source knows of.
 */
function countedRows(rows: readonly BarRun[]): BarRun[] {
  const unjoined = new Map<string, number>();
  for (const run of rows) {
    if (run.parts.live && run.parentKey === undefined && !run.parts.bus && !run.parts.task) unjoined.set(agentKey(run.agent), (unjoined.get(agentKey(run.agent)) ?? 0) + 1);
  }
  if (unjoined.size === 0) return [...rows];
  const skipped = new Set<BarRun>();
  for (const run of [...rows].reverse()) {
    const room = unjoined.get(agentKey(run.agent)) ?? 0;
    if (run.parentKey === undefined || run.parts.live || room === 0) continue;
    unjoined.set(agentKey(run.agent), room - 1);
    skipped.add(run);
  }
  return rows.filter((run) => !skipped.has(run));
}

function summary(model: SwarmBarModel, rows: readonly BarRun[], theme: BarTheme | undefined, mark: Seg, nowMono: number): Seg[] {
  const count = { run: 0, wait: 0, queued: 0, done: 0, fail: 0, timeout: 0, cancel: 0, stall: 0 };
  for (const run of countedRows(rows)) {
    if (run.state === "running" || run.state === "starting" || run.state === "handing_off") count.run++;
    else if (run.state === "waiting") count.wait++;
    else if (run.state === "queued") count.queued++;
    else if (run.state === "completed") count.done++;
    else if (run.state === "failed") count.fail++;
    else if (run.state === "timed_out") count.timeout++;
    else count.cancel++;
    if (run.stalled) count.stall++;
  }
  const live = rows.some((run) => !isTerminal(run.state));
  let elapsed = rows.reduce((max, run) => Math.max(max, runElapsedS(run, nowMono)), 0);
  if (live && model.swarmTimer) elapsed = Math.max(elapsed, timerAt(model.swarmTimer, nowMono));
  const parts: Seg[] = [{ plain: `${mark.plain} swarm ${formatElapsed(elapsed)}`, painted: `${mark.painted} ${bold(theme, "swarm")} ${formatElapsed(elapsed)}` }];
  parts.push(seg(theme, count.run > 0 ? "accent" : "dim", `${count.run} run`));
  if (count.wait > 0) parts.push(seg(theme, "dim", `${count.wait} wait`));
  parts.push(seg(theme, "dim", `${count.queued} queued`));
  parts.push(seg(theme, count.done > 0 ? "success" : "dim", `${count.done} done`));
  parts.push(seg(theme, count.fail > 0 ? "error" : "dim", `${count.fail} fail`));
  if (count.timeout > 0) parts.push(seg(theme, "error", `${count.timeout} timeout`));
  if (count.stall > 0) parts.push(seg(theme, "warning", `${count.stall} stall`));
  if (count.cancel > 0) parts.push(seg(theme, "muted", `${count.cancel} cancel`));
  if (model.slotLimit !== undefined) parts.push(seg(theme, "dim", `slots ${count.run}/${model.slotLimit}`));
  if (model.error) parts.push(seg(theme, "warning", "telemetry unavailable"));
  return parts;
}

/** The bar as rows (at most 2) plus the state it is in. Pure: the same inputs give the same output. */
export function renderSwarmBarFrame(model: SwarmBarModel, width: number, theme: BarTheme | undefined, nowMono: number): BarFrame {
  const base = barState(model, nowMono);
  if (width <= 0 || base === "idle") return { lines: [], state: base, shown: 0, hidden: 0 };
  const rows = visibleRuns(model, nowMono);
  if (rows.length === 0) {
    const marker = seg(theme, "dim", "swarm: telemetry unavailable");
    return { lines: [fitBand(theme, [marker], width) ?? clipToWidth(marker.plain, width)], state: "error", shown: 0, hidden: 0 };
  }
  const spin = spinner(model, theme, nowMono);
  const live = rows.some((run) => !isTerminal(run.state));
  const failed = base === "failed";
  const cancelled = !failed && rows.some((run) => run.state === "cancelled");  // cancelled work is not a clean finish
  const mark: Seg = live
    ? { plain: DEFAULT_SPINNER, painted: paint(theme, "accent", spin) }
    : failed
      ? seg(theme, "error", "✕")
      : cancelled
        ? seg(theme, "muted", "⊘")
        : seg(theme, "success", "✓");
  const head = summary(model, rows, theme, mark, nowMono);
  let line1: string | undefined;
  for (let keep = head.length; keep >= 2 && line1 === undefined; keep--) line1 = fitBand(theme, head.slice(0, keep), width);
  line1 ??= fitBand(theme, head.slice(0, 1), width) ?? plainLine(head, width);

  const candidates = rows.slice(0, MAX_CANDIDATES);
  const cap = rows.length <= 2 ? 40 : rows.length <= 4 ? 24 : 16;
  let line2: string | undefined;
  let shown = 0;
  // as many rows as fit, each with as much activity as fits; only then fewer rows
  for (let k = candidates.length; k >= 1 && line2 === undefined; k--) {
    const hiddenCount = rows.length - k;
    for (const activityCap of [cap, 12, 0]) {
      const parts = candidates.slice(0, k).map((run) => rowSegment(run, theme, spin, nowMono, activityCap));
      if (hiddenCount > 0) parts.push(seg(theme, "dim", `+${hiddenCount} more`));
      const fitted = fitBand(theme, parts, width);
      if (fitted !== undefined) {
        line2 = fitted;
        shown = k;
        break;
      }
    }
  }
  if (line2 === undefined) {
    const only = rowSegment(candidates[0], theme, spin, nowMono, 0);
    line2 = clipToWidth(rows.length > 1 ? `${only.plain} | +${rows.length - 1} more` : only.plain, width);
    shown = 1;
  }
  const hidden = rows.length - shown;
  return { lines: [line1, line2], state: hidden > 0 ? "overflow" : base, shown, hidden };
}

/** The bar as at most 2 rows, each no wider than `width` columns. */
export function renderSwarmBar(model: SwarmBarModel, width: number, theme: BarTheme | undefined, nowMono: number): string[] {
  return renderSwarmBarFrame(model, width, theme, nowMono).lines;
}

/** Compact one-line text for `setStatus("swarm", …)`; undefined when nothing is live or failing. */
export function statusText(model: SwarmBarModel, nowMono: number): string | undefined {
  const rows = visibleRuns(model, nowMono);
  const count = { run: 0, queued: 0, wait: 0, fail: 0 };
  for (const run of countedRows(rows)) {
    if (run.state === "running" || run.state === "starting" || run.state === "handing_off") count.run++;
    else if (run.state === "queued") count.queued++;
    else if (run.state === "waiting") count.wait++;
    else if (run.state === "failed" || run.state === "timed_out") count.fail++;
  }
  const parts = [
    count.run > 0 ? `${count.run} run` : "",
    count.wait > 0 ? `${count.wait} wait` : "",
    count.queued > 0 ? `${count.queued} queued` : "",
    count.fail > 0 ? `${count.fail} fail` : "",
  ].filter((part) => part.length > 0);
  return parts.length === 0 ? undefined : `swarm: ${parts.join(", ")}`;
}

// ---------------------------------------------------------------------------------------------------------------------
// 4. Controller
// ---------------------------------------------------------------------------------------------------------------------

export interface BarTimers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

/** Looked up per call, so fake timers installed after import still apply. Handles are opaque to the controller. */
const REAL_TIMERS: BarTimers = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as never),
  setInterval: (fn, ms) => globalThis.setInterval(fn, ms),
  clearInterval: (handle) => globalThis.clearInterval(handle as never),
};

export interface SwarmBarOptions {
  /** Environment read once at start (default `process.env`). */
  env?: Record<string, string | undefined>;
  /** Consumer-monotonic clock in ms (default `performance.now`). */
  now?: () => number;
  /** Wall clock in ms; used only to age a live.json that already existed at first read (default `Date.now`). */
  wallNow?: () => number;
  timers?: BarTimers;
  /** Headless stderr sink (default `process.stderr.write`). */
  stderr?: (text: string) => void;
  /** Swarm state dir for `cwd` (default `paths.swarmDir`). */
  swarmDir?: (cwd: string) => string;
}

export type BarEvent = (typeof REMOUNT_EVENTS)[number];

export interface SwarmBar {
  /** A session event: start the bar on first use, track turn/tool activity and schedule the remount. */
  event(ctx: ExtensionContext, name: BarEvent): void;
  /** A `task` tool call the guard accepted (last-resort rows). */
  taskSpawn(ctx: ExtensionContext, input: unknown, toolCallId: string | undefined): void;
  /** The matching tool result. */
  taskResult(ctx: ExtensionContext, toolCallId: string | undefined, isError: boolean): void;
  /** Unsubscribe, clear timers, remove widget and status. Safe to call twice. */
  stop(): void;
}

type Mode = "off" | "ui" | "headless";

const STATUS_RENDER_GAP_MS = 250;
const TICK_MS = 1000;
const LIVE_FILE = "live.json";
const LIVE_MAX_BYTES = 1_048_576;
const IDLE_POLL_DEFAULT_MS = 2000;
const IDLE_POLL_MIN_MS = 500;
const IDLE_POLL_MAX_MS = 30_000;
const DETACHED_DEFAULT_S = 900;
const DETACHED_MIN_S = 60;
const DETACHED_MAX_S = 86_400;

function positive(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return value !== undefined && value.trim() !== "" && Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/** Per-session controller. Nothing here touches fs, env or timers until the first event of an eligible session. */
export function createSwarmBar(pi: { events?: EventBus }, options: SwarmBarOptions = {}): SwarmBar {
  const timers = options.timers ?? REAL_TIMERS;
  const clock = options.now ?? (() => performance.now());
  const wall = options.wallNow ?? (() => Date.now());
  const writeErr = options.stderr ?? ((text: string) => void process.stderr.write(text));

  let mode: Mode | undefined;
  let model = createModel();
  let ui: ExtensionUIContext | undefined;
  let host: WidgetHost | undefined;
  let unsubs: Array<() => void> = [];
  let ticker: unknown;
  /** The idle watcher: an unref'd mtime `stat` of live.json, alive only while the 1 Hz ticker (which polls itself) is not. */
  let watcher: unknown;
  /** What the last look at live.json said (`mtime:size`, or `err:<code>`); a different stat wakes the full poll. */
  let watchKey: string | undefined;
  let idlePollMs = IDLE_POLL_DEFAULT_MS;
  let wake: unknown;
  const remountTimers = new Set<unknown>();
  let remountPending = false;
  let generation = 0;
  let turnActive = false;
  let inflight = 0;
  let lastRender = -Infinity;
  let wasShown = false;
  let lastStatus: string | undefined;
  let staleMs = 30_000;
  /** The last read document came from the in-session Task Store (`producer: "derived"`): written on transitions only, so mtime says nothing about liveness. */
  let liveDerived = false;
  let liveDir: string | undefined;
  let liveStamp: { mtime: number; size: number; changedMono: number } | undefined;
  const announced = new Set<string>();
  /** Set by `stop()`; only a new session (`session_start`/`session_switch`) clears it. */
  let closed = false;
  /** The widget is currently mounted (it is only while there is something to show). */
  let mounted = false;

  const safe = (fn: () => void): void => {
    try {
      fn();
    } catch {
      // the bar is display-only: a failure here must never reach the host or change a tool decision
    }
  };

  function decide(ctx: ExtensionContext): Mode {
    if ((options.env ?? process.env).SWARM_BAR?.trim().toLowerCase() === "off") return "off";
    let topLevel = false;
    try {
      topLevel = ctx.agent?.kind !== "sub" && sessionAgent(ctx).topLevel;
    } catch {
      topLevel = false;
    }
    if (!topLevel) return "off";
    if (ctx.hasUI === true && ctx.mode === "tui" && typeof ctx.ui?.setWidget === "function") return "ui";
    if (ctx.hasUI === false && (ctx.mode === "print" || ctx.mode === "json")) return "headless";
    return "off";
  }

  const factory: WidgetFactory = (tui: WidgetHost, theme: Theme) => {
    host = tui;
    return {
      render(width: number): readonly string[] {
        try {
          const lines = renderSwarmBar(model, width, theme, clock());
          // last line of defense: a width bug must not crash the host's renderer
          return lines.map((line) => (displayWidth(line) > width ? clipToWidth(line, width) : line));
        } catch {
          return [];
        }
      },
      invalidate() {},
      dispose() {
        if (host === tui) host = undefined;
      },
    };
  };

  function mount(): void {
    ui?.setWidget?.(SWARM_BAR_KEY, factory, { placement: "aboveEditor" });
  }

  function unmount(): void {
    ui?.setWidget?.(SWARM_BAR_KEY, undefined, { placement: "aboveEditor" });
  }

  /** One zero-delay timer that forgets its own handle when it fires. */
  function later(fn: () => void): void {
    const handle: unknown = timers.setTimeout(() => {
      remountTimers.delete(handle);
      fn();
    }, 0);
    remountTimers.add(handle);
  }

  function scheduleRemount(): void {
    if (mode !== "ui" || remountPending) return;
    remountPending = true;
    const mine = generation;
    // widgets stack in insertion order and re-setting a key moves it to the bottom: land after the other bars' single-tick remounts
    later(() =>
      later(() => {
        remountPending = false;
        // only while something is showing: an idle bar stays unmounted (no spacer row above the editor)
        if (mine === generation && mounted) safe(mount);
      }),
    );
  }

  function syncMount(shown: boolean): void {
    if (shown && !mounted) {
      mounted = true;
      safe(mount);
    } else if (!shown && mounted) {
      mounted = false;
      safe(unmount);
    }
  }

  function render(force: boolean): void {
    if (mode !== "ui") return;
    const now = clock();
    const shown = barState(model, now) !== "idle";
    syncMount(shown);
    if (!shown && !wasShown) return;
    if (!force && now - lastRender < STATUS_RENDER_GAP_MS) return;
    wasShown = shown;
    lastRender = now;
    safe(() => host?.requestRender());
    const text = statusText(model, now);
    if (text !== lastStatus) {
      lastStatus = text;
      safe(() => ui?.setStatus?.(SWARM_STATUS_KEY, text));
    }
  }

  function announce(): void {
    if (mode !== "headless") return;
    const now = clock();
    const current = new Set<string>();
    for (const run of model.runs.values()) {
      const line = `[swarm] ${run.agent} ${run.state} ${formatElapsed(runElapsedS(run, now))}\n`;
      const spawned = `${run.key}@${run.seq}:spawn`;
      const settled = `${run.key}@${run.seq}:settle`;
      current.add(spawned).add(settled);
      if (!announced.has(spawned)) {
        announced.add(spawned);
        // a run first seen already settled prints once, as its settle
        if (!isTerminal(run.state)) writeErr(line);
      }
      if (isTerminal(run.state) && !announced.has(settled)) {
        announced.add(settled);
        writeErr(line);
      }
    }
    // run generations are never reused (`seq`), so the marks of rows that went away can go too
    for (const mark of announced) if (!current.has(mark)) announced.delete(mark);
  }

  function pollLive(now: number): void {
    if (mode !== "ui" || liveDir === undefined) return;
    const path = join(liveDir, LIVE_FILE);
    let stat: Stats;
    try {
      stat = statSync(path);
    } catch (error) {
      watchKey = `err:${(error as NodeJS.ErrnoException)?.code ?? "?"}`;
      liveDerived = false;
      liveStamp = undefined;
      dropLive(model, now);
      model.error = (error as NodeJS.ErrnoException)?.code === "ENOENT" ? undefined : "live.json unreadable";
      return;
    }
    watchKey = `${stat.mtimeMs}:${stat.size}`;
    const previous = liveStamp;
    if (previous && previous.mtime === stat.mtimeMs && previous.size === stat.size) {
      if (model.error === "live.json stale" && now - previous.changedMono > model.failLingerMs) {
        // the dead swarm has aged into history: stop warning about it
        model.error = undefined;
      } else if (!liveDerived && now - previous.changedMono > staleMs && [...model.runs.values()].some((run) => run.parts.live && !isTerminal(run.parts.live.state ?? "completed"))) {
        // a file that was live while watched and then stopped updating
        dropLive(model, now);
        model.error = "live.json stale";
      }
      return;
    }
    // only the first read of a file sees an age; after that every change is "now"
    const age = previous ? 0 : Math.max(0, wall() - stat.mtimeMs);
    liveStamp = { mtime: stat.mtimeMs, size: stat.size, changedMono: now - age };
    if (age > model.failLingerMs) return; // history: a swarm that ended (or died) long before this session says nothing about it
    let doc: unknown;
    try {
      if (stat.size > LIVE_MAX_BYTES) throw new Error("live.json too large");
      doc = JSON.parse(readFileSync(path, "utf8"));
    } catch {
      doc = undefined;
    }
    if (!isLiveSnapshot(doc)) {
      liveDerived = false;
      dropLive(model, now);
      model.error = "live.json malformed";
      return;
    }
    model.error = undefined;
    liveDerived = record(doc)?.producer === "derived";
    if (!liveDerived && snapshotHasLive(doc) && age > staleMs) {
      // recently dead when first seen: do not paint its runs as live, but say why the bar is empty
      dropLive(model, now);
      model.error = "live.json stale";
      return;
    }
    ingestLive(model, doc, now, age);
  }

  function reschedule(): void {
    if (mode !== "ui") return;
    const now = clock();
    const busy = hasLive(model) || turnActive || inflight > 0;
    if (busy && ticker === undefined) {
      const handle = timers.setInterval(tick, TICK_MS);
      (handle as { unref?: () => void } | undefined)?.unref?.();
      ticker = handle;
    } else if (!busy && ticker !== undefined) {
      timers.clearInterval(ticker);
      ticker = undefined;
    }
    // the ticker polls live.json itself while busy: the watcher only covers the idle stretch, never both
    if (ticker !== undefined && watcher !== undefined) {
      timers.clearInterval(watcher);
      watcher = undefined;
    } else if (ticker === undefined && watcher === undefined && liveDir !== undefined) {
      const handle = timers.setInterval(watch, idlePollMs);
      (handle as { unref?: () => void } | undefined)?.unref?.();
      watcher = handle;
    }
    if (wake !== undefined) {
      timers.clearTimeout(wake);
      wake = undefined;
    }
    const left = busy ? undefined : nextExpiryMs(model, now);
    if (left !== undefined) {
      const handle = timers.setTimeout(tick, left + 1);
      (handle as { unref?: () => void } | undefined)?.unref?.();
      wake = handle;
    }
  }

  /** Idle poll: one `stat`; the full poll (read, parse, mount) runs only when it differs from the last look. */
  function watch(): void {
    safe(() => {
      if (mode !== "ui" || liveDir === undefined || ticker !== undefined) return;
      let key: string;
      try {
        const stat = statSync(join(liveDir, LIVE_FILE));
        key = `${stat.mtimeMs}:${stat.size}`;
      } catch (error) {
        key = `err:${(error as NodeJS.ErrnoException)?.code ?? "?"}`;
      }
      if (key !== watchKey) tick();
    });
  }

  function tick(): void {
    safe(() => {
      if (mode !== "ui") return;
      const now = clock();
      pollLive(now);
      pruneExpired(model, now);
      render(true);
      reschedule();
    });
  }

  function changed(structural: boolean): void {
    safe(() => {
      if (mode === undefined || mode === "off") return;
      announce();
      // headless has no ticker: settled rows (and their announcements) are trimmed as events arrive
      if (mode === "headless") pruneExpired(model, clock());
      render(structural);
      reschedule();
    });
  }

  function start(ctx: ExtensionContext): void {
    if (mode !== undefined) return;
    mode = decide(ctx);
    if (mode === "off") return;
    const env = options.env ?? process.env;
    model = createModel({
      lingerMs: positive(env.SWARM_BAR_LINGER_S, 20) * 1000,
      failLingerMs: positive(env.SWARM_BAR_FAIL_LINGER_S, 600) * 1000,
      detachedMs: Math.min(DETACHED_MAX_S, Math.max(DETACHED_MIN_S, positive(env.SWARM_BAR_DETACHED_S, DETACHED_DEFAULT_S))) * 1000,
      reducedMotion: env.SWARM_BAR_REDUCED_MOTION === "1",
    });
    staleMs = 3 * positive(env.SWARM_HEARTBEAT_S, 10) * 1000;
    idlePollMs = Math.min(IDLE_POLL_MAX_MS, Math.max(IDLE_POLL_MIN_MS, positive(env.SWARM_BAR_IDLE_POLL_MS, IDLE_POLL_DEFAULT_MS)));
    const bus = pi.events;
    if (bus && typeof bus.on === "function") {
      unsubs.push(
        bus.on(PROGRESS_CHANNEL, (data) => {
          safe(() => {
            const before = barSignature(model);
            if (ingestProgress(model, data, clock())) changed(barSignature(model) !== before);
          });
        }),
        bus.on(LIFECYCLE_CHANNEL, (data) => {
          safe(() => {
            if (ingestLifecycle(model, data, clock())) changed(true);
          });
        }),
      );
    }
    if (mode === "ui") {
      ui = ctx.ui;
      try {
        liveDir = options.swarmDir ? options.swarmDir(ctx.cwd) : resolveSwarmDir(ctx.cwd);
      } catch {
        liveDir = undefined; // no live.json source; the bus and task rows still work
      }
      // nothing is mounted yet: the widget appears with the first thing to show (DESIGN 6, idle = unmounted)
    }
  }

  function stop(): void {
    generation++;
    // omp emits session_shutdown before it aborts the agent: in-flight calls still produce events afterwards, and those must
    // not start the bar again. Only session_start / session_switch (a new session) reopen it.
    closed = true;
    const hadUi = mode === "ui";
    for (const unsub of unsubs) safe(unsub);
    unsubs = [];
    if (ticker !== undefined) timers.clearInterval(ticker);
    if (watcher !== undefined) timers.clearInterval(watcher);
    if (wake !== undefined) timers.clearTimeout(wake);
    for (const handle of remountTimers) timers.clearTimeout(handle);
    remountTimers.clear();
    ticker = undefined;
    watcher = undefined;
    watchKey = undefined;
    wake = undefined;
    remountPending = false;
    if (hadUi) {
      if (mounted) safe(unmount);
      safe(() => ui?.setStatus?.(SWARM_STATUS_KEY, undefined));
    }
    mounted = false;
    mode = undefined;
    model = createModel();
    ui = undefined;
    host = undefined;
    turnActive = false;
    inflight = 0;
    lastRender = -Infinity;
    wasShown = false;
    lastStatus = undefined;
    liveDir = undefined;
    liveDerived = false;
    liveStamp = undefined;
    announced.clear();
  }

  return {
    event(ctx, name) {
      safe(() => {
        if (name === "session_switch") stop();
        if (name === "session_switch" || name === "session_start") closed = false;
        if (closed) return;
        start(ctx);
        if (mode === undefined || mode === "off") return;
        if (name === "turn_start" || name === "before_agent_start" || name === "agent_start") turnActive = true;
        else if (name === "agent_end") {
          turnActive = false;
          inflight = 0;
          // a `task` call something blocked after our guard never gets a tool_result: its row would stay live forever
          if (settleOrphanedSpawns(model, clock())) changed(true);
        } else if (name === "tool_execution_start") inflight++;
        else if (name === "tool_execution_end") inflight = Math.max(0, inflight - 1);
        scheduleRemount();
        if (mode === "ui") {
          tick();
        }
      });
    },
    taskSpawn(ctx, input, toolCallId) {
      safe(() => {
        if (closed) return;
        start(ctx);
        if (mode === undefined || mode === "off") return;
        if (ingestTaskSpawn(model, input, toolCallId, clock()) > 0) changed(true);
      });
    },
    taskResult(ctx, toolCallId, isError) {
      safe(() => {
        if (closed) return;
        start(ctx);
        if (mode === undefined || mode === "off") return;
        ingestTaskResult(model, toolCallId, isError, clock());
        changed(true);
      });
    },
    stop,
  };
}

/** Cheap fingerprint of what the rows say; a bus frame that changes it renders at once, others wait for the gap. */
function barSignature(model: SwarmBarModel): string {
  let sig = String(model.runs.size);
  for (const run of model.runs.values()) sig += `|${run.key}:${run.state}:${run.stalled ? 1 : 0}:${isDetached(run) ? 1 : 0}`;
  return sig;
}
