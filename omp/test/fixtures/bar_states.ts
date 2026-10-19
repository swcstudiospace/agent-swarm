/**
 * Every state of the Agent Swarm bar, rendered by the REAL renderer (`renderSwarmBar`) with the REAL omp theme
 * (`dark-lavender`, truecolor, nerd symbols: the user's configured theme). Every row is exactly as wide as its capture says:
 * the seven states at widths 40, 80 and 120, and the token-savings captures (`TOKEN_SCENARIOS`) at the widths they name.
 *
 * Used by `omp/test/render_states.test.ts` (docs/swarm-bar.md and `bar_states.ansi` must equal what the renderer produces
 * today). To refresh both after a renderer change, from the repo root:
 *
 *     bun omp/test/fixtures/bar_states.ts --write
 *
 * Without `--write` the script prints the plain captures. No module-scope state; the only I/O is the theme import and, with
 * `--write`, the two files below.
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { EventBus, ExtensionContext, Theme } from "../../src/omp-api.ts";
import {
  createModel,
  createSwarmBar,
  ingestLive,
  ingestProgress,
  ingestTaskSpawn,
  renderSwarmBar,
  type SwarmBarModel,
  statusText,
} from "../../src/swarm_bar.ts";
import { stripAnsi } from "../../src/swarm_dock.ts";
import type { TokenSavingsSnapshot } from "../../src/token_savings.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
export const ANSI_FIXTURE = join(HERE, "bar_states.ansi");
export const DOC_PATH = join(HERE, "..", "..", "..", "docs", "swarm-bar.md");
export const THEME_NAME = "dark-lavender";
export const WIDTHS = [40, 80, 120] as const;
const THEME_SUBDIR = join("node_modules", "@oh-my-pi", "pi-tui", "src", "theme");
/** Where omp's own pi-tui theme sources usually live: a reflex-managed bun install or a plain global bun install. */
function themeDirCandidates(): string[] {
  const home = homedir();
  return [
    join(home, ".local", "share", "reflex", "bun", "install", "global", THEME_SUBDIR),
    join(home, ".bun", "install", "global", THEME_SUBDIR),
  ];
}

/** Captures: key -> the rendered rows (ANSI included). A key is `scenario@width` or a bare name for width-less captures. */
export type Captures = Record<string, string[]>;

const T0 = 1_000_000;
const SEC = 1000;

// ---------------------------------------------------------------------------------------------------------------------
// Real theme
// ---------------------------------------------------------------------------------------------------------------------

/** pi-tui's `Theme` for `dark-lavender` (truecolor, nerd symbols), or undefined when omp's package is not installed here. */
export async function loadRealTheme(): Promise<Theme | undefined> {
  const dirs = process.env.OMP_PI_TUI_THEME_DIR ? [process.env.OMP_PI_TUI_THEME_DIR] : themeDirCandidates();
  const dir = dirs.find((d) => existsSync(join(d, "loader.ts")));
  if (!dir) return undefined;
  const loader = (await import(join(dir, "loader.ts"))) as {
    createTheme(json: unknown, options: { mode: string; symbolPresetOverride: string }): Theme;
    loadThemeJsonSync(name: string): unknown;
  };
  return loader.createTheme(loader.loadThemeJsonSync(THEME_NAME), { mode: "truecolor", symbolPresetOverride: "nerd" });
}

// ---------------------------------------------------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------------------------------------------------

interface LiveRun {
  run_id: string;
  task_id: string;
  agent: string;
  state: string;
  elapsed_s: number;
  stalled: boolean;
  activity: string;
  error?: string;
}

function run(agent: string, state: string, elapsed: number, extra: Partial<LiveRun> = {}): LiveRun {
  return { run_id: `${agent}.1`, task_id: agent, agent, state, elapsed_s: elapsed, stalled: false, activity: "", ...extra };
}

