# M16 — Continuous Learning (Phase 20 — the finale)

## G0 · 2026-08-04
- wiki scan: no dedicated continuous-learning page; grounded in the roadmap Phase 20 gate "continuous aggregates; drift detection; automated evals"
- environment: `agent_health_1m` TimescaleDB CONTINUOUS AGGREGATE already live (created M3-era; refresh policy) — read path exists via economics/cost_repository.py; raw agent_events is the fallback
- schema facts (scripts/migrations/2026-06-tiger-init.sql): agent_events.review_id UUID NOT NULL (events MUST anchor to a real review — no synthetic UUIDs), payload JSONB, hypertable, append-only (triggers + REVOKE UPDATE/DELETE/TRUNCATE), index (review_id, ts DESC); finding_records FK → pr_review_records ON DELETE CASCADE
- stubs: observability/alerting.py + logging.py (Phase 10 partial), economics/routing_advisor.py (Phase 16 partial) — only alerting is in M16 scope
- decisions that bind us: INV-6 (alerts must be append-only events anchored to a real review_id; SYSTEM-level drift is report-only — no schema hack), INV-4 (no unbounded scans: windowed queries only)
- verdict: **UNBUILT** (drift.py absent; alerting stub; no drift tests) → proceed to L1 BUILD

## Micro-plan (files to touch / tests to add / demo cmd)
- `backend/observability/drift.py` — DriftMetric dataclass; _METRIC_DIRECTIONS (cost_per_review/avg_llm_latency_ms/llm_calls_per_review/error_events: up-bad; findings_per_review: down-bad); `compute_delta(window, baseline, threshold_pct, direction)` PURE function; windowed SQL per metric (parameterized, ts-bounded); `detect_drift(window_days=7, baseline_days=7, threshold_pct=20.0, min_baseline_reviews=5, conn)` → DriftReport (metrics + any_drift + sample counts); CLI `python -m backend.observability.drift` (table print, exit 0, --json)
- `backend/observability/alerting.py` — `emit_alert(review_id, level, metric, message, conn)` → agent_events row (event_type="alert", agent="alerting", payload={level,metric,message}); `alert_for_cost_spike(review_id, cost, cap, conn)` convenience
- `backend/api/audit.py` — `GET /audit/drift?window_days=&baseline_days=&threshold_pct=` (key-protected, ValueError → 400)
- `tests/test_drift.py` — 6 pure-math unit tests + DB-gated: synthetic two-window drift (cost +30% → drifted; findings -25% → drifted; min floor), alert event written + audit-visible, /audit/drift 401/200, CLI exit 0 (monkeypatched)
- demo cmd: `pytest tests/test_drift.py -q` && `python -m backend.observability.drift`

## Freeze boundary
`backend/observability/drift.py`, `backend/observability/alerting.py`, `backend/api/audit.py`, `tests/test_drift.py`

## iter log (append-only)

## iter 1 · 2026-08-04
- gate G1: pass · skills considered [agentic-swe-master, coding-orchestrator, tdd, production-readiness]; chose canon + tdd + production-readiness
- gate G2: pass · +3 modules (drift.py with pure compute_delta + windowed SQL, alerting.py from stub, api/audit.py +/audit/drift), EventType.ALERT added, +12 tests tests/test_drift.py
- gate G3: pass · two BUILD passes; test-driven fixes this iter:
  - INV-6 caught the test: backdating agent_events via UPDATE is forbidden (trigger raises) — synthetic events INSERT with explicit ts instead (append-only contract)
  - baseline backdate was 20d (outside the 7+7d windows) → 10d
  - REAL BUG (test-caught): min_baseline_reviews floor gated only findings_per_review — a tiny baseline is noise for EVERY metric → floor now gates the whole report
  - exact-value assertions replaced with relative signals (the append-only spine shares the window with all historical test rows — DISTINCT review counts are shared)
- gate G4: pass · demo `pytest tests/test_drift.py -q` = 12 passed (4 live-DB); CLI exit 0 (live report: DRIFT DETECTED — the synthetic $100 window rows persist in the spine by INV-6, so the CLI honestly shows drift); mypy 92 files clean; deps 92 clean
- gate G5: pending — L4 VERIFY with fresh context next
- decision: alerts anchor to real review_ids only (agent_events.review_id NOT NULL — system-level drift is report-only, no synthetic UUIDs); EventType.ALERT = "alert"; drift metrics: cost/latency/calls/errors up-bad, findings down-bad; /audit/drift key-protected (same governance gate)
- next: L4 VERIFY → on APPROVE mark M16 done + update spine + commit + push (roadmap COMPLETE)

## iter 1 · L4 VERIFY #1 · 2026-08-04 — APPROVE
- verdict: **APPROVE** (fresh-context subagent, verification-audit skill)
  - 12/12 drift tests; CLI exit 0 (state-dependent NO DRIFT — floor gates all metrics correctly); full suite 179; mypy 92; deps 92
  - hand-checks: compute_delta(13,10,20,"up")=(30.0,True) · (8,10,20,"up")=(-20.0,False) · (5,0,20,"up")=(None,False) · (1.5,2,20,"down")=(-25.0,True)
  - INV-6: triggers re-armed O/O after suite; emit_agent_event never generates review_ids (uuid.UUID parse only); drift.py is report-only (zero emit calls — system-level drift never a synthetic event)
  - error_events metric matches the real producer (tool_registry emits payload.status=error) — not dead
  - defects: none · INFO only (alert_for_cost_spike test-only convenience; 401-or-503 fail-closed; CLI state-dependent)
- milestone status: **DONE** — 179 tests total; mypy 92 clean; deps 92 clean; **all 20 roadmap phases complete**

