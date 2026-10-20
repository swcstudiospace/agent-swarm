#!/usr/bin/env bun
/** Pass-through to scripts/plat_catalog_lint.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("plat_catalog_lint"));
