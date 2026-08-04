# CURRENT
- active_loop: NONE
- target: M18+ — remaining partials (HITL disputes, threat model, logging, routing_advisor, dashboard drift page)
- iteration: 0
- last_gate: M17 DONE — L4 VERIFY APPROVE (round 3, 2026-08-04)
- last_action: M17 complete — ARQ Async Worker (webhook → queue → worker → auto-pipeline, fail-soft); spine updated
- next_action: slice M18 (partials) after user picks — or env reconciliation (duplicate uvicorn/arq/redis processes from earlier sessions)
- model: gpt-4o-mini (backend agents) · kimi-k3/hy3 (Hermes loop, opencode-go)
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## Notes
- Design source of truth: https://www.antern.co/blogs/production-grade-ai-pr-review-agent
- Remote: https://github.com/adi9336/AI_PR_REVIEWER (public) — push after each milestone; GCM + credential store
- Stack: Python 3.11+ / FastAPI / LangGraph / Tiger Cloud / OpenAI (gpt-4o-mini) / Docker (sandbox + redis) / Next.js 15 (frontend/) / arq 0.28
- 187 tests all green (179 through M16 + 8 M17 job_queue) · mypy 92 clean · check_deps 92 clean
- M17: webhook claims (Postgres durable) → enqueues (arq) → worker auto-runs M9 pipeline; fail-soft = 202 queued:false + anchored error event (payload.status=error, drift shape); enqueue_review normalizes ALL redis failures (create_pool AND enqueue_job TOCTOU) → builtin ConnectionError; REDIS_URL in backend/.env
- L4 lesson (M17, 3 rounds): redis-py exceptions are RedisError subclasses, NOT builtin ConnectionError — tests must raise the REAL exception class; verify failure paths at HTTP level, not just unit level
- Env: native Windows redis on ::1:6379 shadows docker hermes-redis for localhost (works; docker container redundant); a system-python311 uvicorn may serve :8000 — reconcile duplicates before the next milestone
- Roadmap: ALL 20 PHASES COMPLETE. Remaining = partials: HITL disputes (hitl/dispute.py + feedback.py), threat model (security/threat_model.md), logging (observability/logging.py), routing_advisor (economics/routing_advisor.py); dashboard extras: drift page, explain page
- L4 discipline: rounds-1 REJECTs caught real defects in M12 (dead sandbox gate), M13 (vacuous eval gate), M14 (masking leak, non-ASCII 500, invalid-UUID 500), M17 ×2 (redis exception class ×2)
- M2 folded into M9 · backend/.env uses gpt-4o-mini (LlmClient = OpenAI direct) · M13 canary chirp-proven
