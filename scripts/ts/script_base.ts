#!/usr/bin/env bun
/**
 * Shared CLI for every TypeScript agent script.
 * Mirrors swarm/script_base.py: --task-id --correlation-id --root --json --dry-run
 * Exit 0 ok / 1 finding-fail / 2 taxonomy error.
 */
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

export const EXIT = { OK: 0, FAIL: 1, ERROR: 2 } as const;

export type AgentArgs = {
  taskId?: string;
  correlationId?: string;
  json: boolean;
  dryRun: boolean;
  root: string;
  rest: string[];
};

export function parseAgentArgs(argv: string[]): AgentArgs {
  const out: AgentArgs = { json: false, dryRun: false, root: ".", rest: [] };
  const args = [...argv];
  while (args.length) {
    const tok = args.shift() as string;
    if (tok === "--task-id") out.taskId = args.shift();
    else if (tok === "--correlation-id") out.correlationId = args.shift();
    else if (tok === "--root") out.root = args.shift() ?? ".";
    else if (tok === "--json") out.json = true;
    else if (tok === "--dry-run") out.dryRun = true;
    else out.rest.push(tok);
  }
  return out;
}

function swarmDir(root: string): string {
  return process.env.SWARM_DIR || resolve(root, ".swarm");
}

function emitEvent(root: string, payload: Record<string, unknown>): void {
  const dir = swarmDir(root);
  if (!existsSync(dir) && !process.env.SWARM_DIR) return;
  mkdirSync(dir, { recursive: true });
  const line = JSON.stringify({ ts: new Date().toISOString(), ...payload }) + "\n";
  appendFileSync(resolve(dir, "events.jsonl"), line);
}

export async function runAgentScript(opts: {
  agentId: string;
  name: string;
  run: (args: AgentArgs) => Promise<Record<string, unknown>> | Record<string, unknown>;
  argv?: string[];
}): Promise<number> {
  const args = parseAgentArgs(opts.argv ?? process.argv.slice(2));
  try {
    const result: Record<string, unknown> = {
      agent: opts.agentId,
      script: opts.name,
      status: "ok",
      ...(await opts.run(args)),
    };
    result.agent ??= opts.agentId;
    result.script ??= opts.name;
    result.status ??= "ok";
    emitEvent(args.root, { type: `script.${opts.name}`, ...result, task_id: args.taskId, correlation_id: args.correlationId });
    if (args.json) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      const status = String(result.status ?? "ok").toUpperCase();
      console.log(`[${opts.agentId}/${opts.name}] ${status}`);
    }
    return result.status === "fail" ? EXIT.FAIL : EXIT.OK;
  } catch (err) {
    const error = {
      agent: opts.agentId,
      script: opts.name,
      status: "error",
      error: { code: "E-DEP", message: err instanceof Error ? err.message : String(err) },
    };
    emitEvent(args.root, { type: `script.${opts.name}.error`, ...error, task_id: args.taskId });
    if (args.json) console.log(JSON.stringify(error, null, 2));
    else console.log(`[${opts.agentId}/${opts.name}] ERROR`);
    return EXIT.ERROR;
  }
}
