#!/usr/bin/env bun
/** Pass-through to scripts/orch_from_graph.py — the plan is built in Python.

Relative --summary and --out are prefixed with this process's cwd. passthrough
then runs Python with cwd at the runtime checkout, so a relative path would
otherwise name a different file than a direct python3 invocation.

Absolute paths and empty values are left unchanged. The join does not call
path.resolve: that collapses ".." before Python follows a symlink, so
`/ws/link/../summary.json` (link → /other/child) would read /ws/summary.json
instead of /other/summary.json, and `--out=` would become the cwd.

passthrough also runs Python with cwd at the checkout, and AgentScript resolves
`--root` and a relative `SWARM_DIR` from that cwd. Both are pinned to the
caller first (a missing `--root` becomes the caller cwd) so events land in the
caller's workspace.
*/
import { passthrough } from "./passthrough.ts";

const FILE_FLAGS = new Set(["--summary", "--out"]);

function joinCaller(cwd: string, value: string): string {
  if (value === "" || value.startsWith("/")) return value;
  const base = cwd === "/" ? "" : cwd.replace(/\/+$/, "");
  return `${base}/${value}`;
}

function pinRoot(value: string, cwd: string): string {
  if (value === "") return cwd;
  return joinCaller(cwd, value);
}

function absolutize(argv: string[], cwd: string): string[] {
  const dir = process.env.SWARM_DIR;
  if (dir && !dir.startsWith("/")) process.env.SWARM_DIR = joinCaller(cwd, dir);

  const out: string[] = [];
  let sawRoot = false;
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const eq = arg.indexOf("=");
    const flag = eq === -1 ? arg : arg.slice(0, eq);
    if (FILE_FLAGS.has(flag) && eq !== -1) {
      out.push(`${flag}=${joinCaller(cwd, arg.slice(eq + 1))}`);
      continue;
    }
    if (flag === "--root" && eq !== -1) {
      sawRoot = true;
      out.push(`--root=${pinRoot(arg.slice(eq + 1), cwd)}`);
      continue;
    }
    const next = argv[i + 1];
    if (FILE_FLAGS.has(arg) && next !== undefined && !next.startsWith("-")) {
      out.push(arg, joinCaller(cwd, next));
      i++;
      continue;
    }
    if (arg === "--root" && next !== undefined && !next.startsWith("-")) {
      sawRoot = true;
      out.push(arg, pinRoot(next, cwd));
      i++;
      continue;
    }
    out.push(arg);
  }
  if (!sawRoot) out.push("--root", cwd);
  return out;
}

process.exit(passthrough("orch_from_graph", absolutize(process.argv.slice(2), process.cwd())));
