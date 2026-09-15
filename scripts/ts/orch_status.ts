#!/usr/bin/env bun
/** Pass-through to scripts/orch_status.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("orch_status"));
