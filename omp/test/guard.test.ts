/**
 * The tool_call guard (Phase 4): HOOK-04 A01 depth cap (D-06), HOOK-03 swarm-state tools (D-05), identity (D-01)
 * and fail modes (D-02), over the pure guardToolCall and through the handler the factory registers.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Bridge } from "../src/bridge.ts";
import { callingAgent } from "../src/context.ts";
import { GUARD_ERROR_PREFIX, type GuardFacts, guardToolCall, normalize, RULES, ruleReason, SWARM_SLUGS } from "../src/guard.ts";
import { createSwarmExtension } from "../src/index.ts";
import type { ExtensionContext, SessionEntry, ToolCallEvent } from "../src/omp-api.ts";
import { agentCtx, fakeCtx, fakePi, type FakePiOptions, isolateEnv, REPO_ROOT } from "./helpers.ts";

isolateEnv("SWARM_AGENT", "SWARM_TASK_ID");
beforeEach(() => {
  delete process.env.SWARM_AGENT;
  delete process.env.SWARM_TASK_ID;
});

const A01 = "a01-orchestrator";
const CWD = "/nonexistent-guard-cwd";
const HOME = "/nonexistent-guard-home";
const DEPTH_PREFIX = "BLOCKED needs: depth";

const call = (toolName: string, input: unknown = {}): ToolCallEvent => ({ toolName, toolCallId: "tc-1", input });
const yieldData = (data: unknown) => call("yield", { data });

/** A01 at the depth cap: no `task`, not a restricted child, not in plan mode. */
const capped: GuardFacts = { agent: A01, restricted: false, planMode: false, hasTask: false, topLevel: false, env: {}, cwd: CWD, home: HOME };

/** The tool_call handler the real factory registers, with a runtime getActiveTools (a bridge call would throw). */
function guardHandler(opts: FakePiOptions = {}) {
  const bridge: Bridge = () => {
    throw new Error("the guard must never reach the bridge");
  };
  const pi = fakePi(opts);
  pi.load(createSwarmExtension({ bridge }));
  const handler = pi.handler("tool_call");
  return { pi, run: (event: ToolCallEvent, ctx: ExtensionContext) => handler(event, ctx) };
}

/** A01's own session: session_init names it, tools not restricted (a real subagent, not a plan-mode child). */
const a01Ctx = (entries: SessionEntry[] = []) => agentCtx(CWD, A01, entries, false);

