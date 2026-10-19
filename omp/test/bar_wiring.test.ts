import { describe, expect, test } from "bun:test";
import { mkdirSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Bridge } from "../src/bridge.ts";
import { createSwarmExtension } from "../src/index.ts";
import type { EventBus, ExtensionContext, SessionEntry, WidgetComponent, WidgetFactory, WidgetHost } from "../src/omp-api.ts";
import { LIFECYCLE_CHANNEL, PROGRESS_CHANNEL, SWARM_BAR_KEY, SWARM_STATUS_KEY, type BarTimers } from "../src/swarm_bar.ts";
import { displayWidth, stripAnsi, type BarTheme } from "../src/swarm_dock.ts";
import { fakeCtx, fakePi, type FakePi, gitRepo, isolateEnv, REPO_ROOT, tmpDir } from "./helpers.ts";

isolateEnv("SWARM_BAR", "SWARM_DIR", "SWARM_AGENT");

const theme: BarTheme = {
  fg: (color, text) => `\x1b[36m${text}\x1b[39m`,
  bold: (text) => `\x1b[1m${text}\x1b[22m`,
  getSpinnerFrames: () => ["⠋", "⠙"],
};

/** omp's EventBus: handlers run synchronously on emit, `on` returns an unsubscribe. */
class FakeBus implements EventBus {
  readonly listeners = new Map<string, Set<(data: unknown) => void>>();
  emit(channel: string, data: unknown): void {
    for (const handler of this.listeners.get(channel) ?? []) handler(data);
  }
  on(channel: string, handler: (data: unknown) => void): () => void {
    const set = this.listeners.get(channel) ?? new Set();
    set.add(handler);
    this.listeners.set(channel, set);
    return () => void set.delete(handler);
  }
  count(): number {
    return [...this.listeners.values()].reduce((sum, set) => sum + set.size, 0);
  }
}

/** Timers the test turns by hand: `turn()` is one macrotask, so a timer queued by a timer fires on the NEXT turn. */
class FakeTimers implements BarTimers {
  private seq = 0;
  readonly timeouts = new Map<number, { fn: () => void; ms: number }>();
  readonly intervals = new Map<number, () => void>();
  readonly intervalMs = new Map<number, number>();
  setTimeout(fn: () => void, ms: number): unknown {
    this.timeouts.set(++this.seq, { fn, ms });
    return this.seq;
  }
  clearTimeout(handle: unknown): void {
    this.timeouts.delete(handle as number);
  }
  setInterval(fn: () => void, ms: number): unknown {
    this.intervals.set(++this.seq, fn);
    this.intervalMs.set(this.seq, ms);
    return this.seq;
  }
  clearInterval(handle: unknown): void {
    this.intervals.delete(handle as number);
    this.intervalMs.delete(handle as number);
  }
  /** Intervals running at `ms`: the 1 Hz ticker is 1000, the idle watcher defaults to 2000. */
  count(ms: number): number {
    return [...this.intervalMs.values()].filter((value) => value === ms).length;
  }
  turn(): void {
    const due = [...this.timeouts].filter(([, t]) => t.ms === 0);
    for (const [id, t] of due) {
      this.timeouts.delete(id);
      t.fn();
    }
  }
  /** The non-zero timeouts (the linger wake). */
  wakeUp(): void {
    for (const [id, t] of [...this.timeouts].filter(([, entry]) => entry.ms > 0)) {
      this.timeouts.delete(id);
      t.fn();
    }
  }
  tickInterval(): void {
    for (const fn of [...this.intervals.values()]) fn();
  }
}

interface WidgetCall {
  op: "widget";
  key: string;
  /** `undefined` removes the widget. */
  content: string[] | WidgetFactory | undefined;
}
interface StatusCall {
  op: "status";
  key: string;
  text: string | undefined;
}
type UiCall = WidgetCall | StatusCall;

/** The host side of `ctx.ui`: widgets stack in insertion order, re-setting a key moves it to the bottom and disposes the old component. */
function fakeUi(options: { throwOnWidget?: boolean } = {}) {
  const calls: UiCall[] = [];
  const stack = new Map<string, WidgetComponent | string[]>();
  const tui = { renders: 0, requestRender() { this.renders++; } } satisfies WidgetHost & { renders: number };
  let throwOnWidget = options.throwOnWidget ?? false;
  const ui = {
    notify() {},
    setStatus(key: string, text: string | undefined) {
      calls.push({ op: "status", key, text });
    },
    setWidget(key: string, content: string[] | WidgetFactory | undefined) {
      if (throwOnWidget) throw new Error("host widget failure");
      calls.push({ op: "widget", key, content });
      const old = stack.get(key);
      if (old && !Array.isArray(old)) old.dispose?.();
      stack.delete(key);
      if (content === undefined) return;
      // a partial theme stands in for omp's Theme: the bar reads it defensively
      stack.set(key, typeof content === "function" ? content(tui, theme as never) : content);
    },
  };
  return {
    ui,
    tui,
    calls,
    stack,
    widgetCalls: () => calls.filter((call): call is WidgetCall => call.op === "widget"),
    order: () => [...stack.keys()],
    statuses: () => calls.filter((call): call is StatusCall => call.op === "status").map((call) => call.text),
    lines(width = 120): string[] {
      const component = stack.get(SWARM_BAR_KEY);
      return component && !Array.isArray(component) ? [...component.render(width)].map((line) => stripAnsi(line)) : [];
    },
    failWidgets(on: boolean) {
      throwOnWidget = on;
    },
  };
}

interface Harness {
  pi: FakePi;
  bus: FakeBus;
  timers: FakeTimers;
  host: ReturnType<typeof fakeUi>;
  ctx: ExtensionContext;
  stderr: string[];
  clock: { mono: number };
  fire(event: string, payload?: unknown): unknown;
}

function harness(
  options: {
    ctx?: Partial<ExtensionContext>;
    env?: Record<string, string | undefined>;
    swarmDir?: string;
    wallNow?: () => number;
    entries?: SessionEntry[];
    hostOptions?: { throwOnWidget?: boolean };
    activeTools?: string[];
  } = {},
): Harness {
  const bus = new FakeBus();
  const timers = new FakeTimers();
  const host = fakeUi(options.hostOptions);
  const stderr: string[] = [];
  const clock = { mono: 50_000 };
  const bridge: Bridge = async () => ({ code: 0, stdout: "{}", stderr: "", json: {} }) as never;
  const pi = fakePi({ activeTools: options.activeTools });
  pi.api.events = bus;
  const dir = options.swarmDir;
  pi.load(
    createSwarmExtension({
      bridge,
      bar: {
        env: options.env ?? {},
        now: () => clock.mono,
        wallNow: options.wallNow ?? (() => Date.now()),
        timers,
        stderr: (text) => void stderr.push(text),
        swarmDir: dir === undefined ? () => join(tmpDir("swarm-nolive-"), "none") : () => dir,
      },
    }),
  );
  const entries = options.entries ?? [];
  const ctx: ExtensionContext = {
    ...fakeCtx(gitRepo(), entries),
    ui: host.ui,
    mode: "tui",
    hasUI: true,
    agent: { kind: "main", id: "Main", name: "main", depth: 0 },
    ...options.ctx,
  };
  return {
    pi,
    bus,
    timers,
    host,
    ctx,
    stderr,
    clock,
    fire: (event, payload = {}) => pi.handler(event)(payload, ctx),
  };
}

function frame(id: string, agent: string, status: string, durationMs = 0, extra: Record<string, unknown> = {}) {
  return {
    index: 0,
    agent,
    task: "t",
    progress: { index: 0, id, agent, status, task: "t", toolCount: 1, requests: 1, tokens: 1, cost: 0, durationMs, ...extra },
  };
}

