/**
 * Measured token savings for the swarm bar's right-hand chips. The producer is omp-token-opt (`token-opt:savings`, v1 snapshot;
 * the same document is its `live.json`). This is a faithful port of the parser Ultrathink used to carry: same checks, same
 * rejections. Pure functions, no module state.
 */

const SAVINGS_SOURCES = ["provider", "compress", "graph"] as const;

export interface TokenSavingsPoint {
  at: number;
  source: (typeof SAVINGS_SOURCES)[number];
  baseline: number;
  current: number;
  saved: number;
}

/** Measured session savings. `saved` is always `baseline - current`; never an estimate. */
export interface TokenSavingsSnapshot {
  v: 1;
  sessionId: string;
  updatedAt: number;
  baselineTokens: number;
  currentTokens: number;
  savedTokens: number;
  points: TokenSavingsPoint[];
  /** Wall time of the last completed assistant turn. Omitted until measured. */
  lastTurnMs?: number;
  /** Session cache-hit percent, 0-100. Omitted until a provider turn exists. */
  cacheHitPct?: number;
  /** Last graphify spawn or cache-hit latency. Omitted until measured. */
  lastQueryMs?: number;
  /** RTK session delta since start. Not included in savedTokens. */
  rtkSavedTokens?: number;
  /** Last provider request payload size in characters. */
  lastPayloadChars?: number;
  /** Time to first token on the last turn. */
  ttftMs?: number;
  /** Idle time since the last turn. Not included in savedTokens. */
  lastIdleMs?: number;
  /** Session cost in cents. Not included in savedTokens. */
  costCents?: number;
  /** Provider billed input tokens. Not included in savedTokens. */
  providerInput?: number;
  /** Provider billed output tokens. Not included in savedTokens. */
  providerOutput?: number;
  /** Provider cache-read tokens. Not included in savedTokens; the hit rate stays on cacheHitPct. */
  providerCacheRead?: number;
  /** Provider cache-write tokens. Not included in savedTokens. */
  providerCacheWrite?: number;
  /** 1 when the prompt prefix moved. */
  prefixChanged?: 0 | 1;
  /** 1 when the tool set moved. */
  toolsChanged?: 0 | 1;
  /** 1 when the graph is ready. */
  graphReady?: 0 | 1;
}

/** A session id the producer may have written: 1..128 characters, no `/` (it names a file). */
export function isSessionId(value: unknown): value is string {
  return typeof value === "string" && value.length >= 1 && value.length <= 128 && !value.includes("/");
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function oneOf<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

function parsePoint(value: unknown): TokenSavingsPoint | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const { at, source, baseline, current, saved } = value as Record<string, unknown>;
  if (!isCount(at) || !oneOf(SAVINGS_SOURCES, source) || !isCount(baseline) || !isCount(current) || !isCount(saved)) return undefined;
  if (saved !== baseline - current) return undefined;
  return { at, source, baseline, current, saved };
}

/** Absent -> undefined. Present but not a safe integer in `0..max` -> null, which rejects the snapshot. */
function optionalPerf(data: Record<string, unknown>, key: string, max = Number.MAX_SAFE_INTEGER): number | undefined | null {
  if (!Object.hasOwn(data, key)) return undefined;
  const value = data[key];
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > max) return null;
  return value;
}

