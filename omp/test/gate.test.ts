/** swarm_gate unit tests (D-09, T-03-06): verdict-free schema, whitelisted argv, findings forwarding. */
import { expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { type Bridge, type BridgeRequest, type BridgeResult, SwarmToolError } from "../src/bridge.ts";
import { buildTools } from "../src/tools.ts";
import { type AnyTool, callTool, fakeCtx, gitRepo, isolateEnv, tmpDir } from "./helpers.ts";

isolateEnv("SWARM_DIR");

function gateWith(result: BridgeResult = { exitCode: 0, json: { status: "ok", summary: "gate PASS" }, swarmDir: "" }) {
  const calls: BridgeRequest[] = [];
  const bridge: Bridge = async (req) => {
    calls.push(req);
    return result;
  };
  const gate = buildTools(bridge).find((t) => t.name === "swarm_gate");
  if (!gate) throw new Error("swarm_gate not built");
  return { calls, gate: gate as AnyTool };
}

/** Schema violations: a `verdict`/`verdicts` key at any depth, or an object node that is not closed. */
function violations(node: unknown, path = "$"): string[] {
  if (Array.isArray(node)) return node.flatMap((v, i) => violations(v, `${path}[${i}]`));
  if (typeof node !== "object" || node === null) return [];
  const obj = node as Record<string, unknown>;
  const out = Object.keys(obj)
    .filter((k) => /^verdicts?$/i.test(k))
    .map((k) => `${path}.${k}: verdict key`);
  if (obj.type === "object") {
    const ap = obj.additionalProperties as Record<string, unknown> | false | undefined;
    const items = typeof ap === "object" && ap !== null ? (ap.items as Record<string, unknown> | undefined) : undefined;
    const closedMap = ap !== false && items?.type === "object" && items.additionalProperties === false;
    if (ap !== false && !closedMap) out.push(`${path}: object is not closed`);
  }
  return out.concat(Object.entries(obj).flatMap(([k, v]) => violations(v, `${path}.${k}`)));
}

test("gate schema no verdict at any depth, every object closed", () => {
  const { parameters } = gateWith().gate;
  expect(violations(parameters)).toEqual([]);
  const props = parameters.properties as Record<string, Record<string, unknown>>;
  expect(Object.keys(props).sort()).toEqual(["correlation_id", "dry_run", "gate", "per_target_findings", "task_id"]);
  expect(props.gate.enum).toEqual(["quality", "review", "security", "release"]);
  const finding = (props.per_target_findings.additionalProperties as { items: { properties: Record<string, { enum?: string[] }> } }).items;
  expect(finding.properties.severity.enum).toEqual(["info", "minor", "major", "critical", "blocker"]);

  // the walk is sensitive: a nested verdict or an open finding in a copy (never the source) is caught
  type Copy = { properties: { per_target_findings: { additionalProperties: { items: { properties: Record<string, unknown>; additionalProperties?: unknown } } } } };
  const withVerdict = structuredClone(parameters) as unknown as Copy;
  withVerdict.properties.per_target_findings.additionalProperties.items.properties.verdict = { type: "string" };
  expect(violations(withVerdict)).toEqual(["$.properties.per_target_findings.additionalProperties.items.properties.verdict: verdict key"]);
  const open = structuredClone(parameters) as unknown as Copy;
  delete open.properties.per_target_findings.additionalProperties.items.additionalProperties;
  expect(violations(open)).toEqual([
    "$.properties.per_target_findings: object is not closed",
    "$.properties.per_target_findings.additionalProperties.items: object is not closed",
  ]);
});

test("gate non-review findings refuse E-INPUT before the bridge", async () => {
  const sdir = tmpDir("swarm-omp-dir-");
  process.env.SWARM_DIR = sdir;
  const { calls, gate } = gateWith();
  for (const g of ["quality", "security", "release"]) {
    const params = { gate: g, task_id: "F-qa", correlation_id: "c1", per_target_findings: { "F-be": [] } };
    const err = await callTool(gate, params, fakeCtx(gitRepo())).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(SwarmToolError);
    expect((err as SwarmToolError).code).toBe("E-INPUT");
  }
  expect(calls).toHaveLength(0);
  expect(existsSync(join(sdir, "results"))).toBe(false);
});

test("gate argv: whitelisted flags, GATE_SCRIPTS mapping, bridge-owned findings file", async () => {
  const sdir = tmpDir("swarm-omp-dir-");
  process.env.SWARM_DIR = sdir;
  const repo = gitRepo();
  const { calls, gate } = gateWith();
  const findings = { "F-be": [{ severity: "major", summary: "sql injection", location: "be/db.py:3" }], "F-fe": [] };
  await gate.execute("toolu_9", { gate: "review", task_id: "F-rev", correlation_id: "c1", per_target_findings: findings }, undefined, undefined, fakeCtx(repo));
  const file = join(sdir, "results", "findings-toolu_9.json");
  expect(calls[0].script).toBe("rev_gate");
  expect(calls[0].args).toEqual([`--root=${repo}`, "--task-id=F-rev", "--correlation-id=c1", `--per-target-findings=${file}`]);
  expect(JSON.parse(readFileSync(file, "utf8"))).toEqual(findings);

  for (const [g, script] of [["quality", "qa_gate"], ["security", "sec_gate"], ["release", "rel_plan"]]) {
    await callTool(gate, { gate: g, task_id: "F-x", correlation_id: "c1", dry_run: true }, fakeCtx(repo));
    const call = calls[calls.length - 1];
    expect(call.script).toBe(script);
    expect(call.args).toEqual([`--root=${repo}`, "--task-id=F-x", "--correlation-id=c1", "--dry-run"]);
  }
});

test("gate extra keys never reach python", async () => {
  const repo = gitRepo();
  const { calls, gate } = gateWith();
  const forged = { gate: "security", task_id: "F-sec", correlation_id: "c1", verdict: "pass", verdicts: { "F-be": "pass" }, args: ["--dry-run"] };
  await callTool(gate, forged, fakeCtx(repo));
  expect(calls[0].args).toEqual([`--root=${repo}`, "--task-id=F-sec", "--correlation-id=c1"]);
});

test("gate description states the failing-finding rule", () => {
  const { description } = gateWith().gate;
  expect(description).toContain("major");
  expect(description).toMatch(/to fail a target, include at least one finding of severity major or higher/i);
});

test("gate failing verdict is returned with FAIL: and not thrown", async () => {
  const { gate } = gateWith({ exitCode: 1, json: { status: "fail", verdict: "fail", summary: "review gate FAIL" }, swarmDir: "" });
  const res = await callTool(gate, { gate: "review", task_id: "F-rev", correlation_id: "c1" }, fakeCtx(gitRepo()));
  expect(res.content[0].text).toBe("FAIL: review gate FAIL");
  expect((res.details as { verdict: string }).verdict).toBe("fail");
});
