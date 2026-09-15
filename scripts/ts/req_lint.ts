#!/usr/bin/env bun
/** A02 — lint AC-* Given/When/Then criteria. Native TS twin of req_lint.py. */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { parseAgentArgs, runAgentScript, type AgentArgs } from "./script_base.ts";

const AC = /\b(AC-\d[A-Za-z0-9_.]*)\b/g;
const VAGUE = ["fast", "quick", "user-friendly", "intuitive", "easy", "simple", "robust", "scalable"];

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if ([".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".swarm"].includes(name)) continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (p.endsWith(".md") || p.endsWith(".json")) out.push(p);
  }
  return out;
}

function lint(text: string, file: string): { findings: object[]; criteria: number } {
  const findings: object[] = [];
  const ids = [...text.matchAll(AC)];
  let n = 0;
  for (const m of ids) {
    n++;
    const id = m[1];
    const start = m.index ?? 0;
    const body = text.slice(start, start + 600).toLowerCase();
    if (!body.includes("given") || !body.includes("when") || !body.includes("then")) {
      findings.push({ id: `RF-${n}`, severity: "major", kind: "structure", summary: `${id} lacks Given/When/Then`, location: file });
    }
    const vague = VAGUE.filter((w) => body.includes(w));
    if (vague.length) findings.push({ id: `RF-${n}v`, severity: "minor", kind: "vague", summary: `${id} vague: ${vague.join(",")}`, location: file });
  }
  return { findings, criteria: n };
}

async function run(args: AgentArgs) {
  if (args.dryRun) {
    return { status: "ok", summary: "dry-run: 3 criteria linted, 0 findings", dry_run: true, files: [{ file: "docs/acceptance.md", criteria: 3, manual: 0 }], findings: [], verdict: "pass" };
  }
  const fileFlag = args.rest.includes("--file") ? args.rest[args.rest.indexOf("--file") + 1] : undefined;
  const root = resolve(args.root);
  const files = fileFlag ? [resolve(root, fileFlag)] : walk(root).filter((p) => readFileSync(p, "utf8").includes("AC-"));
  const findings: object[] = [];
  const reports: object[] = [];
  for (const f of files) {
    try {
      const r = lint(readFileSync(f, "utf8"), f);
      findings.push(...r.findings);
      reports.push({ file: f, criteria: r.criteria });
    } catch { /* skip unreadable */ }
  }
  const fail = findings.some((x) => (x as { severity?: string }).severity === "major");
  return { status: fail ? "fail" : "ok", verdict: fail ? "fail" : "pass", files: reports, findings, summary: `req lint ${fail ? "FAIL" : "PASS"}` };
}

if (import.meta.main) {
  // consume --file into rest via parseAgentArgs in runAgentScript
  await runAgentScript({ agentId: "A02", name: "req_lint", run });
}
