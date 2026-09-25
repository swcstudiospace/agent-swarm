/**
 * The tool_call guard (Phase 4): HOOK-04 A01 depth cap (D-06), HOOK-03 swarm-state tools (D-05), identity (D-01)
 * and fail modes (D-02), over the pure guardToolCall and through the handler the factory registers.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import type { Bridge } from "../src/bridge.ts";
import { GUARD_ERROR_PREFIX, type GuardFacts, guardToolCall } from "../src/guard.ts";
import { createSwarmExtension } from "../src/index.ts";
import type { ExtensionContext, SessionEntry, ToolCallEvent } from "../src/omp-api.ts";
import { agentCtx, fakeCtx, fakePi, type FakePiOptions, isolateEnv } from "./helpers.ts";

isolateEnv("SWARM_AGENT", "SWARM_TASK_ID");
beforeEach(() => {
  delete process.env.SWARM_AGENT;
  delete process.env.SWARM_TASK_ID;
});

const A01 = "a01-orchestrator";
const CWD = "/nonexistent-guard-cwd";
const DEPTH_PREFIX = "BLOCKED needs: depth";

const call = (toolName: string, input: unknown = {}): ToolCallEvent => ({ toolName, toolCallId: "tc-1", input });
const yieldData = (data: unknown) => call("yield", { data });

/** A01 at the depth cap: no `task`, not a restricted child, not in plan mode. */
const capped: GuardFacts = { agent: A01, restricted: false, planMode: false, hasTask: false, topLevel: false, env: {} };

/** The tool_call handler the real factory registers, with a runtime getActiveTools (a bridge call would throw). */
function guardHandler(opts: FakePiOptions = {}) {
  const bridge: Bridge = () => {
    throw new Error("the guard must never reach the bridge");
  };
  const pi = fakePi(opts);
  pi.load(createSwarmExtension({ bridge }));
  const handler = pi.handler("tool_call");
  return { pi, run: (event: ToolCallEvent, ctx: ExtensionContext) => handler(event, ctx) };
}

/** A01's own session: session_init names it, tools not restricted (a real subagent, not a plan-mode child). */
const a01Ctx = (entries: SessionEntry[] = []) => agentCtx(CWD, A01, entries, false);