describe("HOOK-04", () => {
  test.each(["bash", "read", "write", "edit", "swarm_plan", "swarm_status", "eval", "grep"])(
    "at the cap, %s is blocked with the depth reason naming the one permitted yield",
    (toolName) => {
      const res = guardToolCall(call(toolName, { command: "ls" }), capped);
      expect(res?.block).toBe(true);
      expect(res?.reason?.startsWith(DEPTH_PREFIX)).toBe(true);
      expect(res?.reason).toContain("yield");
      expect(res?.reason).toContain('state: "BLOCKED"');
      expect(res?.reason).toContain('needs: "depth"');
    },
  );

  test.each([
    ["IN_REVIEW", { data: { task_id: "T-1", state: "IN_REVIEW" } }],
    ["DONE", { data: { task_id: "T-1", state: "DONE", needs: "depth" } }],
    ["error", { error: "x" }],
    ["empty input", {}],
    ["empty data", { data: {} }],
    ["BLOCKED without needs", { data: { state: "BLOCKED" } }],
    ['needs ""', { data: { state: "BLOCKED", needs: "" } }],
    ["needs []", { data: { state: "BLOCKED", needs: [] } }],
    ["needs {}", { data: { state: "BLOCKED", needs: {} } }],
    ['needs "depths"', { data: { state: "BLOCKED", needs: "depths" } }],
    ['needs "no-depth"', { data: { state: "BLOCKED", needs: "no-depth" } }],
    ['needs ["depths"]', { data: { state: "BLOCKED", needs: ["depths"] } }],
    ["needs {Depth} (object keys are exact)", { data: { state: "BLOCKED", needs: { Depth: 1 } } }],
    ["inherited depth key", { data: { state: "BLOCKED", needs: Object.create({ depth: 1 }) } }],
    ['state "blocked" (case-sensitive)', { data: { state: "blocked", needs: "depth" } }],
    ["needs without state", { data: { needs: "depth" } }],
    ["null input", null],
    ["string input", "BLOCKED depth"],
    ["array data", { data: ["BLOCKED", "depth"] }],
  ])("at the cap, yield %s is blocked", (_name, input) => {
    const res = guardToolCall(call("yield", input), capped);
    expect(res?.block).toBe(true);
    expect(res?.reason?.startsWith(DEPTH_PREFIX)).toBe(true);
  });

  test.each([
    ['"depth"', "depth"],
    ['["depth"]', ["depth"]],
    ["{depth: …}", { depth: "cap" }],
    ['" Depth "', " Depth "],
    ['["other", "DEPTH"]', ["other", "DEPTH"]],
  ])("at the cap, yield BLOCKED with needs %s is allowed without task_id", (_name, needs) => {
    expect(guardToolCall(yieldData({ state: "BLOCKED", needs }), capped)).toBeUndefined();
    expect(guardToolCall(yieldData({ task_id: "T-1", correlation_id: "C-1", state: "BLOCKED", needs }), capped)).toBeUndefined();
  });

  test.each([
    ["task active", { hasTask: true }],
    ["restricted child", { restricted: true }],
    ["plan mode", { planMode: true }],
  ])("one step off the cap (%s): no HOOK-04 block", (_name, override) => {
    const facts = { ...capped, ...override };
    expect(guardToolCall(call("read", { path: "x" }), facts)).toBeUndefined();
    expect(guardToolCall(yieldData({ state: "IN_REVIEW" }), facts)).toBeUndefined();
  });

  test.each([["a05-backend"], ["task"], [undefined]])("agent %p without task is not capped", (agent) => {
    expect(guardToolCall(call("read", { path: "x" }), { ...capped, agent, topLevel: agent === undefined })).toBeUndefined();
    expect(guardToolCall(yieldData({ state: "DONE" }), { ...capped, agent, topLevel: agent === undefined })).toBeUndefined();
  });

  test("adjacency: A01 at the cap calling swarm_transition gets the depth reason", () => {
    const res = guardToolCall(call("swarm_transition", { task_id: "T-1", to: "DONE" }), capped);
    expect(res?.reason?.startsWith(DEPTH_PREFIX)).toBe(true);
  });

  test("pure: repeated calls give equal results and leave event and facts unchanged", () => {
    const events = [call("bash", { command: "ls" }), yieldData({ state: "BLOCKED", needs: ["depth"] }), call("swarm_ingest", {})];
    const facts: GuardFacts = { ...capped, env: { SWARM_AGENT: A01 } };
    const before = JSON.stringify({ events, facts });
    for (const event of events) expect(guardToolCall(event, facts)).toEqual(guardToolCall(event, facts));
    expect(JSON.stringify({ events, facts })).toBe(before);
  });

  test("registered handler: A01 without task reproduces the pure decision", () => {
    const { pi, run } = guardHandler({ activeTools: ["read", "bash", "yield"] });
    const events = [
      call("bash", { command: "ls" }),
      call("swarm_transition", {}),
      yieldData({ state: "IN_REVIEW" }),
      yieldData({ state: "BLOCKED", needs: "depth" }),
    ];
    for (const event of events) expect(run(event, a01Ctx())).toEqual(guardToolCall(event, capped));
    expect(run(call("bash", {}), a01Ctx())).toEqual({ block: true, reason: expect.stringMatching(/^BLOCKED needs: depth/) });
    expect(pi.runtimeCalls.length).toBe(events.length + 1);
  });

  test("registered handler: A01 with task, as a restricted child or in plan mode is not capped", () => {
    const withTask = guardHandler({ activeTools: ["read", "task", "yield"] });
    expect(withTask.run(call("bash", {}), a01Ctx())).toBeUndefined();

    const noTask = guardHandler({ activeTools: ["read", "yield"] });
    expect(noTask.run(call("bash", {}), agentCtx(CWD, A01))).toBeUndefined(); // restrictToolNames: true
    expect(noTask.run(call("bash", {}), a01Ctx([{ type: "mode_change", mode: "plan" }]))).toBeUndefined();
  });

  test("registered handler: headless A01 (no session_init, SWARM_AGENT) is capped from its env identity", () => {
    process.env.SWARM_AGENT = A01;
    const { run } = guardHandler({ activeTools: ["read"] });
    expect((run(call("bash", {}), fakeCtx(CWD)) as { reason?: string } | undefined)?.reason?.startsWith(DEPTH_PREFIX)).toBe(true);
  });
});

