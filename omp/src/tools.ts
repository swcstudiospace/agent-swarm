/**
 * Typed swarm tools (TOOL-01..06). Each execute builds `--flag=value` argv and calls the injected
 * Bridge exactly once; Python decides everything (TS holds no state or validation logic).
 * D-01: every tool is hidden + essential, so only sessions whose agent frontmatter names it get it.
 * D-03: mutating tools refuse with E-POLICY in plan mode before any argv, file or bridge work.
 * WR-03: swarm_gate runs only the calling gate agent's own gate (GATE_AGENTS), E-POLICY otherwise.
 */
import { type Bridge, type BridgeResult, type Script, SwarmToolError, writeInputFile } from "./bridge.ts";
import { callingAgent, GATE_AGENTS, inPlanMode } from "./context.ts";
import type { ExtensionContext, JsonSchema, ToolDefinition, ToolResult } from "./omp-api.ts";
import { gitToplevel } from "./paths.ts";

/**
 * Schema pattern for ids that become argv values and (task ids) file names: mirrors swarm/script_base.py
 * TASK_ID_RE + no "..", which python enforces (CR-01). Never dash-leading, so argparse never reads a flag.
 */
export const ID_PATTERN = "^(?!.*\\.\\.)[A-Za-z0-9][A-Za-z0-9._-]{0,127}$";
const ID = { type: "string", pattern: ID_PATTERN, minLength: 1 } as const;

/** The 14 Task Store states (swarm/taskstore.py:22-36 TaskState). */
const STATES = [
  "CREATED", "VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW", "CHANGES_REQUESTED",
  "APPROVED", "DONE", "BLOCKED", "FAILED", "RETRY", "ESCALATED", "CANCELLED",
] as const;

/** orch_plan.py choices: --pattern (PATTERNS only: `custom` needs --plan <path>, which no tool exposes), --risk-class
 * (taskstore.py GATES_BY_RISK), --priority. */
export const PATTERNS = ["feature", "hotfix", "dependency", "parallel"] as const;
export const RISK_CLASSES = ["low", "medium", "high"] as const;
const PRIORITIES = ["P0", "P1", "P2", "P3"] as const;

/** Gate → script (parity mirror of swarm/verdicts.py GATE_SCRIPTS; omp/test/gate.test.ts pins equal);
 * the script derives and signs the verdict. */
const GATE_SCRIPTS = { quality: "qa_gate", review: "rev_gate", security: "sec_gate", release: "rel_plan" } as const satisfies Record<string, Script>;
type Gate = keyof typeof GATE_SCRIPTS;
/** Every gate has its owner in GATE_AGENTS (context.ts), and nothing else. */
GATE_AGENTS satisfies Record<Gate, string>;

/** Finding severities (copied from swarm/gates.py:10 SEVERITIES); BLOCKING_SEVERITY = "major". */
const SEVERITIES = ["info", "minor", "major", "critical", "blocker"] as const;
/** qa_gate's own --timeout default (1800 s) plus a margin, so the bridge never kills a test run first. */
const QA_GATE_TIMEOUT_MS = (1800 + 60) * 1000;

/** A finding (swarm/gates.py make_finding fields). No verdict field anywhere: severities decide (D-09). */
const FINDING = {
  type: "object",
  properties: {
    severity: { type: "string", enum: SEVERITIES, description: "major, critical or blocker fails the target" },
    summary: { type: "string", minLength: 1, description: "What is wrong" },
    id: { type: "string" },
    kind: { type: "string", description: "e.g. semantic, security, functional" },
    evidence: { type: "string" },
    ac_ref: { type: ["string", "null"], description: "Acceptance criterion it violates" },
    owner_suggestion: { type: ["string", "null"], description: "Agent that should fix it, e.g. A05" },
    location: { type: ["string", "null"], description: "path:line" },
  },
  required: ["severity", "summary"],
  additionalProperties: false,
} as const;

type Details = Record<string, unknown>;

/** A registered swarm tool as omp sees it (params are the model's JSON, shaped by `parameters`). */
export type SwarmTool = ToolDefinition<Record<string, unknown>, Details>;

