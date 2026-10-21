#!/usr/bin/env bun
/** Pass-through to scripts/audit_findings_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("audit_findings_check"));
