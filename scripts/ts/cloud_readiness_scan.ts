#!/usr/bin/env bun
/** Pass-through to scripts/cloud_readiness_scan.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("cloud_readiness_scan"));