/** `token-opt:savings` v1, or undefined when any required check fails. A present optional field outside its range rejects the snapshot. */
export function parseTokenSavings(data: unknown): TokenSavingsSnapshot | undefined {
  try {
    if (typeof data !== "object" || data === null || Array.isArray(data)) return undefined;
    const record = data as Record<string, unknown>;
    const { v, sessionId, updatedAt, baselineTokens, currentTokens, savedTokens, points } = record;
    if (v !== 1 || !isSessionId(sessionId) || !isCount(updatedAt)) return undefined;
    if (!isCount(baselineTokens) || !isCount(currentTokens) || !isCount(savedTokens)) return undefined;
    if (savedTokens !== baselineTokens - currentTokens) return undefined;
    if (!Array.isArray(points) || points.length > 200) return undefined;
    const lastTurnMs = optionalPerf(record, "lastTurnMs");
    const cacheHitPct = optionalPerf(record, "cacheHitPct", 100);
    const lastQueryMs = optionalPerf(record, "lastQueryMs");
    const rtkSavedTokens = optionalPerf(record, "rtkSavedTokens");
    const lastPayloadChars = optionalPerf(record, "lastPayloadChars");
    const ttftMs = optionalPerf(record, "ttftMs");
    const lastIdleMs = optionalPerf(record, "lastIdleMs");
    const costCents = optionalPerf(record, "costCents");
    const providerInput = optionalPerf(record, "providerInput");
    const providerOutput = optionalPerf(record, "providerOutput");
    const providerCacheRead = optionalPerf(record, "providerCacheRead");
    const providerCacheWrite = optionalPerf(record, "providerCacheWrite");
    const prefixChanged = optionalPerf(record, "prefixChanged", 1);
    const toolsChanged = optionalPerf(record, "toolsChanged", 1);
    const graphReady = optionalPerf(record, "graphReady", 1);
    if (lastTurnMs === null || cacheHitPct === null || lastQueryMs === null || rtkSavedTokens === null || lastPayloadChars === null || ttftMs === null) return undefined;
    if (lastIdleMs === null || costCents === null) return undefined;
    if (providerInput === null || providerOutput === null || providerCacheRead === null || providerCacheWrite === null) return undefined;
    if (prefixChanged === null || toolsChanged === null || graphReady === null) return undefined;
    const parsed: TokenSavingsPoint[] = [];
    for (const point of points) {
      const next = parsePoint(point);
      if (!next) return undefined;
      parsed.push(next);
    }
    const snapshot: TokenSavingsSnapshot = { v: 1, sessionId, updatedAt, baselineTokens, currentTokens, savedTokens, points: parsed };
    if (lastTurnMs !== undefined) snapshot.lastTurnMs = lastTurnMs;
    if (cacheHitPct !== undefined) snapshot.cacheHitPct = cacheHitPct;
    if (lastQueryMs !== undefined) snapshot.lastQueryMs = lastQueryMs;
    // separate measured counts: never folded into savedTokens
    if (rtkSavedTokens !== undefined) snapshot.rtkSavedTokens = rtkSavedTokens;
    if (lastPayloadChars !== undefined) snapshot.lastPayloadChars = lastPayloadChars;
    if (ttftMs !== undefined) snapshot.ttftMs = ttftMs;
    if (lastIdleMs !== undefined) snapshot.lastIdleMs = lastIdleMs;
    if (costCents !== undefined) snapshot.costCents = costCents;
    if (providerInput !== undefined) snapshot.providerInput = providerInput;
    if (providerOutput !== undefined) snapshot.providerOutput = providerOutput;
    if (providerCacheRead !== undefined) snapshot.providerCacheRead = providerCacheRead;
    if (providerCacheWrite !== undefined) snapshot.providerCacheWrite = providerCacheWrite;
    if (prefixChanged === 0 || prefixChanged === 1) snapshot.prefixChanged = prefixChanged;
    if (toolsChanged === 0 || toolsChanged === 1) snapshot.toolsChanged = toolsChanged;
    if (graphReady === 0 || graphReady === 1) snapshot.graphReady = graphReady;
    return snapshot;
  } catch {
    return undefined;
  }
}

/** 999, 1.2k, 1.2M. Only for counts already checked as non-negative integers. */
export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  // tenths of a thousand, rounded once; a count that rounds up to 1000k is 1M
  const tenthsOfK = Math.round(n / 100);
  const [value, suffix]: [number, string] = tenthsOfK >= 10_000 ? [Math.round(n / 100_000), "M"] : [tenthsOfK, "k"];
  return `${Number.isInteger(value / 10) ? String(value / 10) : (value / 10).toFixed(1)}${suffix}`;
}

/** `text` is the neutral theme text colour. */
export type ChipColor = "text" | "dim" | "success" | "warning";

/** One right-hand chip. Priority 0 is the last to go when the row is narrow. */
export interface TokenChip {
  text: string;
  priority: number;
  color: ChipColor;
}

/**
 * The chips of one snapshot in on-screen order, least important first, so the right-most chip is `saved`. A chip appears only
 * when its measurement exists (nothing is invented), and provider cache tokens and bill totals are never part of `saved`.
 */
export function tokenChips(snapshot: TokenSavingsSnapshot): TokenChip[] {
  const chips: TokenChip[] = [];
  const add = (priority: number, color: ChipColor, text: string): void => void chips.push({ text, priority, color });
  if (snapshot.graphReady === 1) add(11, "dim", "graph ready");
  const bill: string[] = [];
  if (snapshot.providerInput !== undefined) bill.push(`bill ${formatTokens(snapshot.providerInput)}`);
  if (snapshot.providerOutput !== undefined) bill.push(`out ${formatTokens(snapshot.providerOutput)}`);
  if (bill.length > 0) add(10, "dim", bill.join(" · "));
  if (snapshot.lastIdleMs !== undefined) add(9, "text", `idle ${snapshot.lastIdleMs}ms`);
  if (snapshot.lastPayloadChars !== undefined) add(8, "text", `payload ${snapshot.lastPayloadChars}c`);
  if (snapshot.lastQueryMs !== undefined) add(7, "text", `query ${snapshot.lastQueryMs}ms`);
  if (snapshot.rtkSavedTokens !== undefined) add(6, "text", `rtk +${formatTokens(snapshot.rtkSavedTokens)}`);
  if (snapshot.costCents !== undefined) add(5, "text", `cost ${snapshot.costCents}c`);
  if (snapshot.lastTurnMs !== undefined) add(4, "text", `turn ${snapshot.lastTurnMs}ms`);
  add(3, "dim", `before ${formatTokens(snapshot.baselineTokens)} · now ${formatTokens(snapshot.currentTokens)}`);
  if (snapshot.prefixChanged === 1) add(2, "warning", "prefix moved");
  if (snapshot.toolsChanged === 1) add(2, "warning", "tools moved");
  if (snapshot.ttftMs !== undefined) add(2, "text", `ttft ${snapshot.ttftMs}ms`);
  if (snapshot.cacheHitPct !== undefined) add(1, "text", `cache ${snapshot.cacheHitPct}%`);
  add(0, snapshot.savedTokens > 0 ? "success" : "dim", `saved ${formatTokens(snapshot.savedTokens)}`);
  return chips;
}
