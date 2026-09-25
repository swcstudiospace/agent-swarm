/**
 * `/swarm <brief>` (ORCH-03, D-11): the ordered plan → dispatch → wait → report log through the registered command,
 * with a recording bridge; usage / plan-mode / conflict paths never dispatch.
 */
import { expect, test } from "bun:test";
import { type Bridge, type BridgeRequest, type BridgeResult, SwarmToolError } from "../src/bridge.ts";
import { parseSwarmArgs } from "../src/commands.ts";
import { DISPATCH_MARKER } from "../src/hooks.ts";
import { createSwarmExtension } from "../src/index.ts";
import type { SessionEntry } from "../src/omp-api.ts";
import { type CallLog, callTool, commandCtx, fakeCtx, fakePi, gitRepo, isolateEnv } from "./helpers.ts";

isolateEnv("SWARM_DIR", "SWARM_AGENT");

const CORR = "c0ffee01-2222-4333-8444-555555555555";
const PLAN: BridgeResult = {
  exitCode: 0,
  json: {
    status: "ok",
    correlation_id: CORR,
    pattern: "feature",
    tasks: [
      { task_id: "Tc0ff-req", agent_id: "a02", depends_on: [] },
      { task_id: "Tc0ff-arch", agent_id: "a03", depends_on: ["Tc0ff-req"] },
      { task_id: "Tc0ff-be", agent_id: "a05", depends_on: ["Tc0ff-arch", "Tc0ff-req"] },
    ],
    summary: `created 3 tasks for correlation ${CORR}\nTc0ff-req a02 d=0 ← -`,
  },
  swarmDir: "",
};
const HINT = `prefix Tc0ff already used by correlation ${CORR} with a different brief; pass a new --correlation-id or --prefix for this brief`;
const PLAN_MODE: SessionEntry[] = [{ type: "mode_change", mode: "plan" }];

/** A session through the factory: one ordered log shared by the bridge, sendUserMessage, notify and waitForIdle. */
function session(outcome: BridgeResult | Error = PLAN) {
  const log: CallLog = [];
  const bridge: Bridge = async (req) => {
    log.push({ call: "bridge", req });
    if (outcome instanceof Error) throw outcome;
    return outcome;
  };
  const pi = fakePi({ sendUserMessage: (text) => void log.push({ call: "sendUserMessage", text }) });
  pi.load(createSwarmExtension({ bridge }));
  return { log, pi, swarm: pi.command("swarm") };
}

const calls = (log: CallLog) => log.map((e) => e.call);
const bridgeReq = (log: CallLog) => log.find((e) => e.call === "bridge")?.req as BridgeRequest | undefined;
const dispatchText = (log: CallLog) => log.find((e) => e.call === "sendUserMessage")?.text as string | undefined;
/** The task.assign payload line of the dispatch prompt (the one JSON object line). */
function payload(text: string): Record<string, unknown> {
  const line = text.split("\n").find((l) => l.startsWith("{"));
  if (!line) throw new Error(`no payload line in:\n${text}`);
  return JSON.parse(line);
}

test("dispatch order: bridge orch_plan with the shared argv, then sendUserMessage, then waitForIdle, then notify", async () => {
  const repo = gitRepo();
  const { log, swarm } = session();
  await swarm.handler("add a /health endpoint", commandCtx(repo, [], { log }));

  expect(calls(log)).toEqual(["bridge", "sendUserMessage", "waitForIdle", "notify"]);
  const req = bridgeReq(log);
  expect(req?.script).toBe("orch_plan");
  expect(req?.cwd).toBe(repo);
  expect(req?.args).toEqual([`--root=${repo}`, "--brief-text=add a /health endpoint", "--pattern=feature", "--risk-class=medium"]);

  const text = dispatchText(log) ?? "";
  expect(text.startsWith(DISPATCH_MARKER)).toBe(true);
  expect(text).toContain("a01-orchestrator");
  expect(text).toContain("task");
  expect(payload(text)).toEqual({ correlation_id: CORR, capability: "plan.execute", ready_tasks: ["Tc0ff-req"] });
  expect(log.at(-1)?.message).toContain(CORR);
});

test("no UI: same order without notify; the plan summary travels in the dispatch text", async () => {
  const repo = gitRepo();
  const { log, swarm } = session();
  await swarm.handler("add a /health endpoint", commandCtx(repo, [], { log, hasUI: false }));
  expect(calls(log)).toEqual(["bridge", "sendUserMessage", "waitForIdle"]);
  expect(dispatchText(log)).toContain(`created 3 tasks for correlation ${CORR}`);
});

test.each([
  ["--pattern=hotfix --risk=high fix login", "fix login", "hotfix", "high"],
  ["fix login --risk=low", "fix login", "feature", "low"],
  ["migrate  the   users table --pattern=dependency", "migrate  the   users table", "dependency", "medium"],
])("flags: %s → brief %j, pattern %s, risk %s", async (args, brief, pattern, risk) => {
  const repo = gitRepo();
  const { log, swarm } = session();
  await swarm.handler(args, commandCtx(repo, [], { log }));
  expect(bridgeReq(log)?.args).toEqual([`--root=${repo}`, `--brief-text=${brief}`, `--pattern=${pattern}`, `--risk-class=${risk}`]);
  expect(calls(log)).toContain("sendUserMessage");
});

