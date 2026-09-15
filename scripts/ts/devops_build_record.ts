#!/usr/bin/env bun
/** Pass-through to scripts/devops_build_record.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("devops_build_record"));