describe("mounting", () => {
  const started = { id: "a", agent: "a05-backend", status: "started", index: 0 };

  test("an idle session mounts nothing: no widget, no spacer row, no status", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("agent_start");
    h.fire("turn_start");
    h.timers.turn();
    h.timers.turn();
    expect(h.host.widgetCalls()).toEqual([]);
    expect(h.host.order()).toEqual([]);
    expect(h.host.statuses()).toEqual([]);
  });

  test("the widget mounts on the first activity: key agent-swarm, aboveEditor, a component factory", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, started);
    const widgets = h.host.widgetCalls();
    expect(widgets).toHaveLength(1);
    expect(widgets[0]).toMatchObject({ key: SWARM_BAR_KEY });
    expect(typeof widgets[0].content).toBe("function");
    expect(h.host.order()).toEqual([SWARM_BAR_KEY]);
  });

  test("it unmounts (setWidget(key, undefined)) when the last row lingered out, and a remount never revives an idle bar", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, started);
    h.bus.emit(LIFECYCLE_CHANNEL, { ...started, status: "completed" });
    expect(h.host.order()).toEqual([SWARM_BAR_KEY]);
    h.clock.mono += 21_000;
    h.timers.wakeUp();
    expect(h.host.order()).toEqual([]);
    expect(h.host.widgetCalls().at(-1)).toMatchObject({ key: SWARM_BAR_KEY, content: undefined });
    const before = h.host.widgetCalls().length;
    h.fire("turn_end");
    h.timers.turn();
    h.timers.turn();
    expect(h.host.widgetCalls()).toHaveLength(before);
    // and it mounts again with the next activity
    h.bus.emit(LIFECYCLE_CHANNEL, { ...started, id: "b", agent: "a06-frontend" });
    expect(h.host.order()).toEqual([SWARM_BAR_KEY]);
  });

  test("session_start subscribes both bus channels and extension load does neither", () => {
    const h = harness();
    expect(h.bus.count()).toBe(0);
    h.fire("session_start");
    expect(h.bus.listeners.get(PROGRESS_CHANNEL)?.size).toBe(1);
    expect(h.bus.listeners.get(LIFECYCLE_CHANNEL)?.size).toBe(1);
  });

  test("remount discipline: every position-moving event schedules ONE deferred double-tick remount while mounted", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, started);
    expect(h.host.widgetCalls()).toHaveLength(1);
    h.timers.turn(); // tick 1: only the outer timer fires
    expect(h.host.widgetCalls()).toHaveLength(1);
    h.timers.turn(); // tick 2: the remount
    expect(h.host.widgetCalls()).toHaveLength(2);
    h.timers.turn();
    expect(h.host.widgetCalls()).toHaveLength(2);

    // every remount trigger in one tick still produces a single remount, two ticks later
    for (const event of ["agent_start", "turn_start", "before_agent_start", "tool_execution_start", "tool_execution_end", "turn_end", "agent_end", "session_start"]) h.fire(event);
    h.fire("tool_execution_start");
    h.timers.turn();
    expect(h.host.widgetCalls()).toHaveLength(2);
    h.timers.turn();
    expect(h.host.widgetCalls()).toHaveLength(3);
    h.timers.turn();
    h.timers.turn();
    expect(h.host.widgetCalls()).toHaveLength(3);
  });

  test("agent_start and turn_end, the events Ultrathink also remounts on, schedule the remount too", () => {
    for (const event of ["agent_start", "turn_end"]) {
      const h = harness();
      h.bus.emit(LIFECYCLE_CHANNEL, started); // not subscribed yet: ignored
      h.fire("session_start");
      h.bus.emit(LIFECYCLE_CHANNEL, started);
      h.timers.turn();
      h.timers.turn();
      const before = h.host.widgetCalls().length;
      h.fire(event);
      h.timers.turn();
      h.timers.turn();
      expect({ event, remounted: h.host.widgetCalls().length - before }).toEqual({ event, remounted: 1 });
    }
  });

  test("an event between the two ticks is absorbed by the pending remount", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, started);
    h.timers.turn();
    h.fire("turn_start");
    h.timers.turn();
    expect(h.host.widgetCalls()).toHaveLength(2);
    h.timers.turn();
    expect(h.host.widgetCalls()).toHaveLength(2);
  });

  test("the bar lands after another bar's single-tick remount, so it is the last (third) bar", () => {
    const h = harness();
    h.host.ui.setWidget("ultrathink", ["u"]);
    h.host.ui.setWidget("aio-chrome", ["c"]);
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, started); // mounted on first activity: already after the other two
    expect(h.host.order()).toEqual(["ultrathink", "aio-chrome", SWARM_BAR_KEY]);
    // the other bars re-mount themselves after the same event, one tick later
    h.timers.setTimeout(() => {
      h.host.ui.setWidget("ultrathink", ["u"]);
      h.host.ui.setWidget("aio-chrome", ["c"]);
    }, 0);
    h.timers.turn();
    expect(h.host.order().at(-1)).not.toBe(SWARM_BAR_KEY);
    h.timers.turn();
    expect(h.host.order()).toEqual(["ultrathink", "aio-chrome", SWARM_BAR_KEY]);
  });

  test("bus frames only request a render once mounted: no setWidget", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, { ...started, id: "first", agent: "first" });
    h.timers.turn();
    h.timers.turn();
    const widgetsBefore = h.host.widgetCalls().length;
    const rendersBefore = h.host.tui.renders;
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "a05-backend", "running", 1000));
    h.bus.emit(LIFECYCLE_CHANNEL, started);
    expect(h.host.widgetCalls()).toHaveLength(widgetsBefore);
    expect(h.host.tui.renders).toBeGreaterThan(rendersBefore);
    expect(h.host.lines().join("\n")).toContain("a05-backend");
  });

  test("the telemetry-error marker alone mounts the widget", () => {
    const dir = join(tmpDir("swarm-marker-"), ".swarm");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "live.json"), "{ not json");
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.host.order()).toEqual([SWARM_BAR_KEY]);
    expect(h.host.lines()).toEqual(["swarm: telemetry unavailable"]);
  });
});

describe("ticker, status and lingering", () => {
  test("the 1 Hz ticker exists only while something is live; settled rows linger, then everything is cleared", () => {
    const h = harness();
    h.fire("session_start");
    expect(h.timers.count(1000)).toBe(0);
    expect(h.timers.count(2000)).toBe(1); // the idle watcher
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "a", agent: "a05-backend", status: "started", index: 0 });
    expect(h.timers.count(1000)).toBe(1);
    expect(h.timers.intervals.size).toBe(1); // the watcher yields to the ticker: never two pollers
    expect(h.host.statuses().at(-1)).toBe("swarm: 1 run");

    h.clock.mono += 7000;
    h.timers.tickInterval();
    expect(h.host.lines()[1]).toMatch(/a05-backend\s+7s/);
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "a", agent: "a05-backend", status: "completed", index: 0 });
    expect(h.timers.count(1000)).toBe(0);
    expect(h.timers.count(2000)).toBe(1);
    expect(h.host.statuses().at(-1)).toBeUndefined();
    expect(h.host.lines()[1]).toMatch(/✓ a05-backend\s+7s/);

    // the settled row lingers 20 s and one wake-up removes it
    expect([...h.timers.timeouts.values()].some((t) => t.ms > 0)).toBe(true);
    h.clock.mono += 21_000;
    h.timers.wakeUp();
    expect(h.host.lines()).toEqual([]);
    expect([...h.timers.timeouts.values()].filter((t) => t.ms > 0)).toHaveLength(0);
  });

  test("a failed run lingers for the long window and puts a failure count in the status line", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "a05-backend", "running", 0));
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "a05-backend", "failed", 5000));
    expect(h.host.statuses().at(-1)).toBe("swarm: 1 fail");
    h.clock.mono += 60_000;
    h.timers.wakeUp();
    expect(h.host.lines().join("\n")).toContain("✕ FAIL");
    h.clock.mono += 600_000;
    h.timers.wakeUp();
    expect(h.host.lines()).toEqual([]);
    expect(h.host.statuses().at(-1)).toBeUndefined();
  });

  test("the linger windows and reduced motion come from the environment", () => {
    const h = harness({ env: { SWARM_BAR_LINGER_S: "2", SWARM_BAR_FAIL_LINGER_S: "4", SWARM_BAR_REDUCED_MOTION: "1" } });
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "a", "running", 0));
    expect(h.host.lines()[0]).toContain("●");
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "a", "completed", 1000));
    h.clock.mono += 2001;
    h.timers.wakeUp();
    expect(h.host.lines()).toEqual([]);
  });

  test("an active turn keeps the ticker (live.json may appear while the model thinks); agent_end releases it", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("turn_start");
    expect(h.timers.count(1000)).toBe(1);
    h.fire("agent_end");
    expect(h.timers.count(1000)).toBe(0);
    h.fire("tool_execution_start");
    expect(h.timers.count(1000)).toBe(1);
    h.fire("tool_execution_end");
    expect(h.timers.count(1000)).toBe(0);
  });
});