test.each(["--pattern=bogus x", "--risk=urgent x", "--pattern= x", "", "   ", "--risk=low", "--pattern=hotfix --risk=high"])(
  "usage: %j notifies usage with no bridge call and no dispatch",
  async (args) => {
    const repo = gitRepo();
    const { log, swarm } = session();
    await swarm.handler(args, commandCtx(repo, [], { log }));
    expect(calls(log)).toEqual(["notify"]);
    expect(String(log[0].message).toLowerCase()).toContain("usage");
    expect(String(log[0].message)).toContain("/swarm <brief>");
  },
);

test("parseSwarmArgs: flags anywhere are removed from the brief; the last repeated flag wins", () => {
  expect(parseSwarmArgs("--risk=low ship it --pattern=hotfix")).toEqual({ brief: "ship it", pattern: "hotfix", risk_class: "low" });
  expect(parseSwarmArgs("--risk=low --risk=high ship it")).toEqual({ brief: "ship it", pattern: "feature", risk_class: "high" });
  expect(parseSwarmArgs("keep --pattern-ish text")).toEqual({ brief: "keep --pattern-ish text", pattern: "feature", risk_class: "medium" });
  expect(parseSwarmArgs("--pattern=custom x")).toHaveProperty("error");
});

test("plan mode: E-POLICY refusal, no bridge call, no dispatch", async () => {
  const repo = gitRepo();
  const { log, swarm } = session();
  await swarm.handler("add a /health endpoint", commandCtx(repo, PLAN_MODE, { log }));
  expect(calls(log)).toEqual(["notify"]);
  expect(String(log[0].message)).toContain("E-POLICY");
  expect(String(log[0].message)).toContain("plan mode");
});

test.each<[string, BridgeResult | Error]>([
  ["thrown E-CONTRACT", new SwarmToolError("E-CONTRACT", HINT)],
  ["exit 2 result", { exitCode: 2 as unknown as 0, json: { error: { code: "E-CONTRACT", message: HINT } }, swarmDir: "" }],
])("conflict (%s): the hint is notified and nothing is dispatched or awaited", async (_name, outcome) => {
  const repo = gitRepo();
  const { log, swarm } = session(outcome);
  await swarm.handler("add a /health endpoint", commandCtx(repo, [], { log }));
  expect(calls(log)).toEqual(["bridge", "notify"]);
  expect(String(log[1].message)).toContain("pass a new --correlation-id or --prefix");
});

test.each<[string, Error | BridgeResult]>([
  ["E-INPUT", new SwarmToolError("E-INPUT", "orch_plan usage error")],
  ["E-DEP", new SwarmToolError("E-DEP", "python3 not found")],
  ["plain error", new Error("spawn failed")],
  ["exit 1 fail", { exitCode: 1, json: { status: "fail", summary: "store locked" }, swarmDir: "" }],
])("failure (%s): notified, never dispatched", async (_name, outcome) => {
  const repo = gitRepo();
  const { log, swarm } = session(outcome);
  await swarm.handler("add a /health endpoint", commandCtx(repo, [], { log }));
  expect(calls(log)).toEqual(["bridge", "notify"]);
});

test("reused plan: an identical brief dispatches normally", async () => {
  const repo = gitRepo();
  const { log, swarm } = session({ ...PLAN, json: { ...PLAN.json, reused: true, summary: `reused existing plan for correlation ${CORR}` } });
  await swarm.handler("add a /health endpoint", commandCtx(repo, [], { log }));
  expect(calls(log)).toEqual(["bridge", "sendUserMessage", "waitForIdle", "notify"]);
  expect(payload(dispatchText(log) ?? "").ready_tasks).toEqual(["Tc0ff-req"]);
});

test("ready ids: exactly the tasks whose depends_on is empty, in result order", async () => {
  const repo = gitRepo();
  const tasks = [
    { task_id: "X-a", depends_on: ["X-c"] },
    { task_id: "X-b", depends_on: [] },
    { task_id: "X-c", depends_on: [] },
    { task_id: "X-d" },
  ];
  const { log, swarm } = session({ ...PLAN, json: { ...PLAN.json, tasks } });
  await swarm.handler("add a /health endpoint", commandCtx(repo, [], { log }));
  expect(payload(dispatchText(log) ?? "").ready_tasks).toEqual(["X-b", "X-c"]);
});

test("scoped bridge: the command and swarm_plan share one session's inflight set; another session has its own", async () => {
  const repo = gitRepo();
  const reqs: BridgeRequest[] = [];
  const bridge: Bridge = async (req) => {
    reqs.push(req);
    return PLAN;
  };
  const one = fakePi({ sendUserMessage: () => {} });
  const two = fakePi({ sendUserMessage: () => {} });
  one.load(createSwarmExtension({ bridge }));
  two.load(createSwarmExtension({ bridge }));

  await one.command("swarm").handler("add a /health endpoint", commandCtx(repo));
  await callTool(one.tool("swarm_plan"), { brief: "add a /health endpoint", pattern: "feature", risk_class: "medium" }, fakeCtx(repo));
  await two.command("swarm").handler("add a /health endpoint", commandCtx(repo));

  expect(reqs).toHaveLength(3);
  expect(reqs[0].inflight).toBeInstanceOf(Set);
  expect(reqs[0].inflight).toBe(reqs[1].inflight);
  expect(reqs[2].inflight).toBeInstanceOf(Set);
  expect(reqs[2].inflight).not.toBe(reqs[0].inflight);
  expect(reqs[0].args).toEqual(reqs[1].args);
});