export type StatusParams = { correlation_id?: string };
export type PlanParams = {
  brief: string;
  pattern: (typeof PATTERNS)[number];
  risk_class: (typeof RISK_CLASSES)[number];
  prefix?: string;
  correlation_id?: string;
  priority?: (typeof PRIORITIES)[number];
  acceptance?: string[];
  dry_run?: boolean;
  /** Disjoint blast radii. Present on a parallel plan; also selects parallel when the pattern is feature. */
  slices?: Array<{ id: string; paths: string[]; agent?: string; capability?: string; title?: string; acceptance?: string[] }>;
};
export type IngestParams = { task_id: string; result: Record<string, unknown> };
export type Finding = { severity: (typeof SEVERITIES)[number]; summary: string } & Partial<
  Record<"id" | "kind" | "evidence", string> & Record<"ac_ref" | "owner_suggestion" | "location", string | null>
>;
export type GateParams = {
  gate: Gate;
  task_id: string;
  correlation_id: string;
  per_target_findings?: Record<string, Finding[]>;
  dry_run?: boolean;
};
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
    // schemas reuse module constants (enums, FINDING); each session gets its own copy, so no mutable state is shared
    parameters: structuredClone(meta.parameters),
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

/**
 * The orch_plan argv (shared by swarm_plan and `/swarm`, D-11: one convention). `--brief-text=` only: `--brief <path>`
 * would read any file, and `--plan` takes a model path (T-03-08).
 */
