#!/usr/bin/env bun
/** Pass-through to scripts/k8s_manifest_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("k8s_manifest_check"));
