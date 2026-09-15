#!/usr/bin/env bun
/** Pass-through to scripts/devops_ci_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("devops_ci_check"));
