#!/usr/bin/env bun
/** Pass-through to scripts/qa_gate.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("qa_gate"));
