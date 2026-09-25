/** HOOK-01 (D-09/D-10): fixture-driven classifier and before_agent_start behaviour; the hook never spawns. */
import { afterAll, beforeEach, describe, expect, spyOn, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Bridge } from "../src/bridge.ts";
import { SWARM_CONTEXT, swarmContext } from "../src/hooks.ts";
import { createSwarmExtension } from "../src/index.ts";
import type { ExtensionContext } from "../src/omp-api.ts";
import { agentCtx, fakeCtx, fakePi, isolateEnv, REPO_ROOT } from "./helpers.ts";

isolateEnv("SWARM_CHILD", "SWARM_AGENT");

const FIXTURE = JSON.parse(readFileSync(join(REPO_ROOT, "tests", "fixtures", "classifier_prompts.json"), "utf8")) as {
  positive: string[];
  negative: string[];
};
const SDLC = FIXTURE.positive[0];
const PRIOR = ["base prompt", "project rules"];

const spawn = spyOn(Bun, "spawn");
const spawnSync = spyOn(Bun, "spawnSync");
afterAll(() => {
  spawn.mockRestore();
  spawnSync.mockRestore();
});

beforeEach(() => {
  delete process.env.SWARM_CHILD;
  delete process.env.SWARM_AGENT;
});

/** The before_agent_start handler the real factory registers; the bridge throws if ever reached. */
function hook(): (event: unknown, ctx: ExtensionContext) => unknown {
  const bridge: Bridge = () => {
    throw new Error("the context hook must never reach the bridge");
  };
  const pi = fakePi();
  pi.load(createSwarmExtension({ bridge }));
  return pi.handler("before_agent_start");
}

const top = () => fakeCtx("/nonexistent-hook-cwd");

describe("fixture positives inject once, after the prior parts", () => {
  for (const prompt of FIXTURE.positive) {
    test(`positive: ${prompt.slice(0, 60)}`, () => {
      const out = hook()({ prompt, systemPrompt: PRIOR }, top()) as { systemPrompt: string[] };
      expect(out.systemPrompt.slice(0, -1)).toEqual(PRIOR);
      const added = out.systemPrompt.at(-1) ?? "";
      expect(added.startsWith("## AgentSwarm")).toBe(true);
      expect(added).toContain("/swarm <brief>");
      expect(added).toContain("task");
      expect(added).toContain("a01-orchestrator");
      expect(out.systemPrompt.filter((p) => p.includes("## AgentSwarm"))).toHaveLength(1);
    });
  }
});

describe("fixture negatives are silent", () => {
  for (const prompt of FIXTURE.negative) {
    test(`negative: ${JSON.stringify(prompt.slice(0, 60))}`, () => {
      expect(hook()({ prompt, systemPrompt: PRIOR }, top())).toBeUndefined();
    });
  }
});

describe("silence outside a fresh top-level session", () => {
  test("SWARM_CHILD=1", () => {
    process.env.SWARM_CHILD = "1";
    expect(hook()({ prompt: SDLC }, top())).toBeUndefined();
  });
  test("SWARM_AGENT set", () => {
    process.env.SWARM_AGENT = "a05-backend";
    expect(hook()({ prompt: SDLC }, top())).toBeUndefined();
  });
  test("session_init for a swarm agent", () => {
    expect(hook()({ prompt: SDLC }, agentCtx("/x", "a05-backend"))).toBeUndefined();
  });
  test("session_init for a plain task subagent (unrestricted)", () => {
    expect(hook()({ prompt: SDLC }, agentCtx("/x", "task", [], false))).toBeUndefined();
  });
  test("session_init with no agent but restricted tools", () => {
    const ctx = fakeCtx("/x", [{ type: "session_init", restrictToolNames: true }]);
    expect(hook()({ prompt: SDLC }, ctx)).toBeUndefined();
  });
});

/** The repo's own context files as omp loads them into the base system prompt (G-04-05-2). */
const REPO_CONTEXT = ["AGENTS.md", "CLAUDE.md"].map((f) => readFileSync(join(REPO_ROOT, f), "utf8"));
const LIVE_POSITIVE = FIXTURE.positive[1];
const LIVE_NEGATIVE = FIXTURE.negative[0];
/** The SWARM_CONTEXT sentence the live prompts ask the model to continue, and its continuation. */
const SENTENCE_START = "This is SDLC work";
const CONTINUATION = "route it through the AgentSwarm";

test("repo base prompt: AGENTS.md and CLAUDE.md mention the hook's heading, yet an SDLC prompt still gets SWARM_CONTEXT", () => {
  expect(REPO_CONTEXT.some((p) => p.includes("## AgentSwarm"))).toBe(true);
  const out = hook()({ prompt: LIVE_POSITIVE, systemPrompt: REPO_CONTEXT }, top()) as { systemPrompt: string[] };
  expect(out.systemPrompt).toEqual([...REPO_CONTEXT, SWARM_CONTEXT]);
});

test("neutral base prompt that merely mentions the heading still gets the context", () => {
  const prior = ["see ## AgentSwarm below"];
  const out = hook()({ prompt: LIVE_POSITIVE, systemPrompt: prior }, top()) as { systemPrompt: string[] };
  expect(out.systemPrompt).toEqual([...prior, SWARM_CONTEXT]);
});

test("idempotent: an existing SWARM_CONTEXT part is not duplicated", () => {
  const first = hook()({ prompt: SDLC, systemPrompt: PRIOR }, top()) as { systemPrompt: string[] };
  expect(hook()({ prompt: SDLC, systemPrompt: first.systemPrompt }, top())).toBeUndefined();
  expect(hook()({ prompt: SDLC, systemPrompt: [...REPO_CONTEXT, SWARM_CONTEXT] }, top())).toBeUndefined();
});

test("discriminator: the live prompts can only continue the SWARM_CONTEXT sentence if the hook injected it", () => {
  const sentence = SWARM_CONTEXT.split("\n").find((l) => l.startsWith(SENTENCE_START)) ?? "";
  const rest = sentence.slice(SENTENCE_START.length);
  expect(rest).toContain(CONTINUATION);
  for (const source of REPO_CONTEXT) expect(source).not.toContain(CONTINUATION);
  for (const prompt of [LIVE_POSITIVE, LIVE_NEGATIVE]) {
    expect(prompt).toContain(SENTENCE_START);
    expect(prompt).not.toContain(CONTINUATION);
    expect(prompt).not.toContain(rest.trim());
  }
});

test("fail-open: a throwing classifier leaves the prompt untouched", () => {
  const boom = () => {
    throw new Error("classifier exploded");
  };
  expect(swarmContext({ prompt: SDLC, systemPrompt: PRIOR }, top(), {}, boom)).toBeUndefined();
});

test("fail-open: the registered handler returns undefined when the hook module throws", () => {
  const broken: ExtensionContext = {
    cwd: "/x",
    sessionManager: {
      getEntries: () => {
        throw new Error("session log unreadable");
      },
      getBranch: () => [],
    },
  };
  expect(hook()({ prompt: SDLC }, broken)).toBeUndefined();
});

test("no spawn: Bun.spawn and Bun.spawnSync were never called by any hook test above", () => {
  expect(spawn).toHaveBeenCalledTimes(0);
  expect(spawnSync).toHaveBeenCalledTimes(0);
});
