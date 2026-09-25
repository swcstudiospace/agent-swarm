/** Plan-mode detection and identity from the session log (D-03, TOOL-05), plus the mutating-tool refusal. */
import { expect, test } from "bun:test";
import { type Bridge, SwarmToolError } from "../src/bridge.ts";
import { inPlanMode, sessionAgent } from "../src/context.ts";
import type { SessionEntry } from "../src/omp-api.ts";
import { buildTools } from "../src/tools.ts";
import { callTool, fakeCtx } from "./helpers.ts";

const model: SessionEntry = { type: "model_change", model: "m" };
const thinking: SessionEntry = { type: "thinking_level_change", level: "low" };
const planCtx: SessionEntry = { type: "custom_message", customType: "plan-mode-context", content: "plan mode" };
const otherCustom: SessionEntry = { type: "custom_message", customType: "other-context", content: "x" };
const user: SessionEntry = { type: "message", message: { role: "user", content: "go" } };
const assistant: SessionEntry = { type: "message", message: { role: "assistant", content: "ok" } };
const toolResult: SessionEntry = { type: "message", message: { role: "toolResult", content: "r" } };

const planned = (entries: SessionEntry[]) => inPlanMode(fakeCtx("/tmp", entries));

test("plan mode: S2 top-level branch shape", () => {
  expect(planned([model, thinking, planCtx, user, assistant])).toBe(true);
});

test("plan mode: mode_change plan decides", () => {
  expect(planned([model, thinking, { type: "mode_change", mode: "plan" }, user])).toBe(true);
});

test("plan mode: mode_change none decides", () => {
  expect(planned([model, planCtx, user, assistant, { type: "mode_change", mode: "none" }, user])).toBe(false);
});

test("plan mode: no plan entries", () => {
  expect(planned([model, thinking, otherCustom, user, assistant, toolResult])).toBe(false);
});

test("plan mode: mid-turn steer after the latest user message", () => {
  expect(planned([model, user, assistant, toolResult, planCtx, assistant])).toBe(true);
});

test("plan mode: plan-mode-context inside the custom_message run before the user message", () => {
  expect(planned([model, planCtx, otherCustom, user, assistant])).toBe(true);
});

test("plan mode: exit — an old plan-mode-context before an earlier user message", () => {
  expect(planned([model, planCtx, user, assistant, user, assistant])).toBe(false);
});

test("plan mode: empty branch", () => {
  expect(planned([])).toBe(false);
});

test("plan mode reads the branch, not every entry", () => {
  const ctx = { cwd: "/tmp", sessionManager: { getEntries: () => [planCtx, user], getBranch: () => [user, assistant] } };
  expect(inPlanMode(ctx)).toBe(false);
});

test("sessionAgent: session_init names the agent and the restriction", () => {
  const init: SessionEntry = { type: "session_init", agent: "a01-orchestrator", restrictToolNames: true, tools: [] };
  expect(sessionAgent(fakeCtx("/tmp", [model, init, user]))).toEqual({ agent: "a01-orchestrator", restricted: true });
});

test("sessionAgent: no session_init is the unrestricted main session", () => {
  expect(sessionAgent(fakeCtx("/tmp", [model, user]))).toEqual({ agent: undefined, restricted: false });
});

test("sessionAgent: restrictToolNames must be exactly true", () => {
  const init: SessionEntry = { type: "session_init", agent: "a08-qa", restrictToolNames: "yes" };
  expect(sessionAgent(fakeCtx("/tmp", [init]))).toEqual({ agent: "a08-qa", restricted: false });
});

function recordingTools() {
  const calls: unknown[] = [];
  const bridge: Bridge = async (req) => {
    calls.push(req);
    return { exitCode: 0, json: { status: "ok", summary: "ok" }, swarmDir: "/tmp/none" };
  };
  return { calls, tools: buildTools(bridge) };
}

test("wrapper: a mutating tool refuses in plan mode with 0 bridge calls", async () => {
  const { calls, tools } = recordingTools();
  const transition = tools.find((t) => t.name === "swarm_transition");
  if (!transition) throw new Error("swarm_transition not built");
  const ctx = fakeCtx("/tmp", [model, planCtx, user]);
  const err = await callTool(transition, { task_id: "T-1", state: "CLAIMED", reason: "lease" }, ctx).catch((e) => e);
  expect(err).toBeInstanceOf(SwarmToolError);
  expect((err as SwarmToolError).code).toBe("E-POLICY");
  expect((err as Error).message).toContain("plan mode");
  expect(calls).toHaveLength(0);
});

test("wrapper: swarm_status is not mutating and still runs in plan mode", async () => {
  const { calls, tools } = recordingTools();
  const status = tools.find((t) => t.name === "swarm_status");
  if (!status) throw new Error("swarm_status not built");
  const res = await callTool(status, {}, fakeCtx("/tmp", [model, planCtx, user]));
  expect(res.content[0].text).toBe("ok");
  expect(calls).toHaveLength(1);
});
