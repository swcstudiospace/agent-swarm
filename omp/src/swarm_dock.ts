/**
 * Terminal text helpers shared by the swarm bar (`swarm_bar.ts`): ANSI stripping, display-column width
 * (surrogates, wide CJK, emoji clusters), width-safe clipping and the fixed-width powerline row composer.
 * The file keeps its historical name; the one-line dock it used to hold was replaced by the bar.
 * Pure functions, no module state beyond one stateless `Intl.Segmenter`. Width is `Bun.stringWidth(text,
 * { ambiguousIsNarrow: true })`, the host's own measure; parity incl. Indic, Thai and jamo clusters is pinned by `omp/test/swarm_dock.test.ts`.
 */
import type { Theme } from "./omp-api.ts";

/** The theme members the bar reads; every one is optional so a partial or throwing theme degrades to plain text. */
export type BarTheme = Partial<Pick<Theme, "fg" | "bold" | "getFgAnsi" | "getBgAnsi" | "status" | "sep" | "getSpinnerFrames">>;

const SEGMENTER = new Intl.Segmenter(undefined, { granularity: "grapheme" });

/** CSI, OSC (BEL or ST terminated) and two-character escapes. */
const ANSI = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;

export function stripAnsi(text: string): string {
  return text.includes("\x1b") ? text.replace(ANSI, "") : text;
}

/** The host measures with `Bun.stringWidth` (pi-tui `visibleWidth`); using it directly is the only way to match it exactly. */
const HOST_WIDTH = { countAnsiEscapeCodes: false, ambiguousIsNarrow: true } as const;

/**
 * Terminal columns `text` occupies, as the host counts them. ANSI sequences count zero. The host adds code points up inside a
 * grapheme cluster (a Devanagari conjunct `क्ष`, a Thai sara am `กำ` and a leading jamo plus a syllable `ᄀ각` are wider than
 * their widest member), which a hand-written table kept getting wrong, so the measurement is the host's own. pi-tui's
 * `visibleWidth` additionally narrows Hangul compatibility jamo (and counts tabs, which the bar never emits); that can
 * only be narrower than this count, so a row that fits here fits there.
 */
export function displayWidth(text: string): number {
  return Bun.stringWidth(stripAnsi(text), HOST_WIDTH);
}

/**
 * `text` limited to `width` columns, ending in `…` when it was cut. Plain text only (any escape is stripped first, so
 * a cut can never land inside a sequence), and the cut falls between grapheme clusters, never inside a surrogate pair.
 */
export function clipToWidth(text: string, width: number): string {
  if (width <= 0) return "";
  const plain = stripAnsi(text);
  if (displayWidth(plain) <= width) return plain;
  const budget = width - 1;
  let out = "";
  for (const { segment } of SEGMENTER.segment(plain)) {
    if (displayWidth(out + segment) > budget) break;
    out += segment;
  }
  return `${out}…`;
}

/** A piece of a bar row. Priority 0 is the last to be dropped when the row is too narrow. */
export interface BarSegment {
  /** Plain text: the no-theme fallback and the source of a last-resort clip. */
  plain: string;
  /** The same text with theme colors. */
  painted: string;
  priority: number;
}

/** Below this width a powerline row has no room for caps and padding worth drawing: the row is plain. */
const MIN_POWERLINE_WIDTH = 12;
const PLAIN_SEPARATOR = " │ ";
const MIN_GAP = 2;

interface Pieces {
  bg: string;
  fg: string;
  thin: string;
  sepColor: string;
  /** Cap glyphs with their colour; absent for a transparent bar. */
  caps?: { color: string; open: string; close: string };
}

/** The theme's powerline pieces, or undefined when it lacks one or throws (the row is then plain). */
function powerlinePieces(theme: BarTheme | undefined): Pieces | undefined {
  try {
    const bg = theme?.getBgAnsi?.("statusLineBg");
    const fg = theme?.getFgAnsi?.("text");
    const thin = theme?.sep?.powerlineThinLeft;
    const sepColor = theme?.getFgAnsi?.("statusLineSep");
    if (typeof bg !== "string" || typeof fg !== "string" || typeof thin !== "string" || typeof sepColor !== "string") return undefined;
    if (bg === "\x1b[49m") return { bg, fg, thin, sepColor };
    const open = theme?.sep?.powerlineCapLeft ?? "";
    const close = theme?.sep?.powerlineLeft ?? "";
    return { bg, fg, thin, sepColor, caps: { color: bg.replace("\x1b[48;", "\x1b[38;"), open, close } };
  } catch {
    return undefined;
  }
}

interface Entry {
  segment: BarSegment;
  /** Text drawn for the segment (the painted one, or a clip of the plain one) and its columns. */
  text: string;
  columns: number;
  group: "left" | "right";
}

