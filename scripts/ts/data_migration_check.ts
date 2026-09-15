#!/usr/bin/env bun
/** Pass-through to scripts/data_migration_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("data_migration_check"));
