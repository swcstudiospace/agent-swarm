import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { formatElapsed } from "../src/swarm_bar.ts";
import { displayWidth, stripAnsi } from "../src/swarm_dock.ts";
import {
  ANSI_FIXTURE,
  DOC_PATH,
  headlessLines,
  loadRealTheme,
  parseAnsi,
  parseDoc,
  plainText,
  renderCaptures,
  SCENARIOS,
  TOKEN_SCENARIOS,
  WIDTHS,
} from "./fixtures/bar_states.ts";

/**
 * docs/swarm-bar.md shows every state of the bar at widths 40, 80 and 120, and its token-savings chips, as rendered by the real
 * renderer with the real omp theme. This file keeps that page honest:
 *   - the colour fixture (`fixtures/bar_states.ansi`) equals what the renderer produces today (needs omp's pi-tui theme
 *     sources; skipped, loudly, where they are not installed);
 *   - every capture in the doc equals the fixture with the ANSI removed (needs nothing);
 *   - the captures satisfy the bar's contract on their own (one row when nothing runs, two when agents run, each exactly the capture's width, caps in the same
 *     columns, glyph and word of every state, `+N more`, chips right-anchored and dropped by priority).
 * Refresh after a renderer change: `bun omp/test/fixtures/bar_states.ts --write` (see the doc, section 10).
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const fixture = parseAnsi(readFileSync(ANSI_FIXTURE, "utf8"));
const doc = readFileSync(DOC_PATH, "utf8");
const theme = await loadRealTheme();

/** `key` of a width-bound capture: `<scenario>@<width>`. */
const widthOf = (key: string): number | undefined => {
  const match = /@(\d+)$/.exec(key);
  return match ? Number(match[1]) : undefined;
};

/** Captures that are one row: the idle and telemetry-error states (no agents running) and the spinner captures (the agent row alone). */
const ONE_ROW = /^(idle@|error@|tokens-idle@|tokens-minimal@|linger:done\+20s@|linger:failed\+600s@|spinner:)/;

describe("bar captures vs the renderer", () => {
  test.skipIf(theme === undefined)("the colour fixture is exactly what the real renderer and the real theme produce today", () => {
    const rendered = renderCaptures(theme as NonNullable<typeof theme>);
    expect(Object.keys(rendered).sort()).toEqual(Object.keys(fixture).sort());
    for (const key of Object.keys(rendered)) expect({ key, lines: fixture[key] }).toEqual({ key, lines: rendered[key] });
  });

  test("the fixture covers every scenario at every width, plus the width-less captures", () => {
    for (const scenario of [...SCENARIOS, ...TOKEN_SCENARIOS]) {
      for (const width of scenario.widths) expect(Object.keys(fixture)).toContain(`${scenario.name}@${width}`);
    }
    expect(WIDTHS).toEqual([40, 80, 120]);
    expect(SCENARIOS.map((scenario) => scenario.name)).toEqual(expect.arrayContaining(["idle", "loading", "active", "overflow", "completed", "failed", "error"]));
    for (const key of ["headless", "spinner:animated+0s@80", "spinner:reduced+0s@80", "linger:done+19s@80", "linger:failed+600s@80"]) {
      expect(Object.keys(fixture)).toContain(key);
    }
  });

  test("every capture in docs/swarm-bar.md equals the renderer's output (ANSI removed), and the doc shows no other capture", () => {
    const shown = parseDoc(doc);
    expect(Object.keys(shown).sort()).toEqual(Object.keys(fixture).sort());
    for (const key of Object.keys(fixture)) expect({ key, lines: shown[key] }).toEqual({ key, lines: fixture[key].map(plainText) });
  });

  test("the doc's capture blocks are well-formed (no stray ANSI, nothing left as a placeholder)", () => {
    expect(doc).not.toContain("\x1b");
    expect(doc).not.toMatch(/@@[A-Z0-9]+@@/);
    expect(doc.match(/<!-- capture:/g)?.length).toBe(doc.match(/<!-- \/capture -->/g)?.length);
  });
});

