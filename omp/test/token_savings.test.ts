import { describe, expect, test } from "bun:test";
import { formatTokens, isSessionId, parseTokenSavings, tokenChips, type TokenSavingsSnapshot } from "../src/token_savings.ts";

const minimal: TokenSavingsSnapshot = { v: 1, sessionId: "s", updatedAt: 1, baselineTokens: 0, currentTokens: 0, savedTokens: 0, points: [] };

const snapshot: TokenSavingsSnapshot = {
  v: 1,
  sessionId: "sess-1",
  updatedAt: 1_700_000_000_000,
  baselineTokens: 1_200_000,
  currentTokens: 1_200,
  savedTokens: 1_198_800,
  points: [{ at: 1, source: "compress", baseline: 1_200_000, current: 1_200, saved: 1_198_800 }],
};

const PERF_FIELDS = [
  "lastTurnMs",
  "cacheHitPct",
  "lastQueryMs",
  "rtkSavedTokens",
  "lastPayloadChars",
  "ttftMs",
  "lastIdleMs",
  "costCents",
  "providerInput",
  "providerOutput",
  "providerCacheRead",
  "providerCacheWrite",
] as const;

describe("parseTokenSavings: required fields", () => {
  test("accepts a minimal valid object and returns an equal copy", () => {
    expect(parseTokenSavings(minimal)).toEqual(minimal);
    expect(parseTokenSavings(snapshot)).toEqual(snapshot);
  });

  test("rejects anything that is not a plain object", () => {
    for (const value of [undefined, null, 0, "x", true, [], [minimal], () => minimal]) {
      expect({ value, parsed: parseTokenSavings(value) }).toEqual({ value, parsed: undefined });
    }
  });

  test("rejects another schema version, or none", () => {
    expect(parseTokenSavings({ ...minimal, v: 2 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, v: "1" })).toBeUndefined();
    const { v: _v, ...noVersion } = minimal;
    expect(parseTokenSavings(noVersion)).toBeUndefined();
  });

  test("rejects negative, fractional, non-numeric and unsafe counts", () => {
    expect(parseTokenSavings({ ...minimal, savedTokens: -1 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, baselineTokens: -1, savedTokens: -1 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, currentTokens: -1 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, updatedAt: -1 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, baselineTokens: 1.5, currentTokens: 0.5, savedTokens: 1 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, baselineTokens: "5", currentTokens: 5, savedTokens: 0 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, baselineTokens: Number.NaN, currentTokens: Number.NaN, savedTokens: Number.NaN })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, baselineTokens: Number.POSITIVE_INFINITY, currentTokens: 0, savedTokens: Number.POSITIVE_INFINITY })).toBeUndefined();
    const { savedTokens: _saved, ...missing } = minimal;
    expect(parseTokenSavings(missing)).toBeUndefined();
  });

  test("saved must be exactly baseline minus current: never an estimate", () => {
    expect(parseTokenSavings({ ...snapshot, savedTokens: snapshot.savedTokens - 1 })).toBeUndefined();
    expect(parseTokenSavings({ ...snapshot, savedTokens: snapshot.savedTokens + 1 })).toBeUndefined();
    // a session that grew past its baseline has negative savings: it cannot be a valid count
    expect(parseTokenSavings({ ...minimal, baselineTokens: 10, currentTokens: 20, savedTokens: 0 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, baselineTokens: 10, currentTokens: 20, savedTokens: -10 })).toBeUndefined();
  });

  test("points: an array of at most 200, each with a known source and saved === baseline - current", () => {
    const point = { at: 1, source: "graph" as const, baseline: 10, current: 4, saved: 6 };
    expect(parseTokenSavings({ ...minimal, points: [point] })?.points).toEqual([point]);
    for (const source of ["provider", "compress", "graph"]) expect(parseTokenSavings({ ...minimal, points: [{ ...point, source }] })).toBeDefined();
    expect(parseTokenSavings({ ...minimal, points: Array.from({ length: 200 }, () => point) })?.points).toHaveLength(200);
    expect(parseTokenSavings({ ...minimal, points: Array.from({ length: 201 }, () => point) })).toBeUndefined();
    const bad: unknown[] = [
      { ...point, at: -1 },
      { ...point, at: 1.5 },
      { ...point, source: "other" },
      { ...point, source: undefined },
      { ...point, baseline: -1 },
      { ...point, current: "4" },
      { ...point, saved: 5 },
      null,
      7,
      "point",
      [],
    ];
    for (const entry of bad) expect({ entry, parsed: parseTokenSavings({ ...minimal, points: [entry] }) }).toEqual({ entry, parsed: undefined });
    // one bad point among good ones rejects the whole snapshot
    expect(parseTokenSavings({ ...minimal, points: [point, point, { ...point, saved: 0 }] })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, points: "none" })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, points: undefined })).toBeUndefined();
  });

  test("session id: 1..128 characters without a slash", () => {
    expect(parseTokenSavings({ ...minimal, sessionId: "x".repeat(128) })?.sessionId).toBe("x".repeat(128));
    expect(parseTokenSavings({ ...minimal, sessionId: "x" })?.sessionId).toBe("x");
    for (const sessionId of ["", "x".repeat(129), "a/b", "/", "../etc", 7, null, undefined]) {
      expect({ sessionId, parsed: parseTokenSavings({ ...minimal, sessionId }) }).toEqual({ sessionId, parsed: undefined });
    }
  });

  test("isSessionId agrees with the parser's session id rule", () => {
    expect(isSessionId("sess-1")).toBe(true);
    expect(isSessionId("x".repeat(128))).toBe(true);
    for (const value of ["", "x".repeat(129), "a/b", 1, null, undefined, {}]) expect({ value, ok: isSessionId(value) }).toEqual({ value, ok: false });
  });
});

