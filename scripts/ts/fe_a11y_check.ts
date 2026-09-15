#!/usr/bin/env bun
/** A06 — static a11y: img alt, html lang, button name. Native twin of fe_a11y_check.py. */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { runAgentScript, type AgentArgs } from "./script_base.ts";

const EXTS = [".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte"];

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if ([".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".swarm"].includes(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (EXTS.some((e) => p.endsWith(e))) out.push(p);
  }
  return out;
}

async function run(args: AgentArgs) {
  if (args.dryRun) {
    return { status: "ok", summary: "dry-run: a11y scan skipped", dry_run: true, findings: [] };
  }
  const root = resolve(args.root);
  const filesIdx = args.rest.flatMap((t, i, a) => (t === "--file" ? [a[i + 1]] : []));
  const files = filesIdx.length ? filesIdx.map((f) => resolve(root, f!)) : walk(root);
  const findings: object[] = [];
  let n = 0;
  for (const f of files) {
    let text = "";
    try { text = readFileSync(f, "utf8"); } catch { continue; }
    const rel = relative(root, f);
    if (/<html\b(?![^>]*\blang\b)/i.test(text)) findings.push({ id: `A11Y-${++n}`, severity: "major", kind: "lang", summary: "<html> missing lang", location: rel });
    if (/<img\b(?![^>]*\balt\b)/i.test(text)) findings.push({ id: `A11Y-${++n}`, severity: "major", kind: "img-alt", summary: "<img> without alt", location: rel });
  }
  const fail = findings.length > 0;
  return { status: fail ? "fail" : "ok", findings, summary: `a11y ${fail ? "FAIL" : "PASS"} ${findings.length} finding(s)` };
}

if (import.meta.main) await runAgentScript({ agentId: "A06", name: "fe_a11y_check", run });
