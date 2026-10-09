#!/usr/bin/env bun
/**
 * Pass-through to scripts/qa_gate.py — Task Store / gate state stays in Python.
 * Pytest detection matches execution there: importlib.util.find_spec("pytest")
 * on this process's interpreter, then `sys.executable -m pytest`. Not a PATH lookup.
 */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("qa_gate"));
