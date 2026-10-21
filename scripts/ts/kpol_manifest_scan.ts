#!/usr/bin/env bun
/** Pass-through to scripts/kpol_manifest_scan.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("kpol_manifest_scan"));
