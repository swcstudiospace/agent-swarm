#!/usr/bin/env bun
/** Pass-through to scripts/chain_config_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("chain_config_check"));