function liveDoc(runs: LiveRun[], swarm: Record<string, unknown> = {}) {
  return {
    schema: "swarm.live.v1",
    correlation_id: "demo",
    seq: 1,
    updated_wall: 0,
    swarm: { state: "running", elapsed_s: 300, max_parallel: 4, effective_parallel: 4, ...swarm },
    runs,
  };
}

interface Scenario {
  name: string;
  title: string;
  /** Consumer-monotonic moment the bar is drawn at. */
  at: number;
  widths: readonly number[];
  reducedMotion?: boolean;
  build(model: SwarmBarModel): void;
}

function fromLive(runs: LiveRun[], swarm?: Record<string, unknown>): (model: SwarmBarModel) => void {
  return (model) => void ingestLive(model, liveDoc(runs, swarm), T0);
}

const ACTIVE = [
  run("a05-backend", "running", 252, { activity: "edit swarm/handover.py" }),
  run("a06-frontend", "running", 95, { activity: "read omp/src/swarm_bar.ts" }),
  run("a07-data", "running", 14, { activity: "bash pytest tests/test_handover.py" }),
  run("a08-qa", "queued", 0),
];
const EVERY_STATE = [
  run("a05-backend", "failed", 71, { error: "tests failed: 3" }),
  run("a06-frontend", "timed_out", 600, { activity: "timeout after 600s" }),
  run("a07-data", "running", 190, { stalled: true, activity: "no output for 3m10s" }),
  run("a08-qa", "running", 66, { activity: "bash pytest -q" }),
  run("a09-reviewer", "waiting", 12, { activity: "waiting for a08-qa" }),
  run("a10-security", "queued", 0),
  run("a11-devops", "handing_off", 41, { activity: "handover to a12-release" }),
  run("a12-release", "cancelled", 30, { activity: "cancelled by operator" }),
  run("a13-observability", "completed", 128),
];
const OVERFLOW = [
  run("a05-backend", "failed", 71, { error: "tests failed: 3" }),
  ...["a06-frontend", "a07-data", "a08-qa", "a09-reviewer", "a10-security", "a11-devops"].map((agent, i) =>
    run(agent, "running", 300 - i * 40, { activity: "edit docs/runbook.md" }),
  ),
  run("a12-release", "queued", 0),
  run("a13-observability", "queued", 0),
];
const COMPLETED = [run("a05-backend", "completed", 61), run("a06-frontend", "completed", 142), run("a07-data", "completed", 203)];
const FAILED = [
  run("a05-backend", "failed", 71, { error: "tests failed: 3" }),
  run("a06-frontend", "timed_out", 600, { activity: "timeout after 600s" }),
  run("a07-data", "completed", 203),
  run("a08-qa", "completed", 88),
];

/** A nested `AgentProgress` as omp's executor builds it for `TaskToolDetails.progress`. */
function nestedProgress(id: string, index: number, agent: string, status: string, durationMs: number, extra: Record<string, unknown>): Record<string, unknown> {
  return {
    index,
    id,
    agent,
    agentSource: "project",
    status,
    task: `assignment ${id}`,
    assignment: `assignment ${id}`,
    recentTools: [],
    recentOutput: [],
    toolCount: 4,
    requests: 2,
    tokens: 1200,
    cost: 0,
    durationMs,
    ...extra,
  };
}

/**
 * The main flow: the main session spawns A01 (a `name`-keyed `task` item); A01 spawns three specialists in its own session and one
 * of them already returned. Only A01's frame reaches the bar (the specialists' own frames never leave A01's session); the scheduler's
 * live.json knows two of them by task id, which their assignment text names.
 */
