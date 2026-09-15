#!/usr/bin/env bun
/** A10 — secrets regex scan. Native subset of sec_gate.py (full audits stay in Python). */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { runAgentScript, type AgentArgs } from "./script_base.ts";

const RULES: [string, RegExp][] = [
  ["aws-access-key", /\bAKIA[0-9A-Z]{16}\b/],
  ["github-token", /\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})/],
  ["private-key", /-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY( BLOCK)?-----/],
  ["stripe-live-key", /\bsk_live_[0-9a-zA-Z]{24,}/],
];

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if ([".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".swarm"].includes(name)) continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (st.size < 2_000_000) out.push(p);
  }
  return out;
}

async function run(args: AgentArgs) {
  if (args.dryRun) {
    return { status: "ok", summary: "dry-run: security scan skipped", dry_run: true, findings: [], verdict: "pass" };
  }
  const root = resolve(args.root);
  const findings: object[] = [];
  let n = 0;
  for (const f of walk(root)) {
    let text = "";
    try { text = readFileSync(f, "utf8"); } catch { continue; }
    for (const [rule, re] of RULES) {
      if (re.test(text)) findings.push({ id: `SEC-${++n}`, severity: "major", kind: "secret", summary: rule, location: relative(root, f) });
    }
  }
  const fail = findings.length > 0;
  return { status: fail ? "fail" : "ok", verdict: fail ? "fail" : "pass", findings, summary: `sec_gate ${fail ? "FAIL" : "PASS"}` };
}

if (import.meta.main) await runAgentScript({ agentId: "A10", name: "sec_gate", run });
