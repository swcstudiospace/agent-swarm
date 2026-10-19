#!/usr/bin/env bun
/** Pass-through to scripts/swarm_tool.py — the registry call path and its authorization stay in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("swarm_tool"));