export function planArgs(ctx: ExtensionContext, params: PlanParams): string[] {
  const args = [rootArg(ctx), `--brief-text=${params.brief}`, `--pattern=${params.pattern}`, `--risk-class=${params.risk_class}`];
  if (params.prefix) args.push(`--prefix=${params.prefix}`);
  if (params.correlation_id) args.push(`--correlation-id=${params.correlation_id}`);
  if (params.priority) args.push(`--priority=${params.priority}`);
  for (const item of params.acceptance ?? []) args.push(`--acceptance=${item}`);
  if (params.slices?.length) args.push(`--slices-json=${JSON.stringify(params.slices)}`);
  if (params.dry_run) args.push("--dry-run");
  return args;
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

  const plan = define<PlanParams>({
    name: "swarm_plan",
    label: "Swarm plan",
    description:
      "Plan a swarm run from a brief (A01): creates the pattern's task DAG in the Task Store (PLANNED, gates " +
      "derived from the risk class) under one correlation id. Re-running with the same brief, pattern, risk class, " +
      "priority, acceptance and correlation_id reuses the existing plan (`reused: true`) instead of duplicating it. " +
      "dry_run lists the tasks that would be created without writing.",
    parameters: {
      type: "object",
      properties: {
        brief: { type: "string", minLength: 1, description: "The brief text (not a path)" },
        pattern: {
          type: "string",
          enum: PATTERNS,
          description:
            "DAG pattern: feature (13 tasks), hotfix (8), dependency (8), parallel (one lane per slices[] entry, then one Greptile review). parallel requires slices.",
        },
        slices: {
          type: "array",
          minItems: 2,
          description: "Disjoint blast radii for pattern parallel. The same agent may own more than one slice. Paths must not overlap.",
          items: {
            type: "object",
            properties: {
              id: { type: "string", pattern: "^[a-z][a-z0-9-]{0,31}$" },
              paths: { type: "array", items: { type: "string", minLength: 1 }, minItems: 1 },
              agent: { type: "string", description: "Implementer id or slug, for example A05. Omit only when capability names an implementer." },
              capability: { type: "string" },
              title: { type: "string" },
              acceptance: { type: "array", items: { type: "string", minLength: 1 } },
            },
            required: ["id", "paths"],
            additionalProperties: false,
          },
        },
        risk_class: { type: "string", enum: RISK_CLASSES, description: "Decides the required gates per task" },
        prefix: { ...ID, description: "Task id prefix (default: T + 4 hex of the correlation id)" },
        correlation_id: { ...ID, description: "Correlation id (default: a new uuid); reuse it to make a re-run idempotent" },
        priority: { type: "string", enum: PRIORITIES, description: "Task priority (default P2)" },
        acceptance: { type: "array", items: { type: "string", minLength: 1 }, description: "Acceptance criteria" },
        dry_run: { type: "boolean", description: "List the tasks that would be created without writing" },
      },
      required: ["brief", "pattern", "risk_class"],
      additionalProperties: false,
    },
    mutating: true,
    async run(_toolCallId, params, signal, ctx) {
      return toolResult(await bridge({ script: "orch_plan", args: planArgs(ctx, params), cwd: ctx.cwd, signal }));
    },
  });

  const ingest = define<IngestParams>({
    name: "swarm_ingest",
    label: "Swarm ingest",
    description:
      "Ingest an agent's task.result v1 object for a leased task (A01): the same result path the headless runner " +
      "uses, so the task moves to the result's state (e.g. IN_REVIEW) and the DAG is reconciled. The result is " +
      "validated by python against the task.result v1 contract: required task_id and state " +
      "(IN_PROGRESS | IN_REVIEW | FAILED | BLOCKED), optional outputs, metrics, summary_md, needs, error, verdicts, gate.",
    parameters: {
      type: "object",
      properties: {
        task_id: { ...ID, description: "Task the result belongs to" },
        result: { type: "object", description: "A task.result v1 object (validated by python, not here)" },
      },
      required: ["task_id", "result"],
      additionalProperties: false,
    },
    mutating: true,
    async run(toolCallId, params, signal, ctx) {
      // D-05: --ingest takes a path, so the bridge writes the object under SWARM_DIR/results (never a model path)
      const file = writeInputFile(ctx.cwd, "ingest", toolCallId, params.result);
      const args = [rootArg(ctx), `--task-id=${params.task_id}`, `--ingest=${file}`];
      return toolResult(await bridge({ script: "orch_status", args, cwd: ctx.cwd, signal }));
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

  const gate = define<GateParams>({
    name: "swarm_gate",
    label: "Swarm gate",
    description:
      "Run your gate script for a leased (IN_PROGRESS) gate task: quality → qa_gate, review → rev_gate, " +
      "security → sec_gate, release → rel_plan. The script derives the verdict from findings, signs it and records " +
      "one verdict row per target of the gate task; you never state a verdict. Review, quality and security: " +
      "per_target_findings maps each target task id to your findings for it; the script's own findings are added to " +
      "every target (review: a target you omit gets every finding; quality and security: only the script's); a key " +
      "that is not a target of the gate task is refused (E-INPUT). " +
      "To fail a target, include at least " +
      "one finding of severity major or higher (major, critical or blocker); minor and info findings pass. " +
      "A failing verdict is returned as text starting `FAIL:`, not as an error. Call this before swarm_ingest of " +
      "the gate task's result.",
    parameters: {
      type: "object",
      properties: {
        gate: { type: "string", enum: Object.keys(GATE_SCRIPTS), description: "Which gate you run" },
        task_id: { ...ID, description: "Your leased gate task id (its notes name the targets)" },
        correlation_id: { ...ID, description: "The gate task's correlation id" },
        per_target_findings: {
          type: "object",
          additionalProperties: { type: "array", items: FINDING },
          description:
            "review, quality and security gates: {target task id: [finding]}. To fail a target include at least " +
            "one finding of severity major, critical or blocker; an empty list adds nothing to it.",
        },
        dry_run: { type: "boolean", description: "Canned dry-run verdict (per_target_findings is ignored)" },
      },
      required: ["gate", "task_id", "correlation_id"],
      additionalProperties: false,
    },
    mutating: true,
    async run(toolCallId, params, signal, ctx) {
      const script = GATE_SCRIPTS[params.gate];
      if (!script) throw new SwarmToolError("E-INPUT", `unknown gate ${JSON.stringify(params.gate)}`);
      const agent = callingAgent(ctx);
      if (agent !== GATE_AGENTS[params.gate]) {
        throw new SwarmToolError(
          "E-POLICY",
          `swarm_gate refused: the ${params.gate} gate is run only by ${GATE_AGENTS[params.gate]}, ` +
            `not ${agent === undefined ? "an unidentified session" : JSON.stringify(agent)}`,
        );
      }
      if (params.per_target_findings !== undefined && params.gate === "release") {
        throw new SwarmToolError("E-INPUT", "per_target_findings is not accepted by the release gate");
      }
      // argv from whitelisted fields only: extra model keys (e.g. a forged verdict) never reach python (T-03-06)
      const args = [rootArg(ctx), `--task-id=${params.task_id}`, `--correlation-id=${params.correlation_id}`];
      if (params.per_target_findings !== undefined) {
        args.push(`--per-target-findings=${writeInputFile(ctx.cwd, "findings", toolCallId, params.per_target_findings)}`);
      }
      if (params.dry_run) args.push("--dry-run");
      const timeoutMs = params.gate === "quality" ? QA_GATE_TIMEOUT_MS : undefined;
      return toolResult(await bridge({ script, args, cwd: ctx.cwd, signal, timeoutMs }));
    },
  });

  return [status, plan, ingest, transition, gate];
}