describe("HOOK-04", () => {
  test.each(["bash", "read", "write", "edit", "swarm_plan", "swarm_status", "eval", "grep"])(
    "at the cap, %s is blocked with the depth reason naming the one permitted yield",
    (toolName) => {
      const res = guardToolCall(call(toolName, { command: "ls" }), capped);
      expect(res?.block).toBe(true);
      expect(res?.reason?.startsWith(DEPTH_PREFIX)).toBe(true);
      expect(res?.reason).toContain("yield");
      expect(res?.reason).toContain('state: "BLOCKED"');
      expect(res?.reason).toContain('needs: "depth"');
    },
  );

  test.each([
    ["IN_REVIEW", { data: { task_id: "T-1", state: "IN_REVIEW" } }],
    ["DONE", { data: { task_id: "T-1", state: "DONE", needs: "depth" } }],
    ["error", { error: "x" }],
    ["empty input", {}],
    ["empty data", { data: {} }],
    ["BLOCKED without needs", { data: { state: "BLOCKED" } }],
    ['needs ""', { data: { state: "BLOCKED", needs: "" } }],
    ["needs []", { data: { state: "BLOCKED", needs: [] } }],
    ["needs {}", { data: { state: "BLOCKED", needs: {} } }],
    ['needs "depths"', { data: { state: "BLOCKED", needs: "depths" } }],
    ['needs "no-depth"', { data: { state: "BLOCKED", needs: "no-depth" } }],
    ['needs ["depths"]', { data: { state: "BLOCKED", needs: ["depths"] } }],
    ["needs {Depth} (object keys are exact)", { data: { state: "BLOCKED", needs: { Depth: 1 } } }],
    ["inherited depth key", { data: { state: "BLOCKED", needs: Object.create({ depth: 1 }) } }],
    ['state "blocked" (case-sensitive)', { data: { state: "blocked", needs: "depth" } }],
    ["needs without state", { data: { needs: "depth" } }],
    ["null input", null],
    ["string input", "BLOCKED depth"],
    ["array data", { data: ["BLOCKED", "depth"] }],
  ])("at the cap, yield %s is blocked", (_name, input) => {
    const res = guardToolCall(call("yield", input), capped);
    expect(res?.block).toBe(true);
    expect(res?.reason?.startsWith(DEPTH_PREFIX)).toBe(true);
  });

  test.each([
    ['"depth"', "depth"],
    ['["depth"]', ["depth"]],
    ["{depth: …}", { depth: "cap" }],
    ['" Depth "', " Depth "],
    ['["other", "DEPTH"]', ["other", "DEPTH"]],
  ])("at the cap, yield BLOCKED with needs %s is allowed without task_id", (_name, needs) => {
    expect(guardToolCall(yieldData({ state: "BLOCKED", needs }), capped)).toBeUndefined();
    expect(guardToolCall(yieldData({ task_id: "T-1", correlation_id: "C-1", state: "BLOCKED", needs }), capped)).toBeUndefined();
  });

  test.each([
    ["task active", { hasTask: true }],
    ["restricted child", { restricted: true }],
    ["plan mode", { planMode: true }],
  ])("one step off the cap (%s): no HOOK-04 block", (_name, override) => {
    const facts = { ...capped, ...override };
    expect(guardToolCall(call("read", { path: "x" }), facts)).toBeUndefined();
    expect(guardToolCall(yieldData({ state: "IN_REVIEW" }), facts)).toBeUndefined();
  });

  test.each([["a05-backend"], ["task"], [undefined]])("agent %p without task is not capped", (agent) => {
    expect(guardToolCall(call("read", { path: "x" }), { ...capped, agent, topLevel: agent === undefined })).toBeUndefined();
    expect(guardToolCall(yieldData({ state: "DONE" }), { ...capped, agent, topLevel: agent === undefined })).toBeUndefined();
  });

  test("adjacency: A01 at the cap calling swarm_transition gets the depth reason", () => {
    const res = guardToolCall(call("swarm_transition", { task_id: "T-1", to: "DONE" }), capped);
    expect(res?.reason?.startsWith(DEPTH_PREFIX)).toBe(true);
  });

  test("pure: repeated calls give equal results and leave event and facts unchanged", () => {
    const events = [call("bash", { command: "ls" }), yieldData({ state: "BLOCKED", needs: ["depth"] }), call("swarm_ingest", {})];
    const facts: GuardFacts = { ...capped, env: { SWARM_AGENT: A01 } };
    const before = JSON.stringify({ events, facts });
    for (const event of events) expect(guardToolCall(event, facts)).toEqual(guardToolCall(event, facts));
    expect(JSON.stringify({ events, facts })).toBe(before);
  });

  test("registered handler: A01 without task reproduces the pure decision", () => {
    const { pi, run } = guardHandler({ activeTools: ["read", "bash", "yield"] });
    const events = [
      call("bash", { command: "ls" }),
      call("swarm_transition", {}),
      yieldData({ state: "IN_REVIEW" }),
      yieldData({ state: "BLOCKED", needs: "depth" }),
    ];
    for (const event of events) expect(run(event, a01Ctx())).toEqual(guardToolCall(event, capped));
    expect(run(call("bash", {}), a01Ctx())).toEqual({ block: true, reason: expect.stringMatching(/^BLOCKED needs: depth/) });
    expect(pi.runtimeCalls.length).toBe(events.length + 1);
  });

  test("registered handler: A01 with task, as a restricted child or in plan mode is not capped", () => {
    const withTask = guardHandler({ activeTools: ["read", "task", "yield"] });
    expect(withTask.run(call("bash", {}), a01Ctx())).toBeUndefined();

    const noTask = guardHandler({ activeTools: ["read", "yield"] });
    expect(noTask.run(call("bash", {}), agentCtx(CWD, A01))).toBeUndefined(); // restrictToolNames: true
    expect(noTask.run(call("bash", {}), a01Ctx([{ type: "mode_change", mode: "plan" }]))).toBeUndefined();
  });

  test("registered handler: headless A01 (no session_init, SWARM_AGENT) is capped from its env identity", () => {
    process.env.SWARM_AGENT = A01;
    const { run } = guardHandler({ activeTools: ["read"] });
    expect((run(call("bash", {}), fakeCtx(CWD)) as { reason?: string } | undefined)?.reason?.startsWith(DEPTH_PREFIX)).toBe(true);
  });
});