function inSessionFanOut(model: SwarmBarModel): void {
  ingestTaskSpawn(model, { tasks: [{ name: "Orchestrate", agent: "a01-orchestrator", assignment: "run the plan" }] }, "call-1", T0);
  ingestLive(
    model,
    liveDoc([
      run("a05-backend", "running", 62, { task_id: "T7f3a-be", run_id: "T7f3a-be.a1", activity: "store: edit" }),
      run("a08-qa", "waiting", 5, { task_id: "T7f3a-qa", run_id: "T7f3a-qa.a1", activity: "waiting for a06-frontend" }),
    ]),
    T0,
  );
  ingestProgress(
    model,
    {
      index: 0,
      agent: "a01-orchestrator",
      task: "run the plan",
      parentToolCallId: "call-1",
      progress: {
        ...nestedProgress("Orchestrate", 0, "a01-orchestrator", "running", 150_000, { currentTool: "task", toolCount: 3 }),
        inflightTaskDetails: {
          projectAgentsDir: null,
          totalDurationMs: 62_000,
          results: [],
          progress: [
            nestedProgress("be", 0, "a05-backend", "running", 62_000, { assignment: "Backend lane (T7f3a-be): handover module", currentTool: "edit", currentToolArgs: "swarm/handover.py" }),
            nestedProgress("fe", 1, "a06-frontend", "running", 48_000, { currentToolIntent: "Reading the design tokens" }),
            nestedProgress("qa", 2, "a08-qa", "pending", 0, { assignment: "QA lane (T7f3a-qa) after the frontend" }),
            nestedProgress("docs", 3, "a15-docs", "completed", 33_000, {}),
          ],
        },
      },
    },
    T0,
  );
}

/**
 * The async flow: A01's `task` call starts two background jobs (omp's `buildAsyncDetails`: `async.state: "running"`, `results: []`)
 * and returns; A01 goes on with its own tools, so the next frame carries no in-flight details. The specialists keep running, shown
 * with `~` after their elapsed (detached: no frame and no live.json row vouches for them any more).
 */
function backgroundSpecialists(model: SwarmBarModel): void {
  ingestTaskSpawn(model, { tasks: [{ name: "Orchestrate", agent: "a01-orchestrator", assignment: "run the plan" }] }, "call-1", T0);
  const a01 = (at: number, extra: Record<string, unknown>) =>
    ingestProgress(
      model,
      {
        index: 0,
        agent: "a01-orchestrator",
        task: "run the plan",
        parentToolCallId: "call-1",
        progress: nestedProgress("Orchestrate", 0, "a01-orchestrator", "running", 150_000 + (at - T0), extra),
      },
      at,
    );
  a01(T0, {
    currentTool: "task",
    inflightTaskDetails: {
      projectAgentsDir: null,
      results: [],
      totalDurationMs: 40,
      progress: [
        nestedProgress("be", 0, "a05-backend", "running", 62_000, { currentTool: "edit", currentToolArgs: "swarm/handover.py" }),
        nestedProgress("fe", 1, "a06-frontend", "running", 48_000, { currentToolIntent: "Reading the design tokens" }),
      ],
      async: { state: "running", jobId: "be", type: "task" },
    },
  });
  a01(T0 + SEC, { currentTool: "bash", currentToolArgs: "python3 scripts/orch_status.py" });
}

