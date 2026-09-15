#!/usr/bin/env bun
/**
 * TypeScript twin of scripts/build_agents.py.
 * Delegates render logic to the Python generator so dialects cannot drift.
 */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const py = resolve(ROOT, "scripts/build_agents.py");
const result = spawnSync("python3", [py, ...process.argv.slice(2)], { cwd: ROOT, stdio: "inherit" });
process.exit(result.status ?? 2);
