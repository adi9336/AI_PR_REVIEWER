# CURRENT
- active_loop: NONE
- target: NONE — **PROJECT SHIPPED + LIVE-DEPLOYED** (2026-08-04): all 20 roadmap phases + 18 milestones + every stub complete; wired to the real repo adi9336/AI_PR_REVIEWER (webhook 660969576 → cloudflared tunnel → worker) and dogfooded on PR #1 (escalated: CRITICAL sql-injection + anti-patterns + HIGH missing-test)
- iteration: 0
- last_gate: M18 DONE — L4 VERIFY APPROVE (round 1, 2026-08-04)
- last_action: LIVE DEPLOY — real webhook on AI_PR_REVIEWER, GITHUB_TOKEN in backend/.env (gitignored), ping/payload-shape deploy fixes shipped (7511aa9, fc33c6e); PR #1 + demo/vulnerable-sqli branch left as demo artifacts
- next_action: optional — permanent host (replace tunnel), supervisor for the arq worker (it crashed on redis outage), keep or close PR #1
- model: gpt-4o-mini (backend agents) · kimi-k3/hy3 (Hermes loop, opencode-go)
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## Notes
- Design source of truth: https://www.antern.co/blogs/production-grade-ai-pr-review-agent
- Remote: https://github.com/adi9336/AI_PR_REVIEWER (public) — push after each milestone; GCM + credential store
- Stack: Python 3.11+ / FastAPI / LangGraph / Tiger Cloud / OpenAI (gpt-4o-mini) / Docker (sandbox + redis) / Next.js 15 (frontend/) / arq 0.28
- 197 tests all green · mypy 92 clean · check_deps 92 clean · frontend build 7 routes
- SHIPPED capabilities: webhook→claim→enqueue→worker auto-review (fail-soft), 4-agent pipeline + aggregator + HITL gate, audit spine (append-only INV-6) + masking (INV-3) + RBAC, explainability (prompt versions), drift/continuous learning + anchored alerts, disputes/feedback loop, canary + CI gates, sandboxed tools, budget guard, dashboard (list/detail/trace/hitl/drift/explain)
- L4 lessons: tests must exercise REAL failure classes at HTTP level (redis-py exceptions ≠ builtin — M17 ×2 rounds); doc drift gets caught by the verifier (SETUP.md test count); INV-6 means tests backdate via INSERT-with-ts, never UPDATE
- Env: native Windows redis :6379 (docker removed); uvicorn :8000 + Next :3000 serve the current code; stale uvicorn pitfall — verify git HEAD before attributing behavior
- Setup guide: SETUP.md (env vars, GitHub webhook wiring, worker run, troubleshooting)
- Roadmap: COMPLETE. Optional future: GitHub App posting of reviews (auto_post currently uses the PAT/client path), multi-repo fleet onboarding, per-org RBAC scopes, golden-set expansion from disputes
- M2 folded into M9 · backend/.env uses gpt-4o-mini (LlmClient = OpenAI direct)
