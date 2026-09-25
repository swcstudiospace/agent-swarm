/** HOOK-01 (D-09/D-10) and the OPEN-3 R1 runtime part: before_agent_start behaviour; the hook never spawns. */
import { afterAll, beforeEach, describe, expect, spyOn, test } from "bun:test";
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import type { Bridge } from "../src/bridge.ts";
import { SWARM_SLUGS } from "../src/guard.ts";
import { RUNTIME_HEADING, runtimeContext, runtimePart, SWARM_CONTEXT, swarmContext } from "../src/hooks.ts";
import { createSwarmExtension } from "../src/index.ts";
import type { ExtensionContext } from "../src/omp-api.ts";
import { agentCtx, fakeCtx, fakePi, isolateEnv, REPO_ROOT, tmpDir } from "./helpers.ts";

isolateEnv("SWARM_CHILD", "SWARM_AGENT", "SWARM_ROOT");

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
  delete process.env.SWARM_ROOT;
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

/** The injected parts of a handler result (none when it left the prompt untouched). */
const parts = (out: unknown): string[] => (out as { systemPrompt?: string[] } | undefined)?.systemPrompt ?? [];

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
    expect(parts(hook()({ prompt: SDLC }, top()))).not.toContain(SWARM_CONTEXT);
  });
  test("session_init for a swarm agent", () => {
    expect(parts(hook()({ prompt: SDLC }, agentCtx("/x", "a05-backend")))).not.toContain(SWARM_CONTEXT);
  });
  test("session_init for a plain task subagent (unrestricted)", () => {
    expect(hook()({ prompt: SDLC }, agentCtx("/x", "task", [], false))).toBeUndefined();
  });
  test("session_init with no agent but restricted tools", () => {
    const ctx = fakeCtx("/x", [{ type: "session_init", restrictToolNames: true }]);
    expect(hook()({ prompt: SDLC }, ctx)).toBeUndefined();
  });
});

/**
 * A base prompt shaped like omp's repo context (G-04-05-2): two prior parts that both mention the hook's heading —
 * one as a heading of its own, one as a CLAUDE.md-style title line — without being the injected part.
 */
const REPO_LIKE_CONTEXT = [
  "# AGENTS.md\n\n## AgentSwarm\n\nThe swarm's own contract text lives here; it names /swarm and a01-orchestrator.\n",
  "# CLAUDE.md — AgentSwarm\n\nProject rules that mention the ## AgentSwarm heading in passing.\n",
];
const LIVE_POSITIVE = FIXTURE.positive[1];

test("repo-like base prompt: prior parts mentioning the heading do not count as the injected part", () => {
  const out = hook()({ prompt: LIVE_POSITIVE, systemPrompt: REPO_LIKE_CONTEXT }, top()) as { systemPrompt: string[] };
  expect(out.systemPrompt).toEqual([...REPO_LIKE_CONTEXT, SWARM_CONTEXT]);
});

test("neutral base prompt that merely mentions the heading still gets the context", () => {
  const prior = ["see ## AgentSwarm below"];
  const out = hook()({ prompt: LIVE_POSITIVE, systemPrompt: prior }, top()) as { systemPrompt: string[] };
  expect(out.systemPrompt).toEqual([...prior, SWARM_CONTEXT]);
});

test("idempotent: an existing SWARM_CONTEXT part is not duplicated", () => {
  const first = hook()({ prompt: SDLC, systemPrompt: PRIOR }, top()) as { systemPrompt: string[] };
  expect(hook()({ prompt: SDLC, systemPrompt: first.systemPrompt }, top())).toBeUndefined();
  expect(hook()({ prompt: SDLC, systemPrompt: [...REPO_LIKE_CONTEXT, SWARM_CONTEXT] }, top())).toBeUndefined();
});

