# CURRENT
- active_loop: NONE
- target: M17 hardening — ARQ async worker first, then HITL disputes, threat model, logging, routing_advisor (partials)
- iteration: 0
- last_gate: M16 DONE — L4 VERIFY APPROVE (round 1, 2026-08-04)
- last_action: M16 complete — Continuous Learning (Phase 20): drift detection + anchored alerts + /audit/drift; **ALL 20 ROADMAP PHASES COMPLETE**; spine updated
- next_action: build M17 — ARQ async worker (webhook → queue → worker → pipeline; arq 0.28 + redis-py already in venv; redis image needs pull)
- model: gpt-4o-mini (backend agents) · kimi-k3/hy3 (Hermes loop, opencode-go)
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## Notes
- Design source of truth: https://www.antern.co/blogs/production-grade-ai-pr-review-agent
- Remote: https://github.com/adi9336/AI_PR_REVIEWER (public) — push after each milestone; GCM + credential store
- Stack: Python 3.11+ / FastAPI / LangGraph / Tiger Cloud / OpenAI (gpt-4o-mini) / Docker (sandbox) / Next.js 15 (frontend/)
- 179 tests all green (167 through M15 + 12 M16 drift) · mypy 92 clean · check_deps 92 clean
- M16: drift = window vs baseline (5 metrics, direction-aware, floor gates whole report); alerts anchored to real review_ids (INV-6 — system-level drift is report-only); /audit/drift key-protected; CLI exit 0
- Roadmap: ALL 20 PHASES COMPLETE. Remaining = partials: ARQ worker (backend/job_queue/arq_worker.py), HITL disputes (hitl/dispute.py + feedback.py), threat model (security/threat_model.md), logging (observability/logging.py), routing_advisor (economics/routing_advisor.py); dashboard extras: drift page, explain page
- M17 env facts: arq 0.28.0 + redis-py in venv (pyproject deps); Docker UP; no redis image yet (pull redis:7-alpine); webhook claims then returns 202 — enqueue wiring makes reviews automatic
- L4 discipline: rounds-1 REJECTs caught real defects in M12 (dead sandbox gate), M13 (vacuous eval gate), M14 (masking leak, non-ASCII 500, invalid-UUID 500); M15/M16 APPROVE round 1
- Env quirks: stale uvicorn on :8000 silently serves OLD code — kill + restart before live demos; frontend/.env.local (gitignored) holds API_BASE_URL + GOVERNANCE_API_KEY synced with backend/.env
- M2 folded into M9 · backend/.env uses gpt-4o-mini (LlmClient = OpenAI direct) · M13 canary chirp-proven
