import { describe, expect, test } from "bun:test";
import { createSwarmExtension } from "../src/index.ts";
import type { SwarmDockComponent, SwarmDockTheme } from "../src/swarm_dock.ts";
import { createDockState, publishTaskSpawn, settleTaskResult, swarmDockWidget } from "../src/swarm_dock.ts";
import { fakeCtx, fakePi } from "./helpers.ts";

function cardUi() {
  const factories: Array<(tui: unknown, theme: SwarmDockTheme) => SwarmDockComponent> = [];
  const ui = {
    setWidget(_key: string, content: ((tui: unknown, theme: SwarmDockTheme) => SwarmDockComponent) | undefined) {
      if (content) factories.push(content);
    },
  };
  return {
    ui,
    paint(index = factories.length - 1): string {
      const factory = factories[index];
      if (!factory) return "";
      return factory(undefined, undefined).render(80).join("\n");
    },
    paints: () => factories.length,
  };
}

/** Painted card lines that belong to a subagent row (the agent name appears only on rows). */
function rowLines(painted: string): number {
  return painted.split("\n").filter((line) => line.includes("a05-backend")).length;
}

const SPAWN = { tasks: [{ name: "be", agent: "a05-backend" }] };

describe("swarm dock launch lifecycle", () => {
  test("an accepted task spawn does not stay on launching", () => {
    const dock = createDockState();
    const card = cardUi();
    publishTaskSpawn(dock, card.ui, SPAWN, "call-1");
    const first = card.paint(0);
    expect(first).toContain("a05-backend");
    expect(first).toContain("running");
    expect(first).not.toContain("launching");

    const retained = card.paint(0);
    settleTaskResult(dock, card.ui, { toolName: "task", toolCallId: "call-1", isError: false });
    // The host may keep the first component and only call render() again.
    expect(retained).toContain("running");
    expect(card.paint(0)).toContain("completed");
    expect(card.paint(0)).not.toContain("running");
    expect(card.paint()).toContain("completed");
  });

  test("a failed task result marks the row failed", () => {
    const dock = createDockState();
    const card = cardUi();
    publishTaskSpawn(dock, card.ui, SPAWN, "call-2");
    settleTaskResult(dock, card.ui, { toolName: "task", toolCallId: "call-2", isError: true });
    expect(card.paint()).toContain("failed");
    expect(card.paint()).not.toContain("launching");
  });

  test("the extension settles the dock when the task tool returns", () => {
    const pi = fakePi({ activeTools: ["task"] });
    pi.load(createSwarmExtension({ bridge: async () => { throw new Error("bridge must not run"); } }));
    const card = cardUi();
    const ctx = { ...fakeCtx("/tmp/swarm-dock"), ui: card.ui };
    pi.handler("tool_call")(
      { toolName: "task", toolCallId: "call-3", input: SPAWN },
      ctx,
    );
    expect(card.paint()).toContain("running");
    expect(card.paint()).not.toContain("launching");
    pi.handler("tool_result")(
      { toolName: "task", toolCallId: "call-3", isError: false },
      ctx,
    );
    expect(card.paint(0)).toContain("completed");
  });
});

describe("swarm dock card identity", () => {
  test("two tasks in one session keep distinct cards", () => {
    const dock = createDockState();
    const card = cardUi();
    // Two overlapping calls reuse the same task name; settling one must not touch the other's card.
    publishTaskSpawn(dock, card.ui, SPAWN, "call-a");
    publishTaskSpawn(dock, card.ui, SPAWN, "call-b");
    expect(rowLines(card.paint())).toBe(2);
    settleTaskResult(dock, card.ui, { toolName: "task", toolCallId: "call-a", isError: true });
    const painted = card.paint();
    expect(rowLines(painted)).toBe(2);
    expect(painted).toContain("failed");
    expect(painted).toContain("running");
    expect(painted).toContain("1 running");
  });

  test("two sessions keep separate card sets", () => {
    const first = createDockState();
    const second = createDockState();
    const firstCard = cardUi();
    const secondCard = cardUi();
    publishTaskSpawn(first, firstCard.ui, SPAWN, "call-1");
    // The other session reuses the same tool call id; its settle must not reach the first session's card.
    publishTaskSpawn(second, secondCard.ui, SPAWN, "call-1");
    settleTaskResult(second, secondCard.ui, { toolName: "task", toolCallId: "call-1", isError: false });
    expect(secondCard.paint()).toContain("completed");
    expect(firstCard.paint()).toContain("running");
    expect(firstCard.paint()).not.toContain("completed");
  });
});
