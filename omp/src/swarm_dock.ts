/**
 * Agent Swarm status bar: one line above the editor, styled like the Ultrathink bar.
 * Host contract: `ui.setWidget(key, factory, { placement: "aboveEditor" })`.
 * The component reads live state on every render and asks the host to repaint while work is in flight,
 * so the bar does not sit on the last settled "completed" until the next spawn.
 */

export const SWARM_DOCK_KEY = "agent-swarm";

export interface SwarmDockRow {
  id: string;
  agent: string;
  status: string;
  detail?: string;
}

export interface SwarmDock {
  title: string;
  rows: SwarmDockRow[];
  footer?: string;
  /** Tool the session is running right now. Cleared when that call returns. */
  activity?: string;
  /** When the current stretch of work started. The bar paints elapsed from this. */
  startedAt?: number;
}

export interface SwarmDockUi {
  setWidget?(
    key: string,
    content: ((tui: unknown, theme?: SwarmDockTheme) => SwarmDockComponent) | undefined,
    options?: { placement?: "aboveEditor" | "belowEditor" },
  ): void;
}

export interface SwarmDockTheme {
  fg?: (color: string, text: string) => string;
  bold?: (text: string) => string;
  status?: Record<string, string>;
  sep?: Record<string, string>;
  getFgAnsi?: (color: string) => string;
  getBgAnsi?: (color: string) => string;
  getSpinnerFrames?: (kind: string) => string[];
}

export interface SwarmDockComponent {
  render(width: number): readonly string[];
  dispose?(): void;
}

/** Interval owned by one session while work is live. `stop` is safe to call twice. */
export interface DockTicker {
  stop(): void;
}

/** Host surface that can repaint the bar between tool events. */
export interface DockHost {
  requestRender(): void;
}

/** Dock state owned by one session. omp builds the extension once per session, so each owns one. */
export interface DockState {
  live: SwarmDock;
  /** toolCallId → row ids painted for that `task` call. Settled by `tool_result`. */
  pendingSpawns: Map<string, string[]>;
  /** Numbers `task` calls that arrive without a toolCallId, so their rows still get distinct ids. */
  anonymousCalls: number;
  /** Host repaint hooks registered by mounted bar components. */
  notify: Set<() => void>;
  /** Spinner tick. Set only while work is live and a host can repaint. */
  ticker?: DockTicker;
  /** When the current stretch of work started, for the elapsed chip. */
  startedAt?: number;
}

export function createDockState(): DockState {
  return { live: { title: "Agent Swarm", rows: [] }, pendingSpawns: new Map(), anonymousCalls: 0, notify: new Set() };
}

const PLAIN_GLYPHS = { success: "✓", error: "✕", warning: "!", pending: "○", running: "●" };

function isDockHost(value: unknown): value is DockHost {
  return Boolean(value && typeof value === "object" && "requestRender" in value && typeof value.requestRender === "function");
}

function themeOf(theme: SwarmDockTheme | undefined): SwarmDockTheme {
  return theme ?? {};
}

function paintFg(theme: SwarmDockTheme | undefined, color: string, text: string): string {
  try {
    return theme?.fg?.(color, text) ?? text;
  } catch {
    return text;
  }
}

function glyphOf(theme: SwarmDockTheme | undefined, name: keyof typeof PLAIN_GLYPHS): string {
  try {
    const value = theme?.status?.[name];
    return typeof value === "string" && value.length > 0 ? value : PLAIN_GLYPHS[name];
  } catch {
    return PLAIN_GLYPHS[name];
  }
}

function spinnerOf(theme: SwarmDockTheme | undefined, now: number): string {
  try {
    const frames = theme?.getSpinnerFrames?.("activity");
    if (Array.isArray(frames) && frames.length > 0) {
      return frames[Math.floor(now / 100) % frames.length] ?? PLAIN_GLYPHS.running;
    }
  } catch {
    // plain mark
  }
  return PLAIN_GLYPHS.running;
}

