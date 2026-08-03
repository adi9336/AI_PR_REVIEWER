# CURRENT
- active_loop: NONE
- target: M12+ — dashboard, CI/CD for AI, governance, continuous learning (not yet sliced)
- iteration: 0
- last_gate: M11 DONE — L4 VERIFY APPROVE (2026-08-03)
- last_action: M11 complete — Evaluation: golden dataset (fixtures/golden/sqli_pr.json), deterministic judge (precision/recall/F1), regression gate CLI (exit 0/1/2), 11 tests; spine reconciled M1-M11
- next_action: slice M12 (dashboard Phase 2/17, or CI/CD-for-AI Phase 18) after user picks direction
- model: gpt-4o-mini (backend agents) · kimi-k3/hy3 (Hermes loop, opencode-go)
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## Notes
- Design source of truth: https://www.antern.co/blogs/production-grade-ai-pr-review-agent
- Stack: Python 3.11+ / FastAPI / LangGraph / Tiger Cloud / OpenAI (gpt-4o-mini)
- 103 tests all green (M1:5, M3:19, M4:8, M5:5, M6:19, M7:8, M8:13, M9:5, M10:5, webhook/persistence +5, M11 evaluation:11)
- check_deps: 89 files, 0 violations · mypy strict: 89 files, 0 errors
- Webhook flow LIVE-verified 2026-08-03: 202 → real 4-agent run → escalate → CRITICAL sql-injection persisted → HITL queued → 13-event trace
- M11 gate: pytest tests/test_evaluation.py -q (11 passed) · python -m backend.evaluation.regression_gate (exit 0; degraded → exit 1; missing file → exit 2)
- M2 folded into M9 (see implementation-notes.html deviations)
- backend/.env uses gpt-4o-mini for MODEL_REASONING/MODEL_CODEGEN (LlmClient = OpenAI direct; kimi-k3/hy3 are Hermes-loop models)
