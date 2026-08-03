# CURRENT
- active_loop: NONE
- target: M14+ — dashboard (Phase 2/17), governance (Phase 15), continuous learning (Phase 20) — not yet sliced
- iteration: 0
- last_gate: M13 DONE — L4 VERIFY APPROVE (round 2, 2026-08-03)
- last_action: M13 complete — CI/CD for AI: prompt versioning (content-hash on every llm.call), scripts/ci_check.py + GitHub Actions workflow, REAL canary (agents vs golden set); spine updated
- next_action: slice M14 (dashboard Phase 2, governance Phase 15, or continuous learning Phase 20) after user picks direction
- model: gpt-4o-mini (backend agents) · kimi-k3/hy3 (Hermes loop, opencode-go)
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## Notes
- Design source of truth: https://www.antern.co/blogs/production-grade-ai-pr-review-agent
- Stack: Python 3.11+ / FastAPI / LangGraph / Tiger Cloud / OpenAI (gpt-4o-mini) / Docker (sandbox)
- 143 tests all green (129 through M12 + 14 M13: prompts 7 + canary 7)
- check_deps: 90 files, 0 violations · mypy strict: 90 files, 0 errors
- M13: every llm.call event records payload.prompt_version (sha256 of system+user templates, 8 hex); ci_check.py = 4 local gates; ci.yml runs them on push/PR + secrets-gated canary step
- Canary: python -m backend.evaluation.canary — live agents vs golden set; live PASS f1=1.00; chirp-proven discriminating (regressed → exit 1)
- L4 discipline: round 1 REJECT caught the vacuous eval gate (golden-vs-itself) → real canary built; golden guards ONE load-bearing expectation per PR (LLM variance on secondary findings flaked precision)
- M2 folded into M9 · backend/.env uses gpt-4o-mini (LlmClient = OpenAI direct)
