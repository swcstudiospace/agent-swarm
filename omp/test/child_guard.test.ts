/** SC5: port of tests/test_child_guard.py hook cases — swarm children and subagents get no context, nothing spawns. */
import { afterAll, beforeEach, describe, expect, spyOn, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Bridge } from "../src/bridge.ts";
import { SWARM_CONTEXT } from "../src/hooks.ts";
import { createSwarmExtension } from "../src/index.ts";
import { agentCtx, fakeCtx, fakePi, isolateEnv, REPO_ROOT } from "./helpers.ts";

isolateEnv("SWARM_CHILD", "SWARM_AGENT", "AIO_SWARM");

/** The shared fixture (D-10); its first positive is a prompt the hook steers in a fresh top-level session. */
const FIXTURE = JSON.parse(readFileSync(join(REPO_ROOT, "tests", "fixtures", "classifier_prompts.json"), "utf8")) as {
  positive: string[];
};
const PROMPT = FIXTURE.positive[0];

const spawn = spyOn(Bun, "spawn");
const spawnSync = spyOn(Bun, "spawnSync");
afterAll(() => {
  spawn.mockRestore();
  spawnSync.mockRestore();
});

beforeEach(() => {
  delete process.env.SWARM_CHILD;
  delete process.env.SWARM_AGENT;
  delete process.env.AIO_SWARM;
  spawn.mockClear();
  spawnSync.mockClear();
});

function hook() {
  const bridge: Bridge = () => {
    throw new Error("the context hook must never reach the bridge");
  };
  const pi = fakePi();
  pi.load(createSwarmExtension({ bridge }));
  return pi.handler("before_agent_start");
}

describe("test_child_guard port", () => {
  test("hook silent inside swarm child (SWARM_CHILD=1)", () => {
    process.env.SWARM_CHILD = "1";
    expect(hook()({ prompt: PROMPT }, fakeCtx("/x"))).toBeUndefined();
  });

  test("hook silent inside an in-process subagent (session_init)", () => {
    // HOOK-01 stays silent; the OPEN-3 R1 runtime part is the only part a swarm subagent gets
    const out = hook()({ prompt: PROMPT }, agentCtx("/x", "a05-backend")) as { systemPrompt?: string[] } | undefined;
    expect(out?.systemPrompt ?? []).not.toContain(SWARM_CONTEXT);
  });

  test("hook silent with AIO_SWARM=0 and SWARM_CHILD=1 (the Python test's env)", () => {
    process.env.AIO_SWARM = "0";
    process.env.SWARM_CHILD = "1";
    expect(hook()({ prompt: PROMPT }, fakeCtx("/x"))).toBeUndefined();
  });

  test("hook does not spawn a runner, with or without SWARM_CHILD", () => {
    const handler = hook();
    expect(handler({ prompt: PROMPT }, fakeCtx("/x"))).toBeDefined();
    process.env.SWARM_CHILD = "1";
    expect(handler({ prompt: PROMPT }, fakeCtx("/x"))).toBeUndefined();
    expect(spawn).toHaveBeenCalledTimes(0);
    expect(spawnSync).toHaveBeenCalledTimes(0);
  });
});
