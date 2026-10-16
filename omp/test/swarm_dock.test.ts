import { describe, expect, test } from "bun:test";
import { createSwarmExtension } from "../src/index.ts";
import type { SwarmDockComponent, SwarmDockTheme } from "../src/swarm_dock.ts";
import { clearDock, publishTaskSpawn, settleTaskResult, swarmDockWidget } from "../src/swarm_dock.ts";
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

const SPAWN = { tasks: [{ name: "be", agent: "a05-backend" }] };

describe("swarm dock launch lifecycle", () => {
  test("an accepted task spawn does not stay on launching", () => {
    clearDock(undefined);
    const card = cardUi();
    publishTaskSpawn(card.ui, SPAWN, "call-1");
    const first = card.paint(0);
    expect(first).toContain("a05-backend");
    expect(first).toContain("running");
    expect(first).not.toContain("launching");

    const retained = card.paint(0);
    settleTaskResult(card.ui, { toolName: "task", toolCallId: "call-1", isError: false });
    // The host may keep the first component and only call render() again.
    expect(retained).toContain("running");
    expect(card.paint(0)).toContain("completed");
    expect(card.paint(0)).not.toContain("running");
    expect(card.paint()).toContain("completed");
    clearDock(undefined);
  });

  test("a failed task result marks the row failed", () => {
    clearDock(undefined);
    const card = cardUi();
    publishTaskSpawn(card.ui, SPAWN, "call-2");
    settleTaskResult(card.ui, { toolName: "task", toolCallId: "call-2", isError: true });
    expect(card.paint()).toContain("failed");
    expect(card.paint()).not.toContain("launching");
    clearDock(undefined);
  });

  test("the extension settles the dock when the task tool returns", () => {
    clearDock(undefined);
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
    clearDock(undefined);
  });
});
