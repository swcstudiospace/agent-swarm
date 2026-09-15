#!/usr/bin/env bun
/** Pass-through to scripts/docs_bundle.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("docs_bundle"));
