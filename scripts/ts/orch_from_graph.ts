#!/usr/bin/env bun
/** Pass-through to scripts/orch_from_graph.py — the plan is built in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("orch_from_graph"));
