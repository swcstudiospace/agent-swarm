#!/usr/bin/env bun
/** A04 — design tokens JSON scan. Native twin of ux_tokens.py (dry-run + file presence). */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { runAgentScript, type AgentArgs } from "./script_base.ts";

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if ([".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".swarm"].includes(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/tokens?\.json$/i.test(name) || name.includes("tokens")) out.push(p);
  }
  return out;
}

async function run(args: AgentArgs) {
  if (args.dryRun) {
    return { status: "ok", summary: "dry-run: token scan skipped", dry_run: true, findings: [], files: [] };
  }
  const root = resolve(args.root);
  const fileFlag = args.rest.includes("--file") ? args.rest[args.rest.indexOf("--file") + 1] : undefined;
  const files = fileFlag ? [resolve(root, fileFlag)] : walk(root);
  const findings: object[] = [];
  for (const f of files) {
    try {
      JSON.parse(readFileSync(f, "utf8"));
    } catch (e) {
      findings.push({ id: "UX-001", severity: "major", kind: "json", summary: `invalid JSON: ${e}`, location: f });
    }
  }
  const fail = findings.length > 0;
  return { status: fail ? "fail" : "ok", files, findings, summary: `ux_tokens ${files.length} file(s)` };
}

if (import.meta.main) await runAgentScript({ agentId: "A04", name: "ux_tokens", run });
