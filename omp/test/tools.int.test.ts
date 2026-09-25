/** Integration: tools → real bridge → real python scripts → tmp Task Store (never the repo's .swarm/). */
import { expect, test } from "bun:test";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { type Bridge, type BridgeRequest, runPy, SwarmToolError } from "../src/bridge.ts";
import { createSwarmExtension } from "../src/index.ts";
import type { ExtensionContext, SessionEntry } from "../src/omp-api.ts";
import { callTool, type FakePi, fakeCtx, fakePi, gitRepo, isolateEnv, runPython, tmpDir } from "./helpers.ts";

isolateEnv("SWARM_DIR", "SWARM_ROOT", "SWARM_TASK_ID", "SWARM_CORRELATION_ID", "SWARM_AGENT_SESSION");

/** All tools over the real bridge, counting bridge calls. */
function swarm() {
  const calls: BridgeRequest[] = [];
  const bridge: Bridge = (req) => {
    calls.push(req);
    return runPy(req);
  };
  const pi = fakePi();
  createSwarmExtension({ bridge })(pi.api);
  return { calls, tool: pi.tool };
}

/** A tmp git repo with its own tmp SWARM_DIR (exported, as the headless runner does). */
function tmpStore(): { repo: string; sdir: string; ctx: ExtensionContext } {
  const repo = gitRepo();
  const sdir = tmpDir("swarm-omp-dir-");
  process.env.SWARM_DIR = sdir;
  return { repo, sdir, ctx: fakeCtx(repo) };
}

type StatusRow = { task_id: string; state: string; ready: boolean };

async function statusRows(tool: FakePi["tool"], ctx: ExtensionContext, corr: string): Promise<StatusRow[]> {
  const res = await callTool(tool("swarm_status"), { correlation_id: corr }, ctx);
  return (res.details as { tasks: StatusRow[] }).tasks;
}

function stateOf(rows: StatusRow[], id: string): string | undefined {
  return rows.find((r) => r.task_id === id)?.state;
}

async function rejection(p: Promise<unknown>): Promise<SwarmToolError> {
  const err = await p.then(
    () => undefined,
    (e: unknown) => e,
  );
  if (!(err instanceof SwarmToolError)) throw new Error(`expected a SwarmToolError, got ${String(err)}`);
  return err;
}

const HOTFIX = { brief: "fix the login bug", pattern: "hotfix", risk_class: "low", prefix: "H", correlation_id: "c-hot" };

/** Lease a task along the legal path through swarm_transition. */
async function lease(tool: FakePi["tool"], ctx: ExtensionContext, id: string): Promise<void> {
  for (const state of ["CLAIMED", "IN_PROGRESS"]) {
    await callTool(tool("swarm_transition"), { task_id: id, state, reason: "lease" }, ctx);
  }
}

const RESULT = {
  task_id: "H-patch",
  state: "IN_REVIEW",
  outputs: [{ kind: "code.patch", uri: "file://patch.diff", version: "1", digest: "" }],
  summary_md: "patched",
};

test("plan dry: 13/8/8 tasks per pattern", async () => {
  const { ctx } = tmpStore();
  const { tool } = swarm();
  for (const [pattern, n] of [["feature", 13], ["hotfix", 8], ["dependency", 8]] as const) {
    const res = await callTool(tool("swarm_plan"), { brief: "b", pattern, risk_class: "medium", dry_run: true }, ctx);
    const details = res.details as { dry_run: boolean; tasks: unknown[] };
    expect(details.dry_run).toBe(true);
    expect(details.tasks).toHaveLength(n);
  }
});

test("plan real + reuse", async () => {
  const { ctx } = tmpStore();
  const { tool } = swarm();
  const params = { ...HOTFIX, priority: "P1", acceptance: ["login works", "no regression"] };
  const first = (await callTool(tool("swarm_plan"), params, ctx)).details as { reused?: boolean; tasks: unknown[] };
  expect(first.reused).toBeUndefined();
  expect(first.tasks).toHaveLength(8);
  const rows = await statusRows(tool, ctx, "c-hot");
  expect(rows).toHaveLength(8);
  expect(rows.every((r) => r.state === "PLANNED")).toBe(true);
  expect(rows.find((r) => r.task_id === "H-rca")?.ready).toBe(true);

  const again = (await callTool(tool("swarm_plan"), params, ctx)).details as { reused?: boolean; tasks: unknown[] };
  expect(again.reused).toBe(true);
  expect(await statusRows(tool, ctx, "c-hot")).toHaveLength(8);
});