describe("who gets a bar", () => {
  test("a subagent session mounts nothing and subscribes to nothing", () => {
    const h = harness({ ctx: { agent: { kind: "sub", id: "0-Explore", name: "explore", depth: 1, parentId: "Main" } } });
    h.fire("session_start");
    h.fire("turn_start");
    h.timers.turn();
    h.timers.turn();
    expect(h.host.calls).toEqual([]);
    expect(h.bus.count()).toBe(0);
    expect(h.stderr).toEqual([]);
  });

  test("a child session (session_init entry) mounts nothing even where the host sends no agent identity", () => {
    const h = harness({ ctx: { agent: undefined }, entries: [{ type: "session_init", agent: "a05-backend" }] });
    h.fire("session_start");
    expect(h.host.calls).toEqual([]);
    expect(h.bus.count()).toBe(0);
  });

  test("RPC mode, no UI and a context without ui mount nothing", () => {
    for (const ctx of [{ mode: "rpc" as const }, { hasUI: false, mode: "rpc" as const }, { ui: undefined }, { mode: undefined }, { hasUI: false, mode: undefined }]) {
      const h = harness({ ctx });
      h.fire("session_start");
      h.fire("tool_execution_start");
      h.timers.turn();
      h.timers.turn();
      expect({ ctx, calls: h.host.calls, subs: h.bus.count(), err: h.stderr }).toEqual({ ctx, calls: [], subs: 0, err: [] });
    }
  });

  test("SWARM_BAR=off disables the bar everywhere", () => {
    for (const ctx of [{}, { hasUI: false, mode: "print" as const, ui: undefined }]) {
      const h = harness({ env: { SWARM_BAR: " OFF " }, ctx });
      h.fire("session_start");
      h.bus.emit(LIFECYCLE_CHANNEL, { id: "a", agent: "a05-backend", status: "started", index: 0 });
      expect(h.host.calls).toEqual([]);
      expect(h.stderr).toEqual([]);
    }
  });

  test("a plain fake context (no ui, no mode) keeps the pre-existing handlers silent", () => {
    const pi = fakePi();
    pi.load(createSwarmExtension({ bridge: (async () => ({})) as never }));
    for (const event of ["session_start", "turn_start", "tool_execution_start", "agent_end"]) {
      expect(pi.handler(event)({}, fakeCtx(gitRepo()))).toBeUndefined();
    }
  });
});

describe("headless -p", () => {
  function headless(env: Record<string, string | undefined> = {}) {
    return harness({ env, ctx: { hasUI: false, mode: "print", ui: undefined } });
  }

  test("one stderr line on spawn and one on settle, nothing in between, and no widget", () => {
    const h = headless();
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "a", agent: "a05-backend", status: "started", index: 0 });
    h.clock.mono += 3000;
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "a05-backend", "running", 3000, { currentTool: "edit" }));
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "a05-backend", "running", 3100, { currentTool: "read" }));
    expect(h.stderr).toEqual(["[swarm] a05-backend starting 0s\n"]);
    h.clock.mono += 62_000;
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "a05-backend", "completed", 65_000));
    expect(h.stderr).toEqual(["[swarm] a05-backend starting 0s\n", "[swarm] a05-backend completed 1m05s\n"]);
    expect(h.host.calls).toEqual([]);
    expect(h.timers.timeouts.size + h.timers.intervals.size).toBe(0);
  });

  test("a failed settle and staggered agents each print their own lines", () => {
    const h = headless();
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "agent-a", "running", 0));
    h.bus.emit(PROGRESS_CHANNEL, frame("b", "agent-b", "running", 0));
    h.bus.emit(PROGRESS_CHANNEL, frame("b", "agent-b", "failed", 4000));
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "agent-a", "completed", 9000));
    expect(h.stderr).toEqual([
      "[swarm] agent-a running 0s\n",
      "[swarm] agent-b running 0s\n",
      "[swarm] agent-b failed 4s\n",
      "[swarm] agent-a completed 9s\n",
    ]);
  });

  test("agent text on the line is sanitized", () => {
    const h = headless();
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "evil\x1b[31m-agent\r\nsecond line", "running", 0));
    expect(h.stderr).toHaveLength(1);
    expect(h.stderr[0]).toBe("[swarm] evil-agent second line running 0s\n");
  });

  test("json mode prints too; a settled-on-arrival run prints once", () => {
    const h = harness({ ctx: { hasUI: false, mode: "json", ui: undefined } });
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "a", "completed", 2000));
    expect(h.stderr).toEqual(["[swarm] a completed 2s\n"]);
  });

  test("headless sessions trim settled rows as events arrive: a frame after the linger starts a fresh row", () => {
    const h = harness({ ctx: { hasUI: false, mode: "print", ui: undefined } });
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "agent-a", "running", 0));
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "agent-a", "completed", 65_000));
    expect(h.stderr).toHaveLength(2);
    // within the linger a stale "running" frame for the settled id is ignored
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "agent-a", "running", 65_000));
    expect(h.stderr).toHaveLength(2);
    h.clock.mono += 21_000;
    h.bus.emit(PROGRESS_CHANNEL, frame("b", "agent-b", "running", 0)); // any event trims, there is no ticker in -p mode
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "agent-a", "running", 65_000));
    expect(h.stderr.at(-1)).toBe("[swarm] agent-a running 1m05s\n");
  });
});

