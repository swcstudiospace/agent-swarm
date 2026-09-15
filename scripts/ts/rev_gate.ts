#!/usr/bin/env bun
/** Pass-through to scripts/rev_gate.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("rev_gate"));