export const SCENARIOS: readonly Scenario[] = [
  { name: "idle", title: "idle: two quiet rows, nothing live", at: T0, widths: WIDTHS, build: () => {} },
  {
    name: "loading",
    title: "loading: spawned, nothing reported yet",
    at: T0 + 2 * SEC,
    widths: WIDTHS,
    reducedMotion: true,
    build: (model) => void ingestTaskSpawn(model, { tasks: [{ agent: "a05-backend" }, { agent: "a06-frontend" }, { agent: "a07-data" }] }, "call-1", T0),
  },
  { name: "active", title: "active: three running, one queued", at: T0, widths: WIDTHS, reducedMotion: true, build: fromLive(ACTIVE) },
  {
    name: "every-state",
    title: "active: one row per state (failed, timeout, stall, running, wait, queued, handoff, cancelled, done)",
    at: T0,
    widths: WIDTHS,
    reducedMotion: true,
    build: fromLive(EVERY_STATE),
  },
  {
    name: "overflow",
    title: "overflow: more rows than fit",
    at: T0,
    widths: WIDTHS,
    reducedMotion: true,
    build: fromLive(OVERFLOW, { max_parallel: 8, effective_parallel: 8 }),
  },
  { name: "completed", title: "completed: everything settled well", at: T0, widths: WIDTHS, reducedMotion: true, build: fromLive(COMPLETED, { state: "completed" }) },
  { name: "failed", title: "failed: settled with a failure and a timeout", at: T0, widths: WIDTHS, reducedMotion: true, build: fromLive(FAILED, { state: "failed" }) },
  {
    name: "error",
    title: "error: telemetry unavailable, nothing else to show",
    at: T0,
    widths: WIDTHS,
    build: (model) => void (model.error = "live.json stale"),
  },
  {
    name: "error-rows",
    title: "error with rows: the summary carries the marker",
    at: T0 + 5 * SEC,
    widths: WIDTHS,
    reducedMotion: true,
    build: (model) => {
      ingestTaskSpawn(model, { tasks: [{ agent: "a05-backend" }] }, "call-1", T0);
      model.error = "live.json malformed";
    },
  },
  {
    name: "in-session",
    title: "in-session fan-out: A01 spawned three specialists and one already settled",
    at: T0 + 5 * SEC,
    widths: WIDTHS,
    reducedMotion: true,
    build: inSessionFanOut,
  },
  {
    name: "detached",
    title: "background specialists: A01's task call returned, the specialists run on (`~`)",
    at: T0 + 5 * SEC,
    widths: WIDTHS,
    reducedMotion: true,
    build: backgroundSpecialists,
  },
];

const SESSION = "demo-session";

/** A valid `token-opt:savings` snapshot: the required counts (61.4k before, 13.2k now, 48.2k saved) plus whatever `extra` measures. */
function snapshot(extra: Partial<TokenSavingsSnapshot> = {}): TokenSavingsSnapshot {
  return { v: 1, sessionId: SESSION, updatedAt: 1_700_000_000_000, baselineTokens: 61_400, currentTokens: 13_200, savedTokens: 48_200, points: [], ...extra };
}

/** Every optional measurement the producer can add, so every chip of the doc's table appears when the row is wide enough. */
const FULL_TOKENS = snapshot({
  lastTurnMs: 3200,
  cacheHitPct: 87,
  lastQueryMs: 85,
  rtkSavedTokens: 4100,
  lastPayloadChars: 156_523,
  ttftMs: 640,
  lastIdleMs: 1200,
  costCents: 12,
  providerInput: 9400,
  providerOutput: 2100,
  graphReady: 1,
});

function withTokens(build: (model: SwarmBarModel) => void, tokens: TokenSavingsSnapshot): (model: SwarmBarModel) => void {
  return (model) => {
    build(model);
    model.sessionId = SESSION;
    model.tokens = tokens;
  };
}

/** Row 1's right-hand token chips. Captured by name and width like the states; kept apart so `SCENARIOS` stays the seven states. */
export const TOKEN_SCENARIOS: readonly Scenario[] = [
  {
    name: "tokens-active",
    title: "active with a full snapshot: the chips that fit, least important dropped first",
    at: T0,
    widths: [80, 120],
    reducedMotion: true,
    build: withTokens(fromLive(ACTIVE), FULL_TOKENS),
  },
  { name: "tokens-idle", title: "idle with a full snapshot: 200 columns hold the whole chip list", at: T0, widths: [120, 200], build: withTokens(() => {}, FULL_TOKENS) },
  { name: "tokens-minimal", title: "idle with a snapshot that carries only the required counts", at: T0, widths: [80], build: withTokens(() => {}, snapshot()) },
  {
    name: "tokens-foreign",
    title: "a snapshot of another session is not shown once this session's id is known",
    at: T0,
    widths: [80],
    reducedMotion: true,
    build: withTokens(fromLive(ACTIVE), snapshot({ sessionId: "another-session", cacheHitPct: 99 })),
  },
];

