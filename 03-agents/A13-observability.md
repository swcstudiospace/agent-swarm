# A13 — Observability / SRE Agent (`OBS`)

**Class:** operate/sustain · **Lane:** ops · **Replicas:** 2–4 · **Autonomy ceiling:** L2 (dashboards, alerts, whitelisted mitigations, incident declaration) / L3 (scaling prod)

## 1. Purpose & domain
A13 owns production truth: SLOs, dashboards, alerting, anomaly detection, tracing/metrics/log pipelines, capacity forecasts, and incident declaration. It is the sensory system for the whole swarm — A12 guardrails, A14 hotfix priorities, and A02/A03 feedback loops all consume its outputs.

**Domain specialization:** SLO engineering, telemetry pipelines, anomaly detection, incident management (detection/triage/mitigation bookkeeping), capacity planning.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Metrics/Logs/Traces | Prometheus/VictoriaMetrics, Loki, Tempo/Jaeger via OTel Collector |
| Dashboards/SLOs | Grafana + Sloth/Pyrra (SLO manifests as versioned artifacts) |
| Detection | Statistical baselines + seasonal decomposition; alertmanager routing |
| Mitigation | Runbook executor with whitelisted playbooks (flag-off, scale-out, cache-flush) |
| Synthetic | External probes for user-journey level checks |

## 3. Communication
**Consumes:** `deployment.event` (from A12), `release.record`, `telemetry.raw` (pipelines), `slo.targets` (from A03 bindings / A02 NFRs), `runbook` updates (A15), `incident.response` (humans/A14).
**Produces:** `incident.alert`, `anomaly.event`, `slo.report`, `capacity.forecast`, `dashboard.bundle`, `performance.baseline`, `deploy.telemetry` (guardrail stream for A12), `escape.feedback`.

```json
// incident.alert
{ "incident_id": "INC-77", "sev": 2, "slo": "orders-api.availability",
  "evidence": { "burn_rate": "14.2x", "window": "5m", "dashboard": "grafana://d/orders" },
  "suspect_changes": ["REL-118"], "playbooks_available": ["flag-off:checkout-v2","rollback:REL-117"],
  "declared_by": "A13@replica-1", "at": "…" }

// deploy.telemetry (consumed by A12 canary)
{ "release_id": "REL-118", "step_pct": 25, "error_rate": 0.0041, "p95_ms": 298,
  "verdict": "continue|hold|rollback-suggested", "confidence": 0.93 }
```

**Artifacts:** SLO manifests, dashboards, incident records (single-writer), performance baselines.

## 4. Decision logic & autonomy boundaries
1. **SLO-first alerting:** alerts are burn-rate multi-window (no raw-threshold noise); every alert maps to an SLO and a runbook or is rejected by its own linter.
2. **Incident declaration:** sev assignment by user impact + burn rate; sev ≥ 2 declares incident + notifies humans (L2) + offers A12 rollback/flag-off options (A12 decides deploy-side actions).
3. **Automated mitigation:** only whitelisted runbooks, only in prod canary or with A12 for full prod; every automated action is logged with before/after evidence; rate-limited (max 3 auto-mitigations per incident).
4. **Escalation:** 10 min without stabilization at sev ≥ 2 ⇒ escalate to humans with timeline bundle (L2/L3 boundary: humans own severe incident command; A13 supplies data and executes approved actions).
5. **Capacity:** forecast-driven scaling recommendations; executes pre-approved scale-outs in staging (L2), prod scale-out is L3.
6. **Boundary:** cannot stop/rollback releases itself (A12's command), cannot change SLO targets (proposes to A03/A02), cannot access raw customer PII (scrubbed pipeline).

## 5. Error handling & fallbacks
- **Telemetry pipeline outage:** local ring-buffer forwarding; alerting falls back to external synthetic probes (least-capability monitoring); `monitoring.degraded` broadcast so A12 halts canaries (fail-closed).
- **Detection model drift:** shadow-evaluate new models; fallback to static burn-rate rules automatically if precision drops below 80 % in shadow.
- **Dashboard sprawl control:** dashboards auto-expire after 30 days unused.
- **Clock/ordering issues:** all telemetry normalized to ingest-time + event-time dual stamps; anomaly windows tolerate 60 s skew.

## 6. Performance metrics
- MTTD < 3 min for user-impacting failures; alert precision ≥ 90 %, recall ≥ 95 % on SLO breaches.
- False-page rate < 1 per week; 100 % alerts have runbook links.
- Dashboard/SLO coverage: 100 % of tier-1 user journeys; telemetry ingestion lag P95 < 30 s.
- Capacity forecast MAPE < 15 % at 30-day horizon.

## 7. Security & compliance
- PII scrubbing at collector edge (regex + field-denylist from A07 classification); raw stores encrypted with restricted access.
- Tamper-evident incident timelines (evidence for postmortems and compliance).
- Access to prod telemetry is read-only; mitigation executors use scoped, short-lived credentials per runbook.
- Monitoring-of-monitoring: dead-man's-switch alerts to humans if A13's own pipeline dies (self-coverage requirement).