/**
 * Typed swarm tools (TOOL-01..06). Each execute builds `--flag=value` argv and calls the injected
 * Bridge exactly once; Python decides everything (TS holds no state or validation logic).
 * D-01: every tool is hidden + essential, so only sessions whose agent frontmatter names it get it.
 */
import { gitToplevel } from "../../scripts/ts/script_base.ts";
import type { Bridge, BridgeResult } from "./bridge.ts";
import type { ExtensionContext, ToolDefinition, ToolResult } from "./omp-api.ts";

/** Schema pattern for ids that become argv values: never dash-leading (argparse would read a flag). */
const ID = { type: "string", pattern: "^[^-]", minLength: 1 } as const;

type Details = Record<string, unknown>;

export interface StatusParams {
  correlation_id?: string;
}

/** `--root=<git toplevel of ctx.cwd>` (D-06); a cwd outside git is passed as-is. */
function rootArg(ctx: ExtensionContext): string {
  return `--root=${gitToplevel(ctx.cwd) ?? ctx.cwd}`;
}

function toolResult(res: BridgeResult, extra: string[] = []): ToolResult<Details> {
  const summary = typeof res.json.summary === "string" ? res.json.summary : JSON.stringify(res.json);
  const text = [res.exitCode === 1 ? `FAIL: ${summary}` : summary, ...extra].join("\n");
  return { content: [{ type: "text", text }], details: res.json };
}

export function buildTools(bridge: Bridge): ToolDefinition<StatusParams, Details>[] {
  const status: ToolDefinition<StatusParams, Details> = {
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
    hidden: true,
    loadMode: "essential",
    async execute(_toolCallId, params, signal, _onUpdate, ctx) {
      const args = [rootArg(ctx)];
      if (params.correlation_id) args.push(`--correlation-id=${params.correlation_id}`);
      const res = await bridge({ script: "orch_status", args, cwd: ctx.cwd, signal });
      return toolResult(res, typeof res.json.db === "string" ? [`db: ${res.json.db}`] : []);
    },
  };
  return [status];
}