describe("fail", () => {
  const throwing = () => {
    throw new Error("active tools unavailable");
  };
  /** A ctx whose session log reads session_init fine but whose branch read (plan mode) throws. */
  const brokenBranch = (entries: SessionEntry[]): ExtensionContext => ({
    cwd: CWD,
    sessionManager: {
      getEntries: () => entries,
      getBranch: () => {
        throw new Error("branch unreadable");
      },
    },
  });

  test("getActiveTools is called only when the resolved agent is a01-orchestrator", () => {
    const { pi, run } = guardHandler({ activeTools: ["task"] });
    run(call("bash", {}), agentCtx(CWD, "a05-backend", [], false));
    run(call("bash", {}), agentCtx(CWD, "task", [], false));
    run(call("bash", {}), fakeCtx(CWD));
    expect(pi.runtimeCalls).toEqual([]);
    run(call("bash", {}), a01Ctx());
    expect(pi.runtimeCalls).toEqual(["getActiveTools"]);
  });

  test("a throwing getActiveTools for A01 blocks with the guard-error reason", () => {
    const { run } = guardHandler({ activeTools: throwing });
    const res = run(yieldData({ state: "BLOCKED", needs: "depth" }), a01Ctx()) as { block?: boolean; reason?: string };
    expect(res.block).toBe(true);
    expect(res.reason?.startsWith(GUARD_ERROR_PREFIX)).toBe(true);
    expect(res.reason).toContain("active tools unavailable");
  });

  test("a throwing getActiveTools never reaches the main session: its call passes", () => {
    const { pi, run } = guardHandler({ activeTools: throwing });
    expect(run(call("bash", { command: "ls" }), fakeCtx(CWD))).toBeUndefined();
    expect(pi.runtimeCalls).toEqual([]);
  });

  test.each([
    ["the main session", [] as SessionEntry[]],
    ["a generic task child", [{ type: "session_init", agent: "task", restrictToolNames: false }]],
  ])("a guard throw in %s returns undefined", (_name, entries) => {
    const { run } = guardHandler({ activeTools: ["task"] });
    expect(run(call("bash", { command: "ls" }), brokenBranch(entries))).toBeUndefined();
  });

  test("a guard throw with an unreadable session log returns undefined (swarm marker not established)", () => {
    process.env.SWARM_TASK_ID = "T-1";
    const { run } = guardHandler({ activeTools: ["task"] });
    const unreadable: ExtensionContext = { cwd: CWD, sessionManager: { getEntries: throwing, getBranch: throwing } };
    expect(run(call("bash", {}), unreadable)).toBeUndefined();
  });

  test.each([
    ["a swarm specialist", [{ type: "session_init", agent: "a05-backend", restrictToolNames: false }], undefined],
    ["a headless swarm session (SWARM_TASK_ID)", [] as SessionEntry[], "T-1"],
  ])("a guard throw in %s blocks with the guard-error reason", (_name, entries, taskId) => {
    if (taskId !== undefined) process.env.SWARM_TASK_ID = taskId;
    const { run } = guardHandler({ activeTools: ["task"] });
    const res = run(call("read", { path: "x" }), brokenBranch(entries)) as { block?: boolean; reason?: string };
    expect(res.block).toBe(true);
    expect(res.reason?.startsWith(GUARD_ERROR_PREFIX)).toBe(true);
    expect(res.reason).toContain("branch unreadable");
  });
});