describe("the captures satisfy the bar contract", () => {
  const widthKeys = Object.keys(fixture).filter((key) => widthOf(key) !== undefined);

  test("every bar is one row when nothing runs and two when agents run, every row exactly as wide as its capture says, no stray escape sequence", () => {
    expect(widthKeys.length).toBeGreaterThan(40);
    for (const key of widthKeys) {
      const width = widthOf(key) as number;
      const lines = fixture[key];
      // the spinner captures show row 2 only; idle and telemetry error show their one row
      expect({ key, rows: lines.length }).toEqual({ key, rows: ONE_ROW.test(key) ? 1 : 2 });
      for (const [index, line] of lines.entries()) {
        expect({ key, row: index, width: displayWidth(line) }).toEqual({ key, row: index, width });
        // every escape is a complete SGR sequence: nothing is left once they are stripped
        expect({ key, stray: stripAnsi(line).includes("\x1b") }).toEqual({ key, stray: false });
      }
    }
  });

  test("the caps sit in the first and last column of every row, whatever the state", () => {
    for (const key of widthKeys) {
      for (const [index, line] of fixture[key].entries()) {
        const columns = Array.from(plainText(line));
        expect({ key, row: index, caps: [columns[0], columns[columns.length - 1]] }).toEqual({ key, row: index, caps: ["◖", "◗"] });
      }
    }
  });

  test("idle is one row at every width; the settled rows give way to it exactly at their linger (20 s done, 600 s failed)", () => {
    for (const width of WIDTHS) {
      const lines = fixture[`idle@${width}`].map(plainText);
      expect({ width, rows: lines.length }).toEqual({ width, rows: 1 });
      expect({ width, first: lines[0].includes("○ swarm idle") }).toEqual({ width, first: true });
    }
    const first = (key: string): string => plainText(fixture[key][0]);
    for (const key of ["linger:done+0s@80", "linger:done+19s@80"]) expect({ key, done: first(key).includes("3 done") }).toEqual({ key, done: true });
    expect(first("linger:done+20s@80")).toContain("○ swarm idle");
    expect(first("linger:done+20s@80")).not.toContain("done");
    expect(first("linger:failed+599s@80")).toContain("1 fail");
    expect(first("linger:failed+600s@80")).toContain("○ swarm idle");
    expect(fixture["linger:failed+600s@80"]).toHaveLength(1); // the idle row alone, no placeholder under it
  });

  test("a state is never colour alone: every state has its glyph and, where not self-evident, its word", () => {
    const text = fixture["every-state@120"].map(plainText).join("\n");
    for (const mark of ["✕ FAIL", "⏱ TIMEOUT", "! STALL"]) expect(text).toContain(mark);
    // the rows that no longer fit at 120 columns show up at the other end of the scale: all states in one summary
    expect(text).toMatch(/1 wait/);
    expect(text).toMatch(/1 queued/);
    expect(text).toMatch(/1 done/);
    expect(text).toMatch(/1 cancel/);
    expect(text).toMatch(/1 stall/);
    expect(text).toMatch(/1 timeout/);
    // worst first: failed, timed out, stalled, then running
    expect(text.indexOf("✕ FAIL")).toBeLessThan(text.indexOf("⏱ TIMEOUT"));
    expect(text.indexOf("⏱ TIMEOUT")).toBeLessThan(text.indexOf("! STALL"));
    expect(text.indexOf("! STALL")).toBeLessThan(text.indexOf("a08-qa"));
    // the loading state names itself, the queued row carries its word
    expect(fixture["loading@120"].map(plainText).join("\n")).toContain("start a05-backend");
    expect(fixture["active@120"].map(plainText).join("\n")).toContain("○ queued a08-qa");
  });

  test("a detached background specialist carries `~` after its elapsed; the orchestrator row and settled rows do not", () => {
    const rows = plainText(fixture["detached@120"][1]);
    expect(rows).toMatch(/a05-backend\s+1m07s~/);
    expect(rows).toMatch(/a06-frontend\s+53s~/);
    expect(rows).toMatch(/a01-orchestrator\s+2m35s(?!~)/);
    for (const width of WIDTHS) expect(fixture[`detached@${width}`].map(plainText).join("\n")).not.toContain("cancelled");
    for (const key of Object.keys(fixture).filter((name) => !name.startsWith("detached@"))) {
      if (widthOf(key) !== undefined) expect({ key, tilde: fixture[key].map(plainText).join("\n").includes("~") }).toEqual({ key, tilde: false });
    }
  });

  test("overflow counts exactly the rows left out (nine runs exist)", () => {
    for (const width of WIDTHS) {
      const [, row] = fixture[`overflow@${width}`].map(plainText);
      const shown = (row.match(/[│◖] [●✕] /g) ?? []).length;
      const more = Number(/\+(\d+) more/.exec(row)?.[1]);
      expect({ width, total: shown + more }).toEqual({ width, total: 9 });
    }
    // wider bars fit more rows
    const counts = WIDTHS.map((width) => Number(/\+(\d+) more/.exec(plainText(fixture[`overflow@${width}`][1]))?.[1]));
    expect(counts[0]).toBeGreaterThan(counts[1]);
    expect(counts[1]).toBeGreaterThan(counts[2]);
  });

  test("completed ends in the success mark, failed in the failure mark, error in the marker alone", () => {
    for (const width of WIDTHS) {
      expect(plainText(fixture[`completed@${width}`][0])).toContain("✓ swarm");
      expect(plainText(fixture[`failed@${width}`][0])).toContain("✕ swarm");
      const lines = fixture[`error@${width}`].map(plainText);
      expect({ width, rows: lines.length }).toEqual({ width, rows: 1 });
      expect({ width, first: lines[0].includes("swarm: telemetry unavailable") }).toEqual({ width, first: true });
    }
    expect(plainText(fixture["error-rows@80"][0])).toContain("telemetry unavailable");
  });

  test("reduced motion pins the spinner to the plain mark; the default animates once a second", () => {
    const frames = [0, 1, 2, 3].map((s) => plainText(fixture[`spinner:animated+${s}s@80`][0]).split(" ")[1]);
    expect(new Set(frames).size).toBe(4);
    for (const s of [0, 1, 2, 3]) expect(plainText(fixture[`spinner:reduced+${s}s@80`][0]).split(" ")[1]).toBe("●");
  });

  test("the headless stderr lines are exactly what the real controller prints for the scripted swarm", () => {
    const expected = [
      "[swarm] a05-backend starting 0s",
      "[swarm] a06-frontend starting 0s",
      "[swarm] a06-frontend failed 41s",
      "[swarm] a05-backend completed 1m05s",
    ];
    expect(fixture.headless).toEqual(expected);
    expect(headlessLines()).toEqual(expected);
  });
});