/** Settled-row linger: the same snapshot drawn at the edges of the 20 s (done) and 600 s (failed) windows. */
const LINGER: ReadonlyArray<{ key: string; build: (model: SwarmBarModel) => void; after: number }> = [
  { key: "linger:done+0s", build: fromLive(COMPLETED, { state: "completed" }), after: 0 },
  { key: "linger:done+19s", build: fromLive(COMPLETED, { state: "completed" }), after: 19 },
  { key: "linger:done+20s", build: fromLive(COMPLETED, { state: "completed" }), after: 20 },
  { key: "linger:failed+599s", build: fromLive(FAILED, { state: "failed" }), after: 599 },
  { key: "linger:failed+600s", build: fromLive(FAILED, { state: "failed" }), after: 600 },
];

function render(theme: Theme, model: SwarmBarModel, width: number, at: number): string[] {
  return renderSwarmBar(model, width, theme, at);
}

// ---------------------------------------------------------------------------------------------------------------------
// Headless stderr lines (the real controller, `mode: "print"`)
// ---------------------------------------------------------------------------------------------------------------------

/** What `omp -p` prints to stderr for one scripted swarm: a spawn, a failure and a completion. */
export function headlessLines(): string[] {
  const listeners = new Map<string, Array<(data: unknown) => void>>();
  const bus: EventBus = {
    emit: (channel, data) => void (listeners.get(channel) ?? []).forEach((handler) => handler(data)),
    on: (channel, handler) => {
      listeners.set(channel, [...(listeners.get(channel) ?? []), handler]);
      return () => {};
    },
  };
  const out: string[] = [];
  let mono = T0;
  const bar = createSwarmBar({ events: bus }, { env: {}, now: () => mono, stderr: (text) => void out.push(text) });
  const ctx = {
    cwd: "/tmp/headless-demo",
    sessionManager: { getEntries: () => [], getBranch: () => [] },
    hasUI: false,
    mode: "print",
    agent: { kind: "main", id: "Main", name: "main", depth: 0 },
  } as unknown as ExtensionContext;
  bar.event(ctx, "session_start");
  const frame = (id: string, agent: string, status: string, durationMs: number) => ({
    index: 0,
    agent,
    task: "demo",
    parentToolCallId: "call-1",
    progress: { index: 0, id, agent, status, task: "demo", toolCount: 1, requests: 1, tokens: 1, cost: 0, durationMs },
  });
  bus.emit("task:subagent:lifecycle", { id: "a", agent: "a05-backend", status: "started", index: 0, parentToolCallId: "call-1" });
  bus.emit("task:subagent:lifecycle", { id: "b", agent: "a06-frontend", status: "started", index: 1, parentToolCallId: "call-1" });
  mono += 41 * SEC;
  bus.emit("task:subagent:progress", frame("b", "a06-frontend", "failed", 41_000));
  mono += 24 * SEC;
  bus.emit("task:subagent:progress", frame("a", "a05-backend", "completed", 65_000));
  bar.stop();
  return out.map((line) => line.replace(/\n$/, ""));
}

// ---------------------------------------------------------------------------------------------------------------------
// Capture set
// ---------------------------------------------------------------------------------------------------------------------