describe("teardown", () => {
  test("session_shutdown unsubscribes, clears timers, removes the widget and the status text", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "a", agent: "a05-backend", status: "started", index: 0 });
    expect(h.bus.count()).toBe(2);
    expect(h.timers.intervals.size).toBe(1);
    expect(h.timers.timeouts.size).toBeGreaterThan(0);
    h.fire("session_shutdown");
    expect(h.bus.count()).toBe(0);
    expect(h.timers.intervals.size).toBe(0);
    expect([...h.timers.timeouts.values()]).toHaveLength(0);
    expect(h.host.order()).toEqual([]);
    expect(h.host.widgetCalls().at(-1)).toMatchObject({ key: SWARM_BAR_KEY, content: undefined });
    expect(h.host.calls.at(-1)).toEqual({ op: "status", key: SWARM_STATUS_KEY, text: undefined });
    // late bus frames and events no longer do anything
    const before = h.host.calls.length;
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "b", agent: "x", status: "started", index: 0 });
    h.timers.turn();
    h.timers.turn();
    expect(h.host.calls).toHaveLength(before);
    h.fire("session_shutdown");
  });

  test("session_switch tears the old session down and the new one mounts only when it has something to show", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "a", agent: "old-agent", status: "started", index: 0 });
    expect(h.host.lines().join("\n")).toContain("old-agent");
    h.fire("session_switch");
    expect(h.bus.count()).toBe(2);
    expect(h.timers.count(1000)).toBe(0);
    expect(h.timers.count(2000)).toBe(1); // only the new session's idle watcher
    expect(h.host.order()).toEqual([]);
    expect(h.host.widgetCalls().filter((call) => call.content === undefined)).toHaveLength(1);
    h.timers.turn();
    h.timers.turn();
    expect(h.host.order()).toEqual([]);
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "b", agent: "new-agent", status: "started", index: 0 });
    expect(h.host.order()).toEqual([SWARM_BAR_KEY]);
    expect(h.host.lines().join("\n")).toContain("new-agent");
    expect(h.host.lines().join("\n")).not.toContain("old-agent");
  });

  test("a remount that was pending at shutdown never fires", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("session_shutdown");
    const before = h.host.widgetCalls().length;
    h.timers.turn();
    h.timers.turn();
    expect(h.host.widgetCalls()).toHaveLength(before);
  });

  test("events after session_shutdown never restart the bar (omp aborts the agent after it emits shutdown)", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "a", agent: "a05-backend", status: "started", index: 0 });
    h.fire("session_shutdown");
    const before = h.host.calls.length;
    // the in-flight task call settles, and the agent loop winds down: all of it arrives after shutdown
    h.fire("tool_call", { toolName: "task", toolCallId: "call-9", input: { tasks: [{ agent: "a05-backend" }] } });
    h.fire("tool_result", { toolName: "task", toolCallId: "call-9", isError: false });
    h.fire("tool_execution_end", { toolName: "task", toolCallId: "call-9", isError: true });
    h.fire("turn_end");
    h.fire("agent_end");
    h.timers.turn();
    h.timers.turn();
    expect(h.bus.count()).toBe(0);
    expect(h.host.calls).toHaveLength(before);
    expect(h.timers.intervals.size).toBe(0);
    expect(h.timers.timeouts.size).toBe(0);
  });

  test("a new session_start after shutdown reopens the bar", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("session_shutdown");
    h.fire("session_start");
    expect(h.bus.count()).toBe(2);
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "a", agent: "a05-backend", status: "started", index: 0 });
    expect(h.host.order()).toEqual([SWARM_BAR_KEY]);
  });
});

describe("failure isolation", () => {
  test("a host that throws on setWidget or requestRender never throws into a handler", () => {
    const h = harness();
    h.fire("session_start");
    h.host.failWidgets(true);
    h.host.tui.requestRender = () => {
      throw new Error("render failure");
    };
    for (const event of ["session_start", "turn_start", "before_agent_start", "tool_execution_start", "tool_execution_end", "agent_end", "session_switch"]) {
      expect(() => h.fire(event, { prompt: "hello" })).not.toThrow();
    }
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "a", agent: "a", status: "started", index: 0 });
    expect(() => {
      h.timers.turn();
      h.timers.turn();
      h.timers.tickInterval();
      h.fire("session_shutdown");
    }).not.toThrow();
  });

  test("garbage bus payloads are ignored", () => {
    const h = harness();
    h.fire("session_start");
    for (const payload of [null, undefined, 3, "x", [], {}, { progress: null }, { status: "started" }, { progress: { status: "running" } }]) {
      expect(() => {
        h.bus.emit(PROGRESS_CHANNEL, payload);
        h.bus.emit(LIFECYCLE_CHANNEL, payload);
      }).not.toThrow();
    }
    expect(h.host.lines()).toEqual([]);
  });

  test("the task tool_call decision is the guard's, whether or not the bar works", () => {
    const input = { tasks: [{ id: "be", agent: "a05-backend", description: "x", assignment: "y" }] };
    const event = { toolName: "task", toolCallId: "call-1", input };
    const healthy = harness();
    healthy.fire("session_start");
    const withBar = healthy.fire("tool_call", event);
    const broken = harness();
    broken.fire("session_start");
    broken.host.failWidgets(true);
    broken.host.tui.requestRender = () => {
      throw new Error("boom");
    };
    const withBrokenBar = broken.fire("tool_call", event);
    const bare = fakePi();
    bare.load(createSwarmExtension({ bridge: (async () => ({})) as never }));
    const without = bare.handler("tool_call")(event, fakeCtx(gitRepo()));
    expect(withBar).toEqual(without);
    expect(withBrokenBar).toEqual(without);
    expect(without).toBeUndefined();
  });

  test("a task call the guard blocks passes through unchanged, and no row is shown for it", () => {
    process.env.SWARM_AGENT = "a01-orchestrator"; // A01 without the `task` tool: the depth cap blocks the call
    const event = { toolName: "task", toolCallId: "call-1", input: { tasks: [{ name: "be", agent: "a05-backend" }] } };
    const h = harness({ activeTools: [] });
    h.fire("session_start");
    const withBar = h.fire("tool_call", event);
    const bare = fakePi({ activeTools: [] });
    bare.load(createSwarmExtension({ bridge: (async () => ({})) as never }));
    const without = bare.handler("tool_call")(event, fakeCtx(gitRepo()));
    expect(without).toMatchObject({ block: true });
    expect(withBar).toEqual(without);
    expect(h.host.order()).toEqual([]);
    expect(h.host.widgetCalls()).toEqual([]);
  });

  test("a throwing theme or an oversized width cannot make render throw or overflow", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, frame("a", "日本語".repeat(20), "running", 0, { currentToolIntent: "🚀".repeat(80) }));
    for (const width of [1, 5, 20, 80, 400]) {
      for (const line of h.host.lines(width)) expect(displayWidth(line)).toBeLessThanOrEqual(width);
    }
  });
});