describe("parseTokenSavings: optional fields", () => {
  test("accepts every optional field together and copies each value", () => {
    const full = {
      ...snapshot,
      lastTurnMs: 840,
      cacheHitPct: 72,
      lastQueryMs: 15,
      rtkSavedTokens: 10,
      lastPayloadChars: 4096,
      ttftMs: 120,
      lastIdleMs: 40,
      costCents: 3,
      providerInput: 1500,
      providerOutput: 300,
      providerCacheRead: 900,
      providerCacheWrite: 100,
      prefixChanged: 1 as const,
      toolsChanged: 0 as const,
      graphReady: 1 as const,
    };
    expect(parseTokenSavings(full)).toEqual(full);
  });

  test("absent optional fields stay absent: nothing is invented", () => {
    const parsed = parseTokenSavings(minimal) as Record<string, unknown>;
    for (const key of [...PERF_FIELDS, "prefixChanged", "toolsChanged", "graphReady"]) expect({ key, present: key in parsed }).toEqual({ key, present: false });
  });

  test("a zero is a measurement, not an absence", () => {
    for (const key of PERF_FIELDS) expect({ key, parsed: parseTokenSavings({ ...minimal, [key]: 0 })?.[key] }).toEqual({ key, parsed: 0 });
    expect(parseTokenSavings({ ...minimal, prefixChanged: 0, toolsChanged: 0, graphReady: 0 })).toMatchObject({ prefixChanged: 0, toolsChanged: 0, graphReady: 0 });
  });

  test("a present but invalid count rejects the whole snapshot, whichever field it is", () => {
    for (const key of PERF_FIELDS) {
      for (const bad of [-1, 1.5, "10", null, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1, {}, []]) {
        expect({ key, bad, parsed: parseTokenSavings({ ...snapshot, [key]: bad }) }).toEqual({ key, bad, parsed: undefined });
      }
      // an explicit undefined is "present" (own property) and not a valid count either
      expect({ key, parsed: parseTokenSavings({ ...snapshot, [key]: undefined }) }).toEqual({ key, parsed: undefined });
    }
  });

  test("one bad optional field among valid ones still rejects everything", () => {
    expect(parseTokenSavings({ ...snapshot, lastTurnMs: 840, cacheHitPct: 72, ttftMs: -1 })).toBeUndefined();
    expect(parseTokenSavings({ ...snapshot, lastIdleMs: -1, costCents: 3 })).toBeUndefined();
    expect(parseTokenSavings({ ...snapshot, providerInput: 5, providerCacheWrite: -1 })).toBeUndefined();
  });

  test("cacheHitPct is a percent: 0..100 inclusive", () => {
    expect(parseTokenSavings({ ...minimal, cacheHitPct: 100 })?.cacheHitPct).toBe(100);
    expect(parseTokenSavings({ ...minimal, cacheHitPct: 101 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, cacheHitPct: 72.5 })).toBeUndefined();
    expect(parseTokenSavings({ ...minimal, cacheHitPct: -1 })).toBeUndefined();
  });

  test("counts accept the largest safe integer", () => {
    expect(parseTokenSavings({ ...minimal, lastPayloadChars: Number.MAX_SAFE_INTEGER })?.lastPayloadChars).toBe(Number.MAX_SAFE_INTEGER);
  });

  test("flags are 0 or 1 and nothing else", () => {
    for (const key of ["prefixChanged", "toolsChanged", "graphReady"]) {
      for (const flag of [0, 1]) expect({ key, flag, parsed: parseTokenSavings({ ...minimal, [key]: flag })?.[key as "graphReady"] }).toEqual({ key, flag, parsed: flag });
      for (const bad of [2, -1, 0.5, "1", true, null, Number.NaN]) {
        expect({ key, bad, parsed: parseTokenSavings({ ...minimal, [key]: bad }) }).toEqual({ key, bad, parsed: undefined });
      }
    }
  });

  test("provider cache tokens and bill totals are carried separately and never folded into saved", () => {
    const parsed = parseTokenSavings({ ...snapshot, providerInput: 7, providerOutput: 8, providerCacheRead: 9, providerCacheWrite: 10, rtkSavedTokens: 11 });
    expect(parsed?.savedTokens).toBe(snapshot.savedTokens);
    expect(parsed).toMatchObject({ providerInput: 7, providerOutput: 8, providerCacheRead: 9, providerCacheWrite: 10, rtkSavedTokens: 11 });
  });

  test("unknown extra fields are dropped", () => {
    const parsed = parseTokenSavings({ ...minimal, surprise: 1 }) as Record<string, unknown>;
    expect(parsed).toEqual(minimal);
    expect("surprise" in parsed).toBe(false);
  });

  test("a hostile getter that throws is a rejection, never an exception", () => {
    const hostile = { ...minimal };
    Object.defineProperty(hostile, "points", {
      enumerable: true,
      get() {
        throw new Error("boom");
      },
    });
    expect(() => parseTokenSavings(hostile)).not.toThrow();
    expect(parseTokenSavings(hostile)).toBeUndefined();
  });
});