/** Every capture the doc shows, rendered now. */
export function renderCaptures(theme: Theme): Captures {
  const captures: Captures = {};
  const statusRows: string[] = [];
  for (const scenario of [...SCENARIOS, ...TOKEN_SCENARIOS]) {
    const model = createModel({ reducedMotion: scenario.reducedMotion ?? false });
    scenario.build(model);
    for (const width of scenario.widths) captures[`${scenario.name}@${width}`] = render(theme, model, width, scenario.at);
    if (SCENARIOS.includes(scenario)) statusRows.push(`${scenario.name.padEnd(12)} ${statusText(model, scenario.at) ?? "(status cleared)"}`);
  }
  captures.status = statusRows;
  for (const entry of LINGER) {
    const model = createModel({ reducedMotion: true });
    entry.build(model);
    captures[`${entry.key}@80`] = render(theme, model, 80, T0 + entry.after * SEC);
  }
  // animation: the activity spinner advances once a second; SWARM_BAR_REDUCED_MOTION=1 pins it to the plain mark
  for (const [label, reduced] of [["animated", false], ["reduced", true]] as const) {
    const model = createModel({ reducedMotion: reduced });
    fromLive(ACTIVE.slice(0, 1))(model);
    for (const second of [0, 1, 2, 3]) captures[`spinner:${label}+${second}s@80`] = render(theme, model, 80, T0 + second * SEC).slice(1);
  }
  captures.headless = headlessLines();
  return captures;
}

// ---------------------------------------------------------------------------------------------------------------------
// Fixture and doc formats
// ---------------------------------------------------------------------------------------------------------------------

/** The doc shows the Nerd Font powerline glyphs as plain Unicode of the same width: cap left, cap right, thin separator. */
const DOC_GLYPHS: ReadonlyArray<[string, string]> = [["\ue0b6", "◖"], ["\ue0b0", "◗"], ["\ue0b1", "│"]];

export function plainText(line: string): string {
  let text = stripAnsi(line);
  for (const [from, to] of DOC_GLYPHS) text = text.split(from).join(to);
  return text;
}

export function serializeAnsi(captures: Captures): string {
  return Object.entries(captures)
    .map(([key, lines]) => `#### ${key} (${lines.length})\n${lines.map((line) => `${line}\n`).join("")}`)
    .join("\n");
}

export function parseAnsi(text: string): Captures {
  const captures: Captures = {};
  const rows = text.split("\n");
  let i = 0;
  while (i < rows.length) {
    const head = /^#### (\S+) \((\d+)\)$/.exec(rows[i]);
    if (!head) {
      i++;
      continue;
    }
    const count = Number(head[2]);
    captures[head[1]] = rows.slice(i + 1, i + 1 + count);
    i += 1 + count;
  }
  return captures;
}

const BLOCK = /<!-- capture:(\S+) -->\n```text\n([\s\S]*?)\n```\n<!-- \/capture -->/g;

/** The captures a doc carries, as plain text rows. */
export function parseDoc(doc: string): Record<string, string[]> {
  const found: Record<string, string[]> = {};
  for (const match of doc.matchAll(BLOCK)) found[match[1]] = match[2].split("\n");
  return found;
}

function block(key: string, lines: string[]): string {
  const body = lines.map(plainText).join("\n");
  return `<!-- capture:${key} -->\n\`\`\`text\n${body}\n\`\`\`\n<!-- /capture -->`;
}

/** `doc` with every capture block replaced by the freshly rendered one. */
export function rewriteDoc(doc: string, captures: Captures): string {
  return doc.replace(BLOCK, (_all, key: string) => {
    const lines = captures[key];
    if (lines === undefined) throw new Error(`docs/swarm-bar.md names a capture that does not exist: ${key}`);
    return block(key, lines);
  });
}

if (import.meta.main) {
  const theme = await loadRealTheme();
  if (!theme) throw new Error("omp's pi-tui theme package is not installed (set OMP_PI_TUI_THEME_DIR)");
  const captures = renderCaptures(theme);
  if (process.argv.includes("--write")) {
    writeFileSync(ANSI_FIXTURE, serializeAnsi(captures));
    writeFileSync(DOC_PATH, rewriteDoc(readFileSync(DOC_PATH, "utf8"), captures));
    console.log(`wrote ${Object.keys(captures).length} captures to ${ANSI_FIXTURE} and ${DOC_PATH}`);
  } else {
    for (const [key, lines] of Object.entries(captures)) console.log(`--- ${key}\n${lines.map(plainText).join("\n")}`);
  }
}
