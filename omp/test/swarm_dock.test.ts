import { describe, expect, test } from "bun:test";
import { clipToWidth, composeBar, displayWidth, fitsBar, stripAnsi, type BarSegment, type BarTheme } from "../src/swarm_dock.ts";

/** The host measures with `Bun.stringWidth(text, { ambiguousIsNarrow: true })` (pi-tui utils.ts visibleWidth). */
const HOST = { countAnsiEscapeCodes: false, ambiguousIsNarrow: true } as const;
const hostWidth = (text: string): number => Bun.stringWidth(text, HOST);

describe("display width", () => {
  test("matches the host's width for every code point of the scripts the bar can show", () => {
    const ranges: Array<[number, number]> = [
      [0x20, 0x7e], [0xa0, 0x24f], [0x370, 0x52f], [0x2190, 0x21ff], [0x2500, 0x25ff], [0x2600, 0x27bf],
      [0x2e80, 0x2e99], [0x3000, 0x30ff], [0x3400, 0x4dbf], [0x4e00, 0x9fff], [0xac00, 0xd7a3], [0xff00, 0xffef],
      [0x1f300, 0x1f64f], [0x1f680, 0x1f6ff], [0x1f900, 0x1f9ff], [0x20000, 0x2001f],
    ];
    const mismatches: string[] = [];
    for (const [lo, hi] of ranges) {
      for (let cp = lo; cp <= hi; cp++) {
        const ch = String.fromCodePoint(cp);
        if (/^\p{Cn}$/u.test(ch)) continue; // unassigned: no script defines a width
        if (displayWidth(ch) !== hostWidth(ch)) mismatches.push(`U+${cp.toString(16)}`);
      }
    }
    expect(mismatches).toEqual([]);
  });

  test("clusters measure as the host measures them: combining marks, ZWJ, skin tones, flags, keycaps, VS16", () => {
    for (const sample of ["e\u0301", "👨‍👩‍👧", "👍🏽", "🇯🇵", "🇯", "1\ufe0f\u20e3", "⏱\ufe0f", "⏱", "A\ufe0f", "❤️", "✔️", "한", "ᄀ\u1161\u11a8", "日本語", "ｱｲｳ", "Ａ", "a👍b", "✓ ✕ ⊘ ⇄ ○ ◌ ● ⏱"]) {
      expect({ sample, width: displayWidth(sample) }).toEqual({ sample, width: hostWidth(sample) });
    }
  });

  test("Indic conjuncts, Thai and Lao sara am and jamo clusters add up as the host adds them (they used to measure too narrow)", () => {
    // host width of a cluster is the sum of its code points: क्ष is 2, กำ is 2, ᄀ각 is 4, not the widest member
    const samples = ["क्ष", "क्षि", "ক্ষ", "க்ஷ", "ക്ഷ", "ಕ್ಷ", "กำ", "ก้ำ", "ຄຳ", "ᄀ각", "ᄀᄀ각", "ᄀ\u1161\u11a8각", "ꥠ각", "ဍ္ဍ", "ក្ក"];
    for (const sample of samples) expect({ sample, width: displayWidth(sample) }).toEqual({ sample, width: hostWidth(sample) });
    expect(displayWidth("क्ष")).toBe(2);
    expect(displayWidth("กำ")).toBe(2);
    expect(displayWidth("ᄀ각")).toBe(4);
  });

  test("every code point and every adjacent pair of the Indic, Thai, Lao, Myanmar, Khmer and jamo blocks measures as the host does", () => {
    const ranges: Array<[number, number]> = [[0x900, 0xdff], [0xe00, 0xeff], [0x1000, 0x109f], [0x1100, 0x11ff], [0x1780, 0x17ff], [0xa960, 0xa97f], [0xd7b0, 0xd7ff]];
    const mismatches: string[] = [];
    for (const [lo, hi] of ranges) {
      const chars: string[] = [];
      for (let cp = lo; cp <= hi; cp++) {
        const ch = String.fromCodePoint(cp);
        if (!/^\p{Cn}$/u.test(ch)) chars.push(ch);
      }
      for (const [i, ch] of chars.entries()) {
        const pair = ch + chars[(i * 7 + 3) % chars.length];
        for (const text of [ch, pair, `a${pair}b`]) if (displayWidth(text) !== hostWidth(text)) mismatches.push(`U+${[...text].map((c) => c.codePointAt(0)?.toString(16)).join("+")}`);
      }
    }
    expect(mismatches).toEqual([]);
  });

  test("ANSI sequences cost nothing and are stripped", () => {
    const colored = "\x1b[38;5;196mred\x1b[39m \x1b]8;;http://x\x07link\x1b]8;;\x07";
    expect(stripAnsi(colored)).toBe("red link");
    expect(displayWidth(colored)).toBe(8);
  });
});