describe("task tool rows (last resort)", () => {
  test("an accepted task call shows loading rows at once, the bus takes over, the result settles what nothing richer owns", () => {
    const h = harness();
    h.fire("session_start");
    const input = { tasks: [{ id: "be", agent: "a05-backend" }, { id: "fe", agent: "a06-frontend" }] };
    expect(h.fire("tool_call", { toolName: "task", toolCallId: "call-1", input })).toBeUndefined();
    expect(h.host.lines().join("\n")).toContain("start");
    expect(h.host.lines()[0]).toContain("2 run");
    h.clock.mono += 4000;
    h.bus.emit(PROGRESS_CHANNEL, { ...frame("be", "a05-backend", "completed", 4000), parentToolCallId: "call-1", index: 0 });
    h.fire("tool_result", { toolName: "task", toolCallId: "call-1", isError: false });
    const text = h.host.lines().join("\n");
    expect(text).toContain("a05-backend");
    expect(text).toContain("a06-frontend");
    // a05-backend completed through the bus; a06-frontend had no bus part and settled with the call
    expect(h.host.lines()[0]).toContain("2 done");
  });

  test("a task result for another tool or without an id changes nothing", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("tool_call", { toolName: "task", toolCallId: "call-1", input: { tasks: [{ agent: "a05-backend" }] } });
    h.fire("tool_result", { toolName: "read", toolCallId: "call-1", isError: false });
    h.fire("tool_result", { toolName: "task", isError: true });
    h.fire("tool_result", null);
    expect(h.host.lines()[0]).toContain("1 run");
  });

  test("a task call something else blocked after our guard: its tool_execution_end settles the row as cancelled, not a failure", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("turn_start");
    h.fire("tool_call", { toolName: "task", toolCallId: "call-1", input: { tasks: [{ name: "BackendWork", agent: "a05-backend" }] } });
    h.fire("tool_execution_start", { toolName: "task", toolCallId: "call-1" });
    expect(h.host.lines()[0]).toContain("1 run");
    // omp emits tool_execution_end (isError) for a blocked call, but never a tool_result
    h.fire("tool_execution_end", { toolName: "task", toolCallId: "call-1", isError: true, result: {} });
    expect(h.host.lines()[0]).toContain("1 cancel");
    expect(h.host.lines()[0]).not.toContain("1 fail");
    expect(h.host.lines()[1]).toContain("⊘ cancelled");
    expect(h.host.statuses().at(-1)).toBeUndefined();
  });

  test("tool_execution_end of another tool never settles a task row", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("tool_call", { toolName: "task", toolCallId: "call-1", input: { tasks: [{ agent: "a05-backend" }] } });
    h.fire("tool_execution_end", { toolName: "read", toolCallId: "call-1", isError: true });
    h.fire("tool_execution_end", null);
    expect(h.host.lines()[0]).toContain("1 run");
  });

  test("a spawn that never settles (no tool_execution_end, no tool_result) is cancelled at agent_end and the ticker stops", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("turn_start");
    h.fire("tool_call", { toolName: "task", toolCallId: "call-1", input: { tasks: [{ agent: "a05-backend" }] } });
    expect(h.timers.count(1000)).toBe(1);
    h.fire("agent_end");
    expect(h.host.lines()[1]).toContain("⊘ cancelled");
    expect(h.host.statuses().at(-1)).toBeUndefined();
    expect(h.timers.count(1000)).toBe(0);
    // an hour later nothing is left on screen
    h.clock.mono += 3_600_000;
    h.timers.wakeUp();
    expect(h.host.order()).toEqual([]);
  });

  test("agent_end leaves a row the bus reports on alone: a detached swarm outlives the turn", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("turn_start");
    h.fire("tool_call", { toolName: "task", toolCallId: "call-1", input: { tasks: [{ agent: "a05-backend" }] } });
    h.bus.emit(LIFECYCLE_CHANNEL, { id: "x", agent: "a05-backend", status: "started", index: 0, parentToolCallId: "call-1", detached: true });
    h.fire("agent_end");
    expect(h.host.lines()[0]).toContain("1 run");
    expect(h.host.lines()[0]).not.toContain("cancel");
  });

  test("the `name` omp gives a task item aliases its row for the bus: one row, not two", () => {
    const h = harness();
    h.fire("session_start");
    h.fire("tool_call", { toolName: "task", toolCallId: "call-1", input: { tasks: [{ name: "BackendWork", agent: "a05-backend" }] } });
    // the bus names the run by the item's name and calls the agent something else: only the id can tie them together
    h.bus.emit(PROGRESS_CHANNEL, frame("BackendWork", "explore", "running", 1000));
    expect(h.host.lines()[0]).toContain("1 run");
    expect(h.host.lines()[0]).not.toContain("2 run");
    expect(h.host.lines().join("\n")).not.toContain("a05-backend");
  });
});

describe("live.json", () => {
  function liveDir() {
    const dir = join(tmpDir("swarm-live-"), ".swarm");
    mkdirSync(dir, { recursive: true });
    return dir;
  }
  const doc = (runs: unknown[], swarm: Record<string, unknown> = {}) => JSON.stringify({ schema: "swarm.live.v1", seq: 1, swarm: { max_parallel: 4, ...swarm }, runs });
  const running = { run_id: "T1.a1", task_id: "T1", agent: "a05-backend", state: "running", elapsed_s: 30, activity: "edit swarm/x.py" };

  test("an existing live.json is read at session_start and polled once per tick by mtime", () => {
    const dir = liveDir();
    writeFileSync(join(dir, "live.json"), doc([running], { effective_parallel: 3 }));
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.host.lines()[0]).toContain("slots 1/3");
    expect(h.host.lines()[1]).toMatch(/a05-backend\s+30s edit swarm\/x\.py/);
    expect(h.timers.count(1000)).toBe(1);
    h.clock.mono += 5000;
    h.timers.tickInterval();
    expect(h.host.lines()[1]).toMatch(/a05-backend\s+35s/);
    // the producer writes a newer snapshot: queued work shows up
    writeFileSync(join(dir, "live.json"), doc([running, { run_id: "T2.a1", task_id: "T2", agent: "a06-frontend", state: "queued" }]));
    utimesSync(join(dir, "live.json"), new Date(), new Date(Date.now() + 5000));
    h.clock.mono += 1000;
    h.timers.tickInterval();
    expect(h.host.lines()[1]).toContain("a06-frontend");
    expect(h.host.lines()[0]).toContain("1 queued");
  });

  test("no live.json is not an error", () => {
    const h = harness({ swarmDir: liveDir() });
    h.fire("session_start");
    h.fire("turn_start");
    expect(h.host.lines()).toEqual([]);
  });

  test("a malformed live.json shows the telemetry marker, never throws, and clears when it is fixed", () => {
    const dir = liveDir();
    const path = join(dir, "live.json");
    writeFileSync(path, "{ not json");
    const h = harness({ swarmDir: dir });
    expect(() => h.fire("session_start")).not.toThrow();
    expect(h.host.lines()).toEqual(["swarm: telemetry unavailable"]);
    writeFileSync(path, JSON.stringify({ schema: "other.v9", runs: [] }));
    utimesSync(path, new Date(), new Date(Date.now() + 3000));
    h.fire("turn_start");
    expect(h.host.lines()).toEqual(["swarm: telemetry unavailable"]);
    writeFileSync(path, doc([running]));
    utimesSync(path, new Date(), new Date(Date.now() + 6000));
    h.clock.mono += 1000;
    h.fire("turn_start");
    expect(h.host.lines()[1]).toContain("a05-backend");
    expect(h.host.lines().join("\n")).not.toContain("telemetry");
  });

  test("a live.json older than three heartbeats at first read, with live runs, is stale: marker, no rows", () => {
    const dir = liveDir();
    const path = join(dir, "live.json");
    writeFileSync(path, doc([running]));
    const old = new Date(Date.now() - 100_000);
    utimesSync(path, old, old);
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.host.lines()).toEqual(["swarm: telemetry unavailable"]);
  });

  test("an old live.json whose runs all settled long ago is just history: nothing shown, no marker", () => {
    const dir = liveDir();
    const path = join(dir, "live.json");
    writeFileSync(path, doc([{ ...running, state: "completed" }]));
    const old = new Date(Date.now() - 3_600_000);
    utimesSync(path, old, old);
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.host.lines()).toEqual([]);
  });

  test("a live.json from a swarm that died days ago is history even with runs marked live: no marker, nothing mounted", () => {
    const dir = liveDir();
    const path = join(dir, "live.json");
    writeFileSync(path, doc([running]));
    const old = new Date(Date.now() - 2 * 86_400_000);
    utimesSync(path, old, old);
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    h.fire("turn_start");
    expect(h.host.lines()).toEqual([]);
    expect(h.host.order()).toEqual([]);
    expect(h.host.statuses()).toEqual([]);
    // and it never turns into a warning later
    h.clock.mono += 3_600_000;
    h.fire("turn_start");
    expect(h.host.order()).toEqual([]);
  });

  test("a dead swarm seen shortly after it died warns once, and the warning ages out with the failure linger", () => {
    const dir = liveDir();
    const path = join(dir, "live.json");
    writeFileSync(path, doc([running]));
    const old = new Date(Date.now() - 100_000);
    utimesSync(path, old, old);
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.host.lines()).toEqual(["swarm: telemetry unavailable"]);
    h.clock.mono += 601_000;
    h.fire("turn_start");
    expect(h.host.lines()).toEqual([]);
    expect(h.host.order()).toEqual([]);
  });

  test("a snapshot that stops updating while it lists live runs goes stale on the consumer's monotonic clock", () => {
    const dir = liveDir();
    writeFileSync(join(dir, "live.json"), doc([running]));
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.host.lines()[1]).toContain("a05-backend");
    h.clock.mono += 20_000;
    h.timers.tickInterval();
    expect(h.host.lines()[1]).toContain("a05-backend");
    h.clock.mono += 11_000; // 31 s with no new snapshot
    h.timers.tickInterval();
    expect(h.host.lines()).toEqual(["swarm: telemetry unavailable"]);
  });

  test("the heartbeat environment sets the staleness window", () => {
    const dir = liveDir();
    writeFileSync(join(dir, "live.json"), doc([running]));
    const h = harness({ swarmDir: dir, env: { SWARM_HEARTBEAT_S: "1" } });
    h.fire("session_start");
    h.clock.mono += 3500;
    h.timers.tickInterval();
    expect(h.host.lines()).toEqual(["swarm: telemetry unavailable"]);
  });

  test("a removed live.json clears the marker and the live rows", () => {
    const dir = liveDir();
    const path = join(dir, "live.json");
    writeFileSync(path, "garbage");
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.host.lines()).toEqual(["swarm: telemetry unavailable"]);
    writeFileSync(path, doc([running]));
    utimesSync(path, new Date(), new Date(Date.now() + 3000));
    h.fire("turn_start");
    expect(h.host.lines()[1]).toContain("a05-backend");
    // unlink
    rmSync(path);
    h.clock.mono += 1000;
    h.timers.tickInterval();
    expect(h.host.lines()).toEqual([]);
  });

  test("a wall-clock jump does not move a running timer", () => {
    const dir = liveDir();
    writeFileSync(join(dir, "live.json"), doc([running]));
    let wall = Date.now();
    const h = harness({ swarmDir: dir, wallNow: () => wall });
    h.fire("session_start");
    wall += 86_400_000;
    h.clock.mono += 10_000;
    h.timers.tickInterval();
    expect(h.host.lines()[1]).toMatch(/a05-backend\s+40s/);
  });

  test("bus rows and live rows for different agents show side by side", () => {
    const dir = liveDir();
    writeFileSync(join(dir, "live.json"), doc([running]));
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, frame("x", "explore", "running", 2000));
    const text = h.host.lines().join("\n");
    expect(text).toContain("a05-backend");
    expect(text).toContain("explore");
    expect(h.host.lines()[0]).toContain("2 run");
  });
});

