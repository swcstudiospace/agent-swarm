#!/usr/bin/env bun
/** Pass-through to scripts/swarm_run.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("swarm_run"));