describe("clipToWidth", () => {
  test("never exceeds the width, whatever the characters", () => {
    for (const text of ["abcdefghij", "日本語のテキスト", "👨‍👩‍👧👍🏽🇯🇵 emoji", "e\u0301e\u0301e\u0301e\u0301", "𠀀𠀁𠀂𠀃"]) {
      for (let width = 0; width <= displayWidth(text) + 2; width++) {
        const out = clipToWidth(text, width);
        expect({ text, width, fits: displayWidth(out) <= width }).toEqual({ text, width, fits: true });
      }
    }
  });

  test("a wide character is never split: it either fits whole or the clip stops before it", () => {
    expect(clipToWidth("日本語", 5)).toBe("日本…");
    expect(clipToWidth("日本語", 4)).toBe("日…");
    expect(clipToWidth("日本語", 6)).toBe("日本語");
  });

  test("a surrogate pair is never cut in half", () => {
    const out = clipToWidth("a𠀀𠀁b", 4);
    expect(out).toBe("a𠀀…");
    expect(/[\ud800-\udbff](?![\udc00-\udfff])/.test(out)).toBe(false);
  });

  test("width 1 is the ellipsis, width 0 is empty, short text is unchanged", () => {
    expect(clipToWidth("abc", 1)).toBe("…");
    expect(clipToWidth("abc", 0)).toBe("");
    expect(clipToWidth("abc", 3)).toBe("abc");
  });

  test("an escape sequence in the input is stripped, so a cut cannot land inside one", () => {
    const out = clipToWidth("\x1b[38;5;196mred text here\x1b[39m", 6);
    expect(out).toBe("red t…");
    expect(out.includes("\x1b")).toBe(false);
  });

  test("the host's own width never exceeds the clip for conjuncts, sara am and jamo clusters (rows used to overflow and lose '+N more')", () => {
    for (const unit of ["क्ष", "กำ", "ຄຳ", "ᄀ각", "ক্ষ"]) {
      const text = unit.repeat(40);
      for (const width of [1, 2, 3, 7, 30, 59, 60, 200]) {
        const out = clipToWidth(text, width);
        expect({ unit, width, host: hostWidth(out) <= width }).toEqual({ unit, width, host: true });
      }
    }
    expect(hostWidth(clipToWidth("क्ष".repeat(40), 30))).toBeLessThanOrEqual(30);
  });
});

const BG = "\x1b[48;5;236m";
const FG = "\x1b[38;5;252m";
const SEP_FG = "\x1b[38;5;240m";
const OPEN = "\ue0b6";
const THIN = "\ue0b3";
const CLOSE = "\ue0b0";