test("plan dash brief is passed intact", async () => {
  const { ctx } = tmpStore();
  const { tool } = swarm();
  const brief = "--pattern=feature --plan=/etc/passwd";
  const res = await callTool(tool("swarm_plan"), { ...HOTFIX, brief }, ctx);
  const tasks = (res.details as { pattern: string; tasks: Array<{ notes: { brief_excerpt: string } }> });
  expect(tasks.pattern).toBe("hotfix");
  expect(tasks.tasks).toHaveLength(8);
  expect(tasks.tasks[0].notes.brief_excerpt).toBe(brief);
});

test("ingest a leased task's IN_REVIEW result", async () => {
  const { ctx, sdir } = tmpStore();
  const { tool } = swarm();
  await callTool(tool("swarm_plan"), HOTFIX, ctx);
  await lease(tool, ctx, "H-patch");
  expect(stateOf(await statusRows(tool, ctx, "c-hot"), "H-patch")).toBe("IN_PROGRESS");

  const res = await tool("swarm_ingest").execute("toolu_01/../x y", { task_id: "H-patch", result: RESULT }, undefined, undefined, ctx);
  expect(res.content[0].text).toContain("H-patch → IN_REVIEW");
  expect(stateOf(await statusRows(tool, ctx, "c-hot"), "H-patch")).toBe("IN_REVIEW");
  expect(readdirSync(join(sdir, "results"))).toEqual(["ingest-toolu_01_.._x_y.json"]);
  expect(JSON.parse(readFileSync(join(sdir, "results", "ingest-toolu_01_.._x_y.json"), "utf8"))).toEqual(RESULT);
});

test("ingest malformed result rejects with the python taxonomy code", async () => {
  const { ctx } = tmpStore();
  const { tool } = swarm();
  await callTool(tool("swarm_plan"), HOTFIX, ctx);
  await lease(tool, ctx, "H-patch");
  const err = await rejection(callTool(tool("swarm_ingest"), { task_id: "H-patch", result: { task_id: "H-patch" } }, ctx));
  // flagged assumption (03-02): validate_result's "missing state" is observed as E-CONTRACT
  expect(err.code).toBe("E-CONTRACT");
  expect(err.message).toContain("/state: required");
  expect(stateOf(await statusRows(tool, ctx, "c-hot"), "H-patch")).toBe("IN_PROGRESS");
});

test("transition illegal rejects E-CONTRACT and leaves the state", async () => {
  const { ctx } = tmpStore();
  const { tool } = swarm();
  await callTool(tool("swarm_plan"), HOTFIX, ctx);
  const err = await rejection(callTool(tool("swarm_transition"), { task_id: "H-rca", state: "DONE", reason: "skip" }, ctx));
  expect(err.code).toBe("E-CONTRACT");
  expect(err.message).toContain("illegal transition PLANNED → DONE");
  expect(stateOf(await statusRows(tool, ctx, "c-hot"), "H-rca")).toBe("PLANNED");
});

test("transition dry run writes nothing", async () => {
  const { ctx } = tmpStore();
  const { tool } = swarm();
  await callTool(tool("swarm_plan"), HOTFIX, ctx);
  const res = await callTool(tool("swarm_transition"), { task_id: "H-rca", state: "CLAIMED", reason: "x", dry_run: true }, ctx);
  expect(res.content[0].text).toContain("dry-run: would transition H-rca → CLAIMED");
  expect(stateOf(await statusRows(tool, ctx, "c-hot"), "H-rca")).toBe("PLANNED");
});

const PLAN_MODE: SessionEntry[] = [
  { type: "model_change" },
  { type: "custom_message", customType: "plan-mode-context" },
  { type: "message", message: { role: "user" } },
];

test("plan mode store unchanged", async () => {
  const { repo, sdir } = tmpStore();
  const setup = swarm();
  await callTool(setup.tool("swarm_plan"), HOTFIX, fakeCtx(repo));
  await lease(setup.tool, fakeCtx(repo), "H-patch");
  await lease(setup.tool, fakeCtx(repo), "H-rev");
  const db = join(sdir, "tasks.db");
  const sha = () => createHash("sha256").update(readFileSync(db)).digest("hex");
  const before = sha();

  const { calls, tool } = swarm();
  const ctx = fakeCtx(repo, PLAN_MODE);
  const mutations: Array<[string, Record<string, unknown>]> = [
    ["swarm_plan", { ...HOTFIX, prefix: "P", correlation_id: "c-plan-mode" }],
    ["swarm_ingest", { task_id: "H-patch", result: RESULT }],
    ["swarm_transition", { task_id: "H-patch", state: "IN_REVIEW", reason: "x" }],
    ["swarm_gate", { gate: "review", task_id: "H-rev", correlation_id: "c-hot", per_target_findings: { "H-patch": [] } }],
  ];
  for (const [name, params] of mutations) {
    for (let i = 0; i < 2; i++) {
      const err = await rejection(callTool(tool(name), params, ctx));
      expect(err.code).toBe("E-POLICY");
    }
  }
  expect(calls).toHaveLength(0);
  expect(sha()).toBe(before);
  expect(existsSync(join(sdir, "results"))).toBe(false);

  const rows = await statusRows(tool, ctx, "c-hot");
  expect(calls).toHaveLength(1);
  expect(stateOf(rows, "H-patch")).toBe("IN_PROGRESS");
});

