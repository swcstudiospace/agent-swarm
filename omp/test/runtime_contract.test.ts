/**
 * G3 (PKG-01 / OPEN-3 R1): the generated omp preamble and runtimePart() agree on the section heading and the root
 * label. Renaming either on one side alone sends specialists in a foreign workspace to cwd-relative `scripts/`.
 */
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { SWARM_SLUGS } from "../src/guard.ts";
import { RUNTIME_HEADING, RUNTIME_ROOT_LABEL, runtimePart } from "../src/hooks.ts";
import { REPO_ROOT } from "./helpers.ts";

const ROOT = "/contract-runtime-root";

describe("runtime part ↔ omp preamble contract", () => {
  test("runtimePart emits the heading and carries the root on the label line", () => {
    const lines = runtimePart(ROOT).split("\n");
    expect(lines[0]).toBe(RUNTIME_HEADING);
    expect(lines).toContain(`${RUNTIME_ROOT_LABEL} ${ROOT}`);
  });

  test.each([...SWARM_SLUGS])("%s preamble names the section and label runtimePart emits", (slug) => {
    const agent = readFileSync(join(REPO_ROOT, "omp", "agents", `${slug}.md`), "utf8");
    expect(agent).toContain(RUNTIME_HEADING);
    expect(agent).toContain(RUNTIME_ROOT_LABEL);
  });
});