describe("idle watcher", () => {
  function liveDir() {
    const dir = join(tmpDir("swarm-idle-"), ".swarm");
    mkdirSync(dir, { recursive: true });
    return dir;
  }
  const doc = (runs: unknown[]) => JSON.stringify({ schema: "swarm.live.v1", seq: 1, swarm: { max_parallel: 4 }, runs });
  const running = { run_id: "T1.a1", task_id: "T1", agent: "a05-backend", state: "running", elapsed_s: 30, activity: "edit swarm/x.py" };

  test("a swarm that appears while the bar is idle is mounted, and the 1 Hz ticker takes over from the watcher", () => {
    const dir = liveDir();
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.timers.intervals.size).toBe(1);
    expect(h.timers.count(2000)).toBe(1);
    expect(h.host.order()).toEqual([]);
    writeFileSync(join(dir, "live.json"), doc([running]));
    h.clock.mono += 2000;
    h.timers.tickInterval();
    expect(h.host.order()).toEqual([SWARM_BAR_KEY]);
    expect(h.host.lines()[1]).toContain("a05-backend");
    expect(h.timers.count(1000)).toBe(1);
    expect(h.timers.intervals.size).toBe(1);
  });

  test("a live.json that disappears while idle clears the telemetry marker and unmounts", () => {
    const dir = liveDir();
    const path = join(dir, "live.json");
    writeFileSync(path, "{ not json");
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.host.lines()).toEqual(["swarm: telemetry unavailable"]);
    expect(h.timers.count(2000)).toBe(1);
    rmSync(path);
    h.timers.tickInterval();
    expect(h.host.lines()).toEqual([]);
    expect(h.host.order()).toEqual([]);
    expect(h.host.widgetCalls().at(-1)).toMatchObject({ key: SWARM_BAR_KEY, content: undefined });
  });

  test("an unchanged file, or none, costs no setWidget, requestRender or setStatus over 50 ticks", () => {
    const dir = liveDir();
    const settled = join(dir, "live.json");
    for (const present of [false, true]) {
      if (present) {
        writeFileSync(settled, doc([{ ...running, state: "completed" }]));
        const t = new Date(Math.floor(Date.now() / 1000) * 1000 - 1000);
        utimesSync(settled, t, t);
      }
      const h = harness({ swarmDir: dir });
      h.fire("session_start");
      expect(h.timers.count(2000)).toBe(1);
      const calls = h.host.calls.length;
      const renders = h.host.tui.renders;
      for (let i = 0; i < 50; i++) {
        h.clock.mono += 2000;
        h.timers.tickInterval();
      }
      expect(h.host.calls.length).toBe(calls);
      expect(h.host.tui.renders).toBe(renders);
    }
  });

  test("the idle tick is a stat: same mtime and size means the file is not read again", () => {
    const dir = liveDir();
    const path = join(dir, "live.json");
    writeFileSync(path, doc([{ ...running, state: "completed" }]));
    const t = new Date(Math.floor(Date.now() / 1000) * 1000 - 1000);
    utimesSync(path, t, t);
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    expect(h.host.lines()[1]).toContain("a05-backend");
    writeFileSync(path, doc([{ ...running, agent: "a06-backend", state: "completed" }])); // same size
    utimesSync(path, t, t);
    h.timers.tickInterval();
    expect(h.host.lines()[1]).toContain("a05-backend");
    const later = new Date(t.getTime() + 5000);
    utimesSync(path, later, later); // an mtime change does wake the full poll
    h.timers.tickInterval();
    expect(h.host.lines()[1]).toContain("a06-backend");
  });

  test("a file already stale at first sight stays as it was: history shows nothing, a fresh-dead one keeps its single marker", () => {
    const dir = liveDir();
    const path = join(dir, "live.json");
    writeFileSync(path, doc([running]));
    const ancient = new Date(Date.now() - 2 * 86_400_000);
    utimesSync(path, ancient, ancient);
    const history = harness({ swarmDir: dir });
    history.fire("session_start");
    for (let i = 0; i < 50; i++) history.timers.tickInterval();
    expect(history.host.calls).toEqual([]);

    const dead = new Date(Date.now() - 100_000);
    utimesSync(path, dead, dead);
    const warned = harness({ swarmDir: dir });
    warned.fire("session_start");
    expect(warned.host.lines()).toEqual(["swarm: telemetry unavailable"]);
    const calls = warned.host.calls.length;
    for (let i = 0; i < 50; i++) warned.timers.tickInterval();
    expect(warned.host.calls.length).toBe(calls);
    expect(warned.host.lines()).toEqual(["swarm: telemetry unavailable"]);
  });

  test("session_shutdown and session_switch clear the watcher; a late event does not bring it back", () => {
    const h = harness({ swarmDir: liveDir() });
    h.fire("session_start");
    expect(h.timers.intervals.size).toBe(1);
    h.fire("session_shutdown");
    expect(h.timers.intervals.size).toBe(0);
    h.fire("turn_end");
    expect(h.timers.intervals.size).toBe(0);
    h.fire("session_start");
    expect(h.timers.intervals.size).toBe(1);
    h.fire("session_switch");
    expect(h.timers.intervals.size).toBe(1); // the new session's own watcher, never two
    h.fire("session_shutdown");
    expect(h.timers.intervals.size).toBe(0);
  });

  test("SWARM_BAR=off, subagent, headless and RPC sessions create no interval and never look at live.json", () => {
    const cases: Array<{ env?: Record<string, string>; ctx?: Partial<ExtensionContext> }> = [
      { env: { SWARM_BAR: "off" } },
      { ctx: { agent: { kind: "sub", id: "0-Explore", name: "explore", depth: 1, parentId: "Main" } } },
      { ctx: { hasUI: false, mode: "print", ui: undefined } },
      { ctx: { mode: "rpc" } },
    ];
    for (const { env, ctx } of cases) {
      const dir = liveDir();
      writeFileSync(join(dir, "live.json"), doc([running])); // would mount at once if anything looked
      const h = harness({ swarmDir: dir, env, ctx });
      h.fire("session_start");
      h.fire("turn_start");
      expect(h.timers.intervals.size).toBe(0);
      writeFileSync(join(dir, "live.json"), doc([running, { ...running, run_id: "T2.a1" }]));
      h.timers.tickInterval();
      expect(h.host.calls).toEqual([]);
    }
  });

  test("SWARM_BAR_IDLE_POLL_MS sets the period, clamped to 500..30000; junk falls back to 2000", () => {
    const period = (value: string) => {
      const h = harness({ swarmDir: liveDir(), env: { SWARM_BAR_IDLE_POLL_MS: value } });
      h.fire("session_start");
      return [...h.timers.intervalMs.values()];
    };
    expect(period("100")).toEqual([500]);
    expect(period("99999")).toEqual([30_000]);
    expect(period("5000")).toEqual([5000]);
    expect(period("fast")).toEqual([2000]);
    expect(period("0")).toEqual([2000]);
  });

  test("a vanished swarm dir never throws out of the watcher", () => {
    const h = harness({ swarmDir: join(tmpDir("swarm-gone-"), "missing", ".swarm") });
    h.fire("session_start");
    expect(() => h.timers.tickInterval()).not.toThrow();
  });
});

