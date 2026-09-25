/** Integration: tools → real bridge → real python scripts → tmp Task Store (never the repo's .swarm/). */
import { expect, test } from "bun:test";
import { createSwarmExtension } from "../src/index.ts";
import { runPy } from "../src/bridge.ts";
import { callTool, fakeCtx, fakePi, gitRepo, isolateEnv, runPython, tmpDir } from "./helpers.ts";

isolateEnv("SWARM_DIR", "SWARM_ROOT", "SWARM_TASK_ID", "SWARM_CORRELATION_ID");

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