test("gate signed rows: one verdict per gate_for target, derived from its findings", async () => {
  const { ctx, sdir } = tmpStore();
  delete process.env.SWARM_AGENT_SESSION; // in-session, swarm_gate is the recorder
  const { tool } = swarm();
  await callTool(tool("swarm_plan"), { brief: "add search", pattern: "feature", risk_class: "low", prefix: "F", correlation_id: "c-feat" }, ctx);
  await lease(tool, ctx, "F-rev");
  const per_target_findings = {
    "F-be": [{ severity: "major", summary: "unparameterised SQL", location: "be/db.py:12" }],
    "F-fe": [{ severity: "minor", summary: "naming nit" }],
    "F-data": [],
  };
  const res = await callTool(tool("swarm_gate"), { gate: "review", task_id: "F-rev", correlation_id: "c-feat", per_target_findings }, ctx);
  expect(res.content[0].text.startsWith("FAIL: review gate FAIL")).toBe(true); // exit 1 is returned, not thrown
  expect((res.details as { recorded: string[] }).recorded).toEqual(["F-be", "F-data", "F-fe"]);

  const expected: Record<string, string> = { "F-be": "fail", "F-fe": "pass", "F-data": "pass" };
  for (const [target, verdict] of Object.entries(expected)) {
    const h = runPython("orch_status.py", [`--history=${target}`], { SWARM_DIR: sdir });
    expect(h.code, h.stderr).toBe(0);
    const row = (h.json.verdicts as Record<string, { verdict: string; agent_id: string; envelope_json: string }>).review;
    expect(row.verdict).toBe(verdict);
    const env = JSON.parse(row.envelope_json) as { sig: string; payload: { gate_task: string; task_id: string } };
    expect(env.sig).toMatch(/^(hmac|ed25519):.+/);
    expect(env.payload.task_id).toBe(target);
    expect(env.payload.gate_task).toBe("F-rev");
  }
});

function statusTool() {
  const pi = fakePi();
  createSwarmExtension({ bridge: runPy })(pi.api);
  return pi.tool("swarm_status");
}

function plan(repo: string, swarmDir: string | undefined) {
  const r = runPython(
    "orch_plan.py",
    [`--root=${repo}`, "--brief-text=fix the login bug", "--pattern=hotfix", "--risk-class=low", "--prefix=H"],
    { SWARM_DIR: swarmDir, SWARM_TASK_ID: undefined, SWARM_CORRELATION_ID: undefined },
  );
  expect(r.code, r.stderr).toBe(0);
  return r.json;
}

test("status tracer", async () => {
  const repo = gitRepo();
  const sdir = tmpDir("swarm-omp-dir-");
  plan(repo, sdir);
  process.env.SWARM_DIR = sdir;
  const res = await callTool(statusTool(), {}, fakeCtx(repo));
  const details = res.details as { status: string; tasks: Array<{ task_id: string }> };
  expect(details.status).toBe("ok");
  expect(details.tasks.length).toBeGreaterThan(0);
  expect(details.tasks[0].task_id.startsWith("H")).toBe(true);
  expect(res.content[0].text).toContain("TASK");
});

test("status db path", async () => {
  const repo = gitRepo("sub/deeper");
  delete process.env.SWARM_DIR;
  plan(repo, undefined);
  const tool = statusTool();
  const fromRoot = (await callTool(tool, {}, fakeCtx(repo))).details as { db: string; swarm_dir: string };
  const fromSub = (await callTool(tool, {}, fakeCtx(`${repo}/sub/deeper`))).details as { db: string };
  expect(fromRoot.db).toBe(`${repo}/.swarm/tasks.db`);
  expect(fromRoot.db.endsWith("/.swarm/tasks.db")).toBe(true);
  expect(fromSub.db).toBe(fromRoot.db);
  expect(fromRoot.swarm_dir).toBe(`${repo}/.swarm`);
});

test("status env wins", async () => {
  const repo = gitRepo();
  const other = tmpDir("swarm-omp-other-");
  process.env.SWARM_DIR = other;
  const res = await callTool(statusTool(), {}, fakeCtx(repo));
  const details = res.details as { db: string; swarm_dir: string; tasks: unknown[] };
  expect(details.swarm_dir).toBe(other);
  expect(details.db).toBe(`${other}/tasks.db`);
  expect(details.tasks).toEqual([]);
  expect(res.content[0].text).toContain(`db: ${other}/tasks.db`);
});
