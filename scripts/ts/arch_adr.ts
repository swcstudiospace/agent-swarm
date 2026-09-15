#!/usr/bin/env bun
/** Pass-through to scripts/arch_adr.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("arch_adr"));
