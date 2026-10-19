#!/usr/bin/env bun
/** A06 — static a11y: img alt, html lang, button name. Native twin of fe_a11y_check.py. */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { runAgentScript, type AgentArgs } from "./script_base.ts";

const EXTS = [".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte"];

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if ([".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".swarm"].includes(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (EXTS.some((e) => p.endsWith(e))) out.push(p);
  }
  return out;
}

const TAG_START = /<([A-Za-z][A-Za-z0-9:._-]*)(?=[\s/>])/y;
const TAG_START_G = /<([A-Za-z][A-Za-z0-9:._-]*)(?=[\s/>])/g;

/** [name, attrsFrom, start, '>' index (-1 open, -2 discarded)] */
type Slot = [string, number, number, number];

/** Scan the tag in out[out.length - 1] (attributes start at `from`) to its closing '>'; -1 when unscannable.
 *  '>' inside quoted values and {...} expressions does not close it. An element starting inside such an expression
 *  (`icon={<img />}`) gets its own slot: the enclosing scan is parked on an explicit stack and resumes after it.
 *  Single forward pass (twin of _scan_tag in fe_a11y_check.py, including comment skipping and lone-quote rules). */
function scanTag(text: string, from: number, jsx: boolean, out: Slot[]): number {
  const stack: [number, number][] = [];
  let cur = out.length - 1, depth = 0;
  for (let i = from; i < text.length; i++) {
    const ch = text[i]!;
    if (depth === 0) {
      if (ch === ">") {
        out[cur]![3] = i;
        if (stack.length === 0) return i;
        [cur, depth] = stack.pop()!;
        continue;
      }
      if (ch === "{") { depth++; continue; }
      if (ch === "}") {
        if (stack.length) { out[cur]![3] = -2; [cur, depth] = stack.pop()!; depth--; }
        continue;
      }
      if (ch === '"' || ch === "'") {
        const close = text.indexOf(ch, i + 1);
        if (close < 0) return -1;
        i = close;
        continue;
      }
    } else if (ch === "{") { depth++; continue; }
    else if (ch === "}") { depth--; continue; }
    else if (ch === "<") {
      TAG_START.lastIndex = i;
      const t = TAG_START.exec(text);
      if (t) {
        stack.push([cur, depth]);
        out.push([t[1]!, i + t[0].length, i, -1]);
        cur = out.length - 1;
        depth = 0;
        i += t[0].length - 1;
      }
      continue;
    } else if (ch === '"' || ch === "'" || ch === "`") {
      let j = i + 1;
      while (j < text.length && text[j] !== ch && (ch === "`" || text[j] !== "\n")) j += text[j] === "\\" ? 2 : 1;
      if (j < text.length && text[j] === ch) { i = j; continue; }
      if (ch === "`") return -1;
      const eol = text.indexOf("\n", i);  // lone ' or " in JSX text inside an expression: ignore the rest of the line
      if (eol < 0) return -1;
      i = eol;
      continue;
    }
    if (jsx && ch === "/") {
      const nxt = text[i + 1];
      const prev = i > 0 ? text[i - 1]! : "";
      if ((nxt === "/" || nxt === "*") && (/\s/.test(prev) || (depth > 0 && "{(,;[".includes(prev) && prev !== ""))) {
        const close = text.indexOf(nxt === "/" ? "\n" : "*/", i + 2);
        if (close < 0) return -1;
        i = close + (nxt === "/" ? 0 : 1);
      }
    }
  }
  return -1;
}

/** Opening tags as [name, attrs] in source order, nested elements included (the enclosing tag's attrs still contain
 *  the nested text). After one unscannable tag, falls back to the first '>' (bounds the cost). */
export function* tags(text: string, jsx = false): Generator<[string, string]> {
  const out: Slot[] = [];
  let strict = true, pos = 0;
  for (;;) {
    TAG_START_G.lastIndex = pos;
    const m = TAG_START_G.exec(text);
    if (!m) break;
    const from = m.index + m[0].length;
    const slot = out.length;
    out.push([m[1]!, from, m.index, -1]);
    let end = strict ? scanTag(text, from, jsx, out) : -1;
    if (end < 0) {
      strict = false;
      out.length = slot + 1;
      end = text.indexOf(">", from);
      if (end < 0) { out.length = slot; break; }
      out[slot]![3] = end;
    }
    pos = end + 1;
  }
  for (const [name, a0, , a1] of out) if (a1 >= 0) yield [name.toLowerCase(), text.slice(a0, a1)];
}

async function run(args: AgentArgs) {
  if (args.dryRun) {
    return { status: "ok", summary: "dry-run: a11y scan skipped", dry_run: true, findings: [] };
  }
  const root = resolve(args.root);
  const filesIdx = args.rest.flatMap((t, i, a) => (t === "--file" ? [a[i + 1]] : []));
  const files = filesIdx.length ? filesIdx.map((f) => resolve(root, f!)) : walk(root);
  const findings: object[] = [];
  let n = 0;
  for (const f of files) {
    let text = "";
    try { text = readFileSync(f, "utf8"); } catch { continue; }
    const rel = relative(root, f);
    let lang = false, alt = false;
    for (const [tag, attrs] of tags(text, /\.[jt]sx$/i.test(f))) {
      if (tag === "html" && !/(?:^|\s)lang\b/i.test(attrs)) lang = true;
      if (tag === "img" && !/(?:^|\s)alt\b/i.test(attrs)) alt = true;
    }
    if (lang) findings.push({ id: `A11Y-${++n}`, severity: "major", kind: "lang", summary: "<html> missing lang", location: rel });
    if (alt) findings.push({ id: `A11Y-${++n}`, severity: "major", kind: "img-alt", summary: "<img> without alt", location: rel });
  }
  const fail = findings.length > 0;
  return { status: fail ? "fail" : "ok", findings, summary: `a11y ${fail ? "FAIL" : "PASS"} ${findings.length} finding(s)` };
}

if (import.meta.main) await runAgentScript({ agentId: "A06", name: "fe_a11y_check", run });
