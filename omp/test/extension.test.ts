/**
 * SC5 / TOOL-01 / TOOL-06: the extension entry does no I/O at load, registers exactly the five hidden essential
 * swarm tools plus the session_shutdown and tool_call handlers, routes every tool through the single injected
 * bridge, and its tool_call guard does no I/O when it runs.
 *
 * mock.module("node:fs") is process-wide and mock.restore cannot undo it, so this file runs in its own `bun test`
 * process (omp/package.json scripts.test runs it first, then the rest with it ignored).
 */
import { afterAll, expect, mock, spyOn, test } from "bun:test";
import * as nodeFs from "node:fs";
import type { BridgeRequest, BridgeResult } from "../src/bridge.ts";
import type * as EntryModule from "../src/index.ts";
import { agentCtx, callTool, fakeCtx, fakePi, gitRepo, isolateEnv, tmpDir } from "./helpers.ts";

isolateEnv("SWARM_DIR", "SWARM_ROOT", "SWARM_TASK_ID", "SWARM_CORRELATION_ID", "SWARM_AGENT");

// Capture the real functions before mocking, so the wrappers never call themselves.
const realFs: Record<string, unknown> = { ...nodeFs };
let fsCalls = 0;
mock.module("node:fs", () =>
  Object.fromEntries(
    Object.entries(realFs).map(([name, value]) => [
      name,
      typeof value === "function"
        ? (...args: unknown[]) => {
            fsCalls++;
            return Reflect.apply(value, nodeFs, args);
          }
        : value,
    ]),
  ),
);
const spawn = spyOn(Bun, "spawn");
const spawnSync = spyOn(Bun, "spawnSync");
afterAll(() => {
  spawn.mockRestore();
  spawnSync.mockRestore();
});

const NAMES = ["swarm_gate", "swarm_ingest", "swarm_plan", "swarm_status", "swarm_transition"];

type Entry = typeof EntryModule;
let entry: Promise<Entry> | undefined;
/** The entry, imported once under the mocks; the query busts any cached src/index.ts so evaluation is observed. */
function loadEntry(): Promise<Entry> {
  entry ??= import(`../src/index.ts?noio=${Date.now()}`);
  return entry;
}

/** Every object (and function) reachable from `value`, by identity. */
function reachable(value: unknown, out = new Set<object>()): Set<object> {
  if (value === null || (typeof value !== "object" && typeof value !== "function")) return out;
  if (out.has(value)) return out;
  out.add(value);
  for (const child of Object.values(value)) reachable(child, out);
  return out;
}

test("registers five with no io: evaluation and factory do no fs, spawn or spawnSync", async () => {
  fsCalls = 0;
  spawn.mockClear();
  spawnSync.mockClear();
  const { default: factory } = await loadEntry();
  expect(fsCalls).toBe(0); // module evaluation
  expect(spawn).toHaveBeenCalledTimes(0);
  expect(spawnSync).toHaveBeenCalledTimes(0);

  const pi = fakePi();
  factory(pi.api);
  expect(fsCalls).toBe(0); // factory call
  expect(spawn).toHaveBeenCalledTimes(0);
  expect(spawnSync).toHaveBeenCalledTimes(0);
  // registration order is not part of the contract
  expect([...pi.handlers.keys()].sort()).toEqual(["session_shutdown", "tool_call"]);
  expect(pi.tools.map((t) => t.name).sort()).toEqual(NAMES);
  for (const tool of pi.tools) {
    expect(tool.hidden).toBe(true);
    expect(tool.loadMode).toBe("essential");
    expect(tool.parameters.type).toBe("object");
  }
});

test("no action at load: the factory never calls a runtime pi action", async () => {
  const { default: factory } = await loadEntry();
  const pi = fakePi();
  factory(pi.api);
  expect(pi.actionCalls).toEqual([]);
  expect([...pi.handlers.keys()].sort()).toEqual(["session_shutdown", "tool_call"]);
});

