/**
 * `/swarm <brief>` (ORCH-03, D-11): the ordered plan → dispatch → wait → report log through the registered command,
 * with a recording bridge; usage / plan-mode / conflict paths never dispatch.
 */
import { expect, test } from "bun:test";
import { type Bridge, type BridgeRequest, type BridgeResult, runPy, SwarmToolError } from "../src/bridge.ts";
import { parseSwarmArgs, swarmCommand, swarmCorrelationId } from "../src/commands.ts";
import { DISPATCH_MARKER } from "../src/hooks.ts";
import { createSwarmExtension } from "../src/index.ts";
import type { SessionEntry } from "../src/omp-api.ts";
import { type CallLog, callTool, commandCtx, fakeCtx, fakePi, fakeTurn, gitRepo, isolateEnv, type ScriptedTurn, tmpDir } from "./helpers.ts";

isolateEnv("SWARM_DIR", "SWARM_AGENT");

const CORR = "c0ffee01-2222-4333-8444-555555555555";
const BRIEF = "add a /health endpoint";
const BRIEF_CORR = swarmCorrelationId({ brief: BRIEF, pattern: "feature", risk_class: "medium" });
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
/** The task ids in a swarm_status result. */
function statusTaskIds(details: unknown): string[] {
  if (!details || typeof details !== "object" || !("tasks" in details) || !Array.isArray(details.tasks)) throw new Error("no tasks in status");
  return details.tasks.map((t: unknown) => {
    if (!t || typeof t !== "object" || !("task_id" in t) || typeof t.task_id !== "string") throw new Error("task without task_id");
    return t.task_id;
  });
}

