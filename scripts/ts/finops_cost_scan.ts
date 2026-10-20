#!/usr/bin/env bun
/** Pass-through to scripts/finops_cost_scan.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("finops_cost_scan"));
