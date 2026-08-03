# M15 — Frontend Dashboard (Phase 2 + 17 DX)

## G0 · 2026-08-04
- toolchain: node v24.14.0, npm 11.10.1 (verified) — Next.js 15 compatible
- wiki scan: no frontend page exists (wiki = index + log only); grounded in the article's Phase 2 gate "Dashboard shell renders; streaming wired" + Phase 17 (DX trace viewer)
- API surface: /health, /reviews/{id}, /hitl/queue, /reviews/{id}/run, /audit/events, /audit/reviews/{id}/summary, /audit/reviews/{id}/explain/{fid} — MISSING: GET /reviews (list) and a review-level trace endpoint
- repository.py: create_review_record, update_review_status, get_review_record, insert_finding, get_findings_for_review — no list_reviews
- frontend/ does not exist; node CLIs need `node ... < /dev/null`; npm create next-app is interactive → hand-write the app (no scaffolding prompts)
- decision: dashboard fetches SERVER-SIDE only (Next.js RSC) — no CORS, GOVERNANCE_API_KEY stays in frontend/.env.local, never in the browser
- verdict: **UNBUILT** → proceed to L1 BUILD

## Micro-plan (files to touch / tests to add / demo cmd)
- backend: `list_reviews(limit=50, conn)` in repository.py; `GET /reviews` in main.py (newest-first, id/repo/pr_number/status/overall_confidence/created_at); `GET /audit/reviews/{id}/trace` in api/audit.py (query_audit review-scoped — masked, time-ordered; 401 no key, 400 bad UUID)
- tests: `tests/test_dashboard_api.py` — DB-gated list_reviews ordering; TestClient: /reviews 200 shape, /audit/.../trace 401 without key, 400 bad uuid, 200 with key (DB-gated)
- frontend: hand-written Next.js 15 + TS app in `frontend/`
  - package.json (next@15, react@19, typescript; scripts dev/build/start) · next.config.ts (env API_BASE_URL + GOVERNANCE_API_KEY) · tsconfig.json
  - src/lib/api.ts — server-side fetch helpers (listReviews, getReview, getTrace, getHitlQueue); audit calls carry X-API-Key
  - src/app/layout.tsx (nav: Reviews · HITL Queue) + globals.css (dark minimal)
  - src/app/page.tsx — review list table (repo, PR, status pill, confidence, created) wrapped in <Suspense> + src/app/loading.tsx skeleton → STREAMING WIRED
  - src/app/reviews/[id]/page.tsx — review detail: record + findings table + events_count
  - src/app/reviews/[id]/trace/page.tsx — events timeline (agent, type, ts, cost, outcome, masked payload)
  - src/app/hitl/page.tsx — HITL queue table
- demo cmd: `npm run build` (frontend) → compile gate; `pytest tests/test_dashboard_api.py -q`; live smoke: uvicorn :8000 + `npm start` → curl / and /hitl → 200 with expected markers
- node CLI quirk: run npm with `< /dev/null` where TTY matters

## Freeze boundary
`frontend/**`, `backend/main.py`, `backend/database/repository.py`, `backend/api/audit.py`, `tests/test_dashboard_api.py`

## iter log (append-only)

## iter 1 · 2026-08-04
- gate G1: pass · skills considered [agentic-swe-master, coding-orchestrator, tdd, production-readiness]; chose canon + tdd + production-readiness
- gate G2: pass · backend +2 endpoints (GET /reviews list_reviews, GET /audit/reviews/{id}/trace), +1 repo fn, +5 tests tests/test_dashboard_api.py; frontend +17 files (Next.js 15 + TS app router: layout/globals, home w/ Suspense + loading skeleton = streaming wired, review detail, trace timeline, hitl queue, server-side api client)
- gate G3: pass · two BUILD passes (backend then frontend); no research needed (toolchain verified node v24/npm 11)
- gate G4: pass · `npm run build` exit 0 (4 routes, ƒ dynamic); `pytest tests/test_dashboard_api.py` 5 passed; full suite 167 passed; mypy 91 clean; deps 91 clean
- gate G5: pending — L4 VERIFY with fresh context next
- decision: dashboard fetches SERVER-SIDE only (Next.js RSC) — no CORS, GOVERNANCE_API_KEY stays in frontend/.env.local, never in browser; streaming wired via RSC Suspense + loading.tsx skeleton; trace viewer (Phase 17 DX) consumes the key-protected /audit trace endpoint; webhook-claim → POST /reviews/{id}/run?diff= is the current pipeline trigger (ARQ worker milestone later)
- live demo evidence: webhook → claim (pending) → run with golden sqli diff → escalate, 1 CRITICAL finding, 16 llm.call events w/ prompt_version, HITL critical_finding queued; dashboard renders all: home row (test-owner/test-repo escalated), detail (CRITICAL sql-injection src/db.py), trace (16 LLM + 4 decision events), hitl (critical_finding)
- environment notes: stale uvicorn on :8000 (old code) silently swallowed the first demo webhook — killed, restarted fresh; test artifacts (pending rows) cleaned, only the real review remains
- next: L4 VERIFY → on APPROVE mark M15 done + update spine + commit + push

## iter 1 · L4 VERIFY #1 · 2026-08-04 — APPROVE
- verdict: **APPROVE** (fresh-context subagent, verification-audit skill; live commands + bundle greps)
  - all 5 success criteria pass; npm run build exit 0 (4 ƒ routes); 5/5 API tests; full suite 167; mypy 91; deps 91
  - INV-3 proven: governance key VALUE 0 hits in client chunks + served HTML; backend origin 0 hits client-side;
    key only as runtime process.env refs in server files; live trace page 200 proves runtime resolution
  - live smoke: / (row test-owner/test-repo escalated), detail (CRITICAL sql-injection src/db.py),
    trace (16 LLM + prompt_version, time-ordered), /hitl (critical_finding); backend probes 401/400/200 correct
  - defects: none blocking — INFO only → polished: footer no longer hardcodes test count (drifts);
    dash-test cleanup moved to try/finally; upper clamp assertion added (limit=9999 → ≤200)
- after polish: build exit 0; targeted 24 passed; full suite 167 passed; mypy 91 clean; deps 91 clean
- milestone status: **DONE** — 167 tests total; live dashboard on :3000 consuming :8000

