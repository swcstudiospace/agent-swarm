#!/usr/bin/env bun
/** Pass-through to scripts/arch_contract_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("arch_contract_check"));
