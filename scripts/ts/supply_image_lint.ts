#!/usr/bin/env bun
/** Pass-through to scripts/supply_image_lint.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("supply_image_lint"));