describe("fail", () => {
  const throwing = () => {
    throw new Error("active tools unavailable");
  };
  /** A ctx whose session log reads session_init fine but whose branch read (plan mode) throws. */
  const brokenBranch = (entries: SessionEntry[]): ExtensionContext => ({
    cwd: CWD,
    sessionManager: {
      getEntries: () => entries,
      getBranch: () => {
        throw new Error("branch unreadable");
      },
    },
  });

  test("getActiveTools is called only when the resolved agent is a01-orchestrator", () => {
    const { pi, run } = guardHandler({ activeTools: ["task"] });
    run(call("bash", {}), agentCtx(CWD, "a05-backend", [], false));
    run(call("bash", {}), agentCtx(CWD, "task", [], false));
    run(call("bash", {}), fakeCtx(CWD));
    expect(pi.runtimeCalls).toEqual([]);
    run(call("bash", {}), a01Ctx());
    expect(pi.runtimeCalls).toEqual(["getActiveTools"]);
  });

  test("a throwing getActiveTools for A01 blocks with the guard-error reason", () => {
    const { run } = guardHandler({ activeTools: throwing });
    const res = run(yieldData({ state: "BLOCKED", needs: "depth" }), a01Ctx()) as { block?: boolean; reason?: string };
    expect(res.block).toBe(true);
    expect(res.reason?.startsWith(GUARD_ERROR_PREFIX)).toBe(true);
    expect(res.reason).toContain("active tools unavailable");
  });

  test("a throwing getActiveTools never reaches the main session: its call passes", () => {
    const { pi, run } = guardHandler({ activeTools: throwing });
    expect(run(call("bash", { command: "ls" }), fakeCtx(CWD))).toBeUndefined();
    expect(pi.runtimeCalls).toEqual([]);
  });

  test.each([
    ["the main session", [] as SessionEntry[]],
    ["a generic task child", [{ type: "session_init", agent: "task", restrictToolNames: false }]],
  ])("a guard throw in %s returns undefined", (_name, entries) => {
    const { run } = guardHandler({ activeTools: ["task"] });
    expect(run(call("bash", { command: "ls" }), brokenBranch(entries))).toBeUndefined();
  });

  test("a guard throw with an unreadable session log returns undefined (swarm marker not established)", () => {
    process.env.SWARM_TASK_ID = "T-1";
    const { run } = guardHandler({ activeTools: ["task"] });
    const unreadable: ExtensionContext = { cwd: CWD, sessionManager: { getEntries: throwing, getBranch: throwing } };
    expect(run(call("bash", {}), unreadable)).toBeUndefined();
  });

  test.each([
    ["a swarm specialist", [{ type: "session_init", agent: "a05-backend", restrictToolNames: false }], undefined],
    ["a headless swarm session (SWARM_TASK_ID)", [] as SessionEntry[], "T-1"],
  ])("a guard throw in %s blocks with the guard-error reason", (_name, entries, taskId) => {
    if (taskId !== undefined) process.env.SWARM_TASK_ID = taskId;
    const { run } = guardHandler({ activeTools: ["task"] });
    const res = run(call("read", { path: "x" }), brokenBranch(entries)) as { block?: boolean; reason?: string };
    expect(res.block).toBe(true);
    expect(res.reason?.startsWith(GUARD_ERROR_PREFIX)).toBe(true);
    expect(res.reason).toContain("branch unreadable");
  });
});

