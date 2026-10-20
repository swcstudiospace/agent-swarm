#!/usr/bin/env bun
/** Pass-through to scripts/resil_manifest_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("resil_manifest_check"));