describe("in-session specialists (A01 spawning a02..a15)", () => {
  const lane = (id: string, agent: string, status: string, durationMs: number, extra: Record<string, unknown> = {}) => ({
    index: 0,
    id,
    agent,
    agentSource: "project",
    status,
    task: `assignment for ${id}`,
    assignment: `assignment for ${id}`,
    recentTools: [],
    recentOutput: [],
    toolCount: 1,
    requests: 1,
    tokens: 1,
    cost: 0,
    durationMs,
    ...extra,
  });
  /** A01's own frame as the top-level session receives it: its progress carries the in-flight `task` details. */
  const a01Frame = (lanes: unknown[], results: unknown[] = [], durationMs = 0) =>
    frame("Orchestrate", "a01-orchestrator", "running", durationMs, {
      currentTool: "task",
      inflightTaskDetails: { projectAgentsDir: null, results, totalDurationMs: durationMs, progress: lanes },
    });
  const doc = (runs: unknown[]) => JSON.stringify({ schema: "swarm.live.v1", seq: 1, swarm: { max_parallel: 4 }, runs });

  test("the bar shows every specialist with its own timer, live from A01's frames, and settles them", () => {
    const h = harness();
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, a01Frame([lane("be", "a05-backend", "running", 30_000, { currentTool: "edit" }), lane("fe", "a06-frontend", "running", 4000)], [], 31_000));
    const first = h.host.lines();
    expect(first[0]).toContain("3 run");
    expect(first[1]).toMatch(/a05-backend\s+30s edit/);
    expect(first[1]).toMatch(/a06-frontend\s+4s/);
    h.clock.mono += 6000;
    h.timers.tickInterval();
    expect(h.host.lines()[1]).toMatch(/a05-backend\s+36s/);
    expect(h.host.lines()[1]).toMatch(/a06-frontend\s+10s/);
    h.bus.emit(PROGRESS_CHANNEL, a01Frame([lane("be", "a05-backend", "completed", 36_000), lane("fe", "a06-frontend", "failed", 10_000)], [], 37_000));
    const settled = h.host.lines();
    expect(settled[0]).toMatch(/1 run .*1 done .*1 fail/);
    expect(settled[1]).toContain("✕ FAIL a06-frontend");
    expect(h.host.statuses().at(-1)).toBe("swarm: 1 run, 1 fail");
  });

  test("a specialist named by live.json and by A01's frame is one row, joined by task id", () => {
    const dir = join(tmpDir("swarm-nested-"), ".swarm");
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, "live.json"),
      doc([
        { run_id: "T7f3a-be.a1", task_id: "T7f3a-be", agent: "a05-backend", state: "running", elapsed_s: 20, activity: "store" },
        { run_id: "T7f3a-fe.a1", task_id: "T7f3a-fe", agent: "a05-backend", state: "waiting", elapsed_s: 5 },
      ]),
    );
    const h = harness({ swarmDir: dir });
    h.fire("session_start");
    h.bus.emit(
      PROGRESS_CHANNEL,
      a01Frame([
        lane("be", "a05-backend", "running", 21_000, { assignment: "do T7f3a-be: the module", currentTool: "edit", currentToolArgs: "swarm/handover.py" }),
        lane("fe", "a05-backend", "pending", 0, { assignment: "then T7f3a-fe: its tests", index: 1 }),
      ]),
    );
    const lines = h.host.lines();
    expect(lines[0]).toContain("2 run"); // A01 + the running lane
    expect(lines[0]).toContain("1 wait");
    expect(lines[0]).toContain("0 queued");
    expect(lines[1].match(/a05-backend/g)).toHaveLength(2); // one row each, not four
    expect(lines[1]).toContain("edit swarm/handover.py");
  });

  test("headless -p: one stderr line per nested spawn and per settle", () => {
    const h = harness({ ctx: { hasUI: false, mode: "print", ui: undefined } });
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, a01Frame([lane("be", "a05-backend", "running", 0), lane("fe", "a06-frontend", "running", 0, { index: 1 })]));
    h.clock.mono += 12_000;
    h.bus.emit(PROGRESS_CHANNEL, a01Frame([lane("be", "a05-backend", "completed", 12_000), lane("fe", "a06-frontend", "running", 12_000, { index: 1 })], [], 12_000));
    h.bus.emit(PROGRESS_CHANNEL, a01Frame([lane("be", "a05-backend", "completed", 12_000), lane("fe", "a06-frontend", "running", 12_000, { index: 1 })], [], 12_000));
    expect(h.stderr).toEqual([
      "[swarm] a01-orchestrator running 0s\n",
      "[swarm] a05-backend running 0s\n",
      "[swarm] a06-frontend running 0s\n",
      "[swarm] a05-backend completed 12s\n",
    ]);
    expect(h.host.calls).toEqual([]);
  });

  test("hostile nested payloads on the bus never throw out of the handler", () => {
    const h = harness();
    h.fire("session_start");
    for (const details of ["x", 7, null, [], { progress: "no" }, { progress: [null, 1, {}], results: [{}] }]) {
      expect(() => h.bus.emit(PROGRESS_CHANNEL, frame("Orchestrate", "a01-orchestrator", "running", 1000, { currentTool: "task", inflightTaskDetails: details }))).not.toThrow();
    }
    expect(h.host.lines()[1]).toContain("a01-orchestrator");
  });
});

