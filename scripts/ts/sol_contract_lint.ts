#!/usr/bin/env bun
/** Pass-through to scripts/sol_contract_lint.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("sol_contract_lint"));
