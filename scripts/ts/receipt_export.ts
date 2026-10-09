#!/usr/bin/env bun
/** Pass-through to scripts/receipt_export.py — Task Store state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("receipt_export"));