const full: BarTheme = {
  getBgAnsi: () => BG,
  getFgAnsi: (color: string) => (color === "text" ? FG : SEP_FG),
  sep: { powerlineThinLeft: THIN, powerlineCapLeft: OPEN, powerlineLeft: CLOSE },
};
const throwing: BarTheme = {
  getBgAnsi: () => {
    throw new Error("no such color");
  },
  getFgAnsi: () => {
    throw new Error("no such color");
  },
};
/** Every theme shape the bar meets: complete, glyph-poor, transparent, wide separator, partial, hostile, absent. */
const themes: Record<string, BarTheme | undefined> = {
  full,
  noCapGlyphs: { ...full, sep: { powerlineThinLeft: THIN } },
  transparent: { ...full, getBgAnsi: () => "\x1b[49m" },
  wideSeparator: { ...full, sep: { ...full.sep, powerlineThinLeft: "＝" } },
  partial: { getBgAnsi: () => BG },
  nonString: { getBgAnsi: () => 5, getFgAnsi: () => FG, sep: full.sep } as unknown as BarTheme,
  throwing,
  undefined: undefined,
};

/** `painted` defaults to a coloured copy so a test can tell the painted text from the plain fallback. */
const seg = (plain: string, priority: number, painted = `\x1b[36m${plain}\x1b[39m`): BarSegment => ({ plain, painted, priority });

interface Groups {
  left: BarSegment[];
  right: BarSegment[];
}
const ladder: Groups = { left: [seg("AAAA", 0), seg("BBBB", 2), seg("CCCC", 1)], right: [seg("DD", 3), seg("EE", 0)] };
/** Segment sets for the exact-width property: empty, one, many, wide characters, colour resets, far too long. */
const sets: Record<string, Groups> = {
  empty: { left: [], right: [] },
  oneLeft: { left: [seg("solo", 0)], right: [] },
  oneRight: { left: [], right: [seg("solo", 0)] },
  ladder,
  wide: { left: [seg("日本語", 0), seg("🚀 launch", 1), seg("ｗｉｄｅ", 2)], right: [seg("ñandú", 4), seg("🇯🇵 jp", 3)] },
  resets: {
    left: [seg("red tail", 0, "\x1b[31mred\x1b[0m tail"), seg("second", 1, "\x1b[1m\x1b[33msecond\x1b[0m")],
    right: [seg("cyan", 2, "\x1b[1;36mcy\x1b[0man")],
  },
  long: { left: [seg("x".repeat(120), 0)], right: [seg("y".repeat(40), 1)] },
  crowd: { left: Array.from({ length: 8 }, (_, i) => seg(`left-${i}`, i)), right: Array.from({ length: 6 }, (_, i) => seg(`right-${i}`, 10 + i)) },
};

describe("composeBar: exact width (I1, I5)", () => {
  test("every width 1..300 x theme x segment set is exactly that many host columns, on a single line", () => {
    const bad: string[] = [];
    for (const [themeName, theme] of Object.entries(themes)) {
      for (const [setName, { left, right }] of Object.entries(sets)) {
        for (let width = 1; width <= 300; width++) {
          const row = composeBar(theme, left, right, width);
          const columns = hostWidth(row);
          if (columns !== width || row.includes("\n")) bad.push(`${themeName}/${setName}@${width}: ${columns} columns`);
        }
      }
    }
    expect(bad.slice(0, 10)).toEqual([]);
  });

  test("width 0 or below is the empty string, not a blank row", () => {
    for (const [themeName, theme] of Object.entries(themes)) {
      for (const width of [0, -1, -300]) {
        expect({ themeName, width, row: composeBar(theme, ladder.left, ladder.right, width) }).toEqual({ themeName, width, row: "" });
      }
    }
  });

  test("it never throws, whatever the theme does", () => {
    const hostile: BarTheme = {
      getBgAnsi: () => BG,
      getFgAnsi: () => FG,
      get sep(): never {
        throw new Error("sep getter");
      },
    };
    for (const width of [1, 11, 12, 80]) expect(hostWidth(composeBar(hostile, ladder.left, ladder.right, width))).toBe(width);
  });
});

