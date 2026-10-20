/**
 * Agent Swarm bar: an aboveEditor widget (key `agent-swarm`, rank 10: above Ultrathink).
 * Display-only (v2.2 DESIGN section 6, D4); nothing here may throw into the host. Three parts:
 *   1. pure text: `formatElapsed`, `sanitizeText`, the fixed-width `renderSwarmBar` (always exactly 2 rows of exactly `width`
 *      columns: row 1 the swarm summary on the left and the measured token savings on the right, row 2 the agents or the idle placeholder);
 *   2. a model reducer merging three sources by run id: the omp bus (`task:subagent:progress|lifecycle`), `<swarmDir>/live.json`
 *      (`swarm.live.v1`) and the parent's own `task` tool_call/tool_result rows (last resort). Elapsed is extrapolated with the
 *      consumer's monotonic clock (`performance.now()`), never `Date.now()`;
 *   3. `createSwarmBar`: the per-session controller (mounted from the first eligible event until stop, kept in stack order by the
 *      restack protocol, one deferred remount, token savings from `token-opt:savings` and a one-time `live.json` restore, 1 Hz ticker
 *      only while something is live (an unref'd mtime-only idle watcher of live.json otherwise), headless stderr lines, full teardown).
 *      No module-scope state; no fs, spawn or env access before the first event.
 */
