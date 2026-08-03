# CURRENT
- active_loop: NONE
- target: M16+ — continuous learning (Phase 20), hardening (ARQ worker, HITL disputes) — not yet sliced
- iteration: 0
- last_gate: M15 DONE — L4 VERIFY APPROVE (round 1, 2026-08-04)
- last_action: M15 complete — Frontend Dashboard (Next.js 15: review list w/ RSC streaming, detail, trace viewer, HITL queue) + GET /reviews + key-protected trace endpoint; live demo rendered all pages; spine updated
- next_action: slice M16 (continuous learning Phase 20, or hardening: ARQ async worker / HITL disputes) after user picks
- model: gpt-4o-mini (backend agents) · kimi-k3/hy3 (Hermes loop, opencode-go)
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## Notes
- Design source of truth: https://www.antern.co/blogs/production-grade-ai-pr-review-agent
- Remote: https://github.com/adi9336/AI_PR_REVIEWER (public) — push after each milestone; GCM + credential store
- Stack: Python 3.11+ / FastAPI / LangGraph / Tiger Cloud / OpenAI (gpt-4o-mini) / Docker (sandbox) / Next.js 15 (frontend/)
- 167 tests all green (162 through M14 + 5 M15 dashboard API)
- check_deps: 91 files, 0 violations · mypy strict: 91 files, 0 errors
- M15: dashboard = server-side RSC only (no CORS, governance key never in bundle — verifier-grepped 0 hits); streaming via Suspense + loading.tsx; trace viewer consumes /audit/reviews/{id}/trace (key-protected, masked, time-ordered); webhook claims → POST /reviews/{id}/run?diff= triggers pipeline (ARQ worker would wire them)
- L4 discipline: M15 APPROVE round 1 (INFO-only polish applied: footer count, test try/finally, clamp assertion); M13/M14 rounds-1 REJECTs caught real defects (vacuous gate; masking leak, non-ASCII 500, invalid-UUID 500)
- Env quirks: stale uvicorn on :8000 silently serves OLD code — kill + restart before live demos; node 24/npm 11 in frontend/; frontend/.env.local (gitignored) holds API_BASE_URL + GOVERNANCE_API_KEY synced with backend/.env
- M2 folded into M9 · backend/.env uses gpt-4o-mini (LlmClient = OpenAI direct) · M13 canary chirp-proven