describe("HOOK-03", () => {
  const SWARM_STATE = "BLOCKED needs: human-approval (swarm-state)";
  const STATE_TOOLS = ["swarm_transition", "swarm_ingest"];
  const specialist = agentCtx(CWD, "a05-backend", [], false);

  test("SWARM_SLUGS is exactly the agents.json slug list", () => {
    const manifest = JSON.parse(readFileSync(join(REPO_ROOT, "agents.json"), "utf8")) as { agents: { slug: string }[] };
    expect([...SWARM_SLUGS].sort()).toEqual(manifest.agents.map((a) => a.slug).sort());
  });

  test.each([
    ["a05-backend (restricted)", agentCtx(CWD, "a05-backend")],
    ["a05-backend", specialist],
    ["a12-release", agentCtx(CWD, "a12-release", [], false)],
    ["a generic task child", agentCtx(CWD, "task", [], false)],
    ["the unidentified main session", fakeCtx(CWD)],
  ])("from %s, swarm_transition and swarm_ingest are blocked", (_name, ctx) => {
    const { run } = guardHandler({ activeTools: ["task"] });
    for (const toolName of STATE_TOOLS) {
      expect(run(call(toolName, { task_id: "T-1", to: "DONE" }), ctx)).toEqual({ block: true, reason: SWARM_STATE });
    }
  });

  test.each([["a05-backend"], ["task"], [undefined]])("pure: agent %p is blocked on both state tools", (agent) => {
    const facts: GuardFacts = { agent, restricted: true, planMode: false, hasTask: true, topLevel: agent === undefined, env: {}, cwd: CWD, home: HOME };
    for (const toolName of STATE_TOOLS) expect(guardToolCall(call(toolName), facts)).toEqual({ block: true, reason: SWARM_STATE });
  });

  test("from a01-orchestrator with task active, both state tools pass", () => {
    const { run } = guardHandler({ activeTools: ["read", "task", "swarm_transition", "swarm_ingest"] });
    for (const toolName of STATE_TOOLS) {
      expect(run(call(toolName, { task_id: "T-1" }), a01Ctx())).toBeUndefined();
      expect(guardToolCall(call(toolName), { ...capped, hasTask: true })).toBeUndefined();
    }
  });

  test("env bleed: session_init a05-backend with SWARM_AGENT=a01-orchestrator is still blocked", () => {
    process.env.SWARM_AGENT = A01;
    expect(callingAgent(specialist)).toBe("a05-backend");
    const { pi, run } = guardHandler({ activeTools: ["task"] });
    for (const toolName of STATE_TOOLS) expect(run(call(toolName), specialist)).toEqual({ block: true, reason: SWARM_STATE });
    expect(pi.runtimeCalls).toEqual([]); // not resolved as A01, so the A01-only read never happens
  });

  test("top-level session with SWARM_AGENT=a01-orchestrator and no session_init is allowed", () => {
    process.env.SWARM_AGENT = ` ${A01} `;
    const { run } = guardHandler({ activeTools: ["task"] });
    for (const toolName of STATE_TOOLS) expect(run(call(toolName), fakeCtx(CWD))).toBeUndefined();
  });

  test("top-level session with a blank SWARM_AGENT stays unidentified and blocked", () => {
    process.env.SWARM_AGENT = "  ";
    const { run } = guardHandler({ activeTools: ["task"] });
    for (const toolName of STATE_TOOLS) expect(run(call(toolName), fakeCtx(CWD))).toEqual({ block: true, reason: SWARM_STATE });
  });

  test.each([["swarm_plan"], ["swarm_status"]])("%s from a05-backend is not a swarm-state call", (toolName) => {
    const { run } = guardHandler({ activeTools: ["task"] });
    expect(run(call(toolName, { brief: "x" }), specialist)).toBeUndefined();
    expect(run(call(toolName, { brief: "x" }), fakeCtx(CWD))).toBeUndefined();
  });
});