test("guard with no io: the tool_call handler decides without fs, spawn, spawnSync or the bridge", async () => {
  const { createSwarmExtension } = await loadEntry();
  const bridged: BridgeRequest[] = [];
  const pi = fakePi({ activeTools: ["read", "yield"] });
  pi.load(
    createSwarmExtension({
      bridge: async (req) => {
        bridged.push(req);
        return { exitCode: 0, json: {}, swarmDir: "" };
      },
    }),
  );
  const guard = pi.handler("tool_call");
  const a01 = agentCtx("/nonexistent-guard-cwd", "a01-orchestrator", [], false);
  fsCalls = 0;
  spawn.mockClear();
  spawnSync.mockClear();

  const blocked = guard({ toolName: "bash", toolCallId: "tc-1", input: { command: "ls" } }, a01) as { reason?: string };
  expect(blocked.reason?.startsWith("BLOCKED needs: depth")).toBe(true);
  const depthYield = { toolName: "yield", toolCallId: "tc-2", input: { data: { state: "BLOCKED", needs: "depth" } } };
  expect(guard(depthYield, a01)).toBeUndefined();
  expect(guard({ toolName: "bash", toolCallId: "tc-3", input: { command: "ls" } }, fakeCtx("/nonexistent-guard-cwd"))).toBeUndefined();

  expect(fsCalls).toBe(0);
  expect(spawn).toHaveBeenCalledTimes(0);
  expect(spawnSync).toHaveBeenCalledTimes(0);
  expect(bridged).toEqual([]);
  expect(pi.actionCalls).toEqual([]);
  expect(pi.runtimeCalls).toEqual(["getActiveTools", "getActiveTools"]);
});

test("factory twice: two sessions each get five unique tools and share no mutable object", async () => {
  const { default: factory } = await loadEntry();
  const [pi1, pi2] = [fakePi(), fakePi()];
  factory(pi1.api);
  factory(pi2.api);
  for (const pi of [pi1, pi2]) {
    const names = pi.tools.map((t) => t.name);
    expect(new Set(names).size).toBe(names.length);
    expect(names.sort()).toEqual(NAMES);
  }

  const other = reachable(pi2.tools);
  const shared = [...reachable(pi1.tools)].filter((o) => other.has(o)).map((o) => JSON.stringify(o)?.slice(0, 80));
  expect(shared).toEqual([]);

  const before = JSON.stringify(pi2.tool("swarm_plan").parameters);
  const props = pi1.tool("swarm_plan").parameters.properties;
  if (!props || typeof props !== "object") throw new Error("swarm_plan has no properties");
  Reflect.set(props, "injected", { type: "string" });
  Reflect.deleteProperty(props, "brief");
  expect(JSON.stringify(pi2.tool("swarm_plan").parameters)).toBe(before);
});

test("bridge once per tool: one recorded bridge call each with the expected script and argv, no Bun.spawn", async () => {
  const { createSwarmExtension } = await loadEntry();
  // A real tmp git repo as ctx.cwd: `--root=` comes from gitToplevel, a read-only Bun.spawnSync of git at execute
  // time (legitimate), so only Bun.spawn (the python bridge) must stay at 0 here.
  const repo = gitRepo();
  const sdir = tmpDir("swarm-omp-dir-");
  process.env.SWARM_DIR = sdir;
  const calls: BridgeRequest[] = [];
  const bridge = async (req: BridgeRequest): Promise<BridgeResult> => {
    calls.push(req);
    return { exitCode: 0, json: { summary: `${req.script} ok` }, swarmDir: sdir };
  };
  const pi = fakePi();
  createSwarmExtension({ bridge })(pi.api);
  const ctx = fakeCtx(repo);
  const root = `--root=${repo}`;
  const cases: Array<[string, Record<string, unknown>, string, string[]]> = [
    ["swarm_plan", { brief: "fix login", pattern: "hotfix", risk_class: "low" }, "orch_plan",
      [root, "--brief-text=fix login", "--pattern=hotfix", "--risk-class=low"]],
    ["swarm_status", {}, "orch_status", [root]],
    ["swarm_ingest", { task_id: "H-patch", result: { task_id: "H-patch", state: "IN_REVIEW" } }, "orch_status",
      [root, "--task-id=H-patch", `--ingest=${sdir}/results/ingest-call-1.json`]],
    ["swarm_transition", { task_id: "H-rca", state: "CLAIMED", reason: "lease" }, "orch_status",
      [root, "--transition", "H-rca", "CLAIMED", "--reason=lease"]],
    ["swarm_gate", { gate: "review", task_id: "H-rev", correlation_id: "c-hot" }, "rev_gate",
      [root, "--task-id=H-rev", "--correlation-id=c-hot"]],
  ];

  spawn.mockClear();
  for (const [name, params, script, args] of cases) {
    calls.length = 0;
    const res = await callTool(pi.tool(name), params, name === "swarm_gate" ? agentCtx(repo, "a09-reviewer") : ctx);
    expect(calls).toHaveLength(1);
    expect(calls[0].script).toBe(script);
    expect(calls[0].args).toEqual(args);
    expect(calls[0].cwd).toBe(repo);
    expect(res.content[0].text).toBe(`${script} ok`);
  }
  expect(spawn).toHaveBeenCalledTimes(0);
});
