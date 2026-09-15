#!/usr/bin/env bun
/** Pass-through to scripts/be_contract_conformance.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("be_contract_conformance"));