describe("HOOK-02", () => {
  const B05 = "a05-backend";
  const inside = (agent = B05): GuardFacts => ({ agent, restricted: false, planMode: false, hasTask: true, topLevel: false, env: {}, cwd: CWD, home: HOME });
  const main: GuardFacts = { ...inside(), agent: undefined, topLevel: true };
  const bash = (command: string) => call("bash", { command });
  const blockedWith = (res: ReturnType<typeof guardToolCall>, capability: string) => {
    expect(res?.block).toBe(true);
    expect(res?.reason?.startsWith(`BLOCKED needs: human-approval (${capability}: `)).toBe(true);
  };

  test("every RULES row carries at least one positive sample", () => {
    for (const rule of RULES) expect(rule.samples.length).toBeGreaterThan(0);
  });

  const rows = RULES.flatMap((rule) => rule.samples.map((sample) => [rule.id, sample, rule] as const));
  test.each(rows)("row %s: %s blocks in a swarm session (handler) and passes in main", (id, sample, rule) => {
    const { run } = guardHandler({ activeTools: ["task"] });
    const agent = rule.agents?.[0] ?? B05;
    const res = run(bash(sample), agentCtx(CWD, agent, [], false)) as { block?: boolean; reason?: string };
    expect(res).toEqual({ block: true, reason: ruleReason(rule) });
    if (rule.reason === undefined) expect(ruleReason(rule)).toBe(`BLOCKED needs: human-approval (${rule.capability}: ${id})`);
    expect(run(bash(sample), fakeCtx(CWD))).toBeUndefined();
  });

  /** Independent of RULES: one command per D-03/D-04 item and the capability it must name. */
  const D03_CASES: [string, string][] = [
    ["git push --force", "destructive"],
    ["git push -f", "destructive"],
    ["git push --force-with-lease", "destructive"],
    ["git reset --hard", "destructive"],
    ["git clean -fd", "destructive"],
    ["git clean -fx", "destructive"],
    ["git branch -D x", "destructive"],
    ["git checkout -- .", "destructive"],
    ["git restore .", "destructive"],
    ["rm -rf /", "destructive"],
    ["rm -rf ~", "destructive"],
    ["rm -rf ..", "destructive"],
    ["rm -rf .git", "destructive"],
    ["rm -rf .swarm", "destructive"],
    ["rm -rf /etc/x", "destructive"],
    ["chmod -R 777 .", "destructive"],
    ['psql -c "DROP TABLE t"', "destructive_ddl"],
    ['mysql -e "ALTER TABLE t DROP COLUMN c"', "destructive_ddl"],
    ['sqlite3 db "DROP DATABASE d"', "destructive_ddl"],
    ['psql -c "DROP SCHEMA s"', "destructive_ddl"],
    ['psql -c "TRUNCATE t"', "destructive_ddl"],
    ['prisma db execute --stdin <<< "DROP TABLE t"', "destructive_ddl"],
    ["prisma migrate reset", "destructive_ddl"],
    ["prisma db push --accept-data-loss", "destructive_ddl"],
    ["terraform apply", "prod_infra"],
    ["terraform destroy", "prod_infra"],
    ["pulumi up", "prod_infra"],
    ["pulumi destroy", "prod_infra"],
    ["helm install app ./c", "prod_infra"],
    ["helm upgrade app ./c", "prod_infra"],
    ["helm uninstall app", "prod_infra"],
    ["aws ec2 terminate-instances --instance-ids i-1", "prod_infra"],
    ["gcloud app deploy", "prod_infra"],
    ["kubectl apply -n production -f k.yaml", "prod_infra"],
    ["kubectl delete pod x -n production", "prod_infra"],
    ["kubectl rollout restart deploy/api -n production", "prod_infra"],
    ["kubectl scale deploy/api --replicas=3 -n production", "prod_infra"],
    ["vercel --prod", "prod_high_risk"],
    ["fly deploy", "prod_high_risk"],
    ["gh release create v1", "prod_high_risk"],
    ["npm publish", "prod_high_risk"],
    ["pnpm publish", "prod_high_risk"],
    ["bun publish", "prod_high_risk"],
    ["docker push img", "prod_high_risk"],
    ["git push --tags", "prod_high_risk"],
    ["git push origin main", "prod_high_risk"],
    ["git push origin master", "prod_high_risk"],
  ];
  test.each(D03_CASES)("D-03: %s blocks naming %s inside, nothing in main", (command, capability) => {
    blockedWith(guardToolCall(bash(command), inside()), capability);
    expect(guardToolCall(bash(command), main)).toBeUndefined();
  });

  test.each([
    ["sudo git reset --hard", "git-reset-hard"],
    ["env A=1 git clean -fdx", "git-clean-force"],
    ["command rm -rf /", "rm-rf-protected"],
    ["git status && git push -f", "git-force-push"],
    ["cd x && sudo git push -f", "git-force-push"],
    ["ls | xargs echo ; git branch -D x", "git-branch-force-delete"],
    ["echo $(git reset --hard)", "git-reset-hard"],
    ["A=1 B=2 env C=3 sudo -E command git -C sub reset --hard", "git-reset-hard"],
    ["/usr/bin/git push --force", "git-force-push"],
    // WR-01: a quoted assignment value with spaces, and sudo/env flags that take a value
    ['GIT_SSH_COMMAND="ssh -i k" git push --force', "git-force-push"],
    ["DATABASE_URL='postgres://u p@h/db' prisma migrate reset", "prisma-migrate-reset"],
    ['X="a b" Y=\'c d\' git push -f', "git-force-push"],
    ["X=a\\ b git push -f", "git-force-push"],
    ["sudo -u root git reset --hard", "git-reset-hard"],
    ["sudo --user root -E git push -f", "git-force-push"],
    ["env -u HOME -i git clean -fdx", "git-clean-force"],
    ["doas -u root git reset --hard", "git-reset-hard"],
    // WR-02: subshell and brace grouping, backslash-newline continuation
    ["(git push -f)", "git-force-push"],
    ["{ git push -f; }", "git-force-push"],
    ["(cd sub && git reset --hard)", "git-reset-hard"],
    ["f() { git clean -fdx; }", "git-clean-force"],
    ["git \\\npush --force", "git-force-push"],
    ["git push \\\r\n  --force origin x", "git-force-push"],
    // WR-03: an apostrophe in a heredoc body or a comment must not swallow the commands after it
    ["cat <<EOF > notes.md\nDon't panic\nEOF\ngit push --force", "git-force-push"],
    ["cat <<-'EOF' > n.md\n\tDon't\n\tEOF\ngit reset --hard", "git-reset-hard"],
    ["# don't\ngit push -f", "git-force-push"],
    ["echo a #don't\ngit reset --hard", "git-reset-hard"],
    ["echo don't ; git push -f", "git-force-push"],
    // WR-04: wrappers and a literal sh -c / eval argument
    ['bash -c "git push --force"', "git-force-push"],
    ["sh -c 'git reset --hard'", "git-reset-hard"],
    ["/bin/bash -x -ec \"cd x && sh -c 'git clean -fdx'\" arg0", "git-clean-force"],
    ['bash -c "echo don\\"t; git push -f"', "git-force-push"],
    ["timeout 60 git push --force", "git-force-push"],
    ["timeout -s KILL 5m nice -n 5 git push -f", "git-force-push"],
    ["time git push -f", "git-force-push"],
    ["nohup git push -f &", "git-force-push"],
    ["echo origin | xargs git push -f", "git-force-push"],
    ["xargs -n 1 -I{} git push -f {}", "git-force-push"],
    ['eval "git push -f"', "git-force-push"],
    ["eval git push -f", "git-force-push"],
    ["stdbuf -oL exec git reset --hard", "git-reset-hard"],
  ])("normalization: %s → %s", (command, id) => {
    expect(guardToolCall(bash(command), inside())?.reason).toEndWith(`: ${id})`);
  });

  test("normalize splits segments and strips prefixes", () => {
    expect(normalize("env A=1 sudo git status && echo $(git reset --hard) || x | y; z")).toEqual([
      "git reset --hard", "git status", "echo", "x", "y", "z",
    ]);
    expect(normalize('psql -c "SELECT 1; DROP TABLE t" | cat')).toEqual(['psql -c "SELECT 1; DROP TABLE t"', "cat"]);
    expect(normalize('A="b c" B=1 env -u X sudo -u root command -p git status')).toEqual(["git status"]);
    expect(normalize("(cd x && git status) | { read a; echo ${a} {1,2}; }")).toEqual(["cd x", "git status", "read a", "echo ${a} {1,2}"]);
    expect(normalize('echo "(x)" && find . \\( -name x \\)')).toEqual(['echo "(x)"', "find . \\( -name x \\)"]);
    expect(normalize("cat <<EOF > notes.md\nDon't panic; git push -f\nEOF\necho done")).toEqual(["cat <<EOF > notes.md", "echo done"]);
    expect(normalize("echo a#b 'c;d' \"e|f\" x && psql <<< 'SELECT 1; DROP TABLE t'")).toEqual(["echo a#b 'c;d' \"e|f\" x", "psql <<< 'SELECT 1; DROP TABLE t'"]);
    expect(normalize("cat <<EOF\nno delimiter\ngit push -f")).toEqual(["cat <<EOF"]);
    expect(normalize("/bin/bash -x -ec \"cd x && sh -c 'git clean -fdx'\" arg0")).toEqual([
      "/bin/bash -x -ec \"cd x && sh -c 'git clean -fdx'\" arg0", "cd x", "sh -c 'git clean -fdx'", "git clean -fdx",
    ]);
    expect(normalize('bash -c "$VAR" && bash script.sh')).toEqual(['bash -c "$VAR"', "$VAR", "bash script.sh"]);
  });

  test.each([
    "git status", "git commit -m x", "git push origin feat/x", "git push -u origin feat/main-menu", "rm -rf ./build",
    "rm -rf node_modules dist", `rm -rf ${CWD}/tmp`, "bun test", "kubectl get pods", "kubectl apply -f k.yaml",
    "kubectl apply -n staging -f k.yaml", "git checkout -b feat", "git restore src/a.ts", "git branch -d merged",
    "git clean -n", "chmod 755 x", "npm test", "docker build .", "psql -c 'SELECT 1'", "terraform plan", "helm template x",
    "gh release view", "echo main", "aws s3 ls",
  ])("negative inside swarm: %s → undefined", (command) => {
    expect(guardToolCall(bash(command), inside())).toBeUndefined();
  });

  test("top-level session with SWARM_TASK_ID and no session_init is in the swarm", () => {
    const facts: GuardFacts = { ...main, env: { SWARM_TASK_ID: "T-1" } };
    blockedWith(guardToolCall(bash("git reset --hard"), facts), "destructive");
    const { run } = guardHandler({ activeTools: ["task"] });
    process.env.SWARM_TASK_ID = "T-1";
    expect((run(bash("npm publish"), fakeCtx(CWD)) as { reason?: string }).reason).toBe(
      "BLOCKED needs: human-approval (prod_high_risk: package-publish)",
    );
  });

  test("a generic task child (not a swarm slug) is outside", () => {
    expect(guardToolCall(bash("git reset --hard"), inside("task"))).toBeUndefined();
  });

  test.each([undefined, null, {}, { command: "" }, { command: "   \n" }, { command: 42 }, { command: ["rm", "-rf", "/"] }, "rm -rf /"])(
    "bash input %p has nothing to classify",
    (input) => {
      expect(guardToolCall(call("bash", input), inside())).toBeUndefined();
      expect(guardToolCall(call("bash", input), main)).toBeUndefined();
    },
  );
});

