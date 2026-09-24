#!/usr/bin/env bun
/** Use one renderer for both entry points so generated Trae prompts cannot drift. */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const result = spawnSync(
  "python3",
  [resolve(ROOT, "scripts/build_trae_agents.py"), ...process.argv.slice(2)],
  { cwd: ROOT, stdio: "inherit" },
);
process.exit(result.status ?? 2);
