#!/usr/bin/env bun
/** Pass-through to scripts/obs_slo.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("obs_slo"));