import { readFileSync, type Stats, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { BAR_RANK, createRestack, type Restack } from "./bar_stack.ts";
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
import { type BarSegment, type BarTheme, clipToWidth, composeBar, displayWidth, fitsBar, stripAnsi } from "./swarm_dock.ts";
import { isSessionId, parseTokenSavings, type TokenSavingsSnapshot, tokenChips } from "./token_savings.ts";

export const SWARM_BAR_KEY = "agent-swarm";
export const PROGRESS_CHANNEL = "task:subagent:progress";
export const LIFECYCLE_CHANNEL = "task:subagent:lifecycle";
export const LIVE_SCHEMA = "swarm.live.v1";
export const TOKEN_CHANNEL = "token-opt:savings";
/** Events after which the bar re-mounts itself (one deferral later, DESIGN 6); the restack protocol keeps it above Ultrathink. */
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
  "api access secret private signing ssh encryption master auth account session license licence client ed25519 hmac jwt handoff signer";
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
  /(?<![A-Za-z0-9_])(?:gh[oprsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})/g,
  /(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}/g,
  /(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])/g,
  /(?<![A-Za-z0-9])(?:xox[baprs]|xapp)-[A-Za-z0-9-]{10,}/g,
  // the shapes scripts/sec_gate.py detects (Stripe, Google, GitHub) plus the other prefixed vendor tokens that appear in logs
  /(?<![A-Za-z0-9])(?:(?:[spr]k_live|whsec)_[A-Za-z0-9]{16,}|AIza[A-Za-z0-9_-]{30,}|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}|npm_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{16,}|dop_v1_[A-Za-z0-9]{32,}|hf_[A-Za-z0-9]{30,}|dckr_pat_[A-Za-z0-9_-]{20,}|shpat_[A-Za-z0-9]{32,}|lin_api_[A-Za-z0-9]{30,}|pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,})/g,
];
const BEARER = /(?<![A-Za-z0-9])([Bb]earer[ \t\r\n\f\v]{1,16})[A-Za-z0-9._~+/=-]{16,}/g;
/** `Authorization: Basic <base64>` (and Proxy-Authorization); a bare "basic <word>" in prose is not touched. */
const BASIC = /(?<![A-Za-z0-9])((?:Proxy-)?Authorization[ \t]{0,3}[:=][ \t]{0,3}["']?Basic[ \t]{1,16})[A-Za-z0-9+/_-]{4,}={0,2}/gi;
/**
 * Any other `Cookie:` / `Set-Cookie:` / `Authorization:` header: the rest of the line is the credential (Bearer/Basic keep their scheme).
 * The regex matches the lead only; `headerEnd` asks the lexer for the TRUE end of the value (line end, or the quote that encloses the header):
 * never a length cap, a cap would leave the tail of a long session token readable.
 */
const HEADER = /(?<![A-Za-z0-9_-])((?:Set-|Proxy-)?(?:Cookie|Authorization)[ \t]*:(?![ \t]*(?:Bearer|Basic)\b)[ \t]*)(?=[^ \t\r\n\f\v"'])/gi;
/**
 * scheme://user:password@host: the password runs to the LAST '@' before the first '/' or whitespace. Neither part is length-capped: a scan
 * stops at the next '/' (every `://` ends the previous scan), so the scans of different schemes never overlap.
 */
const USERINFO = /((?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,31}:\/\/)[^/ \t\r\n\f\v@:]*:[^/ \t\r\n\f\v]*@/g;
// A VALUE is read by ONE quote-aware `Reader` (twin of `_Reader` in swarm/redact.py) for every option form (NAME=, NAME:, --flag value, -pVALUE,
// -u user:pass ...): only its BARE word differs per carrier (`BARE_*` below). A backslash keeps the next blank in a bare word (`pa\ ss`) but never
// starts an escaped quote (`\"`). Written as loops, not regexes: a megabyte-long value never exhausts the regex engine's backtrack stack.
/**
 * NAME=value / NAME = value (any sensitive name) and NAME: value (strict names only; prose-safe), and `--password hunter2` /
 * `-db-password hunter2` (a sensitive flag followed by a separate value that is not another flag). The regexes match only
 * `NAME<sep>`; `scan` consumes the value of a SENSITIVE name and merely steps past a benign name's separator, so a later
 * assignment inside its "value" (`user=alice,password=x`, `--mode a;--password x`) is still scanned.
 */
const NAME_SEP = /(?<![A-Za-z0-9_])(?<![A-Za-z0-9_][.-])([A-Za-z_][A-Za-z0-9_.-]{0,127})([ \t]{0,32}[=:][ \t]{0,32})/g;
const FLAG_SEP = /(?<![A-Za-z0-9_-])(--?[A-Za-z][A-Za-z0-9_-]{0,63})([ \t]{1,8})(?![- \t\r\n\f\v])/g;
/** Bare-word flavours: a NAME=value stops at a blank, `;`, `&` or a quote; a `--flag value` at a blank or a quote; a command-line word at a blank, `;&|` or a quote. */
const BARE_NAME = 0;
const BARE_FLAG = 1;
const BARE_CLI = 2;

/** End of the bare word that starts at s[i] (`i` when it is empty); explicit ASCII blanks: JS and Python `\s` differ. */
function bareEnd(s: string, i: number, kind: number): number {
  const n = s.length;
  while (i < n) {
    const c = s.charCodeAt(i);
    if (c === 92) {
      const d = s.charCodeAt(i + 1);
      if (d === 32 || d === 9) {
        i += 2;
        continue;
      }
      if (d === 34) break;
      i++;
      continue;
    }
    if (c === 32 || c === 9 || c === 10 || c === 13 || c === 12 || c === 11 || c === 34 || c === 39) break;
    if (kind !== BARE_FLAG && (c === 59 || c === 38)) break;
    if (kind === BARE_CLI && c === 124) break;
    i++;
  }
  return i;
}

// ---- the quote lexer (twin of `_Lexer` in swarm/redact.py; the rules are the same, word for word) -------------------------------------
// The quote/escape context of a string is computed ONCE, by ONE left-to-right lexer, and every detector QUERIES it: whether a quote OPENS,
// CLOSES or is plain CONTENT, and where a quoted value ends, is never re-derived locally (no counting of same-kind quotes on a line, no cache
// shared by scans that restart at earlier positions): every answer is a pure function of (text, position). Rules:
//   * a MARK is a `"` or `'` with the backslashes before it: an odd run of `k` backslashes makes an ESCAPED mark of level `k` (the form a quote
//     takes inside saved JSON: `\"` level 1, `\\\"` level 3), any other run (none, or an even one) a PLAIN mark (level 0).
//   * the lexer keeps a stack of open quotes. A mark of the same kind and level as an open quote CLOSES it (and ends every quote that was opened
//     after it and never closed: a quote nested in a quote ends before the close of the quote that holds it). A PLAIN mark also ends the
//     escaped quotes of its own kind that are open on top of the stack (`\"x"`: the plain quote is the one that closes the JSON string).
//   * any other mark OPENS a quote, nested in the open ones (`"mysql -p'x' db"`, `'a "b c"'`, `"{\"k\": 1}"`), except a PROSE apostrophe or
//     quote: a plain `'` or `"` right after a letter or digit that is not the `-p` of a flag word (`it's`, `don't`, `users'`, `5"`) never
//     opens a quote and is plain content. (`-p'x'`, `-u'a:b'` and `--pass"x"`: the letters belong to a flag word, the quote opens.)
//   * outside quotes, a `#` that STARTS a word (the start of the text, or right after a blank, a newline, `;`, `&`, `|` or `(`, unless that
//     character is backslash-escaped) begins a COMMENT that runs to the end of its line. A comment is text, but the VALUE of a sensitive name is
//     a value wherever it sits. A quote in comment PROSE is a CONTENT mark: it opens and closes nothing, so a stray apostrophe such as `# '` or
//     `# don't` changes no later quote. The VALUE SPAN of a sensitive name in a comment is read as it is outside one: it starts where `NAME=`,
//     `NAME:` (strict names), `"NAME": ` or `--NAME ` (strict) names a sensitive name (the detectors' own test; a `//` after `NAME:` is a URL
//     scheme), and it is a shell WORD: its marks are read by the rules above, so joined segments (`"a"'b'"c"`) and multi-line quotes are one
//     value, until a blank outside quotes ends the word (a blank that a backslash escapes does not), or a `;` or `&` outside quotes when the
//     value is a `NAME=`, `NAME:` or pair value (a flag value reads through them, as `_FLAG_BARE_RE` does), or the line ends. Two readings
//     of one word (`--password :;` is a flag and also `password :`) make one value, the longer. A quote that nothing closes runs to the end
//     of its line. `a#b`, `$#`, `\#` and a `#` inside an open quote are not comments. "Outside quotes" is the lexer's own view: no quote is
//     open on its stack at that point (an unclosed quote still counts as open).
//   * a comment's quote state is bounded by its LINE (shell semantics): at the newline that ends a comment, every quote opened inside it is
//     closed without a closer and runs to that line's end; the lexer never carries an open quote out of a comment, so the next line is read from
//     a clean state (`# PASSWORD="x"'` then `TOKEN='alpha` and `s3cr3t'` read exactly as `TOKEN='alpha` and `s3cr3t'` with no comment above).
//   * quotes of any kind may span lines. A quote that nothing closes runs to the end of its LINE as a value (a truncated log line): the
//     detectors mask from the value start to the end of that line, never a part of it.
//   * a detector that sees a quote where it expects the start of a value asks the lexer: OPEN gives the value's end, a quote the lexer takes
//     for a CLOSE or for prose is read like an opener of its own kind that nothing in the lexer's view contradicts (the next quote of its
//     kind, else the end of the line): over-redacting, never leaving a suffix of the value readable.
const ROLE_CONTENT = 0;
const ROLE_OPEN = 1;
const ROLE_CLOSE = 2;
const HOW_UNCLOSED = 0;
const HOW_CLOSED = 1;
const HOW_CUT = 2;

/** The characters that may precede a `#` which starts a word (a blank, a newline, `;`, `&`, `|`, `(`). */
const COMMENT_LEAD = " \t\r\n;&|(";
const isAsciiAlnum = (c: number): boolean => (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122);

/** Whether the plain quote at s[p] sits right after a letter or digit that is not part of a flag word (`it's`, `users'`, not `-p'x'`). */
function proseQuote(s: string, p: number): boolean {
  if (p === 0 || !isAsciiAlnum(s.charCodeAt(p - 1))) return false;
  let i = p - 1;
  while (i > 0 && isAsciiAlnum(s.charCodeAt(i - 1))) i--;
  return !(i > 0 && s.charCodeAt(i - 1) === 45);
}

/** Whether the `#` at s[p] starts a word, so that it begins a shell comment (`echo x #note`, `#` at the start of a line); not for `a#b`. */
function commentAt(s: string, p: number): boolean {
  if (p === 0) return true;
  if (!COMMENT_LEAD.includes(s.charAt(p - 1))) return false;
  let r = p - 1; // an escaped blank or joiner (`\ #`, `\;#`) does not start a word
  while (r > 0 && s.charCodeAt(r - 1) === 92) r--;
  return (p - 1 - r) % 2 === 0;
}

/**
 * (offset, ends at `;`/`&` too) for every value of a SENSITIVE name in `line` (one comment, from its `#`), ascending (twin of `_value_starts`).
 * The offset is the end of `NAME<sep>` (`=` is tested loosely, `:` strictly, as in `_scan`; a `//` after `NAME:` is a URL scheme, not a
 * value), of a `"NAME": ` / `\"NAME\": ` pair (as in `_pair_spans`), or of `--flag<blank>` (strict). A NAME or pair value stops at `;` and `&`
 * too (`_NAME_BARE_RE`); a flag value does not (`_FLAG_BARE_RE`).
 */
function valueStarts(line: string): Array<[number, boolean]> {
  const out: Array<[number, boolean]> = [];
  NAME_SEP.lastIndex = 0;
  for (const m of line.matchAll(NAME_SEP)) {
    const end = m.index! + m[0].length;
    const eq = m[2]!.includes("=");
    if (!eq && line.startsWith("//", end)) continue;
    if (isNameHit(m[1]!, eq)) out.push([end, true]);
  }
  FLAG_SEP.lastIndex = 0;
  for (const m of line.matchAll(FLAG_SEP)) if (isNameHit(m[1]!, false)) out.push([m.index! + m[0].length, false]);
  PAIR.lastIndex = 0;
  for (const m of line.matchAll(PAIR)) if (isSensitiveKey(m.groups!.name!)) out.push([m.index! + m[0].length, true]);
  ESC_PAIR.lastIndex = 0;
  for (const m of line.matchAll(ESC_PAIR)) {
    if (m.groups!.bs!.length % 2 === 1 && isSensitiveKey(m.groups!.name!)) out.push([m.index! + m[0].length, true]);
  }
  return out.sort((a, b) => a[0] - b[0]);
}

/**
 * Whether s[a:b], outside quotes, ends a value word: a blank that no backslash escapes, or (`joiners`) a `;` or `&` (twin of `_word_break`).
 */
function wordBreak(s: string, a: number, b: number, joiners: boolean): boolean {
  for (let k = a; k < b; k++) {
    const c = s.charCodeAt(k);
    if (joiners && (c === 59 || c === 38)) return true;
    if ((c === 32 || c === 9) && !(k > 0 && s.charCodeAt(k - 1) === 92)) return true;
  }
  return false;
}

/** The next PLAIN quote of s[p]'s kind before `stop` (the end of its comment line), or -1 (twin of `_mate`). */
function proseMate(s: string, p: number, stop: number): number {
  const ch = s.charAt(p);
  let q = s.indexOf(ch, p + 1);
  while (q >= 0 && q < stop) {
    let r = q;
    while (r > 0 && s.charCodeAt(r - 1) === 92) r--;
    if ((q - r) % 2 === 0) return q;
    q = s.indexOf(ch, q + 1);
  }
  return -1;
}

/** First index `i` of the ascending `a[0, n)` with `a[i] >= x` (`n` when there is none). */
function lowerBound(a: Int32Array | number[], n: number, x: number): number {
  let lo = 0;
  let hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (a[mid]! < x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * Every quote mark of one string with its role: parallel arrays indexed by the mark's number (ascending position). `role` is content, open or
 * close; an OPEN mark has `how` (unclosed, closed, or ended by an enclosing close) and `link` (the mark that ended it); a CLOSE mark's `link`
 * is the mark it closes; `top[m]` is the innermost quote open after mark m (-1 for none) and `par[m]` the quote an OPEN mark is nested in.
 */
class Lexer {
  readonly count: number;
  readonly pos: Int32Array;
  readonly lvl: Int32Array;
  readonly role: Uint8Array;
  readonly link: Int32Array;
  readonly how: Uint8Array;
  readonly top: Int32Array;
  readonly par: Int32Array;
  /** the ascending numbers of the quotes that nothing closes */
  readonly lost: number[];
  private readonly keys: Int32Array;
  private nx0: Int32Array | null = null;
  private nxl: Int32Array | null = null;

  constructor(s: string) {
    const len = s.length;
    let marks = 0;
    for (let i = 0; i < len; i++) {
      const c = s.charCodeAt(i);
      if (c === 34 || c === 39) marks++;
    }
    const pos = new Int32Array(marks);
    const lvl = new Int32Array(marks);
    const link = new Int32Array(marks).fill(-1);
    const top = new Int32Array(marks);
    const par = new Int32Array(marks).fill(-1);
    const keys = new Int32Array(marks);
    const role = new Uint8Array(marks);
    const how = new Uint8Array(marks);
    const stack = new Int32Array(marks);
    let sp = 0;
    const cnt = new Map<number, number>();
    const lost: number[] = [];
    let i = 0;
    let skipTo = 0; // the offset up to which the text is a comment: no marks there
    let starts: Array<[number, boolean]> = []; // the value spans of the comment in progress: where each starts and whether it also ends at `;`/`&`, ascending
    let si = 0;
    let vact = false; // a value word of that comment is open ...
    let vscan = 0; // ... and the text before here is checked for the word's end (no break in it); the text from here on is not yet
    let vjoin = true; // whether that word ends at a `;` or `&` as well as at a blank
    let vseg = -1; // the mate of a prose quote that opened a quoted segment of that word: the blanks up to it are inside the quotes
    let commentOpen = false; // a comment began and no mark has come after its line yet: the first mark after that line closes the quotes it opened
    for (let p = 0; p < len; p++) {
      const c = s.charCodeAt(p);
      if (commentOpen && p >= skipTo && (c === 34 || c === 39 || c === 35)) {
        // rule (a): the comment's line has ended; no quote it opened outlives that line
        commentOpen = false;
        for (let k = 0; k < sp; k++) {
          const o = stack[k]!;
          link[o] = i; // the mark that ends it; `how` stays UNCLOSED, so the value it opened runs to the end of its line
          lost.push(o);
        }
        sp = 0;
        cnt.clear();
      }
      if (c === 35) {
        if (p >= skipTo && sp === 0 && commentAt(s, p)) {
          let e = p;
          while (e < len && s.charCodeAt(e) !== 10 && s.charCodeAt(e) !== 13) e++;
          skipTo = e;
          starts = valueStarts(s.slice(p, e)).map(([k, joins]): [number, boolean] => [p + k, joins]);
          si = 0;
          vact = false;
          commentOpen = true;
        }
        continue;
      }
      if (c !== 34 && c !== 39) continue;
      let r = p;
      while (r > 0 && s.charCodeAt(r - 1) === 92) r--;
      const run = p - r;
      const q1 = c === 39 ? 1 : 0;
      const level = run % 2 === 1 ? run : 0;
      const key = level * 2 + q1;
      pos[i] = p;
      lvl[i] = level;
      keys[i] = key;
      const inComment = p < skipTo;
      if (inComment) {
        // a quote in a comment is prose unless it lies in the value span of a sensitive name (the rules above)
        while (si < starts.length && starts[si]![0] <= p) {
          const [st, joins] = starts[si]!;
          if (sp === 0) {
            if (vact && st > vscan) {
              // only the text past the cursor is scanned: every character of the comment is scanned once
              if (wordBreak(s, vscan, st, vjoin)) vact = false;
              vscan = st;
            }
            if (vact) {
              // a second reading of the same word (`--password :;` is also `password :`): the detectors take the union, the longer word
              vjoin = vjoin && joins;
            } else {
              vact = true;
              vscan = st;
              vjoin = joins;
            }
          }
          si++;
        }
        if (vact && sp === 0 && p > vscan) {
          if (wordBreak(s, vscan, p, vjoin)) vact = false;
          vscan = p;
        }
        if (!vact) {
          // comment prose: a CONTENT mark, it opens and closes nothing
          top[i] = sp > 0 ? stack[sp - 1]! : -1;
          i++;
          continue;
        }
        if (sp === 0 && level === 0 && p > vseg && proseQuote(s, p)) {
          // `ab'c d'ef` in a comment: the prose quote opens a quoted segment that its mate on the line closes; the blanks in between are
          // inside the quotes, so the word goes on past them (the reader's rule for the same word outside a comment)
          const mate = proseMate(s, p, skipTo);
          if (mate >= 0) vseg = vscan = mate;
        }
      }
      if (level === 0) {
        // a plain quote ends the escaped quotes of its kind on top of the stack
        while (sp > 0) {
          const o = stack[sp - 1]!;
          const k = keys[o]!;
          if (k < 2 || (k & 1) !== q1) break;
          sp--;
          cnt.set(k, cnt.get(k)! - 1);
          if ((cnt.get(key) ?? 0) > 0) {
            // the plain quote closes a quote that holds the escaped one (`"mysql -p\"x"`); with none it ends nothing
            how[o] = HOW_CUT;
            link[o] = i;
          } else {
            lost.push(o);
          }
        }
      }
      if ((cnt.get(key) ?? 0) > 0) {
        for (;;) {
          const o = stack[--sp]!;
          const k = keys[o]!;
          cnt.set(k, cnt.get(k)! - 1);
          link[o] = i;
          if (k === key) {
            how[o] = HOW_CLOSED;
            role[i] = ROLE_CLOSE;
            link[i] = o;
            break;
          }
          how[o] = HOW_CUT;
        }
      } else if (level !== 0 || !proseQuote(s, p)) {
        role[i] = ROLE_OPEN;
        par[i] = sp > 0 ? stack[sp - 1]! : -1;
        stack[sp++] = i;
        cnt.set(key, (cnt.get(key) ?? 0) + 1);
      }
      top[i] = sp > 0 ? stack[sp - 1]! : -1;
      if (inComment) vscan = Math.max(vscan, p + 1);
      i++;
    }
    for (let k = 0; k < sp; k++) lost.push(stack[k]!);
    lost.sort((a, b) => a - b);
    this.lost = lost;
    this.count = i;
    this.pos = pos;
    this.lvl = lvl;
    this.role = role;
    this.link = link;
    this.how = how;
    this.top = top;
    this.par = par;
    this.keys = keys;
  }

  /** The number of the mark at s[p], or -1. */
  index(p: number): number {
    const i = lowerBound(this.pos, this.count, p);
    return i < this.count && this.pos[i] === p ? i : -1;
  }

  /** For every mark the next PLAIN mark of its kind (`nx0`), and the next mark of its kind and level (`nxl`); built on first use. */
  nextMarks(): [Int32Array, Int32Array] {
    if (this.nx0 === null || this.nxl === null) {
      const n = this.count;
      const nx0 = new Int32Array(n).fill(-1);
      const nxl = new Int32Array(n).fill(-1);
      const last0 = [-1, -1];
      const lastK = new Map<number, number>();
      for (let i = n - 1; i >= 0; i--) {
        const k = this.keys[i]!;
        nx0[i] = last0[k & 1]!;
        nxl[i] = lastK.get(k) ?? -1;
        if (k < 2) last0[k & 1] = i;
        else lastK.set(k, i);
      }
      this.nx0 = nx0;
      this.nxl = nxl;
    }
    return [this.nx0, this.nxl];
  }
}

/**
 * The ONE reader of a value that follows a secret-taking name or option (twin of `_Reader`). It starts where the value starts and understands
 * every way a shell, a JSON string or a log line quotes it: bare (a backslash keeps a blank), `'..'`, `".."`, ANSI-C `$'..'`, locale `$".."`, and
 * the ESCAPED form a quote takes inside saved JSON evidence (`\".."`, `\\\".."` when the JSON was serialised twice). A quoted value is whatever
 * the lexer (above) says it is: it runs through its closing quote honouring backslash escapes (a quote preceded by an odd run of backslashes is
 * content, as is a blank, a tab, a newline, `;&|`, `#`, unicode ...), across lines; an escaped-form value closes at a quote preceded by exactly
 * the opening run and ends before a plain quote (the closing quote of the JSON string that holds it); a quote nested in another ends before the
 * quote that closes the outer one; a quote that never closes runs to the end of its line. A quoted value always is a value, even when it holds
 * only blanks or punctuation. One instance reads one string and keeps no state that depends on the order of the questions: the lexer is
 * immutable once built (lazily, on the first question), the end of a line is a pure memo.
 */
class Reader {
  readonly s: string;
  /** set by `opener`: the quote, the length of the opener, the backslashes before the quote */
  q = "";
  oplen = 0;
  run = 0;
  /** set by `quoted` / `read`: the length of the closing quote (0 when the value ends at a line end or before the quote of an enclosing one) */
  closeLen = 0;
  private lexer: Lexer | null = null;
  private eolFrom = 1;
  private eolTo = 0;
  private eols: number[] | null = null;

  constructor(s: string) {
    this.s = s;
  }

  get lex(): Lexer {
    return (this.lexer ??= new Lexer(this.s));
  }

  /** Whether a quote opens at s[i] (`"`, `'`, `$'`, `$"`, `\"`, `$\"` ...); then `q`, `oplen` and `run` describe it. */
  opener(i: number): boolean {
    const s = this.s;
    let j = i;
    let c = s.charAt(j);
    if (c === "$") {
      j++;
      c = s.charAt(j);
    }
    if (c === '"' || c === "'") {
      this.q = c;
      this.oplen = j + 1 - i;
      this.run = 0;
      return true;
    }
    if (c !== "\\") return false;
    let r = j;
    while (s.charCodeAt(r) === 92) r++;
    const q = s.charAt(r);
    if ((r - j) % 2 === 1 && (q === '"' || q === "'")) {
      this.q = q;
      this.oplen = r + 1 - i;
      this.run = r - j;
      return true;
    }
    return false;
  }

  lineEnd(i: number): number {
    if (this.eolFrom <= i && i <= this.eolTo) return this.eolTo;
    const s = this.s;
    let e = i;
    while (e < s.length && s.charCodeAt(e) !== 10 && s.charCodeAt(e) !== 13) e++;
    this.eolFrom = i;
    this.eolTo = e;
    return e;
  }

  /** End of the quoted value that opens at s[i]; `closeLen` is the length of its closing quote. */
  quoted(i: number, q: string, oplen: number, run: number): number {
    const lex = this.lex;
    const m = lex.index(i + oplen - 1);
    this.closeLen = 0;
    if (m < 0) return this.lineEnd(i);
    if (lex.role[m] === ROLE_OPEN && lex.lvl[m] === run) {
      const how = lex.how[m]!;
      if (how === HOW_UNCLOSED) return this.lineEnd(i);
      const t = lex.link[m]!;
      if (how === HOW_CLOSED) {
        this.closeLen = run !== 0 ? run + 1 : 1;
        return lex.pos[t]! + 1;
      }
      const end = lex.pos[t]! - lex.lvl[t]!;
      if (run !== 0) {
        // an escaped quote ends before the plain quote that closes the JSON string holding it (`"mysql -p\"x"`); any other escaped quote that was
        // cut by an enclosing one is ambiguous (`OPTS='sshpass -p "x'y' z'`): its value runs at least to the end of its line
        return this.s.charAt(lex.pos[t]!) === q ? end : Math.max(end, this.lineEnd(i));
      }
      // a plain quote cut by an enclosing close (`echo a 'x` ... `P="x 'y` ... `s3cr3t"`) is a value that runs to its matching quote, the next plain
      // quote of its kind, which the fallback below finds; a multi-line value stays whole
    }
    // the lexer takes this quote for a closer or for prose: read it as an opener of its own kind (the next quote that ends it)
    const [nx0, nxl] = lex.nextMarks();
    const t0 = nx0[m]!;
    const t1 = run !== 0 && lex.lvl[m] === run ? nxl[m]! : -1;
    if (t1 >= 0 && (t0 < 0 || t1 < t0)) {
      this.closeLen = run + 1;
      return lex.pos[t1]! + 1;
    }
    if (t0 >= 0) {
      if (run === 0) {
        this.closeLen = 1;
        return lex.pos[t0]! + 1;
      }
      return lex.pos[t0]!;
    }
    return this.lineEnd(i);
  }

  /**
   * End of the value at s[i] (`i` when there is none: no bare character, or a quote with nothing after it); `closeLen` as in `quoted`. The value
   * is a shell WORD: segments that touch the one just read belong to it (`-p'a b'c`, `-p"x"'y'`, `x=ab'c'`): a quote the lexer opens (or one it
   * takes for prose) continues it, a quote it closes ends it (the closing quote of an enclosing string), and after a quoted segment a bare
   * character continues it unless it is a closer of prose or Markdown.
   */
  read(i: number, kind: number): number {
    const s = this.s;
    let end: number;
    let quoted: boolean;
    if (this.opener(i)) {
      end = this.quoted(i, this.q, this.oplen, this.run);
      if (!(this.closeLen !== 0 || end > i + this.oplen)) return i;
      quoted = this.closeLen > 0;
    } else {
      end = bareEnd(s, i, kind);
      this.closeLen = 0;
      if (end === i) return i;
      quoted = false;
    }
    const n = s.length;
    let lex: Lexer | null = null;
    let nxt = -1; // the number of the mark that follows the last segment, when that segment was a closed quote
    while (end < n) {
      const c = s.charCodeAt(end);
      if (c === 34 || c === 39 || c === 92 || c === 36) {
        if (!this.opener(end)) {
          const e2 = bareEnd(s, end, kind);
          if (e2 === end) break;
          end = e2;
          quoted = false;
          nxt = -1;
          continue;
        }
        lex ??= this.lex;
        const qpos = end + this.oplen - 1;
        const k = nxt >= 0 && nxt < lex.count && lex.pos[nxt] === qpos ? nxt : lex.index(qpos);
        const role = k >= 0 ? lex.role[k]! : ROLE_CONTENT;
        if (role === ROLE_CLOSE) break;
        if (role === ROLE_OPEN && lex.lvl[k] === this.run) {
          if (lex.how[k] === HOW_CLOSED) {
            const t = lex.link[k]!;
            this.closeLen = this.run !== 0 ? this.run + 1 : 1;
            end = lex.pos[t]! + 1;
            nxt = t + 1;
          } else {
            end = this.quoted(end, this.q, this.oplen, this.run);
            nxt = -1;
          }
        } else {
          // a quote that is prose (`it's`): part of the word, unless its mate on the line closes a quoted segment (`x'a b'`)
          end = this.run === 0 ? this.proseEnd(qpos) : qpos + 1;
          if (this.run !== 0) this.closeLen = 0;
          nxt = -1;
        }
        quoted = true;
      } else if (quoted && !GLUE_STOPS.includes(s.charAt(end))) {
        const e2 = bareEnd(s, end, kind);
        if (e2 === end) break;
        end = e2;
        quoted = false;
        nxt = -1;
      } else {
        break;
      }
    }
    return end;
  }

  /**
   * `end` of a PLAIN quote the lexer takes for prose (`x'a b'`, `ab'c d'ef`) read as the opener of a quoted segment when its mate, the next plain
   * quote of its kind, lies on the same line (twin of `_Reader.prose_end`); else `qpos + 1`, the quote is part of the word. Sets `closeLen`.
   */
  proseEnd(qpos: number): number {
    const lex = this.lex;
    const m = lex.index(qpos);
    const t = m >= 0 ? lex.nextMarks()[0][m]! : -1;
    if (t >= 0 && lex.pos[t]! < this.lineEnd(qpos)) {
      this.closeLen = 1;
      return lex.pos[t]! + 1;
    }
    this.closeLen = 0;
    return qpos + 1;
  }

  /**
   * Whether the quote of the opener at s[i] CLOSES a quote that opened earlier on the same line (`"mysql -p" ok`: a prose quote, not the opener
   * of a password). Two things make that reading unsafe, and the quote is then taken for an opener (over-redacting): the lexer pairs it with a
   * quote on an earlier line, or a quote that nothing closes follows it on its line (`'x && mysql -p'***' db`: the quoting of the line is
   * unbalanced, so the pairing may be an accident of a stray quote).
   */
  closes(i: number, oplen: number, run: number): boolean {
    const lex = this.lex;
    const m = lex.index(i + oplen - 1);
    if (m < 0 || lex.role[m] !== ROLE_CLOSE || lex.lvl[m] !== run) return false;
    let eols = this.eols;
    if (eols === null) {
      eols = this.eols = [];
      const s = this.s;
      for (let k = 0; k < s.length; k++) {
        const c = s.charCodeAt(k);
        if (c === 10 || c === 13) eols.push(k);
      }
    }
    const at = lex.pos[m]!;
    if (lowerBound(eols, eols.length, lex.pos[lex.link[m]!]!) !== lowerBound(eols, eols.length, at)) return false;
    const j = lowerBound(lex.lost, lex.lost.length, m + 1); // the first quote that nothing closes after mark m
    return !(j < lex.lost.length && lex.pos[lex.lost[j]!]! < this.lineEnd(at));
  }

  /**
   * Where the quote that encloses s[p] ends (before its closing quote), or -1 when none does or it never closes. A closing quote right after `=`
   * is not taken for the close of the enclosing quote (`Cookie: a=1; session="x"`: it opens a cookie value): the next enclosing quote out is
   * tried (at most 4 levels, then -1: the caller reads to the end of the line).
   */
  enclosingEnd(p: number): number {
    const lex = this.lex;
    const m = lowerBound(lex.pos, lex.count, p) - 1;
    let o = m >= 0 ? lex.top[m]! : -1;
    for (let hop = 0; hop < 4; hop++) {
      if (o < 0 || lex.how[o] === HOW_UNCLOSED) return -1;
      const t = lex.link[o]!;
      const end = lex.pos[t]! - lex.lvl[t]!;
      if (end === 0 || this.s.charCodeAt(end - 1) !== 61) return end;
      o = lex.par[o]!;
    }
    return -1;
  }
}

// A masked value can swallow the NAME of a further sensitive assignment (`password=a,token: b`, `key=a,token = b`,
// `key=a;--password b`) and stop right before that assignment's own value (a quote, or whitespace around the `:`/`=`/flag):
// the value goes on. A quote right after any other value is not taken as a continuation: it is as likely the closing quote
// of an enclosing string.
const TAIL_NAME = /(?<![A-Za-z0-9_])(?<![A-Za-z0-9_][.-])([A-Za-z_][A-Za-z0-9_.-]{0,127})([=:]?)/y;
const TAIL_FLAG = /(?<![A-Za-z0-9_-])--?[A-Za-z][A-Za-z0-9_-]{0,63}/y;
const AFTER_NAME = /[ \t]{0,32}([=:])[ \t]{0,32}/y;
const AFTER_SEP = /[ \t]{0,32}/y;
const FLAG_GAP = /[ \t]{1,8}(?![- \t\r\n\f\v])/y;
const TAIL_WINDOW = 200;
const TAIL_DELIMS = [",", ";", "&", "/", "?"];

/** `re` (sticky) matched at `at`, or null. */
function stickyAt(re: RegExp, s: string, at: number): RegExpExecArray | null {
  re.lastIndex = at;
  return re.exec(s);
}

/** `re` (sticky) must match exactly s[lo, end). */
function stickySpan(re: RegExp, s: string, lo: number, end: number): RegExpExecArray | null {
  const m = stickyAt(re, s, lo);
  return m !== null && lo + m[0].length === end ? m : null;
}

/** End of the next value segment that still belongs to the secret whose value spans s[pos, end), or -1. */
function continuation(s: string, pos: number, end: number, kind: number, rd: Reader): number {
  const c = s.charAt(end);
  if (c !== " " && c !== "\t" && c !== '"' && c !== "'" && !s.startsWith('\\"', end)) return -1;
  let lo = Math.max(pos, end - TAIL_WINDOW);
  const win = s.slice(lo, end);
  const cut = Math.max(...TAIL_DELIMS.map((d) => win.lastIndexOf(d)));
  if (cut >= 0) lo += cut + 1;
  else if (end - pos > TAIL_WINDOW) return -1;
  let gap: RegExpExecArray | null;
  let colon = false; // `NAME://...` is a URL, not `NAME: value` (see scan)
  const a = stickySpan(TAIL_NAME, s, lo, end);
  if (a !== null) {
    if (a[2] !== "") {
      if (!isNameHit(a[1]!, a[2] === "=")) return -1;
      gap = stickyAt(AFTER_SEP, s, end);
      colon = a[2] === ":";
    } else {
      gap = stickyAt(AFTER_NAME, s, end);
      if (gap === null || !isNameHit(a[1]!, gap[1] === "=")) return -1;
      colon = gap[1] === ":";
    }
  } else if (stickySpan(TAIL_FLAG, s, lo, end) !== null && isNameHit(s.slice(lo, end), false)) {
    gap = stickyAt(FLAG_GAP, s, end);
  } else {
    return -1;
  }
  if (gap === null) return -1;
  if (colon && s.startsWith("//", gap.index + gap[0].length)) return -1;
  const gapEnd = gap.index + gap[0].length;
  const valueEnd = rd.read(gapEnd, kind);
  return valueEnd === gapEnd ? -1 : valueEnd;
}

/** Loose verdict for `=` separators, strict (prose-safe) otherwise: the same rule as `_name_hit` in swarm/redact.py. */
const isNameHit = (name: string, eq: boolean): boolean => (eq ? isSensitiveKey(name) : isStrictKey(name));

// `<vendor>_KEY=<long mixed-case value>` (`STRIPE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`): a bare `*_key` name is too common to mask on its
// name alone (`sort_key=name`), so it is masked when the VALUE looks like a credential (>= 20 token-alphabet characters, upper and
// lower case and a digit: not a hash, uuid, path or kebab-case id) and the word before `key` is not a lookup-style qualifier.
// Twin of `_key_name` / `_long_secret` / `_key_value_end` in swarm/redact.py.
const VENDOR_KEY_SKIP = words(
  "cache sort idempotency partition primary foreign public lookup group shard unique index hash object route routing dedupe dedup lock message",
);
const LONG_SECRET = /^[A-Za-z0-9_+\/=~.-]{20,}$/;
/** The value of a `*_key` name inside running text: the WHOLE token (never length-capped), optionally quoted. */
const KEY_VALUE = /(\\?["']?)([A-Za-z0-9_+\/=~.-]{20,})/y;
const KEY_NAMES = new Map<string, boolean>();
function isKeyName(name: string): boolean {
  let hit = KEY_NAMES.get(name);
  if (hit === undefined) {
    const toks = nameTokens(name);
    hit = toks.length > 1 && toks[toks.length - 1] === "key" && VENDOR_KEY_SKIP[toks[toks.length - 2]!] !== true;
    if (KEY_NAMES.size >= VERDICT_CAP) KEY_NAMES.clear();
    KEY_NAMES.set(name, hit);
  }
  return hit;
}
function isLongSecret(value: string): boolean {
  let a = 0;
  let b = value.length;
  while (a < b && "\"'\\".includes(value.charAt(a))) a++;
  while (b > a && "\"'\\".includes(value.charAt(b - 1))) b--;
  const v = value.slice(a, b);
  return LONG_SECRET.test(v) && /[0-9]/.test(v) && /[a-z]/.test(v) && /[A-Z]/.test(v);
}
/** A sensitive name, or a `*_key` name with a credential-shaped string value (twin of `_credential_pair`). */
const isCredentialPair = (name: string, value: string): boolean => isSensitiveKey(name) || (isKeyName(name) && isLongSecret(value));
/**
 * [end of the credential-shaped token (and its closing quote) that starts at s[pos], or -1; end of the token run that was checked, or 0].
 * A failed check fails for every later start inside the same run: a suffix has no more character classes.
 */
function keyValueEnd(s: string, pos: number): [number, number] {
  const m = stickyAt(KEY_VALUE, s, pos);
  if (m === null) return [-1, 0];
  const runEnd = pos + m[0].length;
  if (!isLongSecret(m[2]!)) return [-1, runEnd];
  const q = m[1]!;
  return [q !== "" && s.startsWith(q, runEnd) ? runEnd + q.length : runEnd, runEnd];
}

// ---- command-line credentials (twins of the `_cli_*` helpers in swarm/redact.py; explicit ASCII whitespace classes: JS and Python `\s` differ) ----
// A command line is read WORD by word, never through a regex that has to skip arguments (a bound on their length or number hid the password
// after a long argument): a word ends at a blank or at `;&|`, a segment (the arguments of one command) at a newline or `;&|`. An option is a
// word that starts with `-` after any run of delimiters (backtick, quote, bracket, Markdown emphasis ...); its value is the next word (or the
// attached `=value` / `-pVALUE`), taken whole. Nothing is capped and every scan moves forward only (failed checks are remembered).
const CLI_BREAKS = "\r\n\f\v;&|";
const CLI_STOPS = ` \t${CLI_BREAKS}`;
const CLI_WORD = /[^ \t\r\n\f\v;&|]+/g;
/** A lookup table without a prototype: a command word such as `constructor` or `__proto__` finds nothing. */
const table = <V>(entries: Record<string, V>): Record<string, V> => Object.assign(Object.create(null) as Record<string, V>, entries);
const CLI_UID_GID = /^[0-9]{1,10}:[0-9]{1,10}$/;
const CLI_CORE = /[A-Za-z0-9_./-]+/;
const isAlnumDash = (c: number): boolean => (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c === 45;
const isNameChar = (c: number): boolean => isAlnumDash(c) || c === 95 || c === 46;
/** A character of a command name: no `_` (no command name has one; `__mysql` is emphasis). */
const isCommandChar = (c: number): boolean => isAlnumDash(c) || c === 46;
const GLUE_STOPS = "`\"')]}>*,;/?&|"; // a value glued to a quoted segment ends here; `_`, `.`, `:` continue it (shell word characters)
const CLI_CLOSERS = "`\"')]}>*_.,;:"; // Markdown/prose closers: a word `-p` followed by only these is a flag in prose, not `-pPASSWORD`
const CLI_MYSQL = 1;
const CLI_LOGIN = 2;
const CLI_REDIS = 4;
const CLI_SQLCMD = 8;
const CLI_KEYGEN = 16;
const CLI_COMMANDS = table<number>({
  mysql: CLI_MYSQL,
  mysqladmin: CLI_MYSQL,
  mysqldump: CLI_MYSQL,
  mysqlcheck: CLI_MYSQL,
  mysqlimport: CLI_MYSQL,
  mysqlshow: CLI_MYSQL,
  mysqlpump: CLI_MYSQL,
  mysqlsh: CLI_MYSQL,
  mariadb: CLI_MYSQL,
  "mariadb-dump": CLI_MYSQL,
  "mariadb-admin": CLI_MYSQL,
  sshpass: CLI_LOGIN,
  mongo: CLI_LOGIN,
  mongosh: CLI_LOGIN,
  mongodump: CLI_LOGIN,
  mongorestore: CLI_LOGIN,
  mongoexport: CLI_LOGIN,
  mongoimport: CLI_LOGIN,
  mongostat: CLI_LOGIN,
  mongotop: CLI_LOGIN,
  "redis-cli": CLI_REDIS,
  sqlcmd: CLI_SQLCMD,
  bcp: CLI_SQLCMD,
  "ssh-keygen": CLI_KEYGEN,
});
/** First words of a two-word command: 1 = `<word> login`, 2 = `helm registry login`. */
const CLI_TWO_WORD = table<number>({ docker: 1, podman: 1, buildah: 1, skopeo: 1, crane: 1, oras: 1, nerdctl: 1, az: 1, helm: 2 });
/** The flag letter of each command kind. */
const CLI_FLAG_KINDS = table<number>({ p: CLI_MYSQL | CLI_LOGIN, a: CLI_REDIS, P: CLI_SQLCMD, N: CLI_KEYGEN });
const CLI_COMMAND_HINTS = ["mysql", "mariadb", "sshpass", "login", "mongo", "redis-cli", "sqlcmd", "bcp", "ssh-keygen"];

/** Whether `s[from, to)` holds a character that is neither a space nor a tab. */
function hasNonBlank(s: string, from: number, to: number): boolean {
  for (let i = from; i < to; i++) {
    const c = s.charCodeAt(i);
    if (c !== 32 && c !== 9) return true;
  }
  return false;
}
/** Whether `s[from, to)` holds a character that is not one of the closers in `CLI_CLOSERS`. */
function hasNonCloser(s: string, from: number, to: number): boolean {
  for (let i = from; i < to; i++) if (!CLI_CLOSERS.includes(s.charAt(i))) return true;
  return false;
}
/** Whether `s[from, to)` holds `ch` (a bounded scan: `indexOf` would read past `to`). */
function hasChar(s: string, ch: string, from: number, to: number): boolean {
  for (let i = from; i < to; i++) if (s.charAt(i) === ch) return true;
  return false;
}
/**
 * Whether the flag at s[at, at + 2) (`-p`) of the word that ends at `end` carries its value attached: `-pVALUE`, `-p'VALUE'`, `-p$'VALUE'`, `-p\"VALUE\"`.
 * A quote that opens the value (one that the lexer does not take for the CLOSE of an earlier quote of the line: `"mysql -p" ok` and `"-p"` are
 * prose) makes everything after it the value, even when it is only punctuation or blanks (`-p" x"`, `-p\" x\"`); an unquoted value is one
 * unless it is made only of closers (`-p`, `(-p)`, `**-p**`) that end the word.
 */
function cliAttached(rd: Reader, at: number, end: number): boolean {
  let v = at + 2;
  if (v >= end) return false;
  if (rd.opener(v)) {
    if (!rd.closes(v, rd.oplen, rd.run)) return true;
    v += rd.oplen; // the quote CLOSES an earlier one: only what follows it can be a value (`"mysql -p" ok`, `\"mysql -p\" ok`)
  }
  return hasNonCloser(rd.s, v, end);
}

/**
 * The values of `-u user:pass` (and `--user`, `--proxy-user`, also `=user:pass`) and of `-passin pass:X` (`-pass`, `-passout`): the option may
 * follow ANY character that is not a letter, digit or `-` (a backtick, a quote, a bracket, `=`, the `_` of Markdown emphasis ...).
 */
function cliOptionSpans(s: string, spans: number[], rd: Reader): void {
  if (!s.includes("-u") && !s.includes("-pass")) return;
  const n = s.length;
  let runFrom = -1; // [runFrom, runTo) holds no blank, `;&|` or quote: the unquoted run every start inside it ends at
  let runTo = -1;
  let colonFrom = 0; // no ':' in [colonFrom, colonAt), a ':' at colonAt (n when there is none)
  let colonAt = -1;
  let pushedEnd = -1; // end of the last value reported: a later start (starts only grow) inside that run is covered
  for (let at = s.indexOf("-"); at >= 0; at = s.indexOf("-", at + 1)) {
    if (at > 0 && isAlnumDash(s.charCodeAt(at - 1))) continue; // the option does not continue a word
    // the option (`CLI_OPTION` in swarm/redact.py, spelled out: a regex match object per candidate costs more than the scan)
    const c1 = s.charCodeAt(at + 1);
    let end = -1;
    let openssl = false;
    if (c1 === 117) end = at + 2; // -u
    else if (c1 === 45) end = s.startsWith("--user", at) ? at + 6 : s.startsWith("--proxy-user", at) ? at + 12 : -1;
    else if (c1 === 112 && s.startsWith("-pass", at)) {
      end = s.startsWith("in", at + 5) ? at + 7 : s.startsWith("out", at + 5) ? at + 8 : at + 5;
      openssl = true;
    }
    if (end < 0) continue;
    const after = s.charCodeAt(end);
    const blank = after === 32 || after === 9;
    if (!blank && (openssl || after !== 61)) continue; // `=` follows the -u family only
    let start = end;
    if (blank) {
      while (start < n && (s.charCodeAt(start) === 32 || s.charCodeAt(start) === 9)) start++;
    } else {
      start++;
    }
    if (start < pushedEnd || start >= n || CLI_STOPS.includes(s.charAt(start))) continue; // (a start inside a reported run is covered)
    const open = rd.opener(start);
    if (openssl) {
      // openssl: `-passin pass:SECRET`, `-passin "pass:SECRET"`, `-passin pass:"a b"`
      if (open) {
        const inner = start + rd.oplen;
        const ve = rd.quoted(start, rd.q, rd.oplen, rd.run);
        if (s.startsWith("pass:", inner) && ve - rd.closeLen > inner + 5) spans.push(inner + 5, ve - rd.closeLen);
      } else if (s.startsWith("pass:", start)) {
        const ve = rd.read(start + 5, BARE_CLI);
        if (ve > start + 5) spans.push(start + 5, ve);
      }
      continue;
    }
    if (open) {
      // the whole `user:pass` is quoted
      const oplen = rd.oplen;
      const ve = rd.quoted(start, rd.q, oplen, rd.run);
      if (hasChar(s, ":", start + oplen, ve)) {
        spans.push(start, ve);
        pushedEnd = ve;
      }
      continue;
    }
    if (!(runFrom <= start && start < runTo)) {
      runFrom = start;
      runTo = bareEnd(s, start, BARE_CLI);
    }
    if (!(colonFrom <= start && start <= colonAt)) {
      colonFrom = start;
      colonAt = s.indexOf(":", start);
      if (colonAt < 0) colonAt = n;
    }
    if (start >= colonAt || colonAt >= runTo) continue; // no user, or no `:` inside the word
    // a user, `:`, a password (both non-empty): bare, or quoted in any form (`curl -u me:'a b'`); a docker uid:gid pair is not a credential
    const p = colonAt + 1;
    let ve: number;
    if (rd.opener(p)) ve = rd.read(p, BARE_CLI);
    else if (p < runTo && !(colonAt - start <= 10 && runTo - colonAt <= 11 && CLI_UID_GID.test(s.slice(start, runTo)))) ve = runTo;
    else continue;
    if (ve > p) {
      spans.push(start, ve);
      pushedEnd = ve;
    }
  }
}

/**
 * The password flag of `mysql -pSECRET`, `docker login -p SECRET`, `sshpass -p`, `redis-cli -a`, `sqlcmd -P`, `ssh-keygen -N`: once a command is
 * seen in a segment its flag is checked in EVERY later word of that segment, however long or numerous the arguments.
 */
function cliCommandSpans(s: string, spans: number[], rd: Reader): void {
  if (!CLI_COMMAND_HINTS.some((h) => s.includes(h))) return;
  let active = 0; // command kinds seen in this segment
  let pending = 0; // a two-word command's next word
  let want = 0; // the next word is the value of a bare flag
  let prevEnd = 0;
  for (const m of s.matchAll(CLI_WORD)) {
    const start = m.index as number;
    const end = start + m[0].length;
    if ((start !== prevEnd + 1 || (s.charCodeAt(prevEnd) !== 32 && s.charCodeAt(prevEnd) !== 9)) && hasNonBlank(s, prevEnd, start)) {
      active = pending = want = 0; // a newline or `;&|`: the next command
    }
    prevEnd = end;
    if (want !== 0) {
      // the value is taken whole, and its words are still read: a value can hold the next command (`redis-cli -a $(sshpass -p x)`)
      want = 0;
      if (s.charAt(start) !== "-") {
        const valueEnd = rd.read(start, BARE_CLI);
        if (valueEnd > start) spans.push(start, valueEnd);
      }
    }
    let at = start;
    while (at < end && !isAlnumDash(s.charCodeAt(at))) at++; // `, ", (, [, <, ** ... may precede an option
    if (at < end && s.charAt(at) === "-") {
      pending = 0;
      const kinds = active !== 0 ? active & (CLI_FLAG_KINDS[s.charAt(at + 1)] ?? 0) : 0;
      if (kinds !== 0) {
        if (cliAttached(rd, at, end)) {
          // `-pSECRET`: the value touches the flag
          const valueEnd = rd.read(at + 2, BARE_CLI);
          if (valueEnd > at + 2) spans.push(at + 2, valueEnd);
          continue;
        }
        if (kinds !== CLI_MYSQL) want = 1; // a bare `-p` is a prompt for mysql, not for the others: the value is the next word
      }
    }
    if (pending !== 0) {
      const core = end - start <= 24 ? CLI_CORE.exec(s.slice(start, end)) : null;
      if (pending === 2) {
        pending = core !== null && core[0] === "registry" ? 3 : 0;
      } else {
        if (core !== null && core[0] === "login") active |= CLI_LOGIN;
        pending = 0;
      }
    }
    let baseStart = end;
    const floor = Math.max(start, end - 41);
    while (baseStart > floor && isCommandChar(s.charCodeAt(baseStart - 1))) baseStart--;
    if (baseStart < end && end - baseStart <= 40) {
      // the command name is the last run of name characters (`/usr/bin/mysql`, `$(mysql`)
      const base = s.slice(baseStart, end);
      const kind = CLI_COMMANDS[base];
      if (kind !== undefined) active |= kind;
      else {
        const two = CLI_TWO_WORD[base];
        if (two !== undefined) pending = two;
      }
    }
  }
}

function cliSpans(s: string, spans: number[], rd: Reader): void {
  cliOptionSpans(s, spans, rd);
  cliCommandSpans(s, spans, rd);
}

// ---- YAML: `- name: DB_PASSWORD` / `value: x` pairs (Kubernetes env) and block scalars (`password: |`) ----
const YAML_NAME =
  /(?<![^\n])[ \t]*(?:-[ \t]+)?name[ \t]*:[ \t]*(["']?)([A-Za-z_][A-Za-z0-9_.-]{0,127})\1[ \t]*(?:#[^\r\n]*)?\r?\n([ \t]*)value[ \t]*:[ \t]*(?=[^ \t\r\n#])/g;
const YAML_BLOCK_KEY = /(?<![^\n])([ \t]*)(?:-[ \t]+)?["']?([A-Za-z_][A-Za-z0-9_.-]{0,127})["']?[ \t]*:[ \t]*(?=[|>])/g;
const YAML_INDICATOR = /[|>][+-]?[0-9]?[+-]?[ \t]*(?:#[^\r\n]*)?(?=\r?\n|$)/y;
const isBlankLine = (line: string): boolean => {
  for (let i = 0; i < line.length; i++) {
    const c = line.charCodeAt(i);
    if (c !== 32 && c !== 9 && c !== 13) return false;
  }
  return true;
};
const indentOf = (line: string): number => {
  let i = 0;
  while (i < line.length && (line.charCodeAt(i) === 32 || line.charCodeAt(i) === 9)) i++;
  return i;
};
/** End of the scalar that begins at s[start]: a block scalar (`|`, `>-`, ...) runs through the following lines indented deeper than `indent` (blank lines inside it included); any other value is the rest of its line. */
function yamlValueEnd(s: string, start: number, indent: number): number {
  const ind = stickyAt(YAML_INDICATOR, s, start);
  if (ind === null) {
    let eol = s.indexOf("\n", start);
    if (eol < 0) eol = s.length;
    return s.charAt(eol - 1) === "\r" ? eol - 1 : eol;
  }
  let end = start + ind[0].length;
  let pos = s.indexOf("\n", end);
  while (pos >= 0) {
    const ls = pos + 1;
    let le = s.indexOf("\n", ls);
    if (le < 0) le = s.length;
    const line = s.slice(ls, le);
    if (!isBlankLine(line)) {
      if (indentOf(line) <= indent) break;
      end = line.endsWith("\r") ? le - 1 : le;
    }
    pos = le < s.length ? le : -1;
  }
  return end;
}
function yamlSpans(s: string, spans: number[], rd: Reader): void {
  if (!s.includes("\n") || !s.includes(":")) return;
  for (const [rx, indentGroup] of [[YAML_NAME, 3], [YAML_BLOCK_KEY, 1]] as const) {
    let last = 0;
    for (const m of s.matchAll(rx)) {
      const at = m.index as number;
      if (at < last || !isSensitiveKey(m[2]!)) continue;
      const start = at + m[0].length;
      let end = yamlValueEnd(s, start, m[indentGroup]!.length);
      if (s.charAt(start) === '"' || s.charAt(start) === "'") end = Math.max(end, rd.quoted(start, s.charAt(start), 1, 0)); // a quoted scalar runs to its closing quote, across lines
      if (end > start) {
        spans.push(start, end);
        last = end;
      }
    }
  }
}


/** Where the name (and its `=`/`:`) that a value may have swallowed at its end starts: at most 130 characters back, never before `floor` (twin of `_tail_name_start`). */
function tailNameStart(s: string, floor: number, end: number): number {
  let i = end;
  if (i > floor && (s.charAt(i - 1) === "=" || s.charAt(i - 1) === ":")) i--;
  const low = Math.max(floor, i - 130);
  while (i > low && isNameChar(s.charCodeAt(i - 1))) i--;
  return i;
}

/** Report the value that follows every sensitive `NAME<sep>` match of `sep`: one forward pass with a cursor (no recursion, no rescans). */
function scan(s: string, sep: RegExp, kind: number, eqSep: boolean, spans: number[], rd: Reader): void {
  let keyFail = 0;
  sep.lastIndex = 0;
  for (let m = sep.exec(s); m !== null; m = sep.exec(s)) {
    const pos = sep.lastIndex;
    if (eqSep && !m[2]!.includes("=") && s.startsWith("//", pos)) continue; // `auth://host`, `token://x`: a URL scheme, not `NAME: value`
    const name = m[1]!;
    const hit = isNameHit(name, eqSep && m[2]!.includes("="));
    if (!hit && !isKeyName(name)) continue;
    const start = pos;
    let end: number;
    if (hit) {
      end = rd.read(pos, kind);
      if (end === pos) continue;
      // an already-masked value (`[REDACTED],x`) ends where the mask does, like a closing quote
      if (s.startsWith(REDACTED, start) && end > start + REDACTED.length && !/[A-Za-z0-9]/.test(s.charAt(start + REDACTED.length))) end = start + REDACTED.length;
    } else {
      if (pos < keyFail) continue; // inside a token run whose tail was already found not to look like a credential
      const [keyEnd, runEnd] = keyValueEnd(s, pos);
      if (keyEnd < 0) {
        keyFail = Math.max(keyFail, runEnd);
        continue;
      }
      end = keyEnd;
    }
    for (let more = continuation(s, pos, end, kind, rd); more >= 0; more = continuation(s, pos, end, kind, rd)) end = more;
    spans.push(start, end);
    // a value can end with the NAME of the next assignment (`--api-key = {--password=\"x\"`): scanning resumes where that name starts
    sep.lastIndex = tailNameStart(s, pos, end);
  }
}

/** End of the header value that begins at `valueStart`: the end of its line, or the quote that closes the quote it sits in (`-H "Cookie: a=b"`), whichever comes first. A quote inside the value (`a="b c"; d=e`, a stray apostrophe) is part of it: the lexer, not a local scan for the next quote, says which quote encloses the header. */
function headerEnd(rd: Reader, valueStart: number): number {
  const end = rd.lineEnd(valueStart);
  const close = rd.enclosingEnd(valueStart);
  return close > valueStart && close < end ? close : end; // a header that starts at the closing quote of an enclosing one is ambiguous: its line
}

function headerSpans(s: string, spans: number[], rd: Reader): void {
  let pos = 0;
  for (;;) {
    HEADER.lastIndex = pos;
    const m = HEADER.exec(s);
    if (m === null) return;
    const start = m.index + m[0].length;
    pos = headerEnd(rd, start);
    spans.push(start, pos);
  }
}

/** "name": "value", 'name': 'value', "name": 'value' (JSON, Python repr, JS): the head up to the opening quote of the value. */
const PAIR = /(?<q>["'])(?<name>[^"'\\\n]{1,80})\k<q>[ \t]{0,8}:[ \t]{0,8}(?=["'])/g;
/** JSON that was serialised again into a string: {\"password\": \"x\"}, and again: {\\\"password\\\": \\\"x\\\"} (the quotes carry an odd run of backslashes). */
const ESC_PAIR = /(?<!\\)(?<bs>\\{1,31})"(?<name>[^"'\\\n]{1,80})\k<bs>"[ \t]{0,8}:[ \t]{0,8}(?=\k<bs>")/g;

/**
 * "name": "value" pairs with a sensitive name (and `*_key` names with a credential-shaped value): the string between the quotes. The value is
 * read to its closing quote (to the end of the line when it never closes); a benign pair does not hide a pair INSIDE its value
 * (`{"message": "login 'password': 'x'"}`): the scan goes on inside it.
 */
function pairSpans(s: string, spans: number[], rd: Reader): void {
  PAIR.lastIndex = 0;
  for (let m = PAIR.exec(s); m !== null; m = PAIR.exec(s)) {
    const p = PAIR.lastIndex;
    const end = rd.quoted(p, s.charAt(p), 1, 0);
    const close = rd.closeLen;
    if (end - close > p + 1 && isCredentialPair(m.groups!.name!, s.slice(p + 1, end - close))) {
      spans.push(p + 1, end - close);
      PAIR.lastIndex = end;
    } else {
      PAIR.lastIndex = p + 1;
    }
  }
  ESC_PAIR.lastIndex = 0;
  for (let m = ESC_PAIR.exec(s); m !== null; m = ESC_PAIR.exec(s)) {
    const p = ESC_PAIR.lastIndex;
    const run = m.groups!.bs!.length;
    ESC_PAIR.lastIndex = p + run + 1;
    if (run % 2 === 0 || !isSensitiveKey(m.groups!.name!)) continue;
    const end = rd.quoted(p, '"', run + 1, run);
    if (end - rd.closeLen > p + run + 1) {
      spans.push(p + run + 1, end - rd.closeLen);
      ESC_PAIR.lastIndex = end;
    }
  }
}

/**
 * Every [start, end) of `s` that must disappear, as a flat list. EVERY detector reads the original text and only reports: nothing is
 * rewritten while detecting, so no detector can measure a value another detector has already shortened, and the union in `redactText`
 * does not depend on the order in which they run.
 */
function collectSpans(s: string): number[] {
  const spans: number[] = [];
  for (const m of s.matchAll(PEM)) spans.push(m.index as number, (m.index as number) + m[0].length);
  for (const rx of [BEARER, BASIC]) {
    for (const m of s.matchAll(rx)) spans.push((m.index as number) + m[1]!.length, (m.index as number) + m[0].length);
  }
  for (const m of s.matchAll(USERINFO)) spans.push((m.index as number) + m[1]!.length, (m.index as number) + m[0].length - 1); // the '@' stays
  const rd = new Reader(s);
  headerSpans(s, spans, rd);
  cliSpans(s, spans, rd);
  yamlSpans(s, spans, rd);
  scan(s, NAME_SEP, BARE_NAME, true, spans, rd);
  scan(s, FLAG_SEP, BARE_FLAG, false, spans, rd);
  for (const rx of [JWT, ...TOKENS]) {
    for (const m of s.matchAll(rx)) spans.push(m.index as number, (m.index as number) + m[0].length);
  }
  pairSpans(s, spans, rd);
  return spans;
}

/** Disjoint, ascending `[start, end)` ranges as a flat list (`[s0, e0, s1, e1, ...]`): the union of every detector's spans, overlapping or touching spans merged. */
function unionSpans(text: string): number[] {
  if (text.length < 6) return [];
  const spans = collectSpans(text);
  if (spans.length === 0) return spans;
  // union by coverage count: +1 at every start, -1 at every end, then one linear walk (no sort)
  const cover = new Int32Array(text.length + 1);
  for (let i = 0; i < spans.length; i += 2) {
    cover[spans[i]!]!++;
    cover[spans[i + 1]!]!--;
  }
  const merged: number[] = [];
  let depth = 0;
  let open = -1;
  for (let i = 0; i < text.length; i++) {
    depth += cover[i]!;
    if (depth > 0) {
      if (open < 0) open = i;
    } else if (open >= 0) {
      merged.push(open, i);
      open = -1;
    }
  }
  if (open >= 0) merged.push(open, text.length);
  return merged;
}

/** TS twin of `swarm/redact.py redact_spans`: the disjoint, ascending `[start, end)` ranges `redactText` replaces (UTF-16 offsets). */
export function redactSpans(text: string): Array<[number, number]> {
  const flat = unionSpans(text);
  const out: Array<[number, number]> = [];
  for (let i = 0; i < flat.length; i += 2) out.push([flat[i]!, flat[i + 1]!]);
  return out;
}

/** TS port of `swarm/redact.py redact_text`: every secret-looking span becomes `[REDACTED]`; overlapping or touching spans of different detectors become ONE `[REDACTED]`. */
export function redactText(text: string): string {
  const merged = unionSpans(text);
  if (merged.length === 0) return text;
  const out: string[] = [];
  let last = 0;
  for (let i = 0; i < merged.length; i += 2) {
    out.push(text.slice(last, merged[i]), REDACTED);
    last = merged[i + 1]!;
  }
  out.push(text.slice(last));
  return out.join("");
}

/** Controls, line separators and bidi overrides (display spoofing), replaced by a space or dropped. */
const CONTROLS = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g;
const BIDI = /[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;
/** Characters with no visible width or order that can split a secret (controls but tab/CR/LF, zero-width and bidi marks). */
const INVISIBLE = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]/g;

/**
 * Agent-supplied text made safe for a status row: ANSI and control characters stripped, secrets redacted (before clipping,
 * so a cut cannot hide half a token), whitespace collapsed, clipped to `max` columns. Non-strings give "".
 * Invisible characters are removed BEFORE redaction: a bidi mark, zero-width space or NUL inside a token defeats the patterns,
 * and deleting it afterwards would re-join the secret into the row.
 */
export function sanitizeText(input: unknown, max = 120): string {
  if (typeof input !== "string") return "";
  const base = stripAnsi(boundInput(input));
  const joined = base.replace(INVISIBLE, "");
  let text = redactText(joined);
  // nothing to hide in the joined view: keep the original's display (controls become spaces) and redact that view as before
  if (text === joined && joined !== base) text = redactText(base);
  const flat = text.replace(BIDI, "").replace(CONTROLS, " ").replace(/\s+/g, " ").trim();
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
  /** Measured token savings (`token-opt:savings`); absent until a valid snapshot arrives. */
  tokens?: TokenSavingsSnapshot;
  /** This session's id once known; after that a snapshot of another session is never shown. */
  sessionId?: string;
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

/** The token snapshot the bar may show: this session's, or any valid one while the session id is not known yet. */
export function shownTokens(model: SwarmBarModel): TokenSavingsSnapshot | undefined {
  const { tokens, sessionId } = model;
  return tokens !== undefined && (sessionId === undefined || tokens.sessionId === sessionId) ? tokens : undefined;
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
const AGENT_COLUMNS = 22; // the longest slug in the 30-agent roster (a30-custody-compliance) is shown whole

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

function prio(priority: number, part: Seg): BarSegment {
  return { ...part, priority };
}

/** Row 1, left: priorities 0 (mark, `swarm`, elapsed) .. 6 (slots); the lower the number the later it is dropped. */
function summary(model: SwarmBarModel, rows: readonly BarRun[], theme: BarTheme | undefined, mark: Seg, nowMono: number): BarSegment[] {
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
  const parts: BarSegment[] = [prio(0, { plain: `${mark.plain} swarm ${formatElapsed(elapsed)}`, painted: `${mark.painted} ${bold(theme, "swarm")} ${formatElapsed(elapsed)}` })];
  parts.push(prio(1, seg(theme, count.run > 0 ? "accent" : "dim", `${count.run} run`)));
  if (count.wait > 0) parts.push(prio(4, seg(theme, "dim", `${count.wait} wait`)));
  parts.push(prio(3, seg(theme, "dim", `${count.queued} queued`)));
  parts.push(prio(4, seg(theme, count.done > 0 ? "success" : "dim", `${count.done} done`)));
  parts.push(prio(2, seg(theme, count.fail > 0 ? "error" : "dim", `${count.fail} fail`)));
  if (count.timeout > 0) parts.push(prio(2, seg(theme, "error", `${count.timeout} timeout`)));
  if (count.stall > 0) parts.push(prio(2, seg(theme, "warning", `${count.stall} stall`)));
  if (count.cancel > 0) parts.push(prio(5, seg(theme, "muted", `${count.cancel} cancel`)));
  if (model.slotLimit !== undefined) parts.push(prio(6, seg(theme, "dim", `slots ${count.run}/${model.slotLimit}`)));
  if (model.error) parts.push(prio(2, seg(theme, "warning", "telemetry unavailable")));
  return parts;
}

/** Row 1, right: the token chips of the snapshot to show (none before the first measurement: zeros are never invented). */
function tokenSegments(model: SwarmBarModel, theme: BarTheme | undefined): BarSegment[] {
  const snapshot = shownTokens(model);
  if (snapshot === undefined) return [];
  return tokenChips(snapshot).map((chip) => ({ plain: chip.text, painted: chip.color === "text" ? chip.text : paint(theme, chip.color, chip.text), priority: chip.priority }));
}

const IDLE_ROW = "no subagents running";

/**
 * The bar as exactly 2 rows of exactly `width` columns (none for `width <= 0`) plus the state it is in. Idle and error keep the
 * two rows: a placeholder instead of the agents. Pure: the same inputs give the same output.
 */
export function renderSwarmBarFrame(model: SwarmBarModel, width: number, theme: BarTheme | undefined, nowMono: number): BarFrame {
  const base = barState(model, nowMono);
  if (width <= 0) return { lines: [], state: base, shown: 0, hidden: 0 };
  const chips = tokenSegments(model, theme);
  const rows = visibleRuns(model, nowMono);
  if (rows.length === 0) {
    const head = base === "error" ? seg(theme, "warning", "swarm: telemetry unavailable") : seg(theme, "dim", "○ swarm idle");
    const lines = [composeBar(theme, [prio(0, head)], chips, width), composeBar(theme, [prio(0, seg(theme, "dim", IDLE_ROW))], [], width)];
    return { lines, state: base, shown: 0, hidden: 0 };
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
  const line1 = composeBar(theme, summary(model, rows, theme, mark, nowMono), chips, width);

  const candidates = rows.slice(0, MAX_CANDIDATES);
  const cap = rows.length <= 2 ? 40 : rows.length <= 4 ? 24 : 16;
  const agentParts = (k: number, activityCap: number): BarSegment[] => {
    const parts = candidates.slice(0, k).map((run, index) => prio(index, rowSegment(run, theme, spin, nowMono, activityCap)));
    if (rows.length > k) parts.push(prio(k, seg(theme, "dim", `+${rows.length - k} more`)));
    return parts;
  };
  let line2: string | undefined;
  let shown = 0;
  // as many rows as fit, each with as much activity as fits; only then fewer rows
  for (let k = candidates.length; k >= 1 && line2 === undefined; k--) {
    for (const activityCap of [cap, 12, 0]) {
      const parts = agentParts(k, activityCap);
      if (fitsBar(theme, parts, [], width)) {
        line2 = composeBar(theme, parts, [], width);
        shown = k;
        break;
      }
    }
  }
  if (line2 === undefined) {
    // not even one row fits: the composer drops `+N more`, then clips the row
    line2 = composeBar(theme, agentParts(1, 0), [], width);
    shown = 1;
  }
  const hidden = rows.length - shown;
  return { lines: [line1, line2], state: hidden > 0 ? "overflow" : base, shown, hidden };
}

/** The bar as exactly 2 rows, each exactly `width` columns wide (no rows for `width <= 0`). */
export function renderSwarmBar(model: SwarmBarModel, width: number, theme: BarTheme | undefined, nowMono: number): string[] {
  return renderSwarmBarFrame(model, width, theme, nowMono).lines;
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
  /** Unsubscribe, clear timers, remove widget. Safe to call twice. */
  stop(): void;
}

type Mode = "off" | "ui" | "headless";

const RENDER_GAP_MS = 250;
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
  let trailingRender: unknown;
  let staleMs = 30_000;
  /** The last read document came from the in-session Task Store (`producer: "derived"`): written on transitions only, so mtime says nothing about liveness. */
  let liveDerived = false;
  let liveDir: string | undefined;
  let liveStamp: { mtime: number; size: number; changedMono: number } | undefined;
  const announced = new Set<string>();
  /** Set by `stop()`; only a new session (`session_start`/`session_switch`) clears it. */
  let closed = false;
  /** The widget is mounted: from the first eligible event until stop. An idle bar stays, drawn as the idle placeholder. */
  let mounted = false;
  let restack: Restack | undefined;

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

  /** Sets the widget, then announces the rank so every higher-ranked bar re-sets its own below this one. */
  function mountAndAnnounce(): void {
    ui?.setWidget?.(SWARM_BAR_KEY, factory, { placement: "aboveEditor" });
    restack?.announce();
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
    // widgets stack in insertion order and re-setting a key moves it to the bottom: re-set one tick after the events that move other
    // bars; the restack announcement then puts every higher-ranked bar back below this one
    later(() => {
      remountPending = false;
      if (mine === generation && mounted) safe(mountAndAnnounce);
    });
  }

  function render(force: boolean): void {
    if (mode !== "ui") return;
    const now = clock();
    if (!force && now - lastRender < RENDER_GAP_MS) return;
    lastRender = now;
    safe(() => host?.requestRender());
  }

  /** A new token snapshot: render now, or once the render gap has passed (snapshots are rare, so none may be left unpainted). */
  function renderTokens(): void {
    if (mode !== "ui") return;
    const wait = RENDER_GAP_MS - (clock() - lastRender);
    if (wait <= 0) return render(true);
    if (trailingRender !== undefined) return;
    const handle = timers.setTimeout(() => {
      trailingRender = undefined;
      safe(() => render(true));
    }, wait + 1);
    (handle as { unref?: () => void } | undefined)?.unref?.();
    trailingRender = handle;
  }

  /** The id of this session, read on every event (a resumed or switched session changes it). */
  function noteSession(ctx: ExtensionContext): void {
    if (mode !== "ui") return;
    let id: unknown;
    try {
      id = ctx.sessionManager?.getSessionId?.();
    } catch {
      id = undefined;
    }
    if (!isSessionId(id)) return;
    model.sessionId = id;
    // a session switch or resume: the stored snapshot belongs to the session that is gone
    if (model.tokens !== undefined && model.tokens.sessionId !== id) {
      model.tokens = undefined;
      renderTokens();
    }
  }

  /** The last snapshot omp-token-opt wrote, once at start: the bar opens with the numbers a restarted session already had. */
  function restoreTokens(env: Record<string, string | undefined>): void {
    try {
      const path = join(env.HOME ? env.HOME : homedir(), ".omp", "agent", "token-opt", LIVE_FILE);
      if (statSync(path).size > LIVE_MAX_BYTES) return;
      const snapshot = parseTokenSavings(JSON.parse(readFileSync(path, "utf8")));
      if (snapshot === undefined || model.tokens !== undefined) return;
      // a live.json written by another session is not this session's numbers
      if (model.sessionId !== undefined && snapshot.sessionId !== model.sessionId) return;
      model.tokens = snapshot;
    } catch {
      // missing, unreadable or malformed: nothing to restore
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
      restack = createRestack(bus, {
        rank: BAR_RANK.swarm,
        remount: () => {
          if (mounted && !closed && mode === "ui") mountAndAnnounce();
        },
      });
      if (bus && typeof bus.on === "function") {
        unsubs.push(
          bus.on(TOKEN_CHANNEL, (data) => {
            safe(() => {
              const snapshot = parseTokenSavings(data);
              if (snapshot === undefined) return;
              // another session's snapshot never replaces this session's last one
              if (model.sessionId !== undefined && snapshot.sessionId !== model.sessionId) return;
              model.tokens = snapshot;
              renderTokens();
            });
          }),
        );
      }
      noteSession(ctx);
      restoreTokens(env);
      // mounted for the whole session, idle included: the bar's shape never depends on what the swarm is doing
      mounted = true;
      safe(mountAndAnnounce);
    }
  }

  function stop(): void {
    generation++;
    // omp emits session_shutdown before it aborts the agent: in-flight calls still produce events afterwards, and those must
    // not start the bar again. Only session_start / session_switch (a new session) reopen it.
    closed = true;
    const hadUi = mode === "ui";
    restack?.dispose();
    restack = undefined;
    if (trailingRender !== undefined) timers.clearTimeout(trailingRender);
    trailingRender = undefined;
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
    if (hadUi && mounted) safe(unmount);
    mounted = false;
    mode = undefined;
    model = createModel();
    ui = undefined;
    host = undefined;
    turnActive = false;
    inflight = 0;
    lastRender = -Infinity;
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
        noteSession(ctx);
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