describe("RV-F late fixes: the derived live.json, background specialists, the evidence timeout", () => {
  const derivedText = (): string => readFileSync(join(REPO_ROOT, "omp", "test", "fixtures", "live_derived_sample.json"), "utf8");
  /** A `.swarm` dir holding `text` as live.json, its mtime `ageMs` in the past. */
  function swarmWith(text: string, ageMs = 0): string {
    const dir = join(tmpDir("swarm-derived-"), ".swarm");
    mkdirSync(dir, { recursive: true });
    const path = join(dir, "live.json");
    writeFileSync(path, text);
    if (ageMs > 0) {
      const old = new Date(Date.now() - ageMs);
      utimesSync(path, old, old);
    }
    return dir;
  }
  const schedulerText = (): string => {
    const doc = JSON.parse(derivedText()) as Record<string, unknown>;
    delete doc.producer;
    return JSON.stringify(doc);
  };
  const entry = (id: string, agent: string, status: string, extra: Record<string, unknown> = {}) => ({
    index: 0,
    id,
    agent,
    agentSource: "project",
    status,
    task: `assignment for ${id}`,
    assignment: `assignment for ${id}`,
    recentTools: [],
    recentOutput: [],
    toolCount: 1,
    requests: 1,
    tokens: 1,
    cost: 0,
    durationMs: 5000,
    ...extra,
  });
  /** omp's `buildAsyncDetails`: results empty, every spawn in progress, `async` on. */
  const asyncDetails = (lanes: unknown[]) => ({ projectAgentsDir: null, results: [], totalDurationMs: 12, progress: lanes, async: { state: "running", jobId: "ApiImpl", type: "task" } });
  const spawnFrame = (lanes: unknown[]) => frame("Orchestrate", "a01-orchestrator", "running", 0, { currentTool: "task", inflightTaskDetails: asyncDetails(lanes) });
  const clearedFrame = () => frame("Orchestrate", "a01-orchestrator", "running", 0, { currentTool: "bash", currentToolArgs: "python3 scripts/orch_status.py" });
  const twoLanes = () => [
    entry("ApiImpl", "a05-backend", "running", { assignment: "implement T38a7-be: the api", currentTool: "edit", currentToolArgs: "swarm/x.py" }),
    entry("UiImpl", "a06-frontend", "running", { index: 1, assignment: "implement T38a7-fe: the ui" }),
  ];

  test("a derived live.json is never stale for want of a transition; the scheduler's document still is (30 s)", () => {
    const derived = harness({ swarmDir: swarmWith(derivedText()) });
    derived.fire("session_start");
    expect(derived.host.lines()[0]).toContain("1 run");
    expect(derived.host.lines()[0]).toContain("10 wait");
    derived.clock.mono += 31_000;
    derived.timers.tickInterval();
    expect(derived.host.lines()[0]).toContain("1 run");
    expect(derived.host.lines().join("\n")).not.toContain("telemetry unavailable");
    derived.clock.mono += 3_600_000;
    derived.timers.tickInterval();
    expect(derived.host.lines()[0]).toContain("10 wait");
    expect(derived.host.lines().join("\n")).not.toContain("telemetry unavailable");

    const scheduler = harness({ swarmDir: swarmWith(schedulerText()) });
    scheduler.fire("session_start");
    expect(scheduler.host.lines()[0]).toContain("1 run");
    scheduler.clock.mono += 31_000;
    scheduler.timers.tickInterval();
    expect(scheduler.host.lines()).toEqual(["swarm: telemetry unavailable"]);
  });

  test("a derived document older than 30 s at first read still supplies live rows; the scheduler's is stale", () => {
    const derived = harness({ swarmDir: swarmWith(derivedText(), 100_000) });
    derived.fire("session_start");
    expect(derived.host.lines()[0]).toContain("1 run");
    expect(derived.host.lines()[0]).toContain("10 wait");
    const scheduler = harness({ swarmDir: swarmWith(schedulerText(), 100_000) });
    scheduler.fire("session_start");
    expect(scheduler.host.lines()).toEqual(["swarm: telemetry unavailable"]);
  });

  test("an old derived live.json joins A01's specialists by task id and protects them after A01's async call returned", () => {
    const h = harness({ swarmDir: swarmWith(derivedText(), 100_000) });
    h.fire("session_start");
    h.bus.emit(
      PROGRESS_CHANNEL,
      spawnFrame([
        entry("Arch", "a03-architect", "running", { assignment: "design it (T38a7-arch)", currentTool: "write", currentToolArgs: "docs/arch.md" }),
        entry("Ux", "a04-ux-designer", "running", { index: 1, assignment: "tokens (T38a7-ux)" }),
      ]),
    );
    const first = h.host.lines(240);
    expect(first[0]).toContain("2 run"); // A01 and the running A03; A04 is queued in the store
    expect(first[0]).toContain("1 queued");
    expect(first[1].match(/a03-architect/g)).toHaveLength(1);
    h.bus.emit(PROGRESS_CHANNEL, clearedFrame());
    h.clock.mono += 31_000;
    h.timers.tickInterval();
    h.bus.emit(PROGRESS_CHANNEL, clearedFrame());
    const later = h.host.lines(240);
    expect(later[0]).toContain("2 run");
    expect(later[0]).not.toContain("cancel");
    expect(later.join("\n")).not.toContain("telemetry unavailable");
    expect(h.host.statuses().at(-1)).toBe("swarm: 2 run, 10 wait, 1 queued");
  });

  test("headless -p: a detached specialist never prints `cancelled` while A01 runs or after it completes", () => {
    const h = harness({ ctx: { hasUI: false, mode: "print", ui: undefined } });
    h.fire("session_start");
    h.bus.emit(PROGRESS_CHANNEL, spawnFrame(twoLanes()));
    h.clock.mono += 1000;
    h.bus.emit(PROGRESS_CHANNEL, clearedFrame()); // the call returned; the specialists run on
    h.clock.mono += 120_000;
    h.bus.emit(PROGRESS_CHANNEL, clearedFrame());
    expect(h.stderr.join("")).not.toContain("cancelled");
    expect(h.stderr).toEqual(["[swarm] a01-orchestrator running 0s\n", "[swarm] a05-backend running 5s\n", "[swarm] a06-frontend running 5s\n"]);
    h.bus.emit(PROGRESS_CHANNEL, frame("Orchestrate", "a01-orchestrator", "completed", 125_000));
    expect(h.stderr.join("")).not.toContain("cancelled");
    expect(h.stderr.at(-1)).toBe("[swarm] a01-orchestrator completed 2m05s\n");

    // A01 failing is an end for its specialists: that, and only that, prints `cancelled`
    const failed = harness({ ctx: { hasUI: false, mode: "print", ui: undefined } });
    failed.fire("session_start");
    failed.bus.emit(PROGRESS_CHANNEL, spawnFrame(twoLanes()));
    failed.bus.emit(PROGRESS_CHANNEL, clearedFrame());
    expect(failed.stderr.join("")).not.toContain("cancelled");
    failed.bus.emit(PROGRESS_CHANNEL, frame("Orchestrate", "a01-orchestrator", "failed", 9000));
    expect(failed.stderr.filter((line) => line.includes("cancelled")).sort()).toEqual(["[swarm] a05-backend cancelled 5s\n", "[swarm] a06-frontend cancelled 5s\n"]);
  });

  test("SWARM_BAR_DETACHED_S: default 900 s, clamped to 60..86400, junk falls back to the default", () => {
    // [env, last tick still showing the row (s after the cleared frame), first tick that has dropped it]; the last evidence is 1 s older
    const cases: Array<[string | undefined, number, number]> = [
      [undefined, 898, 900],
      ["abc", 898, 900],
      ["0", 898, 900],
      ["5", 58, 60],
      ["120", 118, 120],
      ["999999", 86_398, 86_400],
    ];
    for (const [value, keep, drop] of cases) {
      const h = harness({ env: value === undefined ? {} : { SWARM_BAR_DETACHED_S: value } });
      h.fire("session_start");
      h.bus.emit(PROGRESS_CHANNEL, spawnFrame(twoLanes()));
      h.clock.mono += 1000;
      h.bus.emit(PROGRESS_CHANNEL, clearedFrame());
      expect(h.host.lines(240)[1]).toMatch(/a05-backend\s+\S+~/);
      h.clock.mono += keep * 1000;
      h.timers.tickInterval();
      expect({ value, shown: h.host.lines(240)[1].includes("a05-backend") }).toEqual({ value, shown: true });
      h.clock.mono += (drop - keep) * 1000;
      h.timers.tickInterval();
      expect({ value, shown: h.host.lines(240)[1].includes("a05-backend") }).toEqual({ value, shown: false });
      expect(h.host.lines(240)[1]).toContain("a01-orchestrator");
    }
  });
});