describe("composeBar: anatomy", () => {
  test("powerline: open cap, left group joined by the thin separator, fill, right group, close cap", () => {
    const row = composeBar(full, [seg("one", 0), seg("two", 1)], [seg("end", 2)], 40);
    expect(stripAnsi(row)).toMatch(new RegExp(`^${OPEN} one ${THIN} two {2,}end ${CLOSE}$`));
    expect(hostWidth(row)).toBe(40);
  });

  test("a transparent background draws no caps but the same fill", () => {
    const row = composeBar(themes.transparent, [seg("one", 0), seg("two", 1)], [seg("end", 2)], 40);
    expect(stripAnsi(row)).toMatch(new RegExp(`^ one ${THIN} two {2,}end $`));
    expect(row).toContain("\x1b[49m");
    expect(row).not.toContain(OPEN);
    expect(row).not.toContain(CLOSE);
  });

  test("a theme without cap glyphs fills the same way and draws none", () => {
    const row = composeBar(themes.noCapGlyphs, [seg("one", 0), seg("two", 1)], [seg("end", 2)], 40);
    expect(stripAnsi(row)).toMatch(new RegExp(`^ one ${THIN} two {2,}end $`));
  });

  test("a part that resets colours gets the bar's background and text colour re-applied right after it", () => {
    const row = composeBar(full, sets.resets.left, sets.resets.right, 80);
    expect(row).toContain(`red\x1b[0m tail${BG}${FG}`);
    expect(row).toContain(`second\x1b[0m${BG}${FG}`);
    expect(row).toContain(`an${BG}${FG}`);
    expect(row.endsWith("\x1b[0m")).toBe(true);
  });

  test("a missing, partial, non-string or throwing theme gives the plain row: ` │ ` separators, no caps, no colour of its own", () => {
    const parts = [seg("one", 0, "one"), seg("two", 1, "two")];
    for (const name of ["partial", "nonString", "throwing", "undefined"]) {
      const row = composeBar(themes[name], parts, [], 12);
      expect({ name, row }).toEqual({ name, row: `one │ two${" ".repeat(3)}` });
    }
  });

  test("the powerline starts at 12 columns: one column less is the plain row", () => {
    const parts = [seg("ab", 0, "ab"), seg("cd", 1, "cd")];
    expect(composeBar(full, parts, [], 11)).toBe(`ab │ cd${" ".repeat(4)}`);
    const row = composeBar(full, parts, [], 12);
    expect(stripAnsi(row).startsWith(`${OPEN} ab ${THIN} cd`)).toBe(true);
    expect(stripAnsi(row).endsWith(CLOSE)).toBe(true);
  });

  test("below 12 columns no theme draws powerline pieces", () => {
    for (let width = 1; width <= 11; width++) {
      for (const [name, theme] of Object.entries(themes)) {
        const text = stripAnsi(composeBar(theme, ladder.left, ladder.right, width));
        expect({ name, width, glyphs: [OPEN, THIN, CLOSE].filter((glyph) => text.includes(glyph)) }).toEqual({ name, width, glyphs: [] });
      }
    }
  });

  test("only a right group: the fill comes first and the group ends at the right cap", () => {
    expect(composeBar(undefined, [], [seg("right", 0, "right")], 20)).toBe(`${" ".repeat(15)}right`);
    expect(stripAnsi(composeBar(full, [], [seg("right", 0)], 30))).toMatch(new RegExp(`^${OPEN} +right ${CLOSE}$`));
  });

  test("only a left group: the fill runs to the right cap", () => {
    expect(composeBar(undefined, [seg("left", 0, "left")], [], 20)).toBe(`left${" ".repeat(16)}`);
    expect(stripAnsi(composeBar(full, [seg("left", 0)], [], 30))).toMatch(new RegExp(`^${OPEN} left +${CLOSE}$`));
  });

  test("the empty bar is a filled row", () => {
    expect(composeBar(undefined, [], [], 10)).toBe(" ".repeat(10));
    const row = composeBar(full, [], [], 30);
    expect(stripAnsi(row)).toMatch(new RegExp(`^${OPEN} +${CLOSE}$`));
    expect(hostWidth(row)).toBe(30);
  });
});