function entriesOf(left: readonly BarSegment[], right: readonly BarSegment[]): Entry[] {
  const make = (group: Entry["group"]) => (segment: BarSegment): Entry => ({ segment, text: segment.painted, columns: displayWidth(segment.painted), group });
  return [...left.map(make("left")), ...right.map(make("right"))];
}

/** Columns a row needs for `entries`: caps, the two inner spaces, both groups, their separators and the minimum gap. */
function naturalWidth(entries: readonly Entry[], pieces: Pieces | undefined): number {
  const separator = pieces ? displayWidth(pieces.thin) + 2 : displayWidth(PLAIN_SEPARATOR);
  const group = (name: Entry["group"]): number => {
    const own = entries.filter((entry) => entry.group === name);
    return own.reduce((sum, entry) => sum + entry.columns, 0) + Math.max(0, own.length - 1) * separator;
  };
  const left = group("left");
  const right = group("right");
  const caps = pieces?.caps ? displayWidth(pieces.caps.open) + displayWidth(pieces.caps.close) : 0;
  return (pieces ? caps + 2 : 0) + left + right + (left > 0 && right > 0 ? MIN_GAP : 0);
}

/** Whether every segment fits `width` as it is: nothing would have to be dropped or clipped. */
export function fitsBar(theme: BarTheme | undefined, left: readonly BarSegment[], right: readonly BarSegment[], width: number): boolean {
  const pieces = width >= MIN_POWERLINE_WIDTH ? powerlinePieces(theme) : undefined;
  return width > 0 && naturalWidth(entriesOf(left, right), pieces) <= width;
}

function assemble(entries: readonly Entry[], pieces: Pieces | undefined, width: number): string {
  const texts = (name: Entry["group"]) => entries.filter((entry) => entry.group === name).map((entry) => entry.text);
  if (!pieces) {
    const left = texts("left").join(PLAIN_SEPARATOR);
    const right = texts("right").join(PLAIN_SEPARATOR);
    return `${left}${" ".repeat(Math.max(0, width - displayWidth(left) - displayWidth(right)))}${right}`;
  }
  const { bg, fg, thin, sepColor, caps } = pieces;
  const join = (parts: readonly string[]): string => parts.map((part) => `${part}${bg}${fg}`).join(` ${sepColor}${thin}${fg} `);
  const left = join(texts("left"));
  const right = join(texts("right"));
  const room = width - (caps ? displayWidth(caps.open) + displayWidth(caps.close) : 0) - 2;
  const body = `${bg}${fg} ${left}${" ".repeat(Math.max(0, room - displayWidth(left) - displayWidth(right)))}${right} \x1b[0m`;
  return caps ? `${caps.color}${caps.open}\x1b[0m${body}${caps.color}${caps.close}\x1b[0m` : body;
}

/** `row` when it is exactly `width` columns; otherwise a plain row of the same width (a theme glyph the measure disagrees with). */
function exactly(row: string, width: number, plainText: () => string): string {
  if (displayWidth(row) === width) return row;
  const clipped = clipToWidth(plainText(), width);
  return `${clipped}${" ".repeat(Math.max(0, width - displayWidth(clipped)))}`;
}

/**
 * One bar row of display width EXACTLY `width` (nothing for `width <= 0`): `left` segments from the left cap, `right` segments
 * anchored to the right cap, the gap between them filled with the bar's background. Powerline pieces come from the theme
 * (statusLineBg / text / statusLineSep); a theme that lacks one, throws, or a width under 12 gives a plain row (` │ ` separators).
 * When the row is too narrow the segment with the LARGEST priority number goes first (ties: the later on screen), the rest keep
 * their order and place; a last remaining segment that is still too wide is clipped with `…`.
 */
export function composeBar(theme: BarTheme | undefined, left: readonly BarSegment[], right: readonly BarSegment[], width: number): string {
  if (width <= 0) return "";
  const plainText = (): string => [...left, ...right].map((segment) => segment.plain).join(PLAIN_SEPARATOR);
  const layout = (pieces: Pieces | undefined): string | undefined => {
    const entries = entriesOf(left, right);
    while (entries.length > 1 && naturalWidth(entries, pieces) > width) {
      let drop = 0;
      for (let i = 1; i < entries.length; i++) if (entries[i].segment.priority >= entries[drop].segment.priority) drop = i;
      entries.splice(drop, 1);
    }
    const only = entries[0];
    if (only && naturalWidth(entries, pieces) > width) {
      const room = width - naturalWidth([{ ...only, text: "", columns: 0 }], pieces);
      if (room < 1) return undefined;
      only.text = clipToWidth(only.segment.plain, room);
      only.columns = displayWidth(only.text);
    }
    return assemble(entries, pieces, width);
  };
  try {
    const pieces = width >= MIN_POWERLINE_WIDTH ? powerlinePieces(theme) : undefined;
    const row = (pieces ? layout(pieces) : undefined) ?? layout(undefined) ?? "";
    return exactly(row, width, plainText);
  } catch {
    return exactly("", width, plainText);
  }
}
