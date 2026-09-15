/**
 * Pass-through: spawn the Python twin with the same argv.
 * Used for Task Store / gate scripts so SQLite state is not reimplemented in TS.
 */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

export function passthrough(stem: string, argv: string[] = process.argv.slice(2)): number {
  const py = resolve(ROOT, "scripts", `${stem}.py`);
  const r = spawnSync("python3", [py, ...argv], { cwd: ROOT, stdio: "inherit", env: process.env });
  if (r.error) {
    console.error(JSON.stringify({ status: "error", error: { code: "E-DEP", message: String(r.error) } }));
    return 2;
  }
  return r.status ?? 2;
}
