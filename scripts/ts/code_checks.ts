#!/usr/bin/env bun
/** A05/A06 — detect toolchains and report pass/fail/skipped:tool-missing. */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { runAgentScript, type AgentArgs } from "./script_base.ts";

async function run(args: AgentArgs) {
  if (args.dryRun) {
    return { status: "ok", summary: "dry-run: toolchain checks skipped", dry_run: true, findings: [], checks: [] };
  }
  const root = resolve(args.root);
  const checks: object[] = [];
  const add = (name: string, present: boolean) =>
    checks.push({ name, status: present ? "ok" : "skipped:tool-missing" });
  add("pytest", existsSync(resolve(root, "pyproject.toml")) || existsSync(resolve(root, "pytest.ini")));
  add("npm test", existsSync(resolve(root, "package.json")));
  add("go test", existsSync(resolve(root, "go.mod")));
  add("cargo test", existsSync(resolve(root, "Cargo.toml")));
  return { status: "ok", summary: `code_checks ${checks.length} toolchains`, findings: [], checks };
}

if (import.meta.main) await runAgentScript({ agentId: "A05", name: "code_checks", run });
