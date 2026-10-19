import { describe, expect, test } from "bun:test";
import { band, clipToWidth, displayWidth, stripAnsi, type BarTheme } from "../src/swarm_dock.ts";

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

describe("band", () => {
  const powerline: BarTheme = {
    getBgAnsi: () => "\x1b[48;5;236m",
    getFgAnsi: (color: string) => (color === "text" ? "\x1b[38;5;252m" : "\x1b[38;5;240m"),
    sep: { powerlineThinLeft: "\ue0b3", powerlineCapLeft: "\ue0b6", powerlineLeft: "\ue0b0" },
  };

  test("joins parts with the thin powerline separator when the theme supplies every piece", () => {
    const line = band(powerline, ["one", "two"]);
    expect(stripAnsi(line)).toBe("\ue0b6 one \ue0b3 two \ue0b0");
    expect(displayWidth(line)).toBe(displayWidth("\ue0b6 one \ue0b3 two \ue0b0"));
  });

  test("falls back to a plain separator for a missing, partial or throwing theme", () => {
    expect(band(undefined, ["one", "two"])).toBe("one │ two");
    expect(band({ getBgAnsi: () => "\x1b[48;5;236m" }, ["one", "two"])).toBe("one │ two");
    const throwing: BarTheme = {
      getBgAnsi: () => {
        throw new Error("no such color");
      },
    };
    expect(band(throwing, ["one"])).toBe("one");
  });
});
