#!/usr/bin/env bun
/** Pass-through to scripts/orch_from_graph.py — the plan is built in Python.

Relative --summary and --out are resolved against this process's cwd first.
passthrough then runs Python with cwd at the runtime checkout, so a relative
path would otherwise name a different file than a direct python3 invocation.
*/
import { resolve } from "node:path";
import { passthrough } from "./passthrough.ts";

const FILE_FLAGS = new Set(["--summary", "--out"]);

function absolutize(argv: string[], cwd: string): string[] {
  const out: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const eq = arg.indexOf("=");
    const flag = eq === -1 ? arg : arg.slice(0, eq);
    if (FILE_FLAGS.has(flag) && eq !== -1) {
      out.push(`${flag}=${resolve(cwd, arg.slice(eq + 1))}`);
      continue;
    }
    const next = argv[i + 1];
    if (FILE_FLAGS.has(arg) && next !== undefined && !next.startsWith("-")) {
      out.push(arg, resolve(cwd, next));
      i++;
      continue;
    }
    out.push(arg);
  }
  return out;
}

process.exit(passthrough("orch_from_graph", absolutize(process.argv.slice(2), process.cwd())));