describe("the token chips (row 1, right)", () => {
  /** Contract: on-screen order left to right, and the drop priority of each chip of the full snapshot the fixture draws. */
  const CHIPS: ReadonlyArray<readonly [string, number]> = [
    ["graph ready", 11],
    ["bill 9.4k · out 2.1k", 10],
    ["idle 1200ms", 9],
    ["payload 156523c", 8],
    ["query 85ms", 7],
    ["rtk +4.1k", 6],
    ["cost 12c", 5],
    ["turn 3200ms", 4],
    ["before 61.4k · now 13.2k", 3],
    ["ttft 640ms", 2],
    ["cache 87%", 1],
    ["saved 48.2k", 0],
  ];
  const rows = (key: string): string[] => fixture[key].map(plainText);
  const shown = (key: string): Array<readonly [string, number]> => CHIPS.filter(([chip]) => rows(key)[0].includes(chip));

  test("chips sit at the right end of row 1 with `saved` right-most; row 2 never carries them", () => {
    for (const key of ["tokens-active@80", "tokens-active@120", "tokens-idle@120", "tokens-idle@200", "tokens-minimal@80"]) {
      expect({ key, end: rows(key)[0].endsWith("saved 48.2k ◗") }).toEqual({ key, end: true });
      expect({ key, below: rows(key).slice(1).join("\n").includes("saved") }).toEqual({ key, below: false });
    }
  });

  test("a wider row shows the chips of a narrower one plus more, in the same on-screen order; the most important are the last to go", () => {
    // the widest row shows the whole list, in order
    expect(shown("tokens-idle@200")).toEqual(CHIPS);
    const at = (chip: string): number => rows("tokens-idle@200")[0].indexOf(chip);
    CHIPS.slice(1).forEach(([chip], index) => expect({ chip, after: at(chip) > at(CHIPS[index][0]) }).toEqual({ chip, after: true }));
    for (const [narrow, wide] of [["tokens-idle@120", "tokens-idle@200"], ["tokens-active@80", "tokens-active@120"]]) {
      const kept = shown(narrow);
      const more = shown(wide);
      expect({ narrow, wide, subset: kept.every(([chip]) => more.some(([other]) => other === chip)) }).toEqual({ narrow, wide, subset: true });
      expect({ narrow, wide, strictlyMore: more.length > kept.length }).toEqual({ narrow, wide, strictlyMore: true });
    }
    for (const key of ["tokens-idle@120", "tokens-active@80", "tokens-active@120"]) {
      const kept = shown(key);
      const dropped = CHIPS.filter((chip) => !kept.includes(chip));
      expect({ key, saved: kept.some(([chip]) => chip === "saved 48.2k") }).toEqual({ key, saved: true });
      expect(dropped.length).toBeGreaterThan(0);
      // priority, never position: nothing less important than a dropped chip survives
      expect({ key, ok: Math.max(...kept.map(([, priority]) => priority)) <= Math.min(...dropped.map(([, priority]) => priority)) }).toEqual({ key, ok: true });
    }
  });

  test("a snapshot with only the required counts shows only those chips; nothing is invented", () => {
    const text = rows("tokens-minimal@80")[0];
    expect(text).toContain("before 61.4k · now 13.2k");
    expect(text).toContain("saved 48.2k");
    expect(text).not.toMatch(/cache|ttft|turn \d|cost|rtk|payload|query|bill|graph|idle \d|moved/);
  });

  test("a snapshot of another session is not drawn, and the chips never change row 2 or the left of row 1", () => {
    expect(rows("tokens-foreign@80")).toEqual(rows("active@80"));
    expect(rows("tokens-foreign@80")[0]).not.toMatch(/saved|cache/);
    expect(rows("tokens-active@80")[1]).toBe(rows("active@80")[1]);
    expect(rows("tokens-active@80")[0]).toContain("● swarm 5m00s");
  });

  test("an idle swarm keeps the chips in its one row: the idle marker on the left, the savings on the right", () => {
    const lines = rows("tokens-idle@120");
    expect(lines).toHaveLength(1);
    expect(lines[0].startsWith("◖ ○ swarm idle")).toBe(true);
  });
});