function clip(text: string, width: number): string {
  if (width <= 0) return "";
  if (text.length <= width) return text;
  if (width === 1) return "…";
  return `${text.slice(0, width - 1)}…`;
}

function visibleWidth(text: string): number {
  const stripped = text.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, "");
  return stripped.length;
}

function isLiveStatus(status: string): boolean {
  const s = status.toLowerCase();
  return s === "running" || s === "launching" || s === "in_progress" || s === "loading" || s === "retry";
}

export function dockIsLive(state: DockState): boolean {
  if (state.live.activity) return true;
  return state.live.rows.some((row) => isLiveStatus(row.status));
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Status-line band. Uses the host theme's powerline colors when they are all present; otherwise ` │ `. */
function band(theme: SwarmDockTheme | undefined, parts: string[]): string {
  const t = themeOf(theme);
  try {
    const bg = t.getBgAnsi?.("statusLineBg");
    const fg = t.getFgAnsi?.("text");
    const thin = t.sep?.powerlineThinLeft;
    const sepColor = t.getFgAnsi?.("statusLineSep");
    if (typeof bg === "string" && typeof fg === "string" && typeof thin === "string" && typeof sepColor === "string") {
      const body = `${bg}${fg} ${parts.map((part) => `${part}${bg}${fg}`).join(` ${sepColor}${thin}${fg} `)} \x1b[0m`;
      if (bg === "\x1b[49m") return body;
      const capColor = bg.replace("\x1b[48;", "\x1b[38;");
      const open = t.sep?.powerlineCapLeft ?? "";
      const close = t.sep?.powerlineLeft ?? "";
      return `${capColor}${open}\x1b[0m${body}${capColor}${close}\x1b[0m`;
    }
  } catch {
    // plain join
  }
  return parts.join(" │ ");
}

function rowSegment(row: SwarmDockRow): string {
  const detail = row.detail ? ` ${row.detail}` : "";
  return `${row.agent} ${row.status}${detail}`;
}

/**
 * One status line. Live agents and the in-flight tool lead; settled rows trail and drop first when narrow.
 * Status words stay in the text so a host that ignores color still shows the work, not only "completed".
 */
export function renderSwarmDock(dock: SwarmDock, width: number, theme?: SwarmDockTheme, now = Date.now()): string[] {
  if (width <= 0) return [""];
  const live = dock.rows.filter((row) => isLiveStatus(row.status));
  const settled = dock.rows.filter((row) => !isLiveStatus(row.status));
  const working = live.length > 0 || Boolean(dock.activity);
  const failed = dock.rows.some((row) => {
    const s = row.status.toLowerCase();
    return s === "failed" || s === "error";
  });
  const mark = working
    ? paintFg(theme, "accent", spinnerOf(theme, now))
    : failed
      ? paintFg(theme, "error", glyphOf(theme, "error"))
      : dock.rows.length > 0
        ? paintFg(theme, "success", glyphOf(theme, "success"))
        : paintFg(theme, "dim", glyphOf(theme, "pending"));
  const plainName = "agent swarm";
  let name = plainName;
  try {
    name = theme?.bold?.(plainName) ?? plainName;
  } catch {
    name = plainName;
  }
  const elapsed = working && dock.startedAt !== undefined ? ` ${formatElapsed(now - dock.startedAt)}` : "";
  const plainMark = working
    ? PLAIN_GLYPHS.running
    : failed
      ? PLAIN_GLYPHS.error
      : dock.rows.length > 0
        ? PLAIN_GLYPHS.success
        : PLAIN_GLYPHS.pending;
  const plainChip = `${plainMark} ${plainName}${elapsed}`;
  const nameChip = `${mark} ${name}${elapsed}`;
  const segments = [nameChip];
  if (dock.activity) segments.push(paintFg(theme, "accent", dock.activity));
  for (const row of live.slice(-4)) segments.push(paintFg(theme, "accent", rowSegment(row)));
  if (dock.footer) segments.push(paintFg(theme, "dim", clip(dock.footer, 48)));
  for (const row of settled.slice(-4)) {
    const s = row.status.toLowerCase();
    const color = s === "failed" || s === "error" ? "error" : "success";
    segments.push(paintFg(theme, color, rowSegment(row)));
  }
  // Drop trailing segments until the line fits. The name and the current work stay.
  const minimum = dock.activity || live.length > 0 ? 2 : 1;
  let shown = segments;
  while (shown.length > minimum && visibleWidth(band(theme, shown)) > width) shown = shown.slice(0, -1);
  const line = band(theme, shown);
  if (visibleWidth(line) <= width) return [line];
  // Color is applied only to text that already fits. A narrow pane gets plain text, never a cut escape.
  return [clip(plainChip, width)];
}

function touch(state: DockState): void {
  for (const notify of state.notify) {
    try {
      notify();
    } catch {
      // a dead host must not stick the bar
    }
  }
}

function armTicker(state: DockState, request: () => void): void {
  if (state.ticker || !dockIsLive(state)) return;
  const handle = setInterval(() => {
    if (!dockIsLive(state)) {
      clearInterval(handle);
      state.ticker = undefined;
      return;
    }
    request();
  }, 80);
  state.ticker = {
    stop() {
      clearInterval(handle);
    },
  };
}

export function swarmDockWidget(state: DockState): (tui: unknown, theme?: SwarmDockTheme) => SwarmDockComponent {
  // Render from the session's live state, not a snapshot taken at setWidget time.
  return (tui, theme) => {
    const request = (): void => {
      if (!isDockHost(tui)) return;
      try {
        tui.requestRender();
      } catch {
        // display only
      }
    };
    if (isDockHost(tui)) state.notify.add(request);
    return {
      render(width: number) {
        if (isDockHost(tui)) armTicker(state, request);
        const { live, startedAt } = state;
        return renderSwarmDock(
          {
            title: live.title,
            footer: live.footer,
            activity: live.activity,
            startedAt,
            rows: live.rows.map((row) => ({ ...row })),
          },
          width,
          theme,
        );
      },
      dispose() {
        state.notify.delete(request);
      },
    };
  };
}

function shown(state: DockState): boolean {
  return state.live.rows.length > 0 || Boolean(state.live.footer) || Boolean(state.live.activity);
}

export function publishSwarmDock(ui: SwarmDockUi | undefined, state: DockState): void {
  touch(state);
  if (!ui?.setWidget) return;
  if (!shown(state)) {
    ui.setWidget(SWARM_DOCK_KEY, undefined, { placement: "aboveEditor" });
    return;
  }
  ui.setWidget(SWARM_DOCK_KEY, swarmDockWidget(state), { placement: "aboveEditor" });
}

export function upsertDockRow(state: DockState, row: SwarmDockRow): void {
  const rows = state.live.rows;
  const i = rows.findIndex((existing) => existing.id === row.id);
  if (i >= 0) rows[i] = { ...rows[i], ...row };
  else rows.push(row);
  if (rows.length > 8) rows.splice(0, rows.length - 8);
  if (!state.startedAt && isLiveStatus(row.status)) state.startedAt = Date.now();
}

function liveFooter(state: DockState): string {
  const rows = state.live.rows;
  const running = rows.filter((row) => isLiveStatus(row.status)).length;
  const failed = rows.filter((row) => row.status === "failed" || row.status === "error").length;
  if (running > 0) return `${running} running`;
  if (failed > 0) return `${failed} failed`;
  return `${rows.length} done`;
}

/** The `ui` a tool event may carry. Absent when the host has no card surface. */
export function dockUi(ctx: unknown): SwarmDockUi | undefined {
  if (!ctx || typeof ctx !== "object" || !("ui" in ctx)) return undefined;
  const ui = ctx.ui;
  if (!ui || typeof ui !== "object" || !("setWidget" in ui) || typeof ui.setWidget !== "function") return undefined;
  return { setWidget: ui.setWidget.bind(ui) };
}

export function setDockFooter(state: DockState, footer: string): void {
  state.live.footer = footer;
}

export function flushDock(state: DockState, ui: SwarmDockUi | undefined): void {
  publishSwarmDock(ui, state);
}

export function clearDock(state: DockState, ui: SwarmDockUi | undefined): void {
  state.live.rows.length = 0;
  state.live.footer = undefined;
  state.live.activity = undefined;
  state.startedAt = undefined;
  state.pendingSpawns.clear();
  publishSwarmDock(ui, state);
}

/** Stop the spinner tick. Session shutdown calls this so a dead session does not keep repainting. */
export function stopDock(state: DockState): void {
  state.ticker?.stop();
  state.ticker = undefined;
  state.notify.clear();
}

/** Paint the tool the session is running now. `task` is a row, not an activity chip. */
export function noteToolActivity(state: DockState, ui: SwarmDockUi | undefined, toolName: string | undefined): void {
  if (!toolName || toolName === "task") return;
  state.live.activity = toolName;
  if (!state.startedAt) state.startedAt = Date.now();
  flushDock(state, ui);
}

/** Drop the in-flight tool chip when that call returns. Rows settle on their own path. */
export function clearToolActivity(state: DockState, ui: SwarmDockUi | undefined): void {
  const hadActivity = state.live.activity !== undefined;
  state.live.activity = undefined;
  const clock = state.startedAt;
  if (!dockIsLive(state)) state.startedAt = undefined;
  if (!hadActivity && state.startedAt === clock) return;
  flushDock(state, ui);
}

/** Rows from a `task` tool call. Empty when the call is not a subagent spawn. */
export function rowsFromTaskInput(input: unknown, callKey: string): SwarmDockRow[] {
  if (!input || typeof input !== "object" || !("tasks" in input)) return [];
  const tasks: unknown = input.tasks;
  if (!Array.isArray(tasks)) return [];
  const rows: SwarmDockRow[] = [];
  for (let i = 0; i < tasks.length; i++) {
    const item: unknown = tasks[i];
    if (!item || typeof item !== "object") continue;
    const agent = "agent" in item && typeof item.agent === "string" && item.agent.trim() ? item.agent.trim() : "task";
    // The call and the item index keep ids distinct, even when two calls reuse a task name.
    rows.push({ id: `${callKey}#${i}`, agent, status: "running" });
  }
  return rows;
}

export interface TaskResultEvent {
  toolName?: string;
  toolCallId?: string;
  isError?: boolean;
}

export function publishTaskSpawn(state: DockState, ui: SwarmDockUi | undefined, input: unknown, toolCallId?: string): void {
  // Without a toolCallId the call is never settled, but its rows still need their own ids.
  const callKey = toolCallId ?? `anon-${state.anonymousCalls++}`;
  const rows = rowsFromTaskInput(input, callKey);
  if (rows.length === 0) return;
  for (const row of rows) upsertDockRow(state, row);
  if (toolCallId) state.pendingSpawns.set(toolCallId, rows.map((row) => row.id));
  setDockFooter(state, liveFooter(state));
  flushDock(state, ui);
}

/** Move the rows of one accepted `task` call off `running` when the tool returns. */
export function settleTaskResult(state: DockState, ui: SwarmDockUi | undefined, event: TaskResultEvent): void {
  if (event.toolName !== "task" || !event.toolCallId) return;
  const ids = state.pendingSpawns.get(event.toolCallId);
  state.pendingSpawns.delete(event.toolCallId);
  if (!ids || ids.length === 0) return;
  const status = event.isError ? "failed" : "completed";
  for (const id of ids) {
    const existing = state.live.rows.find((row) => row.id === id);
    if (!existing) continue;
    upsertDockRow(state, { id, agent: existing.agent, status });
  }
  if (!dockIsLive(state)) state.startedAt = undefined;
  setDockFooter(state, liveFooter(state));
  flushDock(state, ui);
}
