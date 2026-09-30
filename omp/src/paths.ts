/**
 * Workspace path resolution shared by the omp extension and the TS agent scripts (Phase 7 D-09): the one TS copy,
 * re-exported by scripts/ts/script_base.ts. Nothing runs at import; git is spawned only inside each call.
 */
import { resolve } from "node:path";

/** Git toplevel of `dir`, or undefined when `dir` is not in a repo or git is not on PATH. */
export function gitToplevel(dir: string): string | undefined {
  try {
    const git = Bun.spawnSync(["git", "-C", dir, "rev-parse", "--show-toplevel"], { stdout: "pipe", stderr: "ignore" });
    const top = git.exitCode === 0 ? git.stdout.toString().trim() : "";
    return top || undefined;
  } catch {
    return undefined; // git not on PATH → caller falls back, like swarm/paths.py's OSError fallback
  }
}

export function swarmDir(root: string): string {
  // D-10: env SWARM_DIR (made absolute) → <git toplevel of root>/.swarm → <root>/.swarm
  if (process.env.SWARM_DIR) return resolve(process.env.SWARM_DIR);
  const top = gitToplevel(root);
  return top ? resolve(top, ".swarm") : resolve(root, ".swarm");
}
