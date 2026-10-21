/**
 * Stack order of the aboveEditor bars. omp stacks widgets in insertion order and re-setting a key moves it to the bottom, so a bar
 * with a lower rank must be (re)inserted before one with a higher rank. omp's event bus runs handlers synchronously inside `emit`
 * and swallows their errors, so a plain announcement is a deterministic protocol: every bar announces its rank right after it
 * sets its widget, and every bar of a HIGHER rank answers by re-setting its own widget (which announces in turn). Ranks strictly
 * increase along that chain, so it ends, and whichever bar loads first the stack ends up ordered by rank (swarm above Ultrathink).
 * The same module exists in claude-ultrathink; both pin the same channel and ranks.
 */
import type { EventBus } from "./omp-api.ts";

export const RESTACK_CHANNEL = "bars:restack";
export const BAR_RANK = { swarm: 10, ultrathink: 20 } as const;

export interface RestackOptions {
  /** This bar's rank (`BAR_RANK`). */
  rank: number;
  /** Re-set this bar's widget and announce again. Called only for an announcement of a lower rank. */
  remount(): void;
}

export interface Restack {
  /** Call right after the widget was set. */
  announce(): void;
  /** Stop listening and announcing. Safe to call twice. */
  dispose(): void;
}

function lowerRank(data: unknown, rank: number): boolean {
  if (typeof data !== "object" || data === null) return false;
  const { v, after } = data as { v?: unknown; after?: unknown };
  return v === 1 && typeof after === "number" && Number.isFinite(after) && after < rank;
}

/** Display-only: nothing here throws into the host, and a bus without `on`/`emit` makes both calls no-ops. */
export function createRestack(events: Pick<EventBus, "emit" | "on"> | undefined, options: RestackOptions): Restack {
  let busy = false;
  let disposed = false;
  let off: (() => void) | undefined;
  try {
    off = events?.on(RESTACK_CHANNEL, (data) => {
      if (busy || !lowerRank(data, options.rank)) return;
      busy = true;
      try {
        options.remount();
      } catch {
        // a failing remount must not reach the emitter
      } finally {
        busy = false;
      }
    });
  } catch {
    off = undefined;
  }
  return {
    announce() {
      if (disposed) return;
      try {
        events?.emit(RESTACK_CHANNEL, { v: 1, after: options.rank });
      } catch {
        // display only
      }
    },
    dispose() {
      disposed = true;
      const stop = off;
      off = undefined;
      try {
        stop?.();
      } catch {
        // display only
      }
    },
  };
}
