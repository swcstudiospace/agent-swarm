#!/usr/bin/env bun
/** Pass-through to scripts/event_schema_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("event_schema_check"));
