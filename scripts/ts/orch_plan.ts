#!/usr/bin/env bun
/** Pass-through to scripts/orch_plan.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("orch_plan"));