describe("composeBar: priority, order, gap and anchors", () => {
  const names = ["AAAA", "BBBB", "CCCC", "DD", "EE"];
  const group = (name: string): "left" | "right" => (name === "DD" || name === "EE" ? "right" : "left");
  const rowsDescending = (theme: BarTheme | undefined, minWidth = 1) => {
    const rows: Array<{ width: number; text: string }> = [];
    for (let width = 300; width >= minWidth; width--) rows.push({ width, text: stripAnsi(composeBar(theme, ladder.left, ladder.right, width)) });
    return rows;
  };
  /** The widest width at which `name` is no longer on screen, sweeping down from 300. */
  const firstAbsent = (rows: Array<{ width: number; text: string }>, name: string): number | undefined => rows.find((row) => !row.text.includes(name))?.width;

  test("the largest priority number goes first, ties drop the later one on screen: D(3), B(2), C(1), then E before A (both 0)", () => {
    for (const [themeName, theme] of [["undefined", undefined], ["throwing", throwing]] as const) {
      const rows = rowsDescending(theme);
      const gone = Object.fromEntries(names.map((name) => [name, firstAbsent(rows, name)]));
      const order = [gone.DD, gone.BBBB, gone.CCCC, gone.EE, gone.AAAA];
      expect({ themeName, order }).toEqual({ themeName, order: [...order].sort((a, b) => (b ?? 0) - (a ?? 0)) });
      expect({ themeName, defined: order.every((width) => width !== undefined) }).toEqual({ themeName, defined: true });
      expect({ themeName, strict: new Set(order).size }).toEqual({ themeName, strict: 5 });
    }
  });

  test("the same order holds on a powerline row (12 columns and up)", () => {
    for (const themeName of ["full", "transparent", "noCapGlyphs", "wideSeparator"]) {
      const rows = rowsDescending(themes[themeName], 12);
      const dropped = [firstAbsent(rows, "DD"), firstAbsent(rows, "BBBB"), firstAbsent(rows, "CCCC")];
      expect({ themeName, defined: dropped.every((width) => width !== undefined) }).toEqual({ themeName, defined: true });
      expect({ themeName, strictlyDescending: dropped[0]! > dropped[1]! && dropped[1]! > dropped[2]! }).toEqual({ themeName, strictlyDescending: true });
      // the highest-priority segments survive to the narrowest powerline row
      expect({ themeName, kept: rows.at(-1)!.text.includes("AAAA") }).toEqual({ themeName, kept: true });
    }
  });

  test("nothing that was dropped comes back as the row gets wider again, within the plain range and within the powerline range", () => {
    for (const [themeName, theme] of Object.entries(themes)) {
      for (const [from, to] of [[1, 11], [12, 300]] as const) {
        let previous = new Set(names);
        const regressions: string[] = [];
        for (let width = to; width >= from; width--) {
          const text = stripAnsi(composeBar(theme, ladder.left, ladder.right, width));
          const shown = new Set(names.filter((name) => text.includes(name)));
          for (const name of shown) if (!previous.has(name)) regressions.push(`${name}@${width}`);
          previous = shown;
        }
        expect({ themeName, from, regressions }).toEqual({ themeName, from, regressions: [] });
      }
    }
  });

  test("what is kept stays in the caller's order and place: left group before right group, each in input order", () => {
    for (const [themeName, theme] of Object.entries(themes)) {
      for (let width = 1; width <= 300; width++) {
        const text = stripAnsi(composeBar(theme, ladder.left, ladder.right, width));
        const at = names.filter((name) => text.includes(name)).map((name) => text.indexOf(name));
        expect({ themeName, width, sorted: at.every((index, i) => i === 0 || index > at[i - 1]) }).toEqual({ themeName, width, sorted: true });
      }
    }
  });

  test("two groups are at least 2 columns apart at every width, and exactly 2 when the row just fits", () => {
    for (const [themeName, theme] of Object.entries(themes)) {
      for (let width = 1; width <= 300; width++) {
        const text = stripAnsi(composeBar(theme, ladder.left, ladder.right, width));
        const left = names.filter((name) => group(name) === "left" && text.includes(name));
        const right = names.filter((name) => group(name) === "right" && text.includes(name));
        if (left.length === 0 || right.length === 0) continue;
        const lastLeft = left.at(-1)!;
        const gap = text.indexOf(right[0]) - (text.indexOf(lastLeft) + lastLeft.length);
        expect({ themeName, width, gapOk: gap >= 2 }).toEqual({ themeName, width, gapOk: true });
      }
    }
    const natural = displayWidth("AAAA │ BBBB │ CCCC") + displayWidth("DD │ EE") + 2;
    const text = composeBar(undefined, ladder.left, ladder.right, natural);
    expect(/CCCC( +)DD/.exec(stripAnsi(text))?.[1]).toHaveLength(2);
  });

  test("the right group is anchored to the right cap and the left group to the left cap, whatever else is dropped", () => {
    const tails: Record<string, string> = { full: ` ${CLOSE}`, transparent: " ", noCapGlyphs: " ", undefined: "" };
    const heads: Record<string, string> = { full: `${OPEN} `, transparent: " ", noCapGlyphs: " ", undefined: "" };
    for (const [themeName, tail] of Object.entries(tails)) {
      for (let width = 12; width <= 300; width++) {
        const text = stripAnsi(composeBar(themes[themeName], ladder.left, ladder.right, width));
        if (text.includes("EE")) expect({ themeName, width, after: text.slice(text.lastIndexOf("EE") + 2) }).toEqual({ themeName, width, after: tail });
        if (text.includes("AAAA")) expect({ themeName, width, before: text.slice(0, text.indexOf("AAAA")) }).toEqual({ themeName, width, before: heads[themeName] });
      }
    }
  });

  test("ties inside one group drop the later segment first", () => {
    const left = [seg("XXXX", 1), seg("YYYY", 1), seg("ZZZZ", 1)];
    for (let width = 11; width <= 17; width++) {
      const text = stripAnsi(composeBar(undefined, left, [], width));
      expect({ width, shown: ["XXXX", "YYYY", "ZZZZ"].filter((name) => text.includes(name)) }).toEqual({ width, shown: ["XXXX", "YYYY"] });
    }
  });

  test("ties across the groups drop the right one (later on screen)", () => {
    const text = stripAnsi(composeBar(undefined, [seg("LLLL", 1)], [seg("RRRR", 1)], 9));
    expect(text).toContain("LLLL");
    expect(text).not.toContain("RRRR");
    const both = stripAnsi(composeBar(undefined, [seg("LLLL", 1)], [seg("RRRR", 1)], 10));
    expect(both).toMatch(/^LLLL {2}RRRR$/);
  });

  test("inside the right group the least important segment is the one on the left, and it goes first", () => {
    const right = [seg("R1", 5, "R1"), seg("R2", 1, "R2")];
    expect(composeBar(undefined, [], right, 7)).toBe("R1 │ R2");
    expect(composeBar(undefined, [], right, 6)).toBe("    R2");
  });

  test("a lower priority number is never removed to make room for a higher one", () => {
    const left = [seg("keepme", 0), seg("filler-one", 8), seg("filler-two", 9)];
    for (let width = 6; width <= 40; width++) {
      const text = stripAnsi(composeBar(undefined, left, [], width));
      expect({ width, kept: text.includes("keepme") }).toEqual({ width, kept: true });
    }
  });
});