describe("formatTokens", () => {
  test("below 1000 the count is printed as it is", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(7)).toBe("7");
    expect(formatTokens(999)).toBe("999");
  });

  test("thousands carry one decimal, dropped when it is zero", () => {
    expect(formatTokens(1000)).toBe("1k");
    expect(formatTokens(1049)).toBe("1k");
    expect(formatTokens(1200)).toBe("1.2k");
    expect(formatTokens(12_345)).toBe("12.3k");
    expect(formatTokens(999_949)).toBe("999.9k");
  });

  test("millions carry one decimal, dropped when it is zero", () => {
    expect(formatTokens(1_000_000)).toBe("1M");
    expect(formatTokens(1_198_800)).toBe("1.2M");
    expect(formatTokens(12_340_000)).toBe("12.3M");
  });

  test("uses the k suffix below a million and the M suffix from a million", () => {
    for (const n of [1000, 5000, 999_949]) expect(formatTokens(n)).toMatch(/^\d+(\.\d)?k$/);
    for (const n of [1_000_000, 5_000_000, 123_456_789]) expect(formatTokens(n)).toMatch(/^\d+(\.\d)?M$/);
  });

  test("a count that rounds up to 1000k is printed as 1M, never 1000k", () => {
    expect(formatTokens(999_949)).toBe("999.9k");
    expect(formatTokens(999_950)).toBe("1M");
    expect(formatTokens(999_999)).toBe("1M");
    expect(formatTokens(1_000_000)).toBe("1M");
    expect(formatTokens(1_049_999)).toBe("1M");
    expect(formatTokens(1_050_000)).toBe("1.1M");
    for (let n = 999_950; n <= 999_999; n += 7) expect(formatTokens(n)).toBe("1M");
  });
});

