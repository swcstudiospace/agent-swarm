/**
 * Typed swarm tools (TOOL-01..06). Each execute builds `--flag=value` argv and calls the injected
 * Bridge exactly once; Python decides everything (TS holds no state or validation logic).
 * D-01: every tool is hidden + essential, so only sessions whose agent frontmatter names it get it.
 * D-03: mutating tools refuse with E-POLICY in plan mode before any argv, file or bridge work.
 */
import { gitToplevel } from "../../scripts/ts/script_base.ts";
import { type Bridge, type BridgeResult, SwarmToolError } from "./bridge.ts";
import { inPlanMode } from "./context.ts";
import type { ExtensionContext, JsonSchema, ToolDefinition, ToolResult } from "./omp-api.ts";

/** Schema pattern for ids that become argv values: never dash-leading (argparse would read a flag). */
const ID = { type: "string", pattern: "^[^-]", minLength: 1 } as const;

/** The 14 Task Store states (swarm/taskstore.py:22-36 TaskState). */
const STATES = [
  "CREATED", "VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW", "CHANGES_REQUESTED",
  "APPROVED", "DONE", "BLOCKED", "FAILED", "RETRY", "ESCALATED", "CANCELLED",
] as const;

type Details = Record<string, unknown>;

/** A registered swarm tool as omp sees it (params are the model's JSON, shaped by `parameters`). */
export type SwarmTool = ToolDefinition<Record<string, unknown>, Details>;

export type StatusParams = { correlation_id?: string };
export type TransitionParams = { task_id: string; state: (typeof STATES)[number]; reason: string; dry_run?: boolean };

interface ToolSpec<P> {
  name: string;
  label: string;
  description: string;
  parameters: JsonSchema;
  /** D-03: refused in plan mode before anything else runs. */
  mutating: boolean;
  run(toolCallId: string, params: P, signal: AbortSignal | undefined, ctx: ExtensionContext): Promise<ToolResult<Details>>;
}

/** D-01 registration shape plus the D-03 plan-mode refusal for mutating tools. */
function define<P>({ mutating, run, ...meta }: ToolSpec<P>): ToolDefinition<P, Details> {
  return {
    ...meta,
    hidden: true,
    loadMode: "essential",
    async execute(toolCallId, params, signal, _onUpdate, ctx) {
      if (mutating && inPlanMode(ctx)) {
        throw new SwarmToolError(
          "E-POLICY",
          `${meta.name} refused: omp is in plan mode (read-only), so the Task Store was not touched; ` +
            "leave plan mode to change it (swarm_status still works)",
        );
      }
      return run(toolCallId, params, signal, ctx);
    },
  };
}

/** `--root=<git toplevel of ctx.cwd>` (D-06); a cwd outside git is passed as-is. */
function rootArg(ctx: ExtensionContext): string {
  return `--root=${gitToplevel(ctx.cwd) ?? ctx.cwd}`;
}

/** Exit 1 (status fail: a failing verdict, a release freeze) is data the agent must read, so it is returned with `FAIL:`. */
function toolResult(res: BridgeResult, extra: string[] = []): ToolResult<Details> {
  const summary = typeof res.json.summary === "string" ? res.json.summary : JSON.stringify(res.json);
  const text = [res.exitCode === 1 ? `FAIL: ${summary}` : summary, ...extra].join("\n");
  return { content: [{ type: "text", text }], details: res.json };
}

export function buildTools(bridge: Bridge): SwarmTool[] {
  const status = define<StatusParams>({
    name: "swarm_status",
    label: "Swarm status",
    description:
      "List the swarm Task Store for a correlation (default: the latest): per task its state, agent, attempt, " +
      "ready flag, required gates and verdicts, plus counts, escalations and completion. The result also names " +
      "the absolute Task Store path (`db`, `swarm_dir`), which is identical from any cwd in the repo. Read-only.",
    parameters: {
      type: "object",
      properties: {
        correlation_id: { ...ID, description: "Correlation id of the plan to list (omit for the latest plan)" },
      },
      additionalProperties: false,
    },
    mutating: false,
    async run(_toolCallId, params, signal, ctx) {
      const args = [rootArg(ctx)];
      if (params.correlation_id) args.push(`--correlation-id=${params.correlation_id}`);
      const res = await bridge({ script: "orch_status", args, cwd: ctx.cwd, signal });
      return toolResult(res, typeof res.json.db === "string" ? [`db: ${res.json.db}`] : []);
    },
  });

  const transition = define<TransitionParams>({
    name: "swarm_transition",
    label: "Swarm transition",
    description:
      "Move one Task Store task to a new state along the legal state machine (A01 only), e.g. lease a ready task " +
      "PLANNED → CLAIMED → IN_PROGRESS. An illegal transition fails with E-CONTRACT and leaves the task unchanged. " +
      "Every transition is audited with the reason. dry_run reports the transition without writing.",
    parameters: {
      type: "object",
      properties: {
        task_id: { ...ID, description: "Task id to transition" },
        state: { type: "string", enum: STATES, description: "Target state" },
        reason: { type: "string", minLength: 1, description: "Why (recorded in the task's audit history)" },
        dry_run: { type: "boolean", description: "Report the transition without writing" },
      },
      required: ["task_id", "state", "reason"],
      additionalProperties: false,
    },
    mutating: true,
    async run(_toolCallId, params, signal, ctx) {
      // --transition takes two space-form values (nargs=2): the schema forbids a dash-leading id and enumerates state
      const args = [rootArg(ctx), "--transition", params.task_id, params.state, `--reason=${params.reason}`];
      if (params.dry_run) args.push("--dry-run");
      return toolResult(await bridge({ script: "orch_status", args, cwd: ctx.cwd, signal }));
    },
  });

  return [status, transition];
}