describe("composeBar: last resort clip", () => {
  test("a lone segment wider than the row is cut with an ellipsis, from its plain text", () => {
    const only = [seg("x".repeat(50), 0)];
    expect(stripAnsi(composeBar(undefined, only, [], 20))).toBe(`${"x".repeat(19)}…`);
    const powerline = composeBar(full, only, [], 20);
    expect(stripAnsi(powerline)).toBe(`${OPEN} ${"x".repeat(15)}… ${CLOSE}`);
    expect(powerline).not.toContain("\x1b[36m");
    expect(hostWidth(powerline)).toBe(20);
  });

  test("a segment that fits exactly is not clipped", () => {
    expect(composeBar(undefined, [seg("x".repeat(20), 0, "x".repeat(20))], [], 20)).toBe("x".repeat(20));
  });

  test("the clip never splits a wide character", () => {
    expect(stripAnsi(composeBar(undefined, [seg("日本語日本語", 0)], [], 7))).toBe("日本語…");
    expect(stripAnsi(composeBar(undefined, [seg("日本語日本語", 0)], [], 6))).toBe("日本… ");
  });

  test("lower-priority segments go before the last one is clipped", () => {
    const text = stripAnsi(composeBar(undefined, [seg("x".repeat(50), 0)], [seg("yyyyy", 1)], 20));
    expect(text).not.toContain("y");
    expect(text).toBe(`${"x".repeat(19)}…`);
  });

  test("at one column the row is a single character, never wider", () => {
    for (const [name, theme] of Object.entries(themes)) {
      const row = composeBar(theme, [seg("long text", 0)], [seg("more", 1)], 1);
      expect({ name, columns: hostWidth(row) }).toEqual({ name, columns: 1 });
    }
  });
});