test("dispatch order: bridge orch_plan with the shared argv, then sendUserMessage, then waitForIdle, then notify", async () => {
  const repo = gitRepo();
  const { log, swarm } = session();
  await swarm.handler("add a /health endpoint", commandCtx(repo, [], { log }));

  expect(calls(log)).toEqual(["bridge", "sendUserMessage", "waitForIdle", "notify"]);
  const req = bridgeReq(log);
  expect(req?.script).toBe("orch_plan");
  expect(req?.cwd).toBe(repo);
  expect(req?.args).toEqual([`--root=${repo}`, `--brief-text=${BRIEF}`, "--pattern=feature", "--risk-class=medium", `--correlation-id=${BRIEF_CORR}`]);

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

/** The dispatch user message as omp records it on the branch once the turn starts. */
const DISPATCH_ENTRY: SessionEntry = {
  type: "message",
  message: { role: "user", content: [{ type: "text", text: `${DISPATCH_MARKER} AgentSwarm plan ${CORR} is ready` }] },
};

/** A session whose fake sendUserMessage starts a scripted turn; `opts` reach swarmCommand's DI options directly. */
function turnSession(script: ScriptedTurn, opts?: Parameters<typeof swarmCommand>[2]) {
  const log: CallLog = [];
  const entries: SessionEntry[] = [];
  const turn = fakeTurn(script, entries, log);
  const bridge: Bridge = async (req) => {
    log.push({ call: "bridge", req });
    return PLAN;
  };
  const pi = fakePi({
    sendUserMessage: (text) => {
      log.push({ call: "sendUserMessage", text });
      turn.start();
    },
  });
  pi.load(createSwarmExtension({ bridge }));
  const swarm = opts ? swarmCommand(pi.api, bridge, opts) : pi.command("swarm");
  const ctx = commandCtx(gitRepo(), entries, { log, isIdle: turn.isIdle });
  return { log, turn, swarm, ctx };
}

test("async start: the handler resolves only after the turn that sendUserMessage started asynchronously has ended", async () => {
  const { log, turn, swarm, ctx } = turnSession({ startMs: 30, durationMs: 50 });
  const t0 = Date.now();
  let settled = false;
  const run = swarm.handler(BRIEF, ctx).then(() => void (settled = true));

  await Bun.sleep(60); // the fake turn is streaming (30 ms → 80 ms)
  expect(turn.ended()).toBe(false);
  expect(settled).toBe(false);

  await run;
  expect(Date.now() - t0).toBeGreaterThanOrEqual(80);
  expect(turn.ended()).toBe(true);
  expect(calls(log)).toEqual(["bridge", "sendUserMessage", "turn-start", "waitForIdle", "turn-end", "notify"]);
  expect(log.at(-1)?.message).toContain(`plan ${CORR} dispatched`);
});

test("branch entry only: a turn that runs between two polls is seen through the dispatch message on the branch", async () => {
  const { log, swarm, ctx } = turnSession({ entryMs: 20, entry: DISPATCH_ENTRY });
  await swarm.handler(BRIEF, ctx);
  expect(calls(log)).toEqual(["bridge", "sendUserMessage", "waitForIdle", "notify"]);
  expect(log.at(-1)?.level).toBe("info");
});

test("branch entry: a dispatch message that was already on the branch does not count as this turn's start", async () => {
  const { log, swarm, ctx } = turnSession({}, { startTimeoutMs: 100 });
  ctx.sessionManager.getBranch().push(DISPATCH_ENTRY); // an earlier /swarm of the same brief
  await swarm.handler(BRIEF, ctx);
  expect(calls(log)).toEqual(["bridge", "sendUserMessage", "notify"]);
  expect(log.at(-1)?.level).toBe("error");
});

test("never starts: after the start cap the handler reports the failure at level error and never reports success", async () => {
  const { log, swarm, ctx } = turnSession({}, { startTimeoutMs: 100 });
  const t0 = Date.now();
  await swarm.handler(BRIEF, ctx);
  expect(Date.now() - t0).toBeLessThan(1000);
  expect(calls(log)).toEqual(["bridge", "sendUserMessage", "notify"]);
  const last = log.at(-1);
  expect(last?.level).toBe("error");
  expect(String(last?.message)).toContain("dispatch did not start");
  expect(String(last?.message)).toContain(CORR);
  expect(log.some((e) => String(e.message ?? "").includes("dispatched"))).toBe(false);
});

test("sync start: isIdle already false on the first poll goes straight to waitForIdle", async () => {
  const repo = gitRepo();
  const { log, swarm } = session();
  let startPolls = 0;
  const ctx = commandCtx(repo, [], {
    log,
    // streaming until waitForIdle is called, which ends the turn on its first poll
    isIdle: () => {
      if (log.some((e) => e.call === "waitForIdle")) return true;
      startPolls++;
      return false;
    },
  });
  await swarm.handler(BRIEF, ctx);
  expect(calls(log)).toEqual(["bridge", "sendUserMessage", "waitForIdle", "notify"]);
  expect(startPolls).toBe(1);
});

test.each([
  ["--pattern=hotfix --risk=high fix login", "fix login", "hotfix", "high"],
  ["fix login --risk=low", "fix login", "feature", "low"],
  ["migrate  the   users table --pattern=dependency", "migrate  the   users table", "dependency", "medium"],
])("flags: %s → brief %j, pattern %s, risk %s", async (args, brief, pattern, risk) => {
  const repo = gitRepo();
  const { log, swarm } = session();
  await swarm.handler(args, commandCtx(repo, [], { log }));
  const corr = swarmCorrelationId({ brief, pattern: pattern as "feature", risk_class: risk as "low" });
  expect(bridgeReq(log)?.args).toEqual([`--root=${repo}`, `--brief-text=${brief}`, `--pattern=${pattern}`, `--risk-class=${risk}`, `--correlation-id=${corr}`]);
  expect(calls(log)).toContain("sendUserMessage");
});

test("correlation id: a uuid5 fixed by (pattern, risk, brief); any input change re-keys it", () => {
  const base = { brief: BRIEF, pattern: "feature", risk_class: "medium" } as const;
  expect(BRIEF_CORR).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(swarmCorrelationId({ ...base })).toBe(BRIEF_CORR);
  const variants = [
    swarmCorrelationId({ ...base, brief: `${BRIEF}!` }),
    swarmCorrelationId({ ...base, pattern: "hotfix" }),
    swarmCorrelationId({ ...base, risk_class: "high" }),
  ];
  expect(new Set([BRIEF_CORR, ...variants]).size).toBe(4);
  // flags never change the normalized brief, so their placement never changes the id
  const a = parseSwarmArgs(`--risk=high ${BRIEF}`);
  const b = parseSwarmArgs(`${BRIEF} --risk=high`);
  if ("error" in a || "error" in b) throw new Error("usage error");
  expect(swarmCorrelationId(a)).toBe(swarmCorrelationId(b));
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
  await callTool(one.tool("swarm_plan"), { brief: BRIEF, pattern: "feature", risk_class: "medium", correlation_id: BRIEF_CORR }, fakeCtx(repo));
  await two.command("swarm").handler("add a /health endpoint", commandCtx(repo));

  expect(reqs).toHaveLength(3);
  expect(reqs[0].inflight).toBeInstanceOf(Set);
  expect(reqs[0].inflight).toBe(reqs[1].inflight);
  expect(reqs[2].inflight).toBeInstanceOf(Set);
  expect(reqs[2].inflight).not.toBe(reqs[0].inflight);
  expect(reqs[0].args).toEqual(reqs[1].args);
});

test("real orch_plan: the same brief reuses one plan; a different brief gets its own correlation", async () => {
  const repo = gitRepo();
  process.env.SWARM_DIR = tmpDir("swarm-omp-dir-");
  const log: CallLog = [];
  const pi = fakePi({ sendUserMessage: (text) => void log.push({ call: "sendUserMessage", text }) });
  pi.load(createSwarmExtension({ bridge: runPy }));
  const swarm = pi.command("swarm");
  const ctx = commandCtx(repo, [], { log, hasUI: false });

  await swarm.handler(BRIEF, ctx);
  await swarm.handler(`  ${BRIEF} --risk=medium `, ctx);
  await swarm.handler("fix the login bug --pattern=hotfix", ctx);
  const texts = log.filter((e) => e.call === "sendUserMessage").map((e) => String(e.text));
  expect(texts).toHaveLength(3);

  const [first, again, other] = texts.map(payload);
  expect(first.correlation_id).toBe(BRIEF_CORR);
  expect(again).toEqual(first);
  expect(texts[0]).toContain(`is ready: 13 tasks`);
  expect(texts[1]).toContain(`is reused: 13 tasks`);
  expect(other.correlation_id).not.toBe(BRIEF_CORR);
  expect(other.ready_tasks).toHaveLength(1);

  // one plan for the brief in the store: the second run created nothing
  const mine = statusTaskIds((await callTool(pi.tool("swarm_status"), { correlation_id: BRIEF_CORR }, fakeCtx(repo))).details);
  expect(mine).toHaveLength(13);
  expect(mine).toContain(first.ready_tasks[0]);
  const others = statusTaskIds((await callTool(pi.tool("swarm_status"), { correlation_id: other.correlation_id }, fakeCtx(repo))).details);
  expect(others).toHaveLength(8);
  expect(others).toContain(other.ready_tasks[0]);
});
