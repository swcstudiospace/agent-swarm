/** Shared bun:test helpers: tmp git repos and SWARM_DIRs (never the repo's .swarm/), fake pi and ctx. */
import { afterEach, beforeEach } from "bun:test";
import { mkdirSync, mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import type {
  CommandContext,
  CommandDefinition,
  ExtensionAPI,
  ExtensionContext,
  ExtensionFactory,
  SessionEntry,
  ToolDefinition,
  ToolResult,
} from "../src/omp-api.ts";

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
  /** Commands recorded by registerCommand (legal at load). */
  commands: Map<string, CommandDefinition>;
  handlers: Map<string, Array<(event: unknown, ctx: ExtensionContext) => unknown>>;
  /** Names of runtime action methods that were called during load (each call also throws). */
  actionCalls: string[];
  /** Names of runtime actions called after load() installed them (e.g. getActiveTools from a handler). */
  runtimeCalls: string[];
  tool(name: string): AnyTool;
  /** The registered command `name`. */
  command(name: string): CommandDefinition;
  /** The single handler registered for `event`. */
  handler(event: string): (event: unknown, ctx: ExtensionContext) => unknown;
  /** Run the factory (load time: actions throw), then install the configured runtime actions. */
  load(factory: ExtensionFactory): void;
}

export interface FakePiOptions {
  /** The runtime getActiveTools result, or a function called per invocation (e.g. one that throws). */
  activeTools?: string[] | (() => string[]);
  /** The runtime sendUserMessage (e.g. one that records to a call log); absent = still throws after load. */
  sendUserMessage?: (text: string) => void;
}

/** Records registerTool/on; runtime actions record their name and throw like omp's load-time stubs. */
export function fakePi(opts: FakePiOptions = {}): FakePi {
  const tools: AnyTool[] = [];
  const commands: FakePi["commands"] = new Map();
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
    registerCommand: (name: string, command: CommandDefinition) => void commands.set(name, command),
    sendUserMessage: notAtLoad("sendUserMessage"),
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
    commands,
    handlers,
    actionCalls,
    runtimeCalls,
    tool(name) {
      const found = tools.find((t) => t.name === name);
      if (!found) throw new Error(`tool ${name} not registered`);
      return found;
    },
    command(name) {
      const found = commands.get(name);
      if (!found) throw new Error(`command ${name} not registered`);
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
      if (active !== undefined) {
        api.getActiveTools = () => {
          runtimeCalls.push("getActiveTools");
          return typeof active === "function" ? active() : [...active];
        };
      }
      const send = opts.sendUserMessage;
      if (send !== undefined) {
        api.sendUserMessage = (text) => {
          runtimeCalls.push("sendUserMessage");
          send(text);
        };
      }
    },
  };
}

export function fakeCtx(cwd: string, entries: SessionEntry[] = []): ExtensionContext {
  return { cwd, sessionManager: { getEntries: () => entries, getBranch: () => entries } };
}

/** One ordered record of what a command did: `call` names the seam (bridge, sendUserMessage, waitForIdle, notify). */
export type CallLog = Array<{ call: string } & Record<string, unknown>>;

/**
 * A fake turn driven by timers, started by the fake sendUserMessage: `isIdle` reads false from `startMs` until the
 * turn ends `durationMs` later (logged as turn-start / turn-end), like omp's in-flight count and independent of the
 * fake `waitForIdle`; `entry` is appended to `entries` at `entryMs`. Rows with no `startMs` never flip `isIdle`;
 * rows with no `entry` never append. Real timers on purpose: the handler's polls are async loops and bun 1.4 has
 * no advanceTimersByTimeAsync, so fake timers cannot drive them; rows stay ≤150 ms.
 */
export interface ScriptedTurn {
  startMs?: number;
  durationMs?: number;
  entryMs?: number;
  entry?: SessionEntry;
}

export interface FakeTurn {
  isIdle(): boolean;
  /** Arm the timers (call from the fake sendUserMessage). */
  start(): void;
  /** True once turn-end was logged (or immediately for a turn without `startMs`). */
  ended(): boolean;
}

export function fakeTurn(script: ScriptedTurn, entries: SessionEntry[], log: CallLog): FakeTurn {
  let idle = true;
  let ended = script.startMs === undefined;
  return {
    isIdle: () => idle,
    start() {
      if (script.startMs !== undefined) {
        setTimeout(() => {
          idle = false;
          log.push({ call: "turn-start" });
          setTimeout(() => {
            idle = true;
            ended = true;
            log.push({ call: "turn-end" });
          }, script.durationMs ?? 50);
        }, script.startMs);
      }
      const { entry, entryMs } = script;
      if (entry !== undefined) setTimeout(() => void entries.push(entry), entryMs ?? 0);
    },
    ended: () => ended,
  };
}

/**
 * The default fake turn, once per dispatch: the handler polls twice per dispatch — the first read (not idle) is the
 * synchronous start, the second (idle) the end — so the toggle serves a ctx reused across several handler calls.
 */
function syncTurn(): () => boolean {
  let polls = 0;
  return () => polls++ % 2 === 1;
}

/**
 * A command ctx whose notify / waitForIdle push to `log` (shared with the recording bridge and sendUserMessage).
 * `waitForIdle` always resolves at once, like omp's during a prompt's pre-loop window: it is not a turn signal.
 * `isIdle` is the only turn signal (omp holds it false for the whole prompt); the default is `syncTurn`.
 */
export function commandCtx(
  cwd: string,
  entries: SessionEntry[] = [],
  { hasUI = true, log = [] as CallLog, isIdle = syncTurn() }: { hasUI?: boolean; log?: CallLog; isIdle?: () => boolean } = {},
): CommandContext {
  return {
    ...fakeCtx(cwd, entries),
    hasUI,
    ui: { notify: (message, level) => void log.push({ call: "notify", message, level }) },
    isIdle,
    waitForIdle: async () => void log.push({ call: "waitForIdle" }),
  };
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
