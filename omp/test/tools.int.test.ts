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