describe("docs/swarm-bar.md names what the bar reads", () => {
  test("every switch and every status word of the renderer is documented", () => {
    for (const name of [
      "SWARM_BAR=off",
      "SWARM_BAR_LINGER_S",
      "SWARM_BAR_FAIL_LINGER_S",
      "SWARM_BAR_REDUCED_MOTION",
      "SWARM_BAR_DETACHED_S",
      "SWARM_HEARTBEAT_S",
      "SWARM_STALL_AFTER_S",
      "SWARM_DIR",
      "live.json",
      "task:subagent:progress",
      "task:subagent:lifecycle",
      "+N more",
      "bars:restack",
      "token-opt:savings",
      "$HOME/.omp/agent/token-opt/live.json",
    ]) {
      expect({ name, documented: doc.includes(name) }).toEqual({ name, documented: true });
    }
    for (const chip of [
      "graph ready",
      "bill <n>",
      "idle <n>ms",
      "payload <n>c",
      "query <n>ms",
      "rtk +<n>",
      "cost <n>c",
      "turn <n>ms",
      "before <n> · now <n>",
      "prefix moved",
      "tools moved",
      "ttft <n>ms",
      "cache <n>%",
      "saved <n>",
    ]) {
      expect({ chip, documented: doc.includes(`\`${chip}`) }).toEqual({ chip, documented: true });
    }
    for (const word of ["FAIL", "TIMEOUT", "STALL", "queued", "wait", "handoff", "cancelled", "start"]) {
      expect({ word, documented: doc.includes(word) }).toEqual({ word, documented: true });
    }
    for (const state of ["idle", "loading", "active", "overflow", "completed", "failed", "error"]) {
      expect({ state, documented: doc.includes(`### State \`${state}`) }).toEqual({ state, documented: true });
    }
  });

  test("timer examples in the doc are what formatElapsed prints", () => {
    expect([59, 252, 3900].map(formatElapsed)).toEqual(["59s", "4m12s", "1h05m"]);
    expect(doc).toContain("`59s`, `4m12s`, `1h05m`");
  });
});

describe("recorded real-omp panes (verified on omp 18.12.1)", () => {
  const pane = (name: string): string[] => readFileSync(join(HERE, "fixtures", name), "utf8").split("\n").map(plainText);
  const seconds = (text: string): number => {
    const match = /swarm (?:(\d+)m(\d+)s|(\d+)s)/.exec(text);
    if (!match) throw new Error(`no swarm elapsed in: ${text}`);
    return match[3] !== undefined ? Number(match[3]) : Number(match[1]) * 60 + Number(match[2]);
  };

  test("the second pane, taken 5 s later, shows every live row's elapsed 5 s larger; the settled row did not move", () => {
    const first = pane("omp_pane_t3.ansi");
    const second = pane("omp_pane_t8.ansi");
    const bar = (lines: string[]) => lines.filter((line) => line.includes("swarm ") || line.includes("a05-backend"));
    const [sumA, rowsA] = bar(first);
    const [sumB, rowsB] = bar(second);
    expect(seconds(sumB) - seconds(sumA)).toBe(5);
    const elapsedOf = (row: string, agent: string): number => {
      const match = new RegExp(`${agent}\\s+(?:(\\d+)m(\\d+)s|(\\d+)s)`).exec(row);
      if (!match) throw new Error(`${agent} missing in: ${row}`);
      return match[3] !== undefined ? Number(match[3]) : Number(match[1]) * 60 + Number(match[2]);
    };
    for (const agent of ["a05-backend", "a06-frontend", "a07-data", "a09-reviewer"]) {
      expect({ agent, delta: elapsedOf(rowsB, agent) - elapsedOf(rowsA, agent) }).toEqual({ agent, delta: 5 });
    }
    expect(elapsedOf(rowsB, "a10-security")).toBe(elapsedOf(rowsA, "a10-security"));
    expect(doc).toContain(rowsA.trim());
    expect(doc).toContain(rowsB.trim());
  });
});
