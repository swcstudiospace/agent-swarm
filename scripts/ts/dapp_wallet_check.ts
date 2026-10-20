#!/usr/bin/env bun
/** Pass-through to scripts/dapp_wallet_check.py — Task Store / gate state stays in Python. */
import { passthrough } from "./passthrough.ts";
process.exit(passthrough("dapp_wallet_check"));
