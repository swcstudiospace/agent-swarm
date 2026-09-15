#!/usr/bin/env bun
/** Pass-through to scripts/maint_deps.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("maint_deps"));