describe("tokenChips", () => {
  const full: TokenSavingsSnapshot = {
    ...snapshot,
    lastTurnMs: 840,
    cacheHitPct: 72,
    lastQueryMs: 15,
    rtkSavedTokens: 10,
    lastPayloadChars: 4096,
    ttftMs: 120,
    lastIdleMs: 40,
    costCents: 3,
    providerInput: 1500,
    providerOutput: 300,
    providerCacheRead: 900,
    providerCacheWrite: 100,
    prefixChanged: 1,
    toolsChanged: 1,
    graphReady: 1,
  };
  const texts = (s: TokenSavingsSnapshot): string[] => tokenChips(s).map((chip) => chip.text);

  test("on-screen order runs from the least to the most important chip, ending in saved", () => {
    expect(texts(full)).toEqual([
      "graph ready",
      "bill 1.5k · out 300",
      "idle 40ms",
      "payload 4096c",
      "query 15ms",
      "rtk +10",
      "cost 3c",
      "turn 840ms",
      "before 1.2M · now 1.2k",
      "prefix moved",
      "tools moved",
      "ttft 120ms",
      "cache 72%",
      "saved 1.2M",
    ]);
  });

  test("priorities never increase along the screen order, and only saved is 0 (the last to be dropped)", () => {
    const chips = tokenChips(full);
    const priorities = chips.map((chip) => chip.priority);
    expect(priorities).toEqual([11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 2, 2, 1, 0]);
    expect(chips.at(-1)?.text).toBe("saved 1.2M");
    expect(chips.filter((chip) => chip.priority === 0)).toHaveLength(1);
  });

  test("the minimal snapshot has the before/now and saved chips only: no zeros are invented", () => {
    expect(tokenChips(minimal)).toEqual([
      { text: "before 0 · now 0", priority: 3, color: "dim" },
      { text: "saved 0", priority: 0, color: "dim" },
    ]);
  });

  test("an absent optional field has no chip, a present zero does", () => {
    const withZero = texts({ ...minimal, lastTurnMs: 0, cacheHitPct: 0, lastQueryMs: 0, rtkSavedTokens: 0, lastPayloadChars: 0, ttftMs: 0, lastIdleMs: 0, costCents: 0 });
    expect(withZero).toEqual(["idle 0ms", "payload 0c", "query 0ms", "rtk +0", "cost 0c", "turn 0ms", "before 0 · now 0", "ttft 0ms", "cache 0%", "saved 0"]);
    for (const text of texts(minimal)) expect(text).toMatch(/^(before|saved) /);
  });

  test("flag chips appear only for 1, one chip each", () => {
    expect(texts({ ...minimal, prefixChanged: 0, toolsChanged: 0, graphReady: 0 })).toEqual(["before 0 · now 0", "saved 0"]);
    expect(texts({ ...minimal, prefixChanged: 1 })).toEqual(["before 0 · now 0", "prefix moved", "saved 0"]);
    expect(texts({ ...minimal, toolsChanged: 1 })).toEqual(["before 0 · now 0", "tools moved", "saved 0"]);
    expect(texts({ ...minimal, graphReady: 1 })).toEqual(["graph ready", "before 0 · now 0", "saved 0"]);
    const moved = tokenChips({ ...minimal, prefixChanged: 1, toolsChanged: 1 }).filter((chip) => chip.text.endsWith("moved"));
    expect(moved.map((chip) => [chip.priority, chip.color])).toEqual([[2, "warning"], [2, "warning"]]);
  });

  test("the bill chip shows whichever of input and output exists, joined only when both do", () => {
    expect(texts({ ...minimal, providerInput: 1500 })).toContain("bill 1.5k");
    expect(texts({ ...minimal, providerOutput: 300 })).toContain("out 300");
    expect(texts({ ...minimal, providerOutput: 300 })).not.toContain("bill 0");
    expect(texts({ ...minimal, providerInput: 1500, providerOutput: 300 })).toContain("bill 1.5k · out 300");
    expect(texts({ ...minimal, providerInput: 0, providerOutput: 0 })).toContain("bill 0 · out 0");
    expect(tokenChips({ ...minimal, providerInput: 1 }).find((chip) => chip.text.startsWith("bill"))).toMatchObject({ priority: 10, color: "dim" });
  });

  test("provider cache tokens never get a chip and never change saved", () => {
    const withCache = tokenChips({ ...minimal, providerCacheRead: 900, providerCacheWrite: 100 });
    expect(withCache).toEqual(tokenChips(minimal));
    expect(tokenChips({ ...full, providerCacheRead: 1, providerCacheWrite: 2 }).at(-1)?.text).toBe("saved 1.2M");
  });

  test("saved is success-coloured above zero and dim at zero; the other plain chips use the neutral text colour", () => {
    expect(tokenChips(snapshot).at(-1)).toMatchObject({ color: "success", priority: 0 });
    expect(tokenChips(minimal).at(-1)).toMatchObject({ color: "dim", priority: 0 });
    const colors = Object.fromEntries(tokenChips(full).map((chip) => [chip.text, chip.color]));
    expect(colors).toMatchObject({
      "graph ready": "dim",
      "bill 1.5k · out 300": "dim",
      "before 1.2M · now 1.2k": "dim",
      "prefix moved": "warning",
      "tools moved": "warning",
      "idle 40ms": "text",
      "payload 4096c": "text",
      "query 15ms": "text",
      "rtk +10": "text",
      "cost 3c": "text",
      "turn 840ms": "text",
      "ttft 120ms": "text",
      "cache 72%": "text",
    });
  });

  test("counts are formatted like formatTokens everywhere a token count is shown", () => {
    const chips = texts({ ...snapshot, rtkSavedTokens: 2_500, providerInput: 12_345, providerOutput: 999 });
    expect(chips).toContain("rtk +2.5k");
    expect(chips).toContain("bill 12.3k · out 999");
    expect(chips).toContain("before 1.2M · now 1.2k");
  });
});
