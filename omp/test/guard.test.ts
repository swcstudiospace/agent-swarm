/**
 * The tool_call guard (Phase 4): HOOK-04 A01 depth cap (D-06), HOOK-03 swarm-state tools (D-05), identity (D-01)
 * and fail modes (D-02), over the pure guardToolCall and through the handler the factory registers.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Bridge } from "../src/bridge.ts";
import { callingAgent } from "../src/context.ts";
import { GUARD_ERROR_PREFIX, type GuardFacts, guardToolCall, normalize, RULES, ruleReason, SWARM_SLUGS } from "../src/guard.ts";
import { createSwarmExtension } from "../src/index.ts";
import type { ExtensionContext, SessionEntry, ToolCallEvent } from "../src/omp-api.ts";
import { agentCtx, fakeCtx, fakePi, type FakePiOptions, isolateEnv, REPO_ROOT } from "./helpers.ts";

isolateEnv("SWARM_AGENT", "SWARM_TASK_ID", "SWARM_SUBSTRATE_AGENT", "SUBSTRATE_TOKEN");
beforeEach(() => {
  delete process.env.SWARM_AGENT;
  delete process.env.SWARM_TASK_ID;
  delete process.env.SWARM_SUBSTRATE_AGENT;
  delete process.env.SUBSTRATE_TOKEN;
});

const A01 = "a01-orchestrator";
const CWD = "/nonexistent-guard-cwd";
const HOME = "/nonexistent-guard-home";
const TMP = "/nonexistent-guard-tmp";
const DEPTH_PREFIX = "BLOCKED needs: depth";

const call = (toolName: string, input: unknown = {}): ToolCallEvent => ({ toolName, toolCallId: "tc-1", input });
const yieldData = (data: unknown) => call("yield", { data });

/** A01 at the depth cap: no `task`, not a restricted child, not in plan mode. */
const capped: GuardFacts = { agent: A01, restricted: false, planMode: false, hasTask: false, topLevel: false, env: {}, cwd: CWD, home: HOME, tmp: TMP, runtimeRoots: [] };

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
    const facts: GuardFacts = { agent, restricted: true, planMode: false, hasTask: true, topLevel: agent === undefined, env: {}, cwd: CWD, home: HOME, tmp: TMP, runtimeRoots: [] };
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
  const inside = (agent = B05): GuardFacts => ({ agent, restricted: false, planMode: false, hasTask: true, topLevel: false, env: {}, cwd: CWD, home: HOME, tmp: TMP, runtimeRoots: [] });
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
    // WR-05: +refspec force pushes, --delete --force, value-taking git global options
    ["git push origin +feat/x", "git-force-push"],
    ["git push origin feat/x +HEAD:refs/heads/feat", "git-force-push"],
    ["git branch --delete --force feat", "git-branch-force-delete"],
    ["git branch -d -f feat", "git-branch-force-delete"],
    ["git branch -df feat", "git-branch-force-delete"],
    ["git --work-tree /x push --force", "git-force-push"],
    ["git --git-dir /x/.git --namespace n reset --hard", "git-reset-hard"],
    ["git -c core.x=1 --no-pager push origin +feat", "git-force-push"],
    // T-05-24: a wrapper word spelled as a path strips exactly like the bare word
    ["/usr/bin/env git push --force origin main", "git-force-push"],
    ["/bin/env git push -f", "git-force-push"],
    ["../tools/env A=1 git clean -fdx", "git-clean-force"],
    ["/usr/bin/sudo -u x git reset --hard", "git-reset-hard"],
    ["/usr/bin/doas git reset --hard", "git-reset-hard"],
    ["/usr/bin/command -p git push -f", "git-force-push"],
    ["/usr/bin/time -p git reset --hard", "git-reset-hard"],
    ["/usr/bin/nohup git push -f &", "git-force-push"],
    ["/usr/bin/timeout -s KILL 5m /usr/bin/nice -n 5 git push -f", "git-force-push"],
    ["/usr/bin/stdbuf -oL git reset --hard", "git-reset-hard"],
    ["echo origin | /usr/bin/xargs -n 1 git push -f", "git-force-push"],
    ["/usr/bin/eval 'git push -f'", "git-force-push"],
  ])("normalization: %s → %s", (command, id) => {
    expect(guardToolCall(bash(command), inside())?.reason).toEndWith(`: ${id})`);
  });

  test("normalize splits segments and strips prefixes", () => {
    // a substitution body follows the segment that holds it (it runs as that segment's subshell)
    expect(normalize("env A=1 sudo git status && echo $(git reset --hard) || x | y; z")).toEqual([
      "git status", "echo", "git reset --hard", "x", "y", "z",
    ]);
    expect(normalize('psql -c "SELECT 1; DROP TABLE t" | cat')).toEqual(['psql -c "SELECT 1; DROP TABLE t"', "cat"]);
    expect(normalize('A="b c" B=1 env -u X sudo -u root command -p git status')).toEqual(["git status"]);
    expect(normalize("/usr/bin/env -i /usr/bin/sudo -u root /bin/nice -n 5 git status")).toEqual(["git status"]);
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

  test("normalize is linear: 1 MB of stacked prefixes, quotes or groups scales linearly, never superlinearly (WR-09)", { timeout: 30_000 }, () => {
    // Same-size differential per shape: [name, adversarial input, expected for adversarial].
    // Adversarial keeps the original 1 MB inputs, so full-scale correctness coverage is unchanged.
    const shapes: [string, string, string[] | undefined][] = [
      ["assignment prefixes", `${"A=1 ".repeat(250_000)}git status`, ["git status"]],
      ["sudo stacking", `${"sudo ".repeat(200_000)}git status`, ["git status"]],
      ["timeout stacking", `${"timeout 1 ".repeat(100_000)}git status`, ["git status"]],
      ["sudo -u flags", `sudo ${"-u ".repeat(300_000)}git status`, ["git status"]],
      ["env stacking", `${"/usr/bin/env ".repeat(100_000)}git status`, ["git status"]],
      ["long path word", `${"/".repeat(1_000_000)} x`, [`${"/".repeat(1_000_000)} x`]],
      ["long value", `A=${"b".repeat(1_000_000)}`, [`A=${"b".repeat(1_000_000)}`]],
      ["quoted pairs", "'a b' ".repeat(150_000), undefined],
      ["brace groups", "x { ".repeat(200_000), undefined],
      ["heredoc lines", `${"cat <<EOF\n".repeat(1000)}${"x\n".repeat(100_000)}`, undefined],
    ];
    // Scaling, not wall-clock: each adversarial input is compared against a
    // benign reference of EQUAL byte length (repeated `git status ;` units,
    // truncated to the exact length), so fixed overhead, JIT/GC regime, and
    // heap state cancel out instead of inflating the ratio. The old cross-size
    // ratio compared a small input below normalize's ~200 KB regime knee
    // against a large input above it, which made the ratio inherently
    // environment-sensitive (isolation passed, full suite failed: 43.0x vs the
    // 40x cap on `sudo -u flags`). Equal-size inputs have no knee to straddle.
    // Reps interleave back-to-back (A,B,A,B...) in this process, so CI load
    // slows both sides together and the ratio cannot flake; best-of-N takes
    // the min each side. Linear work costs ~1x against its equal-size
    // reference; the WR-09 backtracking class costs ~1000x or more, so a 40x
    // cap catches a genuine regression decisively with wide headroom. No
    // absolute wall-clock bound is asserted. The { timeout } above is a
    // runaway budget, not a correctness assertion: a genuine superlinear hang
    // still fails fast (via the ratio or the timeout) instead of hanging CI,
    // and normal runs never approach it. Best-of-3 keeps the full-suite cost
    // at ~2-4s even under contention from the ~1400 preceding tests.
    const LINEAR_CAP = 40;
    const REPS = 3;
    const BENIGN_UNIT = "git status ; ";
    const referenceFor = (bytes: number): string =>
      BENIGN_UNIT.repeat(Math.ceil(bytes / BENIGN_UNIT.length)).slice(0, bytes);
    normalize("git status"); // warm up (JIT) before measuring
    const over: string[] = [];
    for (const [name, adversarial, expected] of shapes) {
      // Correctness at the original 1 MB scale is unchanged.
      if (expected !== undefined) expect(normalize(adversarial)).toEqual(expected);
      // The reference must exercise normalize() genuinely, not be skipped.
      const reference = referenceFor(adversarial.length);
      expect(reference.length).toBe(adversarial.length);
      expect(normalize(reference).length).toBeGreaterThan(0);
      let adversarialMs = Infinity;
      let referenceMs = Infinity;
      for (let i = 0; i < REPS; i++) {
        let t = performance.now();
        normalize(adversarial);
        const advMs = performance.now() - t;
        if (advMs < adversarialMs) adversarialMs = advMs;
        t = performance.now();
        normalize(reference);
        const refMs = performance.now() - t;
        if (refMs < referenceMs) referenceMs = refMs;
      }
      const ratio = adversarialMs / referenceMs;
      if (ratio >= LINEAR_CAP) over.push(`${name}: ${ratio.toFixed(1)}x (adversarial ${adversarialMs.toFixed(1)}ms / reference ${referenceMs.toFixed(1)}ms, best of ${REPS})`);
    }
    expect(over).toEqual([]);
  });

  test.each([
    "git status", "git commit -m x", "git push origin feat/x", "git push -u origin feat/main-menu", "rm -rf ./build",
    "rm -rf node_modules dist", `rm -rf ${CWD}/tmp`, "bun test", "kubectl get pods", "kubectl apply -f k.yaml",
    "kubectl apply -n staging -f k.yaml", "git checkout -b feat", "git restore src/a.ts", "git branch -d merged",
    "git clean -n", "chmod 755 x", "npm test", "docker build .", "psql -c 'SELECT 1'", "terraform plan", "helm template x",
    "gh release view", "echo main", "aws s3 ls",
    // WR-05 negatives: plain refspecs, --delete without force, a value-taking global option before a safe subcommand
    "git push origin feat/x:feat/x", "git branch --delete merged", "git --work-tree /x status", "git -c core.x=1 push origin feat",
    // IN-01: the tmp dir subtree is scratch space
    `rm -rf ${TMP}/build-cache`, `rm -rf ${TMP}/swarm-omp-abc/x ${TMP}/y`,
    // T-05-24: absolute-path wrappers around harmless commands, and words that only start like a wrapper
    "/usr/bin/env git status", "/usr/bin/env python3 -m pytest", "/usr/bin/time ls", "/usr/bin/timeout 5 bun test", "/bin/ls -la",
    "/usr/bin/envsubst -V", "/opt/bin/sudoers-lint x", "/usr/bin/sudo -u x git log",
  ])("negative inside swarm: %s → undefined", (command) => {
    expect(guardToolCall(bash(command), inside())).toBeUndefined();
  });

  test.each([`rm -rf ${TMP}`, `rm -rf ${TMP}/`, `rm -rf ${TMP}/../etc`, `rm -rf ${TMP}/.swarm`])("IN-01: %s still blocks", (command) => {
    blockedWith(guardToolCall(bash(command), inside()), "destructive");
  });

  test("IN-01: through the handler the real os.tmpdir() subtree is inside", () => {
    const { run } = guardHandler({ activeTools: ["task"] });
    const a05 = agentCtx(CWD, B05, [], false);
    expect(run(bash(`rm -rf ${tmpdir()}/build-cache`), a05)).toBeUndefined();
    expect((run(bash(`rm -rf ${tmpdir()}`), a05) as { reason?: string }).reason).toEndWith(": rm-rf-protected)");
  });

  /** WR-08: read-only kubectl/helm forms and markers that only occur in file or resource names pass for A11. */
  test.each([
    "kubectl rollout status deploy/api -n production",
    "kubectl rollout history deploy/api -n prod",
    "kubectl apply --dry-run=server -f x.yaml -n production",
    "kubectl apply --dry-run -f x.yaml --context=prod",
    "kubectl apply -f deploy/main-app.yaml",
    "kubectl delete -f main.yaml",
    "kubectl apply -k overlays/production",
    "kubectl apply -f x -l app=main",
    "kubectl rollout restart deploy/main-app -n staging",
    "kubectl get pods -n production",
    "helm upgrade --dry-run app ./chart",
    "helm install --dry-run --debug app ./chart",
  ])("WR-08 negative for a11: %s → undefined", (command) => {
    expect(guardToolCall(bash(command), inside("a11-devops"))).toBeUndefined();
  });

  test.each([
    "kubectl -n prod apply -f x",
    "kubectl scale deploy/api --replicas=0 --namespace=prod",
    "kubectl delete pod x -n staging --context=dev-cluster-main",
    "kubectl delete ns production",
    "kubectl rollout restart deploy/api --kubeconfig ~/.kube/prod",
    // only `--dry-run`, `=client`, `=server` (and legacy `=true`) render; `=none`/`=false` apply for real
    "kubectl apply -n production --dry-run=none -f k.yaml",
    "kubectl apply -n production --dry-run=false -f k.yaml",
  ])("WR-08 positive for a11: %s → kubectl-prod-change", (command) => {
    expect(guardToolCall(bash(command), inside("a11-devops"))?.reason).toEndWith(": kubectl-prod-change)");
  });

  test.each(["helm upgrade app ./chart --dry-run=none", "helm install app ./chart --dry-run=false"])("WR-08: %s is a live helm change", (command) => {
    expect(guardToolCall(bash(command), inside("a11-devops"))?.reason).toEndWith(": helm-release-change)");
  });

  /** HOOK-02 spellings: attached/`=` flag values, clusters, global options before the verb, aliases and verb synonyms. */
  const A11 = "a11-devops";
  const A12 = "a12-release";
  const A07 = "a07-data";
  test.each<[string, string, string]>([
    ...[
      "kubectl -nprod apply -f k.yaml", "kubectl --namespace=prod apply -f k", "kubectl --namespace prod apply -f k", "kubectl -n=prod apply -f k",
      "kubectl --context prod-cluster apply -f k", "kubectl --context=prod-cluster apply -f k", "KUBECONFIG=~/.kube/prod kubectl apply -f k",
      "kubectl --context prod set image deploy/api api=img:2", "oc -n prod delete pod x",
      ...["create -f x", "replace -f x", "patch deploy a -p '{}'", "edit deploy a", "set image deploy/a a=i:1", "label pod a x=y", "annotate pod a x=y",
        "expose deploy a --port 80", "autoscale deploy a --max 3", "drain node1", "cordon node1", "uncordon node1", "taint nodes n k=v:NoSchedule",
        "run x --image=y", "cp f a:/tmp", "exec -it a -- ls", "debug a -it --image=b", "certificate approve csr1", "rollout undo deploy/a",
        "apply edit-last-applied deploy/a"].map((verb) => `kubectl -n prod ${verb}`),
    ].map((c): [string, string, string] => [c, A11, "kubectl-prod-change"]),
    ["helm --kube-context prod upgrade --install a ./c", A11, "helm-release-change"],
    ["helm uninstall app", A11, "helm-release-change"],
    ["terraform -chdir=infra apply -auto-approve", A11, "terraform-apply-destroy"],
    ["terraform apply -destroy", A11, "terraform-apply-destroy"],
    ["terraform state rm aws_instance.x", A11, "terraform-apply-destroy"],
    ["pulumi -C infra update", A11, "pulumi-up-destroy"],
    ["aws --profile prod s3 rm s3://b/x", A11, "cloud-destructive"],
    ["aws s3 sync . s3://b --delete", A11, "cloud-destructive"],
    ["git push -fu origin x", B05, "git-force-push"],
    ["git -C x push -f", B05, "git-force-push"],
    ["git -c k=v push --force", B05, "git-force-push"],
    ["git push origin +main", B05, "git-force-push"],
    ["git push --mirror", B05, "git-force-push"],
    ["git clean -xfd", B05, "git-clean-force"],
    ["git -c clean.requireForce=false clean -d", B05, "git-clean-force"],
    ["git branch -M main", B05, "git-branch-force-delete"],
    ["git checkout -f main", B05, "git-discard-repo"],
    ["rm -Rf /", B05, "rm-rf-protected"],
    ["rm -r -f ~", B05, "rm-rf-protected"],
    ["chmod -R 0777 .", B05, "chmod-777-recursive"],
    ["chmod -R o+w .", B05, "chmod-777-recursive"],
    ['mysql -e "DROP DATABASE x"', A07, "ddl-drop-truncate"],
    ["dropdb app", A07, "ddl-drop-truncate"],
    ["supabase db reset", A07, "prisma-migrate-reset"],
    ["bin/rails db:reset", A07, "prisma-migrate-reset"],
    ["prisma db push --force-reset", A07, "db-push-accept-data-loss"],
    ["git push origin :main", A12, "git-push-protected"],
    ["git push origin --delete main", A12, "git-push-protected"],
    ["gh pr merge 3", A12, "git-push-protected"],
    ["gh -R o/r release create v1", A12, "gh-release-create"],
    ["npm --registry x publish", A12, "package-publish"],
    ["cargo publish", A12, "package-publish"],
    ["twine upload dist/*", A12, "package-publish"],
    ["docker --context x push img", A12, "docker-push"],
    ["docker buildx build --push -t img .", A12, "docker-push"],
    ["fly -a app deploy", A12, "fly-deploy"],
    ["npx vercel@latest deploy --target production", A12, "vercel-prod"],
    ["vc --prod", A12, "vercel-prod"],
  ])("HOOK-02 spelling: %s blocks for %s as %s", (command, agent, id) => {
    expect(guardToolCall(bash(command), inside(agent))?.reason).toEndWith(`: ${id})`);
  });

  test.each<[string, string]>([
    ...[
      "kubectl get pods -n production", "kubectl describe deploy -n prod x", "kubectl logs -n prod x", "kubectl apply -f k.yaml", "kubectl apply -n staging -f k.yaml",
      "kubectl -n prod rollout status deploy/a", "kubectl -n prod apply view-last-applied deploy/a", "kubectl set image deploy/api api=img:v1.2.3",
      "kubectl -n prod port-forward svc/a 8080", "kubectl -n prod diff -f k.yaml", "kubectl config use-context prod", "helm list", "helm template x ./c",
      "helm plugin install https://x", "helm status app", "terraform plan", "terraform plan -destroy", "terraform init", "terraform state list", "pulumi preview",
      "aws s3 ls", "aws s3 cp x s3://b/", "gcloud config list",
    ].map((c): [string, string] => [c, A11]),
    ...["git push origin feature", "git push -u origin feat/x", "git branch -d feature", "git checkout -b x", "git switch -c x", "git stash pop", "git clean -n",
      "chmod -R 755 .", "chmod -R g+w .", "chmod 777 file"].map((c): [string, string] => [c, B05]),
    ...["npm publish --dry-run", "cargo publish --dry-run", "docker build -t x .", "docker pull x", "vercel deploy", "fly status", "gh release view v1",
      "gh pr create -f", "gh api repos/o/r/releases", "gradle publishToMavenLocal"].map((c): [string, string] => [c, A12]),
    ...['psql -c "SELECT 1"', "prisma migrate dev", "prisma db push", "rails db:migrate"].map((c): [string, string] => [c, A07]),
  ])("HOOK-02 benign: %s passes for %s", (command, agent) => {
    expect(guardToolCall(bash(command), inside(agent))).toBeUndefined();
  });

  /** Every exemption token counts only in its own syntactic slot: never as a flag value, a later positional, or after `--`. */
  test.each<[string, string, string]>([
    ["kubectl -n production exec pod -- python3 -c 'x' --dry-run=client", A11, "kubectl-prod-change"],
    ["kubectl -n prod apply -l --dry-run -f k.yaml", A11, "kubectl-prod-change"],
    ["kubectl -n prod rollout restart deploy/status", A11, "kubectl-prod-change"],
    ["kubectl -n prod apply -f view-last-applied", A11, "kubectl-prod-change"],
    ["kubectl -n prod --as get delete ns x", A11, "kubectl-prod-change"],
    ["helm upgrade plugin ./chart", A11, "helm-release-change"],
    ["helm upgrade app ./c --set --dry-run", A11, "helm-release-change"],
    ["helm upgrade app ./c -- --dry-run", A11, "helm-release-change"],
    ["npm publish -- --dry-run", A12, "package-publish"],
    ["cargo publish -- --dry-run", A12, "package-publish"],
    ["gradle publishToMavenLocal publish", A12, "package-publish"],
    ["gzip -- -c .swarm/tasks.db", B05, "protected-path-mutate"],
    ["gzip -S -c .swarm/tasks.db", B05, "protected-path-mutate"],
    ["cd .swarm && wget -- -O https://h/tasks.db", B05, "protected-path-shell"],
    ["echo x > /dev/null/../../nonexistent-guard-cwd/.swarm/x", B05, "protected-path-shell"],
    ["PWD=/nonexistent-guard-cwd/.swarm; echo x > $PWD/tasks.db", B05, "protected-path-shell"],
    ["TMPDIR=.swarm; echo x > $TMPDIR/x", B05, "protected-path-shell"],
    ["bun --cwd . scripts/ts/orch_plan.ts", B05, "swarm-state"],
    ["node -r ./x.js scripts/ts/orch_plan.ts", B05, "swarm-state"],
  ])("exemption slot: %s blocks for %s as %s", (command, agent, id) => {
    expect(guardToolCall(bash(command), inside(agent))?.reason).toMatch(new RegExp(`[(: ]${id}\\)$`));
  });

  test.each<[string, string]>([
    ["kubectl -n prod exec pod --dry-run=client -- ls", A11], ["helm upgrade app ./c --dry-run", A11], ["helm plugin install https://x", A11],
    ["npm publish --dry-run", A12], ["gzip -c .swarm/tasks.db > /tmp/x.gz", B05], ["gzip -S .z -c .swarm/tasks.db > /tmp/x", B05],
    ["cd .swarm && echo x > /dev/null", B05], ["echo x > $PWD/out.txt", B05], ["echo x > $TMPDIR/x", B05],
  ])("exemption slot: %s still passes for %s", (command, agent) => {
    expect(guardToolCall(bash(command), inside(agent))).toBeUndefined();
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

  test("IN-05: an agent-less session_init is a child, not the top level: the SWARM_TASK_ID/SWARM_AGENT fallback never applies", () => {
    const { run } = guardHandler({ activeTools: ["task"] });
    const agentless = fakeCtx(CWD, [{ type: "session_init", restrictToolNames: false, tools: [] }]);
    process.env.SWARM_TASK_ID = "T-1";
    expect(run(bash("git reset --hard"), agentless)).toBeUndefined();
    process.env.SWARM_AGENT = "a05-backend";
    expect(run(bash("git reset --hard"), agentless)).toBeUndefined();
    expect(callingAgent(agentless)).toBeUndefined();
    // the same env in a session without any session_init is the headless swarm session it names
    expect(callingAgent(fakeCtx(CWD))).toBe("a05-backend");
    expect((run(bash("git reset --hard"), fakeCtx(CWD)) as { reason?: string }).reason).toEndWith(": git-reset-hard)");
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
    agent, restricted: false, planMode: false, hasTask: true, topLevel: agent === undefined, env: {}, cwd: CWD, home: HOME, tmp: TMP, runtimeRoots: [], ...extra,
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
    // T-05-24: an absolute-path env wrapper
    "/usr/bin/env python3 scripts/orch_status.py --ingest r.json",
    // WR-02: the executed word's basename, `-m` module runs and versioned interpreters count too
    "cd scripts && python3 orch_status.py --ingest r.json",
    "python3 -m scripts.orch_plan --brief-text x",
    "python3 -m scripts.orch_status --ingest=r.json",
    "python3.12 scripts/orch_status.py --transition T-1 DONE",
    "cd scripts/ts && bun orch_plan.ts",
    "python3 other/orch_plan.py",
  ];

  test.each(ORCH)("HOOK-03 shell: %s blocks for a05, passes for A01 with task and for main", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: SWARM_STATE });
    expect(guardToolCall(bash(command), facts(A01))).toBeUndefined();
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test("HOOK-03 shell: read-only orch_status from a05 passes", () => {
    expect(guardToolCall(bash("python3 scripts/orch_status.py --history"), facts(B05))).toBeUndefined();
  });

  /** Phase 5 D-1: every swarm agent, the gate's owner included, records gates only through swarm_gate, never the shell. */
  test.each([
    ["python3 scripts/review_gate.py --task T-1", B05, true],
    ["python3 scripts/review_gate.py --task T-1", "a09-reviewer", true],
    ["python3 scripts/review_gate.py --task T-1", "a08-qa", true],
    ["python3 scripts/rev_gate.py --task T-1", "a09-reviewer", true],
    ["cd /tmp/ws && python3 scripts/rev_gate.py --task T-1 --json", "a09-reviewer", true],
    ["python3 scripts/qa_gate.py", "a08-qa", true],
    ["bun scripts/ts/qa_gate.ts", "a09-reviewer", true],
    ["python3 scripts/sec_gate.py", "a10-security", true],
    ["python3 scripts/rel_plan.py", "a12-release", true],
    ["python3 scripts/rel_plan.py", B05, true],
    ["python3 scripts/unknown_gate.py", "a09-reviewer", false],
    ["bun run scripts/ts/sec_gate.ts", "a10-security", true],
    ["uv run python scripts/rel_plan.py", B05, true],
    ["/usr/bin/python3 -X dev /repo/scripts/qa_gate.py", "a08-qa", true],
    ["./scripts/rev_gate.py --task T-1", "a09-reviewer", true],
    // T-05-24: an absolute-path env wrapper
    ["/usr/bin/env python3 scripts/rev_gate.py", B05, true],
    ["/usr/bin/env python3 scripts/rev_gate.py --task T-1", "a09-reviewer", true],
    ["/bin/env -i /usr/bin/python3 scripts/qa_gate.py", "a08-qa", true],
    // WR-02: the probe spellings of the owner's own run
    ["cd scripts && python3 rev_gate.py --task-id T-rev --json", "a09-reviewer", true],
    ["cd /opt/agent-swarm/scripts && python3 ./rev_gate.py --task-id T-rev --json", "a09-reviewer", true],
    ["python3 -m scripts.rev_gate --task-id T-rev --json", "a09-reviewer", true],
    ["python3.12 scripts/rev_gate.py --task-id T-rev --json", "a09-reviewer", true],
    ["PYTHONPATH=. python -m scripts.rev_gate --task-id T-rev", "a09-reviewer", true],
    ["bun scripts/ts/rev_gate.ts", "a09-reviewer", true],
    ["python3 -mscripts.sec_gate", "a10-security", true],
    ["python3 -B -m scripts.rel_plan", "a12-release", true],
    ["/usr/bin/python3.11 -X dev scripts/qa_gate.py", "a08-qa", true],
    ["python3 -m scripts.unknown_gate", "a09-reviewer", false],
    ["python3.12 -m pytest tests/test_rev_gate.py", "a09-reviewer", false],
    // T-05-22: the program is the redirected script, or the script was copied aside in the same command
    ["python3 < scripts/rev_gate.py", "a09-reviewer", true],
    ["python3 - --task-id T < scripts/rev_gate.py", "a09-reviewer", true],
    ["cp scripts/rev_gate.py /tmp/r.py && python3 /tmp/r.py", "a05-backend", true],
    ["pypy3 scripts/rev_gate.py", "a09-reviewer", true],
    ["uv run --with pyyaml python scripts/rev_gate.py", "a09-reviewer", true],
    ["uv run --python 3.12 scripts/rev_gate.py", "a09-reviewer", true],
    ["python3 \"/srv/my swarm/scripts/sec_gate.py\"", "a10-security", true],
    ["echo x | python3 -c 'print(1)'", "a09-reviewer", false],
  ])("gate script: %s from %s blocks=%p", (command, agent, blocks) => {
    const res = guardToolCall(bash(command), facts(agent));
    if (blocks) expect(res).toEqual({ block: true, reason: "BLOCKED needs: human-approval (gate: gate-script-shell)" });
    else expect(res).toBeUndefined();
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  const GATE = "BLOCKED needs: human-approval (gate: gate-script-shell)";
  const INTERP = "BLOCKED needs: human-approval (destructive: interpreter-stdin)";
  test.each([
    ["python3 -<scripts/rev_gate.py --task-id T", GATE],
    ["bash -c 'python3 -' < scripts/rev_gate.py", GATE],
    ["python3 -W 'ignore:<x' - --task-id T --root /workspace < scripts/rev_gate.py", GATE],
    ["python3 -W '<x' - < scripts/rev_gate.py", GATE],
    ["cp scripts/re[v]_gate.py /tmp/r.py", GATE],
    ["cp 'scripts/rev_gate.py' /tmp/r.py", GATE],
    ["printf 'print(1)\\n' | bash -c 'python3 -'", INTERP],
    ["echo x | python3 -Wonce", INTERP],
    ["echo x | python3 - '<scripts/rev_gate.py'", INTERP],
    ["echo x | python3 -Xtracemalloc", INTERP],
    ["python3 < /dev/null", INTERP],
  ])("stdin and copy hardening: %s blocks as %s", (command, reason) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test.each([
    "echo x | python3 -c 'print(1)'",
    "echo x | python3 -Bc 'print(1)'",
    "python3 --version < /dev/null",
    "python3 --help < /dev/null",
    "python3 -V < /dev/null",
    "python3 -h < /dev/null",
    "bash -c 'python3 -' '<scripts/rev_gate.py'",
    "python3 - '<scripts/rev_gate.py'",
    "cp 'scripts/re[v]_gate.py' /tmp/r.py",
    "cp notes.txt /tmp/n.txt",
    "cp .swarm/tasks.db /tmp/tasks.db",
    "printf 'print(1)\\n' | bash -c \"$VAR\"",
    "bash -c \"$VAR\" < scripts/rev_gate.py",
  ])("stdin and copy hardening: %s passes", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toBeUndefined();
  });

  /** WR-02: each gate stem, run by its owner or another agent through each spelling, is a run. */
  const GATE_OWNER: Record<string, string> = {
    qa_gate: "a08-qa", quality_gate: "a08-qa", rev_gate: "a09-reviewer", review_gate: "a09-reviewer",
    sec_gate: "a10-security", security_gate: "a10-security", rel_plan: "a12-release", release_gate: "a12-release",
  };
  test.each(
    Object.entries(GATE_OWNER).flatMap(([stem, owner]) =>
      [`cd scripts && python3 ${stem}.py --json`, `python3 -m scripts.${stem} --json`, `python3.12 scripts/${stem}.py`, `bun scripts/ts/${stem}.ts`].flatMap(
        (command) => [[command, owner], [command, B05]],
      ),
    ),
  )("WR-02 gate script: %s from %s blocks", (command, agent) => {
    expect(guardToolCall(bash(command), facts(agent))).toEqual({ block: true, reason: "BLOCKED needs: human-approval (gate: gate-script-shell)" });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  /** WR-07: reading, testing or linting the scripts is not running them; only an execution counts. */
  test.each([
    "cat scripts/orch_plan.py",
    "grep -n foo scripts/ts/orch_status.ts",
    "python3 -m pytest tests/test_release_gate.py",
    "python3 -m pytest tests/test_hook.py scripts/rev_gate.py",
    "cat scripts/qa_gate.py",
    "ruff check scripts/rev_gate.py",
    "git diff scripts/qa_gate.py",
    "bun test tests/ts/orch_plan.test.ts",
    "python3 scripts/orch_status.py --history",
    "bun scripts/ts/orch_status.ts --history",
    // WR-02: basename and module matching still leave non-executions alone
    "python3 -m scripts.orch_status --history",
    "cd scripts && python3 orch_status.py --history",
    "cd scripts && cat rev_gate.py",
    "cd scripts && grep -n verdict rev_gate.py qa_gate.py",
    "python3 -m pytest scripts/rev_gate.py",
    "pytest tests/test_rev_gate.py",
    "python3.12 -m pytest tests/test_qa_gate.py",
    "ruff check rev_gate.py",
    "bun test tests/ts/rel_plan.test.ts",
  ])("WR-07: %s passes for a05, a08 and a09", (command) => {
    for (const agent of [B05, "a08-qa", "a09-reviewer"]) expect(guardToolCall(bash(command), facts(agent))).toBeUndefined();
  });

  test.each(["bun run scripts/ts/orch_status.ts --transition T-1 DONE", "uv run python scripts/orch_plan.py", "/usr/bin/python3 -X dev /repo/scripts/orch_plan.py"])(
    "WR-07: %s is a run and blocks for a05",
    (command) => {
      expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: SWARM_STATE });
    },
  );

  test.each([{}, { code: "1+1" }, null])("D-08: eval %p blocks inside, passes in main", (input) => {
    expect(guardToolCall(call("eval", input), facts(B05))).toEqual({ block: true, reason: "BLOCKED needs: human-approval (eval: eval-in-swarm)" });
    expect(guardToolCall(call("eval", input), facts(undefined))).toBeUndefined();
  });

  test.each([
    "xd://run_code",
    "xd://run_code?code=1",
    "XD://Run_Code",
    "xd://debug",
    "xd://debug?x=1",
    "xd://run_code/session",
  ])("D-08: write %s blocks inside, passes in main", (path) => {
    const blocked = { block: true, reason: "BLOCKED needs: human-approval (eval: xd-device)" };
    expect(guardToolCall(call("write", { path, content: "1+1" }), facts(B05))).toEqual(blocked);
    expect(guardToolCall(call("write", { file_path: path }), facts(B05))).toEqual(blocked);
    expect(guardToolCall(call("write", { path }), facts(undefined))).toBeUndefined();
  });

  test("D-08: write xd://lsp passes inside", () => {
    expect(guardToolCall(call("write", { path: "xd://lsp", content: "{}" }), facts(B05))).toBeUndefined();
  });

  test("S4 scoped substrate: the registered guard admits own-token calls but not another agent or operator MCP", () => {
    process.env.SWARM_AGENT = B05;
    process.env.SWARM_SUBSTRATE_AGENT = B05;
    process.env.SUBSTRATE_TOKEN = "tok-a05";
    const { run } = guardHandler({ activeTools: ["task"] });
    const own = fakeCtx(CWD);
    expect(run(call("mcp__substrate_memory_brief", {}), own)).toBeUndefined();
    expect(run(call("write", { path: "xd://mcp__substrate_events_emit", content: "{}" }), own)).toBeUndefined();
    expect(run(call("mcp__substrate_graph_get", {}), agentCtx(CWD, A01, [], false))?.block).toBe(true);
    expect(run(call("mcp__linear_save_issue", {}), own)?.block).toBe(true);
    expect(run(call("write", { path: "xd://mcp__notion_update_page", content: "{}" }), own)?.block).toBe(true);
  });

  test("S4 scoped substrate: a token alone, missing token, or mismatched scope never unlocks MCP", () => {
    for (const env of [
      { SUBSTRATE_TOKEN: "tok-a05" },
      { SWARM_SUBSTRATE_AGENT: B05 },
      { SWARM_SUBSTRATE_AGENT: A01, SUBSTRATE_TOKEN: "tok-a01" },
      { SWARM_SUBSTRATE_AGENT: B05, SUBSTRATE_TOKEN: "tok-a05", SUBSTRATE_DISABLED: "1" },
    ]) {
      const inside = facts(B05, { env });
      expect(guardToolCall(call("mcp__substrate_memory_brief"), inside)?.block).toBe(true);
      expect(guardToolCall(call("write", { path: "xd://mcp__substrate_events_emit" }), inside)?.block).toBe(true);
    }
  });

  test("S4 scoped substrate: namespace lookalikes and mixed device targets remain blocked", () => {
    const inside = facts(B05, { env: { SWARM_SUBSTRATE_AGENT: B05, SUBSTRATE_TOKEN: "tok-a05" } });
    for (const path of [
      "xd://mcp__substrate_admin_delete",
      "xd://mcp__substrate_graph_get/other",
      "xd://mcp__substrate_graph_get?server=linear",
      "xd://mcp__substrate_other_graph_get",
    ]) expect(guardToolCall(call("write", { path }), inside)?.block).toBe(true);
    expect(guardToolCall(call("write", {
      path: "xd://mcp__substrate_graph_get", file_path: "xd://mcp__linear_save_issue",
    }), inside)?.block).toBe(true);
  });

  test("S4 scoped substrate: A01's depth cap still precedes the own-token exception", () => {
    const inside = { ...capped, env: { SWARM_SUBSTRATE_AGENT: A01, SUBSTRATE_TOKEN: "tok-a01" } };
    expect(guardToolCall(call("mcp__substrate_graph_get"), inside)?.reason).toStartWith(DEPTH_PREFIX);
  });

  /** T-06-07: operator-credential MCP tools and their devices remain closed to every swarm session. */
  const MCP_TOOLS = ["mcp__linear_save_issue", "mcp__notion_update_page", "mcp__linear__delete_comment", "MCP__Greptile_Review"];
  const MCP_DEVICES = ["xd://mcp__linear_save_issue", "XD://MCP__Notion_Create_Pages", "xd://mcp__linear_save_issue?x=1", "xd://mcp__relume_get_component/sub", "  xd://mcp__aio_status  "];
  const headless = (): GuardFacts => facts(undefined, { env: { SWARM_AGENT: B05 } });
  const inSwarm: [string, () => GuardFacts][] = [["a05", () => facts(B05)], ["a01", () => facts(A01)], ["headless SWARM_AGENT", headless]];

  test.each(inSwarm)("T-06-07: %s blocks operator-credential MCP tools and device writes", (_, inside) => {
    for (const name of MCP_TOOLS) {
      expect(guardToolCall(call(name, { id: "x" }), inside())).toEqual({ block: true, reason: `BLOCKED needs: human-approval (mcp: ${name})` });
    }
    for (const path of MCP_DEVICES) {
      const blocked = { block: true, reason: `BLOCKED needs: human-approval (mcp: ${path.trim()})` };
      expect(guardToolCall(call("write", { path, content: "{}" }), inside())).toEqual(blocked);
      expect(guardToolCall(call("write", { file_path: path, content: "{}" }), inside())).toEqual(blocked);
    }
  });

  test("T-06-07: the main session and a generic task child still reach MCP tools and devices", () => {
    for (const outside of [facts(undefined), facts("task")]) {
      for (const name of MCP_TOOLS) expect(guardToolCall(call(name, { id: "x" }), outside)).toBeUndefined();
      for (const path of MCP_DEVICES) expect(guardToolCall(call("write", { path, content: "{}" }), outside)).toBeUndefined();
    }
  });

  test("T-06-07: through the handler, a05, A01 and a headless SWARM_AGENT session block; main passes", () => {
    const { run } = guardHandler({ activeTools: ["task"] });
    const expected = { block: true, reason: "BLOCKED needs: human-approval (mcp: mcp__linear_save_issue)" };
    const device = { block: true, reason: "BLOCKED needs: human-approval (mcp: xd://mcp__linear_save_issue)" };
    for (const ctx of [agentCtx(CWD, B05, [], false), a01Ctx()]) {
      expect(run(call("mcp__linear_save_issue", {}), ctx)).toEqual(expected);
      expect(run(call("write", { path: "xd://mcp__linear_save_issue", content: "{}" }), ctx)).toEqual(device);
    }
    expect(run(call("mcp__linear_save_issue", {}), fakeCtx(CWD))).toBeUndefined();
    expect(run(call("write", { path: "xd://mcp__linear_save_issue", content: "{}" }), fakeCtx(CWD))).toBeUndefined();
    process.env.SWARM_AGENT = B05;
    expect(run(call("mcp__linear_save_issue", {}), fakeCtx(CWD))).toEqual(expected);
    expect(run(call("write", { path: "xd://mcp__linear_save_issue", content: "{}" }), fakeCtx(CWD))).toEqual(device);
  });

  test.each([
    ["xd://lsp", undefined],
    ["xd://ast_edit", undefined],
    ["xd://mcp_x", undefined],
    ["xd://run_code", "BLOCKED needs: human-approval (eval: xd-device)"],
    ["xd://debug?x=1", "BLOCKED needs: human-approval (eval: xd-device)"],
  ])("T-06-07: write %s keeps its outcome inside", (path, blocked) => {
    const res = guardToolCall(call("write", { path, content: JSON.stringify({ ops: [], paths: ["src"] }) }), facts(B05));
    expect(res).toEqual(blocked === undefined ? undefined : { block: true, reason: blocked });
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
    ["write", "xd://ast_edit paths via file_path", { file_path: "xd://ast_edit", content: JSON.stringify({ ops: [], paths: [".swarm/tasks.db"] }) }],
    ["write", "XD://AST_EDIT?x=1 paths (any case, query)", { path: "XD://AST_EDIT?x=1", content: JSON.stringify({ ops: [], paths: ["~/.omp/x"] }) }],
    ["write", "xd://Ast_Edit/sub paths", { path: " xd://Ast_Edit/sub", content: JSON.stringify({ ops: [], paths: [".omp/config.yml"] }) }],
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

  test.each([
    "cat .swarm/tasks.db", "cp .omp/config.yml /tmp/x.yml", "echo x > out.txt", "ls 2>&1 | tee log.txt",
    // WR-06 negatives: reads and mutations elsewhere
    "sed 's/a/b/' .omp/config.yml", "sed -i 's/a/b/' README.md", "sqlite3 /tmp/x.db 'SELECT 1'", "tar -tf .swarm/a.tar", "tar -xf a.tar -C build",
    "dd if=.swarm/tasks.db of=/tmp/x", "chmod 755 x", "mkdir -p build/out", "touch out.txt", "ln -s /a /b", "rsync -a .swarm/ /tmp/bak/",
    "unzip a.zip -d build", "rmdir build", "truncate -s0 log.txt", "mv a.txt b.txt", "mv -t build a.txt", "rsync -a --remove-source-files out/ /tmp/bak/",
  ])("D-08 shell: %s passes", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toBeUndefined();
  });

  /** WR-06: in-place mutation of the state dirs through other tools; rm of them is the destructive row. */
  test.each([
    ["truncate -s0 .swarm/tasks.db", "protected_path: protected-path-mutate"],
    ["sed -i 's/a/b/' .omp/config.yml", "protected_path: protected-path-mutate"],
    ["sed -i -e 's/a/b/' .omp/config.yml", "protected_path: protected-path-mutate"],
    ["sed --in-place=.bak 's/a/b/' ~/.omp/agent/config.yml", "protected_path: protected-path-mutate"],
    ["sqlite3 ~/.omp/x.db 'DELETE FROM t'", "protected_path: protected-path-mutate"],
    ["chmod 600 .swarm/keys", "protected_path: protected-path-mutate"],
    ["chown u:g .swarm", "protected_path: protected-path-mutate"],
    ["ln -s /tmp/x .swarm/tasks.db", "protected_path: protected-path-mutate"],
    ["rsync -a src/ .swarm/", "protected_path: protected-path-mutate"],
    ["dd if=/dev/zero of=.swarm/tasks.db", "protected_path: protected-path-mutate"],
    ["tar -xf a.tar -C .swarm", "protected_path: protected-path-mutate"],
    ["tar xf a.tar --directory=.swarm", "protected_path: protected-path-mutate"],
    ["unzip a.zip -d ~/.omp", "protected_path: protected-path-mutate"],
    ["touch .swarm/x", "protected_path: protected-path-mutate"],
    ["mkdir -p .omp/extensions", "protected_path: protected-path-mutate"],
    ["rmdir .swarm/plans", "protected_path: protected-path-mutate"],
    ["unlink .omp/config.yml", "protected_path: protected-path-mutate"],
    ["mv .swarm/tasks.db /tmp/x", "protected_path: protected-path-mutate"],
    ["mv -t /tmp .omp/config.yml", "protected_path: protected-path-mutate"],
    ["mv --target-directory=/tmp a .swarm/keys", "protected_path: protected-path-mutate"],
    ["rsync -a --remove-source-files .swarm/results/ /tmp/bak/", "protected_path: protected-path-mutate"],
    ["rm .swarm/tasks.db", "destructive: rm-rf-protected"],
    ["rm -f .swarm/tasks.db", "destructive: rm-rf-protected"],
    ["rm -r .swarm", "destructive: rm-rf-protected"],
    ["rm -r .git", "destructive: rm-rf-protected"],
    ["rm -f ~/.omp/agent/config.yml", "destructive: rm-rf-protected"],
    [`rm ${CWD}/.omp/config.yml`, "destructive: rm-rf-protected"],
  ])("WR-06: %s blocks inside naming %s, passes in main", (command, tail) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${tail})` });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test("WR-06: sqlite3 on the Task Store is swarm-state for a05 and a protected-path mutation for A01", () => {
    const write = bash("sqlite3 .swarm/tasks.db \"UPDATE tasks SET state='DONE'\"");
    expect(guardToolCall(write, facts(B05))).toEqual({ block: true, reason: SWARM_STATE });
    expect(guardToolCall(bash(`sqlite3 ${CWD}/.swarm/tasks.db .dump`), facts(B05))).toEqual({ block: true, reason: SWARM_STATE });
    expect(guardToolCall(write, facts(A01))).toEqual({ block: true, reason: "BLOCKED needs: human-approval (protected_path: protected-path-mutate)" });
    expect(guardToolCall(write, facts(undefined))).toBeUndefined();
  });

  /** A `cd`/`pushd`/`env -C` earlier in the command moves where later relative targets resolve (Greptile P1). */
  test.each([
    ["cd .swarm && touch tasks.db", "protected_path: protected-path-mutate"],
    ["cd .omp; echo x > config.yml", "protected_path: protected-path-shell"],
    ["cd sub && cd ../.swarm && rm tasks.db", "destructive: rm-rf-protected"],
    ["cd .swarm/results && sed -i s/a/b/ x", "protected_path: protected-path-mutate"],
    [`pushd ${CWD}/.omp && tee config.yml`, "protected_path: protected-path-shell"],
    ["cd ~/.omp/agent && echo x >> config.yml", "protected_path: protected-path-shell"],
    ["cd -P -- .swarm && truncate -s0 tasks.db", "protected_path: protected-path-mutate"],
    ["(cd .swarm; touch tasks.db)", "protected_path: protected-path-mutate"],
    ['bash -c "cd .swarm && touch tasks.db"', "protected_path: protected-path-mutate"],
    ["cd .swarm && bash -c 'echo x > tasks.db'", "protected_path: protected-path-shell"],
    ["cd .git && rm -r objects", "destructive: rm-rf-protected"],
    ["cd .. && rm -rf nonexistent-guard-cwd", "destructive: rm-rf-protected"],
    // env -C / sudo -D run the command (and its sh -c payload) in that directory
    [`env -C ${CWD}/.swarm sh -c 'echo x > tasks.db'`, "protected_path: protected-path-shell"],
    ["env --chdir=.omp touch config.yml", "protected_path: protected-path-mutate"],
    ["env -iC .swarm touch tasks.db", "protected_path: protected-path-mutate"],
    ["sudo -D .swarm touch tasks.db", "protected_path: protected-path-mutate"],
    // a directory that cannot be resolved fails closed for every later relative write target
    ['cd "$DIR" && touch x', "protected_path: protected-path-mutate"],
    ["cd - && echo x > y", "protected_path: protected-path-shell"],
    ["cd $(dirname .swarm/x) && touch tasks.db", "protected_path: protected-path-mutate"],
    ['cd .swarm && echo "$(touch tasks.db)"', "protected_path: protected-path-mutate"],
    // scope: a move that may fail or may not run keeps the old directory too; only restored scopes drop it
    ["true || cd .swarm; touch tasks.db", "protected_path: protected-path-mutate"],
    ["cd .swarm; cd /nonexistent; touch tasks.db", "protected_path: protected-path-mutate"],
    ["pushd /nonexistent; cd .swarm; popd; touch tasks.db", "protected_path: protected-path-mutate"],
    ["cd .swarm; pushd /; pushd /nonexistent; popd; touch tasks.db", "protected_path: protected-path-mutate"],
    ["cd .swarm; cd .. & touch tasks.db", "protected_path: protected-path-mutate"],
    ["cd .swarm; nohup cd ..; touch tasks.db", "protected_path: protected-path-mutate"],
    ["cd .swarm; for d in; do cd ..; done; touch tasks.db", "protected_path: protected-path-mutate"],
    ["if cd .swarm; then touch tasks.db; fi", "protected_path: protected-path-mutate"],
    ["echo | cd .swarm; touch tasks.db", "protected_path: protected-path-mutate"],
    ["{ cd .swarm; }; touch tasks.db", "protected_path: protected-path-mutate"],
    ["(cd .swarm; case x in a) touch tasks.db;; esac)", "protected_path: protected-path-mutate"],
    ["pushd .swarm; popd +1; touch tasks.db", "protected_path: protected-path-mutate"],
  ])("D-08 cd: %s blocks inside naming %s, passes in main", (command, tail) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${tail})` });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test.each([
    "cd src && touch a.ts", "cd .swarm && cat tasks.db", "cd .swarm && ls; cd .. && git status", "cd /tmp && echo x > out.txt",
    'cd "$DIR" && echo x > /tmp/out.txt', "env -C build make", "cd build && make -j$(nproc)", "pushd src && sed -i s/a/b/ x.ts && popd",
    // restored or one-command scopes do not reach later writes
    "pushd .omp; popd; echo x > out.txt", "(cd .swarm && cat x); echo y > out.txt", "env -C .swarm true; touch a.txt",
    "cd .swarm; cd ..; touch a.txt", "cd .swarm | touch a.txt", "cd .swarm & touch a.txt", "sudo -D .swarm true && touch a.txt",
  ])("D-08 cd: %s passes", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toBeUndefined();
  });

  test("D-08 cd: sqlite3 opened after cd .swarm is swarm-state for a05", () => {
    expect(guardToolCall(bash("cd .swarm && sqlite3 tasks.db \"UPDATE tasks SET state='DONE'\""), facts(B05))).toEqual({ block: true, reason: SWARM_STATE });
    expect(guardToolCall(bash(`env -C ${CWD}/.swarm sqlite3 tasks.db .dump`), facts(B05))).toEqual({ block: true, reason: SWARM_STATE });
  });

  /** Compound keywords and a lone `&` put a command behind them; every row still sees it. */
  test.each([
    ["if true; then git push -f; fi", "destructive: git-force-push"],
    ["for i in 1; do touch .swarm/x; done", "protected_path: protected-path-mutate"],
    ["true & git push --force", "destructive: git-force-push"],
    ["! git push -f", "destructive: git-force-push"],
    ["while false; do :; done; until true; do rm -rf /; done", "destructive: rm-rf-protected"],
    ["if false; then :; elif true; then git reset --hard; else :; fi", "destructive: git-reset-hard"],
    ["make |& git push -f", "destructive: git-force-push"],
    // `>|` forms are redirections, not pipes
    ["echo x >| .swarm/tasks.db", "protected_path: protected-path-shell"],
    ["echo x 2>| .omp/config.yml", "protected_path: protected-path-shell"],
    ["echo x &>| .swarm/tasks.db", "protected_path: protected-path-shell"],
    // busybox/toybox run the applet named next
    ["busybox tee .swarm/tasks.db", "protected_path: protected-path-shell"],
    ["busybox cp /tmp/x .swarm/tasks.db", "protected_path: protected-path-shell"],
    ["toybox mv .swarm/tasks.db /tmp/x", "protected_path: protected-path-mutate"],
    ["/bin/busybox rm -rf .swarm", "destructive: rm-rf-protected"],
    ["sudo busybox sed -i s/a/b/ .omp/config.yml", "protected_path: protected-path-mutate"],
  ])("keywords: %s blocks inside naming %s", (command, tail) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${tail})` });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test.each([
    "if [ -f x ]; then echo ok; fi", "make &> build.log", "ls 2>&1 | tee log.txt", "sleep 1 & wait; echo x > out.txt", "cat a >&2",
    "ls | tee out.txt", "false || echo x > out.txt", "echo x >| out.txt", "busybox ls .swarm", "busybox --list", "toybox cat .omp/config.yml",
  ])(
    "keywords: %s passes",
    (command) => {
      expect(guardToolCall(bash(command), facts(B05))).toBeUndefined();
    },
  );

  const MUTATE = "protected_path: protected-path-mutate";
  const SHELL = "protected_path: protected-path-shell";
  const RM = "destructive: rm-rf-protected";
  const STDIN = "destructive: shell-stdin";
  /** Lexical spellings bash reads as the plain command: quoting, escapes, ANSI-C `$'…'`, `${IFS}`, brace lists, redirection placement. */
  test.each([
    ["echo x \\> | tee .swarm/tasks.db", SHELL],
    ["echo x \\>& git push -f", "destructive: git-force-push"],
    ['busybox "rm" -rf .swarm', RM],
    ["toybox 'tee' .swarm/x", SHELL],
    ["busybox \\tee .swarm/x", SHELL],
    ['"rm" -rf .swarm', RM],
    ["'git' push -f", "destructive: git-force-push"],
    ["\\rm -rf .swarm", RM],
    ['r""m -rf .swarm', RM],
    ['"sudo" rm -rf .swarm', RM],
    ['sed "-i" s/a/b/ .omp/config.yml', MUTATE],
    ["$'touch' .swarm/x", MUTATE],
    ["touch $'\\x2eswarm/x'", MUTATE],
    ["echo $'\\''; touch .swarm/x; echo '", MUTATE],
    ['sh -c "touch .sw\\arm/x"', MUTATE],
    ["touch${IFS}.swarm/x", MUTATE],
    ["{touch,.swarm/x}", MUTATE],
    ["2>/dev/null rm -rf .swarm", RM],
    ["cp x .swarm/y 2>/dev/null", SHELL],
    ["> .swarm/x", SHELL],
    ["cat <> .swarm/tasks.db", SHELL],
    ["cmd >& .swarm/x", SHELL],
    ["exec 3>.swarm/x", SHELL],
    ["touch .SWARM/x", MUTATE],
  ])("lexical: %s blocks inside naming %s", (command, tail) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${tail})` });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  /** Targets and commands bash only knows at run time fail closed: expansions, cut substitutions, `~user`, `/proc/*\/cwd`, globs that may name a state dir. */
  test.each([
    ['echo x > "$(printf .swarm/tasks.db)"', SHELL],
    ["echo x > $(echo .swarm)/tasks.db", SHELL],
    ["touch $(echo .swarm)/x", MUTATE],
    ['echo x > "$F"', SHELL],
    ['echo x | tee "$SWARM_DIR/tasks.db"', SHELL],
    ["rm -rf $(mktemp -d)", RM],
    ['for f in *.log; do rm "$f"; done', RM],
    ["echo x > ~root/x", SHELL],
    ["cd .swarm; cd ..; touch ~-/tasks.db", MUTATE],
    ["cd .swarm && touch /proc/self/cwd/x", MUTATE],
    ["HOME=/nonexistent-guard-cwd/.swarm; touch ~/x", MUTATE],
    ["CDPATH=/nonexistent-guard-cwd/.swarm cd x && touch y", MUTATE],
    ["$RM -rf .swarm", MUTATE],
    ["$(which rm) -rf .swarm", MUTATE],
    ["/bin/r? -rf .swarm", MUTATE],
    ["touch .swar[m]/x", MUTATE],
    ["rm -rf .sw*", RM],
    ["echo x > .{swarm,x}/tasks.db", SHELL],
    ["touch .{a..z}warm/x", MUTATE],
    ["touch .sw@(a)rm/x", MUTATE],
    ["rm -rf .gi?", RM],
    ["rm -rf .*", RM],
    ["rm -rf /tmp/{x,..}", RM],
    ["touch /nonexistent-guard-cwd/.?/x", MUTATE],
    ["shopt -s dotglob", "protected_path: glob-dotfiles"],
    ["GLOBIGNORE=x", "protected_path: glob-dotfiles"],
    ["shopt -qs extglob dotglob", "protected_path: glob-dotfiles"],
    ["declare -x GLOBIGNORE=.", "protected_path: glob-dotfiles"],
    ["unsetopt no_glob_dots", "protected_path: glob-dotfiles"],
    ["set -o GLOB_DOTS", "protected_path: glob-dotfiles"],
    ["zsh --globdots -c 'rm -r *'", "protected_path: glob-dotfiles"],
    ["echo x > /proc/self/cwd/.swarm/x", SHELL],
    ["echo x > /proc/self/" + "root/nonexistent-guard-cwd/.swarm/x", SHELL],
    ["echo x > /proc/1/cwd/x", SHELL],
    ["echo x > /proc/1/fd/3", SHELL],
  ])("fail closed: %s blocks inside naming %s", (command, tail) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${tail})` });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test.each([
    "grep -R dotglob .", 'git commit -m "fix dotglob handling"', "echo GLOBIGNORE", "man shopt", "shopt dotglob", "shopt -u dotglob",
    'git commit -m "GLOBIGNORE=x is unsafe"', "setopt noglobdots", "echo x > /proc/self/fd/1", "echo x > /proc/$$/fd/2",
    "echo x > /proc/thread-self/fd/0", "cmd 2> /proc/self/fd/1", "echo x > /dev/fd/3",
  ])("dotglob and descriptors: %s passes", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toBeUndefined();
  });

  /** Commands run by another command (find, xargs, parallel, runners, stdin scripts) and the less common writers. */
  test.each([
    ["find . -name tasks.db -exec rm {} \\;", RM],
    ["find . -name '*.pyc' -delete", MUTATE],
    ["find . -exec sh -c 'touch .swarm/x' \\;", MUTATE],
    ["find . -fprint .swarm/x", MUTATE],
    ["echo .swarm/tasks.db | xargs rm", RM],
    ["ls | xargs -I{} sh -c 'rm {}'", RM],
    ["parallel rm ::: a", RM],
    ["setsid rm -rf .swarm", RM],
    ["flock /tmp/l -c 'touch .swarm/x'", MUTATE],
    ["flock /tmp/l touch .swarm/x", MUTATE],
    ["su -c 'touch .swarm/x' root", MUTATE],
    ["env -S 'touch .swarm/x'", MUTATE],
    ["watch -n1 'touch .swarm/x'", MUTATE],
    ["exec -a x rm -rf .swarm", RM],
    ["pkexec touch .swarm/x", MUTATE],
    ["alias ls='rm -rf .swarm'", RM],
    ["trap 'touch .swarm/x' EXIT", MUTATE],
    ["bash -o pipefail -c 'touch .swarm/x'", MUTATE],
    ["bash <<EOF\ntouch .swarm/x\nEOF", STDIN],
    ["sh <<< 'touch .swarm/x'", STDIN],
    ["echo 'touch .swarm/x' | sh", STDIN],
    ["sudo -e .omp/config.yml", MUTATE],
    ["perl -pi -e 's/a/b/' .omp/config.yml", MUTATE],
    ["awk -i inplace '{print}' .omp/config.yml", MUTATE],
    ["sort -o .swarm/tasks.db x", SHELL],
    ["curl -o .swarm/tasks.db http://x", SHELL],
    ["cd .swarm && curl -O http://x/tasks.db", SHELL],
    ["install -d .swarm/x", MUTATE],
    ["ln .swarm/tasks.db /tmp/x", MUTATE],
    ["cp -al .swarm /tmp/x", MUTATE],
    ["npx tsx scripts/ts/orch_plan.ts", "swarm-state"],
    ["gzip .swarm/tasks.db", MUTATE],
    ["tar -cf .swarm/tasks.db src", MUTATE],
    ["zip .swarm/x.zip a", MUTATE],
    ["sponge .omp/config.yml", MUTATE],
    ["git checkout -- .omp/config.yml", MUTATE],
    ["git -C .omp checkout -- config.yml", MUTATE],
    ["git push --all origin", "prod_high_risk: git-push-protected"],
  ])("runners and writers: %s blocks inside naming %s", (command, tail) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${tail})` });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test.each([
    "echo x > /dev/null", "cmd 2>&1", "make 2>&1 | tee out.txt", "cmd >&2", "ls *.ts > out.txt", "find . -name x -exec grep y {} \\;",
    'echo x > "$HOME/notes.txt"', 'echo x > "$TMPDIR/x"', 'echo "$(date)" > build.log', "rm -rf *", "rm -r ?omp", "cp a{,.bak}",
    "echo '>' | tee out.txt", "[ -f .swarm/x ] && echo ok", '"$PYTHON" -m pytest', "git ls-files | xargs wc -l", "ls | xargs -I{} cp {} /tmp/",
    "gzip -c .swarm/tasks.db > /tmp/x.gz", "tar -czf out.tgz src", "git checkout main", "kubectl get pods -o json",
    "curl -sSL https://example.com -o /tmp/x.html", "cat <<EOF > notes.md\ntouch .swarm/x\nEOF", 'git commit -m "fix: a -> b"',
  ])("lexical: %s passes", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toBeUndefined();
  });

  /** Output-file options are per command; a substitution body runs in its holder's directory as a subshell. */
  test.each([
    ["curl -o .swarm/x https://x", SHELL],
    ["curl -sSLo .swarm/x https://x", SHELL],
    ["wget -qO.swarm/x https://x", SHELL],
    ["sort -o .swarm/x in", SHELL],
    ["gcc -o .omp/x a.c", SHELL],
    ["go test -coverprofile=.swarm/c.out ./...", SHELL],
    ['cd .swarm && echo "$(date)" > tasks.db', SHELL],
    ['echo "$(cd .swarm && touch x)"', MUTATE],
    ['cd "$(pwd)/.swarm" && touch x', MUTATE],
    ["$(touch .swarm/x)", MUTATE],
    ["cd .swarm && cat <<EOF\n$(touch x)\nEOF", MUTATE],
    ['cd .swarm && bash -c "echo $(touch x)"', MUTATE],
  ])("scoped: %s blocks inside naming %s", (command, tail) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${tail})` });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  /** Everyday development commands a specialist runs must never need approval. */
  test.each([
    "grep -o .swarm/tasks.db log.txt", "rg -o foo .swarm", 'cd build && echo "$(date)" > out.txt',
    'cd src && cat "$(git rev-parse --show-toplevel)/README.md" > /tmp/r', "echo $(git rev-parse HEAD) > VERSION",
    "npm install", "npm ci", "npm run build", "npm test -- --watch=false", "npx prettier --check .", "bun install", "bun run test",
    "bun test test/x.test.ts", "bunx tsc --noEmit", "pip install -r requirements.txt", "python3 -m pytest -q", "pytest -x tests/test_a.py",
    "git add -A", 'git commit -m "feat: x"', "git status --short", "git diff HEAD~1 -- src/", "git checkout -b feat/x", "git switch main",
    "git stash", "git pull --rebase", "git push -u origin feat/x", "make -j8 test", "cargo build --release", "cargo test", "go test ./...",
    "go build -o bin/app ./cmd/app", "docker build -t app .", "docker compose up -d", "ls -la > files.txt", "echo done | tee -a log.txt",
    "mkdir -p dist && cp -r src/*.md dist/", "rm -rf node_modules dist", "find . -name '*.ts' | xargs grep -l foo", "tar -czf dist.tgz dist",
    "curl -o /tmp/x.json https://x", "uvicorn app:app --reload &", "export NODE_ENV=test && bun test", "ruff check . --fix", "tsc -p tsconfig.json",
    "cd omp && bun run test 2>&1 | tail -20", "for f in src/*.ts; do echo $f; done", "wc -l $(git ls-files '*.py')", "date > build/stamp.txt",
  ])("common: %s passes", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toBeUndefined();
  });

  /** eval, watch, parallel and `sudo -s|-i` join all their arguments into one command line; `env -S` splits its string and appends the rest. */
  const FORCE = "destructive: git-force-push";
  test.each([
    ['eval "git push" --force', FORCE],
    ['eval git push "--force"', FORCE],
    ["eval 'git' 'push' '-f'", FORCE],
    ["eval -- git push -f", FORCE],
    ["eval $'git push \\x2df'", FORCE],
    ['env -S "git push" --force', FORCE],
    ["env -S'git push' -f", FORCE],
    ['watch -n 1 "git push" --force', FORCE],
    ['sudo -i "git push" -f', FORCE],
    ["nice -n 5 eval 'git push' -f", FORCE],
    ['eval "echo x" "> .swarm/y"', SHELL],
    ["alias a=ls b='rm -rf .swarm'", RM],
    ["trap -- 'touch .swarm/x' EXIT", MUTATE],
    ["su -c'touch .swarm/x' root", MUTATE],
    ["su --command='touch .swarm/x' root", MUTATE],
    ["su - root -c 'touch .swarm/x'", MUTATE],
    ["flock /tmp/l -c'touch .swarm/x'", MUTATE],
    ["script -q -c 'touch .swarm/x' /dev/null", MUTATE],
    // a shell's `-c` line: attached, clustered, or after `--`
    ["bash -c'touch .swarm/x'", MUTATE],
    ['sh -c"git push --force"', FORCE],
    ["bash -lc'git push -f'", FORCE],
    ['sh -ec"touch .swarm/x"', MUTATE],
    ["bash -cl 'git push -f'", FORCE],
    ["sh -c -- 'git push -f'", FORCE],
    ["/bin/zsh -o pipefail -c 'git push -f'", FORCE],
    // a shell reading its script from stdin is blocked whatever the script: it may be built at run time
    ["/bin/echo 'touch .swarm/x' | sh", STDIN],
    ["/usr/bin/printf '%s\\n' 'git push --force' | bash", STDIN],
    ["command echo 'ls' | sh", STDIN],
    ["echo -e 'touch .swarm/x' | sh", STDIN],
    ["cat <<< 'git push -f' | sh", STDIN],
    ["(echo ls) | { bash; }", STDIN],
    ["sh -s -- -y", STDIN],
    ["bash /dev/stdin < s.sh", STDIN],
    // a command that really reassigns HOME makes `~` unknowable
    ["HOME=.swarm; echo x > ~/tasks.db", SHELL],
    ["export HOME=/x; echo > ~/y", SHELL],
    ["read -r HOME; echo > ~/y", SHELL],
    ["eval 'HOME=/x'; touch ~/y", MUTATE],
  ])("joined lines: %s blocks inside naming %s", (command, tail) => {
    expect(guardToolCall(bash(command), facts(B05))).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${tail})` });
    expect(guardToolCall(bash(command), facts(undefined))).toBeUndefined();
  });

  test.each([
    "bash -c 'echo ok' 'git push -f'", 'eval "echo ok"', "watch -n 5 ls", "alias ll='ls -la'", "trap 'rm -f /tmp/lock' EXIT",
    "echo HOME > ~/results.txt", "grep HOME .env > /tmp/x", "echo $HOME > ~/y", "bash -lc 'echo ok'", "/bin/echo 'git push -f'",
    "printf 'git push -f\\n' > notes.txt", "echo 'touch .swarm/x' | cat",
    // a shell with its own script or `-c` line, and non-shells ending in `sh`, may take stdin
    "bash script.sh < in.txt", "echo x | bash -c 'cat'", "echo x | ssh host cat", "mosh host < /dev/null", "source env.sh",
    "echo ls | bash -cl 'cat'",
  ])("joined lines: %s passes", (command) => {
    expect(guardToolCall(bash(command), facts(B05))).toBeUndefined();
  });

  test("bash tool cwd and env: the command starts in `cwd`; HOME, CDPATH, GLOBIGNORE and BASHOPTS reach the shell", () => {
    const run = (input: Record<string, unknown>) => guardToolCall(call("bash", input), facts(B05));
    expect(run({ command: "touch tasks.db", cwd: ".swarm" })).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${MUTATE})` });
    expect(run({ command: "touch tasks.db", cwd: "src" })).toBeUndefined();
    // an internal URL cwd is a directory the guard cannot place
    expect(run({ command: "touch tasks.db", cwd: "local://x" })).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${MUTATE})` });
    expect(run({ command: "touch ~/tasks.db", env: { HOME: `${CWD}/.swarm` } })).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${MUTATE})` });
    expect(run({ command: "cd x && touch y", env: { CDPATH: `${CWD}/.swarm` } })).toEqual({ block: true, reason: `BLOCKED needs: human-approval (${MUTATE})` });
    for (const key of ["GLOBIGNORE", "BASHOPTS"]) {
      expect(run({ command: "ls", env: { [key]: "x" } })).toEqual({ block: true, reason: "BLOCKED needs: human-approval (protected_path: glob-dotfiles)" });
    }
    expect(guardToolCall(call("bash", { command: "touch tasks.db", cwd: ".swarm" }), facts(undefined))).toBeUndefined();
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

  /** D-08: the agent-swarm runtime root (the live guard, the gate scripts the runner runs with keys) is not writable. */
  const ROOT = "/nonexistent-swarm-root";
  const WS = "/nonexistent-ws";
  const inWs = (cwd = WS) => facts(B05, { cwd, runtimeRoots: [ROOT] });
  const RUNTIME_WRITES: [string, unknown, string][] = [
    ["write", { path: `${ROOT}/omp/src/guard.ts`, content: "x" }, "protected-path-write"],
    ["write", { file_path: `${ROOT}/agents.json`, content: "x" }, "protected-path-write"],
    ["edit", { input: `[${ROOT}/scripts/qa_gate.py#AB12]\nPUT 1.=1:\n+x\n` }, "protected-path-write"],
    ["write", { path: "xd://ast_edit", content: JSON.stringify({ ops: [], paths: [`${ROOT}/swarm/gates.py`] }) }, "protected-path-write"],
    ["bash", { command: `echo x > ${ROOT}/scripts/qa_gate.py` }, "protected-path-shell"],
    ["bash", { command: `cp x ${ROOT}/scripts/rev_gate.py` }, "protected-path-shell"],
    ["bash", { command: `cd ${ROOT}/scripts && touch qa_gate.py` }, "protected-path-mutate"],
    ["bash", { command: `sed -i s/a/b/ ${ROOT}/hooks/autonomous_run.py` }, "protected-path-mutate"],
    ["bash", { command: `mv ${ROOT}/scripts/qa_gate.py /tmp/x` }, "protected-path-mutate"],
    ["bash", { command: `echo x &>| ${ROOT}/swarm/envelope.py` }, "protected-path-shell"],
    ["bash", { command: `busybox cp /tmp/x ${ROOT}/scripts/qa_gate.py` }, "protected-path-shell"],
    ["bash", { command: "touch /nonexistent-swarm-roo?/scripts/qa_gate.py" }, "protected-path-mutate"],
    ["bash", { command: "echo x > /nonexistent-swarm-root/{scripts,x}/qa_gate.py" }, "protected-path-shell"],
    ["bash", { command: "echo x > /NONEXISTENT-SWARM-ROOT/scripts/qa_gate.py" }, "protected-path-shell"],
    ["bash", { command: `cd ../nonexistent-swarm-root && echo x >> omp/src/guard.ts` }, "protected-path-shell"],
  ];
  test.each(RUNTIME_WRITES)("runtime root: %s %p blocks from another workspace naming %s", (tool, input, id) => {
    expect(guardToolCall(call(tool, input), inWs())).toEqual({ block: true, reason: `BLOCKED needs: human-approval (protected_path: ${id})` });
    expect(guardToolCall(call(tool, input), facts(undefined, { cwd: WS, runtimeRoots: [ROOT] }))).toBeUndefined();
  });

  test("runtime root: rm of a runtime file is destructive", () => {
    expect(guardToolCall(bash(`rm ${ROOT}/scripts/sec_gate.py`), inWs())).toEqual({ block: true, reason: "BLOCKED needs: human-approval (destructive: rm-rf-protected)" });
  });

  test.each<[string, unknown]>([
    ["bash", { command: `cat ${ROOT}/scripts/qa_gate.py` }], ["bash", { command: `python3 ${ROOT}/scripts/code_checks.py --json` }],
    ["bash", { command: `cp ${ROOT}/scripts/x.py ./x.py` }], ["bash", { command: "echo x > out.txt" }],
    ["bash", { command: `echo x > ${ROOT}-other/x` }], ["write", { path: "src/app.ts", content: "x" }],
  ])("runtime root: %s %p passes from another workspace", (tool, input) => {
    expect(guardToolCall(call(tool, input), inWs())).toBeUndefined();
  });

  test.each([ROOT, `${ROOT}/omp`])("runtime root: a session working in the checkout (cwd %s) writes it (self-development residual)", (cwd) => {
    expect(guardToolCall(bash(`echo x > ${ROOT}/scripts/foo.py`), inWs(cwd))).toBeUndefined();
    expect(guardToolCall(call("write", { path: `${ROOT}/omp/src/guard.ts`, content: "x" }), inWs(cwd))).toBeUndefined();
    expect(guardToolCall(bash(`echo x > ${ROOT}/.swarm/tasks.db`), inWs(cwd))).toEqual({
      block: true, reason: "BLOCKED needs: human-approval (protected_path: protected-path-shell)",
    });
  });

  test("runtime root through the handler: the package's repo is protected from another workspace, not from itself", () => {
    const { run } = guardHandler({ activeTools: ["task"] });
    const write = call("write", { path: join(REPO_ROOT, "scripts", "qa_gate.py"), content: "x" });
    expect(run(write, agentCtx(WS, B05, [], false))).toEqual({ block: true, reason: "BLOCKED needs: human-approval (protected_path: protected-path-write)" });
    expect(run(write, agentCtx(REPO_ROOT, B05, [], false))).toBeUndefined();
    expect(run(write, fakeCtx(WS))).toBeUndefined();
  });

  const MALFORMED: [string, unknown][] = [
    ["bash", null], ["bash", { command: 7 }], ["bash", { command: { x: 1 } }], ["write", null], ["write", {}], ["write", { path: 3 }],
    ["edit", { path: null }], ["edit", "path"], ["read", undefined], ["yield", null], ["eval", undefined], ["write", { path: "" }],
  ];
  test.each(MALFORMED)("malformed %s %p never throws inside or outside", (tool, input) => {
    for (const f of [facts(B05), facts(undefined), facts(A01), facts(undefined, { env: { SWARM_TASK_ID: "T" } }), facts(B05, { cwd: "", home: "", tmp: "" })]) {
      expect(() => guardToolCall(call(tool, input), f)).not.toThrow();
    }
    expect(guardToolCall(call(tool, input), facts(undefined))).toBeUndefined();
  });
});
