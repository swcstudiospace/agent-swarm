#!/usr/bin/env bun
/** Pass-through to scripts/swarm_live.py — live run state is derived from the Task Store and live.json in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("swarm_live"));
