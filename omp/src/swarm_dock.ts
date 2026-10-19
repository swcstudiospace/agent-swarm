/**
 * Terminal text helpers shared by the swarm bar (`swarm_bar.ts`): ANSI stripping, display-column width
 * (surrogates, wide CJK, emoji clusters), width-safe clipping and the Ultrathink-style powerline band.
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

/**
 * Powerline band in the Ultrathink style (statusLineBg / text / statusLineSep) when the theme supplies every piece;
 * `plainSeparator` between parts otherwise. The caller measures the result and falls back to plain text when it does not fit.
 */
export function band(theme: BarTheme | undefined, parts: readonly string[], plainSeparator = " │ "): string {
  try {
    const bg = theme?.getBgAnsi?.("statusLineBg");
    const fg = theme?.getFgAnsi?.("text");
    const thin = theme?.sep?.powerlineThinLeft;
    const sepColor = theme?.getFgAnsi?.("statusLineSep");
    if (typeof bg === "string" && typeof fg === "string" && typeof thin === "string" && typeof sepColor === "string") {
      const body = `${bg}${fg} ${parts.map((part) => `${part}${bg}${fg}`).join(` ${sepColor}${thin}${fg} `)} \x1b[0m`;
      if (bg === "\x1b[49m") return body;
      const capColor = bg.replace("\x1b[48;", "\x1b[38;");
      const open = theme?.sep?.powerlineCapLeft ?? "";
      const close = theme?.sep?.powerlineLeft ?? "";
      return `${capColor}${open}\x1b[0m${body}${capColor}${close}\x1b[0m`;
    }
  } catch {
    // a theme that throws gets the plain join
  }
  return parts.join(plainSeparator);
}