describe("HOOK-03 shell twin and D-08", () => {
  const B05 = "a05-backend";
  const facts = (agent: string | undefined, extra: Partial<GuardFacts> = {}): GuardFacts => ({
    agent, restricted: false, planMode: false, hasTask: true, topLevel: agent === undefined, env: {}, cwd: CWD, home: HOME, ...extra,
  });
  const bash = (command: string) => call("bash", { command });
  const SWARM_STATE = "BLOCKED needs: human-approval (swarm-state)";
  const ORCH = [
    "python3 scripts/orch_status.py --ingest r.json",
    "python3 scripts/orch_status.py --transition T-1 DONE",
    "python3 scripts/orch_plan.py --brief-text x",
    "bun scripts/ts/orch_plan.ts",
    "scripts/ts/orch_status.ts --ingest x",
    "cd /repo && SWARM_DIR=/tmp python3 ./scripts/orch_status.py --ingest=r.json",
  ];

  test.each(ORCH)("HOOK-03 shell: %s blocks for a05, passes for A01 with task and for main", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: SWARM_STATE });
    expect(guardToolCall(bash(command), facts(A01))).toBeUndefined();
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test("HOOK-03 shell: read-only orch_status from a05 passes", () => {
    expect(guardToolCall(bash("python3 scripts/orch_status.py --history"), facts(B05))).toBeUndefined();
  });

  test.each([
    ["python3 scripts/review_gate.py --task T-1", B05, true],
    ["python3 scripts/review_gate.py --task T-1", "a09-reviewer", false],
    ["python3 scripts/review_gate.py --task T-1", "a08-qa", true],
    ["python3 scripts/rev_gate.py --task T-1", "a09-reviewer", false],
    ["python3 scripts/qa_gate.py", "a08-qa", false],
    ["bun scripts/ts/qa_gate.ts", "a09-reviewer", true],
    ["python3 scripts/sec_gate.py", "a10-security", false],
    ["python3 scripts/rel_plan.py", "a12-release", false],
    ["python3 scripts/rel_plan.py", B05, true],
    ["python3 scripts/unknown_gate.py", "a09-reviewer", true],
  ])("gate script: %s from %s blocks=%p", (command, agent, blocks) => {
    const res = guardToolCall(bash(command), facts(agent));
    if (blocks) expect(res).toEqual({ block: true, reason: "BLOCKED needs: human-approval (gate: gate-script-foreign)" });
    else expect(res).toBeUndefined();
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test.each([{}, { code: "1+1" }, null])("D-08: eval %p blocks inside, passes in main", (input) => {
    expect(guardToolCall(call("eval", input), facts(B05))).toEqual({ block: true, reason: "BLOCKED needs: human-approval (eval: eval-in-swarm)" });
    expect(guardToolCall(call("eval", input), facts(undefined))).toBeUndefined();
  });

  const PROTECTED = [".swarm/tasks.db", "./.omp/config.yml", "~/.omp/agent/config.yml", `${CWD}/.swarm/x`, `${HOME}/.omp/y`, "src/../.swarm/z"];
  test.each(PROTECTED.flatMap((path) => [["write", path], ["edit", path]]))("D-08: %s %s blocks inside, passes in main", (tool, path) => {
    expect(guardToolCall(call(tool, { path, content: "x" }), facts(B05))).toEqual({
      block: true, reason: "BLOCKED needs: human-approval (protected_path: protected-path-write)",
    });
    expect(guardToolCall(call(tool, { path, content: "x" }), facts(undefined))).toBeUndefined();
  });

  /** omp's hashline (default), apply_patch and sloppy edit modes name the file inside `input`; write can drive xd://ast_edit. */
  const INPUT_SHAPED: [string, string, unknown][] = [
    ["edit", "hashline header with tag", { input: "[.omp/config.yml#AB12]\nPUT 1.=1:\n+extensions: []\n" }],
    ["edit", "hashline header without tag", { input: "*** Begin Patch\n[.swarm/tasks.db]\nPUT >$:\n+x\n*** End Patch\n" }],
    ["edit", "hashline header after a clean section", { input: "[src/app.ts#1A2B]\nPUT 1.=1:\n+a\n[~/.omp/agent/config.yml#C3D4]\nPUT 1.=1:\n+b\n" }],
    ["edit", "hashline MV into .swarm", { input: "[src/app.ts#1A2B]\nMV .swarm/app.ts\n" }],
    ["edit", "apply_patch Update File", { input: "*** Begin Patch\n*** Update File: .swarm/tasks.db\n@@\n-a\n+b\n*** End Patch\n" }],
    ["edit", "apply_patch Add File", { input: "*** Begin Patch\n*** Add File: .omp/config.yml\n+x\n*** End Patch\n" }],
    ["edit", "apply_patch Move to", { input: `*** Begin Patch\n*** Update File: src/a.ts\n*** Move to: ${CWD}/.omp/a.ts\n@@\n-a\n+b\n*** End Patch\n` }],
    ["edit", "apply_patch Delete File", { input: "*** Begin Patch\n*** Delete File: ~/.omp/x\n*** End Patch\n" }],
    ["write", "xd://ast_edit paths", { path: "xd://ast_edit", content: JSON.stringify({ ops: [{ pat: "a", out: "b" }], paths: ["src/x.ts", ".omp/config.yml"] }) }],
  ];
  test.each(INPUT_SHAPED)("D-08: %s %s blocks inside, passes in main", (tool, _shape, input) => {
    expect(guardToolCall(call(tool, input), facts(B05))).toEqual({
      block: true, reason: "BLOCKED needs: human-approval (protected_path: protected-path-write)",
    });
    expect(guardToolCall(call(tool, input), facts(undefined))).toBeUndefined();
  });

  test.each<[string, unknown]>([
    ["hashline into src", { input: "[src/app.ts#1A2B]\nPUT 1.=1:\n+x\n" }],
    ["hashline body row that looks like a header", { input: "[src/app.ts#1A2B]\nPUT 1.=1:\n+[.omp/config.yml#AB12]\n" }],
    ["apply_patch into src", { input: "*** Begin Patch\n*** Update File: src/app.ts\n@@\n-a\n+b\n*** End Patch\n" }],
    ["apply_patch body mentioning .swarm", { input: "*** Begin Patch\n*** Update File: docs/x.md\n@@\n-a\n+see .swarm/tasks.db\n*** End Patch\n" }],
    ["ast_edit on src", { path: "xd://ast_edit", content: JSON.stringify({ ops: [], paths: ["src"] }) }],
    ["ast_edit with unparsable content", { path: "xd://ast_edit", content: "{not json" }],
    ["input that is not a string", { input: 42 }],
  ])("D-08: edit %s passes inside", (_shape, input) => {
    expect(guardToolCall(call("edit", input), facts(B05))).toBeUndefined();
  });

  test.each(["src/app.ts", "docs/omp.md", ".swarmish/x", `${CWD}/a.omp`])("D-08: write %s passes", (path) => {
    expect(guardToolCall(call("write", { path }), facts(B05))).toBeUndefined();
  });

  test.each(["echo x > .swarm/a", "tee -a .omp/config.yml", "cp f ~/.omp/x", "mv a .swarm/b", "cat a >> ~/.omp/b", "install -t .swarm f"])(
    "D-08 shell: %s blocks inside, passes in main",
    (command) => {
      expect(guardToolCall(bash(command), facts(B05))).toEqual({
        block: true, reason: "BLOCKED needs: human-approval (protected_path: protected-path-shell)",
      });
      expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
    },
  );

  test.each(["cat .swarm/tasks.db", "cp .omp/config.yml /tmp/x.yml", "echo x > out.txt", "ls 2>&1 | tee log.txt"])("D-08 shell: %s passes", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toBeUndefined();
  });

  test("through the handler: eval, protected write and orch shell block for a05; main untouched", () => {
    const { run } = guardHandler({ activeTools: ["task"] });
    const a05 = agentCtx(CWD, B05, [], false);
    expect((run(call("eval", { code: "x" }), a05) as { block?: boolean }).block).toBe(true);
    expect((run(call("write", { path: ".omp/config.yml" }), a05) as { block?: boolean }).block).toBe(true);
    expect((run(bash(ORCH[0]), a05) as { reason?: string }).reason).toBe(SWARM_STATE);
    for (const ev of [call("eval", { code: "x" }), call("write", { path: ".omp/config.yml" }), bash(ORCH[0])]) {
      expect(run(ev, fakeCtx(CWD))).toBeUndefined();
    }
  });

  const MALFORMED: [string, unknown][] = [
    ["bash", null], ["bash", { command: 7 }], ["bash", { command: { x: 1 } }], ["write", null], ["write", {}], ["write", { path: 3 }],
    ["edit", { path: null }], ["edit", "path"], ["read", undefined], ["yield", null], ["eval", undefined], ["write", { path: "" }],
  ];
  test.each(MALFORMED)("malformed %s %p never throws inside or outside", (tool, input) => {
    for (const f of [facts(B05), facts(undefined), facts(A01), facts(undefined, { env: { SWARM_TASK_ID: "T" } }), facts(B05, { cwd: "", home: "" })]) {
      expect(() => guardToolCall(call(tool, input), f)).not.toThrow();
    }
    expect(guardToolCall(call(tool, input), facts(undefined))).toBeUndefined();
  });
});
