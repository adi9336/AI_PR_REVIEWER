# CURRENT
- active_loop: NONE
- target: M8 — Aggregator + confidence-weighted HITL gate
- iteration: 0
- last_gate: M7 PASSED (64/64 tests, 8 orchestrator tests green)
- last_action: M7 complete — LangGraph fan-out to 4 specialists, parallel via Send API
- next_action: M8 BUILD — aggregator merge/dedup/score + HITL gate routing
- model: glm-5.2 (session) / kimi-k3 (default config) / hy3 (codegen)
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## Notes
- Design source of truth: https://www.antern.co/blogs/production-grade-ai-pr-review-agent
- Stack: Python 3.11+ / FastAPI / LangGraph / Redis+ARQ / Tiger Cloud / Next.js
- OPENAI_API_KEY is set (embeddings + LLM chat work)
- M1: 5, M3: 19, M4: 8, M5: 5, M6: 19, M7: 8 — total 64 all green
- M7: LangGraph StateGraph with Send API fan-out, 4 agents in parallel, aggregate+decide
- State uses TypedDict + Annotated[list, add_list] for parallel accumulation