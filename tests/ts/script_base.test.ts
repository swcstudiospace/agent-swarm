import { expect, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { EXIT, loggedId, parseAgentArgs, runAgentScript } from "../../scripts/ts/script_base.ts";

test("parseAgentArgs reads shared flags", () => {
  const a = parseAgentArgs(["--task-id", "T-1", "--json", "--dry-run", "--root", "/tmp"]);
  expect(a.taskId).toBe("T-1");
  expect(a.json).toBe(true);
  expect(a.dryRun).toBe(true);
  expect(EXIT.OK).toBe(0);
  expect(EXIT.FAIL).toBe(1);
  expect(EXIT.ERROR).toBe(2);
});


type Record_ = Record<string, unknown>;

/** Runs one script in a temporary events log and returns the exit code and the parsed events.jsonl records. */
async function emit(argv: string[], run: () => Record<string, unknown> = () => ({ summary: "canned" })): Promise<{ code: number; raw: string; records: Record_[] }> {
  const dir = mkdtempSync(join(tmpdir(), "a18-events-"));
  const saved = process.env.SWARM_DIR;
  process.env.SWARM_DIR = join(dir, "state");
  try {
    const code = await runAgentScript({ agentId: "A18", name: "demo", run, argv: [...argv, "--root", dir] });
    const raw = readFileSync(join(dir, "state", "events.jsonl"), "utf8");
    return { code, raw, records: raw.trim().split("\n").map((line) => JSON.parse(line) as Record_) };
  } finally {
    if (saved === undefined) delete process.env.SWARM_DIR;
    else process.env.SWARM_DIR = saved;
    rmSync(dir, { recursive: true, force: true });
  }
}

/** The record without the fields that vary between runs (time and the id under test). */
function rest(record: Record_): Record_ {
  const { ts: _ts, task_id: _task, ...others } = record;
  return others;
}

test("an invalid task id is logged as null and the rest of the event is unchanged", async () => {
  const bad = await emit(["--task-id", "u:hunter2@host"]);
  const good = await emit(["--task-id", "T-1"]);
  expect(bad.code).toBe(EXIT.OK);
  expect(bad.raw).not.toContain("hunter2");
  expect(bad.records).toHaveLength(1);
  expect(bad.records[0].task_id).toBeNull();
  expect(bad.records[0].type).toBe("script.demo");
  expect(rest(bad.records[0])).toEqual(rest(good.records[0]));
});

test("an invalid task id on the error path is logged as null too", async () => {
  const failing = await emit(["--task-id", "u:hunter2@host"], () => {
    throw new Error("boom");
  });
  expect(failing.code).toBe(EXIT.ERROR);
  expect(failing.raw).not.toContain("hunter2");
  expect(failing.records[0].type).toBe("script.demo.error");
  expect(failing.records[0].task_id).toBeNull();
});

test("a valid task id is written unchanged", async () => {
  const good = await emit(["--task-id", "T-1.a_b-2", "--correlation-id", "corr-1"]);
  expect(good.records[0].task_id).toBe("T-1.a_b-2");
  expect(good.records[0].correlation_id).toBe("corr-1");
});

test("an absent task id is not written at all, as before", async () => {
  const none = await emit([]);
  expect("task_id" in none.records[0]).toBe(false);
});

test("loggedId keeps safe ids and nulls every other value, matching the Python shape rule", () => {
  expect(loggedId(undefined)).toBeUndefined();
  expect(loggedId("T-1")).toBe("T-1");
  expect(loggedId("a".repeat(128))).toBe("a".repeat(128));
  for (const bad of ["u:hunter2@host", "", "-x", ".x", "a..b", "a b", "a".repeat(129), "T-1\n"]) expect(loggedId(bad)).toBeNull();
});
