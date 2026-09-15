import { expect, test } from "bun:test";
import { parseAgentArgs, EXIT } from "../../scripts/ts/script_base.ts";

test("parseAgentArgs reads shared flags", () => {
  const a = parseAgentArgs(["--task-id", "T-1", "--json", "--dry-run", "--root", "/tmp"]);
  expect(a.taskId).toBe("T-1");
  expect(a.json).toBe(true);
  expect(a.dryRun).toBe(true);
  expect(EXIT.OK).toBe(0);
  expect(EXIT.FAIL).toBe(1);
  expect(EXIT.ERROR).toBe(2);
});