test("idempotent: a prior part that only contains SWARM_CONTEXT as a substring is not the injected part", () => {
  const wrapped = [`preamble\n${SWARM_CONTEXT}\npostamble`];
  const out = hook()({ prompt: SDLC, systemPrompt: wrapped }, top()) as { systemPrompt: string[] };
  expect(out.systemPrompt).toEqual([...wrapped, SWARM_CONTEXT]);
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

// ── OPEN-3 R1: the runtime part ──────────────────────────────────────────────

describe("runtime part", () => {
  const root = () => realpathSync(REPO_ROOT);

  test.each(SWARM_SLUGS.map((slug) => [slug]))("%s subagent gets the absolute runtime root, once, after the prior parts", (slug) => {
    for (const ctx of [agentCtx("/x", slug), agentCtx("/x", slug, [], false)]) {
      const out = parts(hook()({ prompt: SDLC, systemPrompt: PRIOR }, ctx));
      expect(out).toEqual([...PRIOR, runtimePart(root())]);
      expect(isAbsolute(root())).toBe(true);
      expect(out.at(-1)).toContain(`\nRuntime root: ${root()}\n`);
      expect(out.at(-1)).toContain(`python3 ${root()}/scripts/<script>.py … --root <repo> --json`);
    }
  });

  test("the root is swarmRoot() at handler time: env SWARM_ROOT wins", () => {
    const alt = tmpDir("swarm-omp-root-");
    mkdirSync(join(alt, "scripts"));
    writeFileSync(join(alt, "scripts", "orch_plan.py"), "");
    const handler = hook(); // registered before SWARM_ROOT is set: nothing was resolved at load
    process.env.SWARM_ROOT = alt;
    expect(parts(handler({ prompt: SDLC }, agentCtx("/x", "a05-backend")))).toEqual([runtimePart(alt)]);
  });

  test("the top-level main session never gets it", () => {
    expect(parts(hook()({ prompt: SDLC, systemPrompt: PRIOR }, top()))).toEqual([...PRIOR, SWARM_CONTEXT]);
    expect(hook()({ prompt: "what is this repo?", systemPrompt: PRIOR }, top())).toBeUndefined();
  });

  test.each([
    ["a plain task subagent", agentCtx("/x", "task", [], false)],
    ["a scout subagent (restricted)", agentCtx("/x", "scout")],
    ["a session_init with no agent", fakeCtx("/x", [{ type: "session_init", restrictToolNames: true }])],
  ])("%s does not get it", (_name, ctx) => {
    expect(hook()({ prompt: SDLC, systemPrompt: PRIOR }, ctx)).toBeUndefined();
  });

  test("env bleed: SWARM_AGENT does not make a non-swarm subagent a swarm session", () => {
    process.env.SWARM_AGENT = "a05-backend";
    expect(hook()({ prompt: SDLC }, agentCtx("/x", "task", [], false))).toBeUndefined();
  });

  test("headless: SWARM_AGENT=a05-backend with SWARM_CHILD=1 gets it (and no HOOK-01 context)", () => {
    process.env.SWARM_AGENT = "a05-backend";
    process.env.SWARM_CHILD = "1";
    expect(parts(hook()({ prompt: SDLC, systemPrompt: PRIOR }, top()))).toEqual([...PRIOR, runtimePart(root())]);
  });

  test("headless: a non-slug SWARM_AGENT does not get it", () => {
    process.env.SWARM_AGENT = "not-a-swarm-agent";
    process.env.SWARM_CHILD = "1";
    expect(hook()({ prompt: SDLC, systemPrompt: PRIOR }, top())).toBeUndefined();
  });

  test("idempotent: an existing runtime part is not duplicated", () => {
    const ctx = agentCtx("/x", "a05-backend");
    const first = parts(hook()({ prompt: SDLC, systemPrompt: PRIOR }, ctx));
    expect(hook()({ prompt: SDLC, systemPrompt: first }, ctx)).toBeUndefined();
    expect(hook()({ prompt: SDLC, systemPrompt: [...PRIOR, runtimePart("/elsewhere")] }, ctx)).toBeUndefined();
  });

  test("a prior part that merely mentions the heading does not count as the runtime part", () => {
    const prior = [`see ${RUNTIME_HEADING} below`];
    expect(parts(hook()({ prompt: SDLC, systemPrompt: prior }, agentCtx("/x", "a05-backend")))).toEqual([...prior, runtimePart(root())]);
  });

  test("fail-open: a throwing swarmRoot leaves the prompt untouched", () => {
    const boom = () => {
      throw new Error("agent-swarm scripts not found");
    };
    expect(runtimeContext({ prompt: SDLC, systemPrompt: PRIOR }, agentCtx("/x", "a05-backend"), boom)).toBeUndefined();
  });
});

test("no spawn: Bun.spawn and Bun.spawnSync were never called by any hook test above", () => {
  expect(spawn).toHaveBeenCalledTimes(0);
  expect(spawnSync).toHaveBeenCalledTimes(0);
});
