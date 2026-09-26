/** G-3 (PV-1): a real swarm.gates.make_finding dict validates against swarm_gate's per_target_findings schema. */
import { expect, test } from "bun:test";
import type { Bridge } from "../src/bridge.ts";
import { buildTools } from "../src/tools.ts";
import { REPO_ROOT } from "./helpers.ts";

type Schema = {
  type?: string | string[];
  enum?: unknown[];
  minLength?: number;
  properties?: Record<string, Schema>;
  required?: string[];
  additionalProperties?: boolean | Schema;
  items?: Schema;
};

const bridge: Bridge = async () => {
  throw new Error("the schema test never calls the bridge");
};

function perTargetFindings(): Schema {
  const gate = buildTools(bridge).find((t) => t.name === "swarm_gate");
  if (!gate) throw new Error("swarm_gate not built");
  return (gate.parameters as { properties: Record<string, Schema> }).properties.per_target_findings;
}

const jsonType = (v: unknown): string => (v === null ? "null" : Array.isArray(v) ? "array" : Number.isInteger(v) ? "integer" : typeof v);

/** Violations of the JSON Schema keywords the swarm_gate parameters use, read from the schema itself. */
function violations(value: unknown, schema: Schema, path = "$"): string[] {
  const t = jsonType(value);
  if (schema.type !== undefined) {
    const allowed = [schema.type].flat();
    if (!allowed.includes(t) && !(t === "integer" && allowed.includes("number"))) return [`${path}: ${t} not in [${allowed}]`];
  }
  const out: string[] = [];
  if (schema.enum !== undefined && !schema.enum.includes(value)) out.push(`${path}: ${JSON.stringify(value)} not in enum`);
  if (t === "string" && schema.minLength !== undefined && (value as string).length < schema.minLength) out.push(`${path}: shorter than ${schema.minLength}`);
  if (t === "array" && schema.items !== undefined) {
    (value as unknown[]).forEach((v, i) => out.push(...violations(v, schema.items as Schema, `${path}[${i}]`)));
  }
  if (t === "object") {
    const obj = value as Record<string, unknown>;
    for (const key of schema.required ?? []) if (!Object.hasOwn(obj, key)) out.push(`${path}: required ${key} missing`);
    for (const [key, v] of Object.entries(obj)) {
      const prop = schema.properties?.[key];
      if (prop !== undefined) out.push(...violations(v, prop, `${path}.${key}`));
      else if (schema.additionalProperties === false) out.push(`${path}.${key}: not allowed`);
      else if (typeof schema.additionalProperties === "object") out.push(...violations(v, schema.additionalProperties, `${path}.${key}`));
    }
  }
  return out;
}

/** swarm.gates.make_finding run for real in python, keyword arguments from stdin. */
function makeFinding(args: Record<string, unknown>): Record<string, unknown> {
  const py = Bun.spawnSync(
    ["python3", "-c", "import json,sys\nfrom swarm.gates import make_finding\nprint(json.dumps(make_finding(**json.load(sys.stdin))))"],
    { cwd: REPO_ROOT, stdin: Buffer.from(JSON.stringify(args)), stdout: "pipe", stderr: "pipe" },
  );
  if (py.exitCode !== 0) throw new Error(`make_finding failed: ${py.stderr.toString()}`);
  return JSON.parse(py.stdout.toString()) as Record<string, unknown>;
}

const REQUIRED_ARGS = { fid: "F-1", severity: "major", kind: "semantic", summary: "wrong greeting" };

test.each([
  ["every optional field left at its None default", REQUIRED_ARGS],
  ["every optional field set", { ...REQUIRED_ARGS, evidence: "hello.py:3 returns ''", ac_ref: "AC-1", owner_suggestion: "A05", location: "hello.py:3" }],
])("G-3: make_finding with %s validates against swarm_gate per_target_findings", (_label, args) => {
  const schema = perTargetFindings();
  const item = (schema.additionalProperties as Schema).items as Schema;
  const finding = makeFinding(args);
  expect(item.additionalProperties).toBe(false);
  expect(Object.keys(finding).filter((k) => !Object.hasOwn(item.properties ?? {}, k))).toEqual([]);
  expect((item.required ?? []).filter((k) => !Object.hasOwn(finding, k))).toEqual([]);
  expect(violations(finding, item)).toEqual([]);
  // the whole parameter value the gate agent passes back: {target task id: [finding]}
  expect(violations({ "T-be": [finding] }, schema)).toEqual([]);
});
