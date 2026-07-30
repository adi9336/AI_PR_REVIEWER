# CURRENT
- active_loop: NONE
- target: M1 — Repo skeleton + dependency rule enforced
- iteration: 0
- last_gate: —
- last_action: genesis ritual G0–G6 complete; spine filled from the antern.co architecture study
- next_action: run G0 Existence Pre-Flight on M1, then start L1 BUILD
- model: claude-haiku-4-5
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## Notes
- Design source of truth: https://www.antern.co/blogs/production-grade-ai-pr-review-agent
- Local text extract of the article: /tmp/antern.txt (re-fetch with curl if lost)
- Chosen approach (G0.5): C — grounded agentic fan-out
- Stack: Python 3.11+ / FastAPI / LangGraph / Redis+ARQ / Tiger Cloud / Next.js
- EXPLAIN_DIFF is OFF for this project (set at scaffold time)
- M1 demo command: `python scripts/check_deps.py && pytest tests/test_architecture.py -q && mypy backend/core backend/models`
