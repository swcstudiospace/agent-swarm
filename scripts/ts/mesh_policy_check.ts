#!/usr/bin/env bun
/** Pass-through to scripts/mesh_policy_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("mesh_policy_check"));
