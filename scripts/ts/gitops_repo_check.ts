#!/usr/bin/env bun
/** Pass-through to scripts/gitops_repo_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("gitops_repo_check"));
