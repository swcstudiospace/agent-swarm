/** Shared bun:test helpers: tmp git repos and SWARM_DIRs (never the repo's .swarm/), fake pi and ctx. */
import { afterEach, beforeEach } from "bun:test";
import { mkdirSync, mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import type { ExtensionAPI, ExtensionContext, ExtensionFactory, SessionEntry, ToolDefinition, ToolResult } from "../src/omp-api.ts";

/** agent-swarm repo root, from this file's location (omp/test → ../..), so tests run from any cwd. */
export const REPO_ROOT = resolve(import.meta.dir, "..", "..");

const created: string[] = [];

/** A fresh realpath'd tmp dir, removed after the current test. */
export function tmpDir(prefix = "swarm-omp-"): string {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  created.push(dir);
  return dir;
}

/** A tmp dir with `git init -q`; `subdirs` are created inside it. */
export function gitRepo(...subdirs: string[]): string {
  const dir = tmpDir("swarm-omp-repo-");
  const git = Bun.spawnSync(["git", "init", "-q", dir], { stdout: "ignore", stderr: "pipe" });
  if (git.exitCode !== 0) throw new Error(`git init failed: ${git.stderr.toString()}`);
  for (const sub of subdirs) mkdirSync(join(dir, sub), { recursive: true });
  return dir;
}

/** Save these env vars before each test and restore them after it; also removes tmp dirs. */
export function isolateEnv(...keys: string[]): void {
  const saved = new Map<string, string | undefined>();
  beforeEach(() => {
    for (const k of keys) saved.set(k, process.env[k]);
  });
  afterEach(() => {
    for (const [k, v] of saved) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
    for (const dir of created.splice(0)) rmSync(dir, { recursive: true, force: true });
  });
}

export interface PyRun {
  code: number;
  json: Record<string, unknown>;
  stderr: string;
}

/** Setup-only: run `python3 ${REPO_ROOT}/scripts/<script>` --json with an explicit env overlay. */
export function runPython(script: string, args: string[], env: Record<string, string | undefined>): PyRun {
  const merged: Record<string, string | undefined> = { ...process.env, ...env };
  for (const [k, v] of Object.entries(env)) if (v === undefined) delete merged[k];
  const r = Bun.spawnSync(["python3", `${REPO_ROOT}/scripts/${script}`, ...args, "--json"], {
    env: merged,
    stdout: "pipe",
    stderr: "pipe",
  });
  const stdout = r.stdout.toString();
  let json: Record<string, unknown> = {};
  try {
    json = JSON.parse(stdout) as Record<string, unknown>;
  } catch {
    throw new Error(`${script} printed non-JSON (exit ${r.exitCode}): ${stdout}${r.stderr.toString()}`);
  }
  return { code: r.exitCode ?? -1, json, stderr: r.stderr.toString() };
}

/** Tools as tests see them: loosely-typed params so one helper can call any tool. */
export type AnyTool = ToolDefinition<Record<string, unknown>, unknown>;

export interface FakePi {
  api: ExtensionAPI;
  tools: AnyTool[];
  handlers: Map<string, Array<(event: unknown, ctx: ExtensionContext) => unknown>>;
  /** Names of runtime action methods that were called during load (each call also throws). */
  actionCalls: string[];
  /** Names of runtime actions called after load() installed them (e.g. getActiveTools from a handler). */
  runtimeCalls: string[];
  tool(name: string): AnyTool;
  /** The single handler registered for `event`. */
  handler(event: string): (event: unknown, ctx: ExtensionContext) => unknown;
  /** Run the factory (load time: actions throw), then install the runtime getActiveTools, if configured. */
  load(factory: ExtensionFactory): void;
}

export interface FakePiOptions {
  /** The runtime getActiveTools result, or a function called per invocation (e.g. one that throws). */
  activeTools?: string[] | (() => string[]);
}

/** Records registerTool/on; runtime actions record their name and throw like omp's load-time stubs. */
export function fakePi(opts: FakePiOptions = {}): FakePi {
  const tools: AnyTool[] = [];
  const handlers: FakePi["handlers"] = new Map();
  const actionCalls: string[] = [];
  const runtimeCalls: string[] = [];
  const notAtLoad = (name: string) => () => {
    actionCalls.push(name);
    throw new Error(`runtime action ${name} called during load`);
  };
  const api: ExtensionAPI & Record<string, unknown> = {
    // heterogeneous tools stored for loosely-typed test calls
    registerTool: <P, D>(t: ToolDefinition<P, D>) => void tools.push(t as unknown as AnyTool),
    on: ((event: string, handler: (event: unknown, ctx: ExtensionContext) => unknown) => {
      handlers.set(event, [...(handlers.get(event) ?? []), handler]);
    }) as ExtensionAPI["on"],
    exec: notAtLoad("exec"),
    getActiveTools: notAtLoad("getActiveTools"),
    getAllTools: notAtLoad("getAllTools"),
    setActiveTools: notAtLoad("setActiveTools"),
    sendMessage: notAtLoad("sendMessage"),
    appendEntry: notAtLoad("appendEntry"),
  };
  return {
    api,
    tools,
    handlers,
    actionCalls,
    runtimeCalls,
    tool(name) {
      const found = tools.find((t) => t.name === name);
      if (!found) throw new Error(`tool ${name} not registered`);
      return found;
    },
    handler(event) {
      const found = handlers.get(event) ?? [];
      if (found.length !== 1) throw new Error(`expected one ${event} handler, got ${found.length}`);
      return found[0];
    },
    load(factory) {
      factory(api);
      const active = opts.activeTools;
      if (active === undefined) return;
      api.getActiveTools = () => {
        runtimeCalls.push("getActiveTools");
        return typeof active === "function" ? active() : [...active];
      };
    },
  };
}

export function fakeCtx(cwd: string, entries: SessionEntry[] = []): ExtensionContext {
  return { cwd, sessionManager: { getEntries: () => entries, getBranch: () => entries } };
}

/**
 * A ctx whose session_init names `agent` (e.g. the gate agent a swarm_gate call must come from). `restricted`
 * is session_init.restrictToolNames: true (the default) is a plan-mode child, which HOOK-04 never caps.
 */
export function agentCtx(cwd: string, agent: string, entries: SessionEntry[] = [], restricted = true): ExtensionContext {
  return fakeCtx(cwd, [{ type: "session_init", agent, restrictToolNames: restricted, tools: [] }, ...entries]);
}

/** Call a registered tool's execute with omp's argument order (signal 3rd). */
export function callTool(
  tool: AnyTool,
  params: Record<string, unknown>,
  ctx: ExtensionContext,
  signal?: AbortSignal,
): Promise<ToolResult<unknown>> {
  return tool.execute("call-1", params, signal, undefined, ctx);
}
