# M18 — The Partials: disputes/feedback, threat model, logging, routing advisor, dashboard extras + SETUP.md

## G0 · 2026-08-04
- stubs confirmed: hitl/dispute.py, hitl/feedback.py, economics/routing_advisor.py (typed stubs), security/threat_model.md ("Stub — filled in a later milestone"), observability/logging.py (stub)
- reuse: agent_events anchors (disputes/feedback as events — agent=hitl, event_type=dispute/feedback; NO schema change; audit-visible via M14 query_audit), query_audit filters, model_router defaults (routing advisor base), drift.cost_per_review delta (advisor input), governance key dependency (API protection), dashboard api client pattern
- decisions that bind us: INV-6 (disputes/feedback are append-only events anchored to real review_ids — never new tables with mutable state), INV-3 (no untrusted content in logs; JSON logging is server-side), INV-1/2 (deps inward)
- verdict: **UNBUILT** → proceed to L1 BUILD

## Micro-plan (files to touch / tests to add / demo cmd)
- `backend/hitl/dispute.py` — record_dispute(review_id, finding_id, reason, reviewer="", conn) → emit_agent_event(agent="hitl", event_type="dispute", payload={finding_id, reason, reviewer}); list_disputes(review_id, conn) → query_audit(event_type="dispute")
- `backend/hitl/feedback.py` — record_feedback(review_id, finding_id, helpful: bool, note="", conn) → event_type="feedback", payload={finding_id, helpful, note}
- `backend/observability/logging.py` — get_logger(name) → stdlib logger with JSON formatter (ts/level/logger/msg + extra fields like review_id); NO untrusted content (mask_secrets on messages)
- `backend/economics/routing_advisor.py` — suggest_model(step, cost_drift_pct, threshold_pct=20.0) → RoutingSuggestion (step, suggested_model, reason, pressure); uses model_router step default; cheaper tier when drift past threshold; 'already at floor' when default is cheapest
- `backend/security/threat_model.md` — real doc: assets, trust boundaries, threats (STRIDE-ish) → mitigations mapped to shipped modules
- `backend/api/hitl_router.py` — POST /hitl/reviews/{id}/findings/{fid}/dispute + /feedback (key-protected), GET /hitl/reviews/{id}/disputes; mount in main.py
- frontend: `src/app/drift/page.tsx` (metrics table from /audit/drift), `src/app/reviews/[id]/explain/[fid]/page.tsx` (finding explain view); server-side fetch w/ governance key
- `SETUP.md` — real-use-case deployment guide (GitHub App/webhook, .env, worker, Docker, dashboard)
- `tests/test_m18.py` — dispute/feedback event shape + audit visibility (DB-gated), logging JSON parseability, routing advisor pure logic, API 401/200
- demo cmd: `pytest tests/test_m18.py -q` && `npm run build` (frontend/)

## Freeze boundary
`backend/hitl/dispute.py`, `backend/hitl/feedback.py`, `backend/observability/logging.py`, `backend/economics/routing_advisor.py`, `backend/security/threat_model.md`, `backend/api/hitl_router.py`, `backend/main.py`, `frontend/src/app/drift/page.tsx`, `frontend/src/app/reviews/[id]/explain/[fid]/page.tsx`, `tests/test_m18.py`, `SETUP.md`

## iter log (append-only)

## iter 1 · 2026-08-04
- gate G1: pass · skills considered [agentic-swe-master, coding-orchestrator, tdd, security-engineering, production-readiness]; chose canon + tdd + security-engineering + production-readiness
- gate G2: pass · +4 backend modules from stubs (dispute, feedback, logging, routing_advisor), EventType.DISPUTE/FEEDBACK, +threat_model.md (real doc), +api/hitl_router.py (mounted), +2 dashboard pages (drift, explain) + api client types, +SETUP.md, +10 tests tests/test_m18.py
- gate G3: pass · test-driven fixes: LoggerAdapter generic arity on Python 3.11 typeshed (1 type arg; process takes MutableMapping not dict) — 2 fix passes
- gate G4: pass · demo `pytest tests/test_m18.py -q` = 10 passed (3 live-DB); `npm run build` exit 0 (7 routes incl. /drift + /reviews/[id]/explain/[fid]); full suite 197 passed; mypy 92 clean; deps 92 clean
- gate G5: pending — L4 VERIFY with fresh context next
- decision: disputes/feedback are anchored append-only events (agent=hitl, event_type=dispute/feedback — NO new tables, audit-visible via M14); logging = JSON lines, server-side, secrets masked; routing advisor consumes M16 cost drift, pure + deterministic (never auto-switches — human/deploy acts); threat model maps threats → SHIPPED mitigations only; HITL API key-protected (governance gate)
- live smoke: POST dispute → recorded event → GET disputes count 1 (reason intact); /drift page renders NO DRIFT + 5 metrics (cold-start drift query initially timed out the dashboard fetch — warm now); explain page renders finding + decision events
- next: L4 VERIFY → on APPROVE mark M18 done + update spine + commit + push (project SHIPS)

## iter 1 · L4 VERIFY #1 · 2026-08-04 — APPROVE
- verdict: **APPROVE round 1** (fresh-context subagent, verification-audit skill) — zero HIGH/MEDIUM defects
  - suite: 10/10 test_m18 · full 197 passed (reproduced on the exact tree; its run-1 4 transient failures
    all pass in isolation — migration/retrieval order flakiness, not M18) · mypy 92 clean · deps 92 clean
  - live smoke: dispute POST→200 recorded (count 1→2→3), no key→401, wrong key→401, empty reason→400;
    feedback→200; /audit/drift→200 with exactly 5 metrics; explain API keys match ExplainResponse;
    /drift + explain pages render; logging JSON lines parseable w/ review_id; INV-3 adversarial probe
    (dispute reason containing a secret read back masked); INV-6 triggers armed; key VALUE 0 hits in .next
  - LOW (fixed): SETUP.md said "187 tests" → 197; test_m18.py:115 tautological self-assert → real default
    assertion (gpt-4o-mini). Both re-verified 10/10 + mypy + deps.
- milestone status: **DONE** — 197 tests total; mypy 92 clean; deps 92 clean; build 7 routes
- **THE PROJECT SHIPS** — all 20 roadmap phases + every stub complete; SETUP.md is the deployment guide