describe("fitsBar", () => {
  test("true exactly when nothing is dropped or clipped, at every width and theme", () => {
    const bad: string[] = [];
    for (const [themeName, theme] of Object.entries(themes)) {
      for (const [setName, { left, right }] of Object.entries(sets)) {
        for (let width = 1; width <= 300; width++) {
          const text = stripAnsi(composeBar(theme, left, right, width));
          const everything = [...left, ...right].every((segment) => text.includes(segment.plain));
          const fits = fitsBar(theme, left, right, width);
          if (fits !== everything) bad.push(`${themeName}/${setName}@${width}: fits=${fits} everything=${everything}`);
        }
      }
    }
    expect(bad.slice(0, 10)).toEqual([]);
  });

  test("the boundary is the natural width: caps and inner spaces, groups with their separators, a 2-column gap", () => {
    const plain = displayWidth("AAAA │ BBBB │ CCCC") + displayWidth("DD │ EE") + 2;
    expect(fitsBar(undefined, ladder.left, ladder.right, plain)).toBe(true);
    expect(fitsBar(undefined, ladder.left, ladder.right, plain - 1)).toBe(false);
    const caps = displayWidth(OPEN) + displayWidth(CLOSE);
    const powerline = displayWidth(`AAAA ${THIN} BBBB ${THIN} CCCC`) + displayWidth(`DD ${THIN} EE`) + 2 + 2 + caps;
    expect(fitsBar(full, ladder.left, ladder.right, powerline)).toBe(true);
    expect(fitsBar(full, ladder.left, ladder.right, powerline - 1)).toBe(false);
    // a transparent bar draws no caps, so it needs less than the capped one
    expect(fitsBar(themes.transparent, ladder.left, ladder.right, powerline - caps)).toBe(true);
    expect(fitsBar(themes.transparent, ladder.left, ladder.right, powerline - caps - 1)).toBe(false);
  });

  test("an empty bar fits anywhere there is a column; no column fits nothing", () => {
    for (const theme of Object.values(themes)) {
      expect(fitsBar(theme, [], [], 1)).toBe(true);
      expect(fitsBar(theme, [], [], 80)).toBe(true);
      expect(fitsBar(theme, ladder.left, ladder.right, 0)).toBe(false);
      expect(fitsBar(theme, [], [], -5)).toBe(false);
    }
  });
});
