# A09 — Code Reviewer (`REV`)

**Class:** verify · **Lane:** verify · **Replicas:** 2–6 · **Autonomy ceiling:** L2 (approve low-risk PRs) / L3 (high-risk approvals require SEC co-sign + human policy)

## 1. Purpose & domain
A09 owns the review gate: evaluates every PR produced by A05/A06/A07/A14 for correctness, standards conformance, architectural fitness, test adequacy, and maintainability — and issues `review.verdict`. It complements A08 (behavioral verification) by focusing on code quality and design conformance; it complements A10 (security) which runs in parallel.

**Domain specialization:** static analysis orchestration, diff-based semantic review, complexity/maintainability assessment, standards enforcement, review knowledge base.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Static analysis | ESLint/ruff/golangci-lint/SpotBugs per language; type checkers; complexity (cyclomatic, cognitive) |
| Semantic review | LLM reviewer constrained to standards corpus + diff context; AST tools for precise comments |
| Repo integration | Git provider review API (posts comments, sets approvals via swarm identity) |
| Standards | A03's `review.standards` + per-repo style guides; decisions corpus from memory |

## 3. Communication
**Consumes:** `code.patch` (PR events), `review.standards`, `architecture.blueprint` (fitness context), `security.findings` (A10 context, not verdict), `test.results` (A08 context), `review.feedback` (producer rebuttals).
**Produces:** `review.verdict`, `review.comments`, `code.quality.report`, `review.metrics`.

```json
// review.verdict
{ "gate": "review", "task_id": "T-884", "pr": "512", "verdict": "request_changes",
  "blocking": [ { "file": "svc/orders/handler.go", "line": 88, "rule": "ARCH.layering",
    "md": "handler calls repository directly; use service port per ADR-041" } ],
  "non_blocking": [ { "rule": "STYLE.naming", "suggestion_md": "…" } ],
  "risk_tier": "medium", "co_sign_required": false, "expires_s": 172800 }
```

**Artifacts:** review verdicts/comments (append-only), quality report per PR, reviewer standards annotations.

## 4. Decision logic & autonomy boundaries
1. **Verdict ladder:** `approve` (no blocking findings), `request_changes` (blocking findings), `block` (architecture/contract violation or gate deadlock risk). Fail-closed: missing analysis ⇒ `request_changes`, never default-approve.
2. **Auto-approve thresholds (L2):** diff < 100 lines, no changes to contracts/auth/payments/migrations, SAST clean, tests present, author has first-pass rate > 90 %. Anything else: full semantic review.
3. **High-risk paths** (auth, payments, PII, infra-as-code): require A10 co-sign before approval can be recorded (A01 enforces conjunction).
4. **Precision discipline:** every comment links a rule ID and evidence; rule disagreement by producer twice ⇒ rule flagged for standards review (fights nit-picking drift).
5. **Boundary:** suggests, never edits producer code directly (except trivial auto-fix branches labeled `auto-fix/*` which still need producer merge); cannot approve own class's output; cannot override A08/A10 verdicts — conflicts go to A01 arbitration.

## 5. Error handling & fallbacks
- **LLM unavailable/degraded:** rules-only mode (linters + AST checks); verdict annotated `mode: rules-only`; PRs with diff > 400 lines pended instead of guessed.
- **Repo API outage:** verdicts queued and replayed idempotently.
- **Oversized diffs:** request split (≤ 400 lines guidance) before semantic review; emergency path = two independent REV replicas cross-review.
- **Poison input (binary/huge files):** structural checks only + flag.

## 6. Performance metrics
- Review latency: P95 < 30 min for PRs < 400 lines.
- Comment precision ≥ 80 % (accepted/uncontested); false-approve rate < 2 % (defects later found in approved code).
- Escaped-defect delta: approved code escape rate ≤ half of unreviewed baseline.
- Standards coverage: 100 % PRs reviewed; auto-approve precision ≥ 97 %.

## 7. Security & compliance
- Read-only repository access; review identity distinct from committer identities (separation of duties).
- Comments and verdicts are tamper-evident (signed, append-only); retention per audit policy.
- No secrets/PII may be quoted into review comments (redaction filter); flagged content is referenced by path/line, not pasted.
- Co-sign rules enforce two-agent control on high-risk changes (maker–checker equivalent for compliance frameworks).