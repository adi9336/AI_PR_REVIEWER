# M11 — Evaluation: golden dataset + LLM-as-judge + regression gate

## G0 · 2026-08-03
- wiki pages read: [[llmops-ai-agents/concepts/Evaluation-Frameworks]] (golden dataset, LLM-as-judge, regression gate), [[llmops-ai-agents/concepts/Observability-and-Cost-Control]] (INV-6 proof layer)
- implementation-notes searched for: "golden", "judge", "regression" — found: backend/evaluation/** listed as wip (M11 in flight)
- codebase grep: `golden_dataset|judge|regression_gate` — 3 stub files (6 lines each, "typed stub"), `tests/` has zero eval tests, `fixtures/` has no golden dir → unbuilt
- decisions that bind us: 0001 (one store — no new tables needed; scores persist via agent_events INV-6), 0002 (backend LLM = gpt-4o-mini via OpenAI; judge uses deterministic core so tests never need a live key)
- verdict: **UNBUILT** → proceed to L1 BUILD

## Micro-plan (files to touch / tests to add / demo cmd)
- `backend/evaluation/golden_dataset.py` — GoldenFinding + GoldenPR (Pydantic), `load_golden_dataset()` reading `fixtures/golden/*.json`
- `fixtures/golden/sqli_pr.json` — the SQLi diff (matches the live-verified run: security CRITICAL sql-injection + quality HIGH error handling)
- `backend/evaluation/judge.py` — deterministic scoring core `score_findings(golden, actual) -> EvaluationScore(precision, recall, f1)`; exact match on (file_path, line_start, severity, category), partial credit on category-only; optional `JudgeClient` LLM wrapper (mockable, not required for the gate)
- `backend/evaluation/regression_gate.py` — `evaluate_and_gate(golden, actual, min_f1=0.8) -> EvaluationReport`; raises `RegressionGateError` (exit non-zero) below threshold; emits one `evaluation.run` agent_event with the score (INV-6)
- `tests/test_evaluation.py` — dataset schema validity; known-good → F1 ≥ 0.8; degraded (missed finding + wrong severity) → gate blocks; event emitted
- Demo command: `pytest tests/test_evaluation.py -q` (must exit 0) + G4: mypy backend/evaluation, check_deps, full pytest

## Freeze boundary
`backend/evaluation/**`, `fixtures/golden/**`, `tests/test_evaluation.py`

## iter log (append-only)

## iter 1 · 2026-08-03
- gate G1: pass · skills considered [agentic-swe-master, coding-orchestrator, tdd, llmops-ai-agents]; chose canon + tdd + llmops-ai-agents (evaluation frameworks)
- gate G2: pass · +4 modules backend/evaluation/, +1 fixture fixtures/golden/sqli_pr.json, +11 tests tests/test_evaluation.py, CLI regression gate
- gate G3: pass · well under budget (single BUILD pass, no research needed — wiki already had Evaluation-Frameworks)
- gate G4: pass · demo `pytest tests/test_evaluation.py -q` = 11 passed; `python -m backend.evaluation.regression_gate` exit 0 (PASS sqli_pr); mypy backend/evaluation = 0 errors; check_deps 89 files clean
- gate G5: pending — L4 VERIFY with fresh context next
- decision: deterministic scoring core (precision/recall/F1 with greedy match) so CI never needs a live LLM key; LLM-as-judge JudgeClient is a mockable wrapper on top; gate emits one evaluation decision event (INV-6)
- fixes this iter: CLI self-gate passed actual=[] (bug) → gate each PR against its own expected findings; emit_event=True requires a real review_id (agent_events.review_id is UUID)
- next: L4 VERIFY → on APPROVE mark M11 done + update spine + commit

## iter 1 · L4 VERIFY · 2026-08-03
- verdict: **APPROVE** (fresh-context subagent, verification-audit skill; ran all checks itself)
  - demo `pytest tests/test_evaluation.py -q` → 11 passed, 0 skipped (live-DB event test ran against Tiger Cloud: 1 row, decision/evaluation/approved/conf 1.0)
  - self-gate exit 0; degraded findings exit 1 (f1 0.67, combo 0.33) — criterion (3) matched
  - mypy 4 files clean; check_deps 89 files clean; no langgraph outside orchestrator; hand-math (1.0 / 0.667 / 0.75) matches code
  - verifier INFO notes: (a) missing --findings file raised unhandled FileNotFoundError → FIXED post-approval: clean error + exit 2 (usage) vs exit 1 (gate fail); re-ran 11/11 + CLI matrix, green; (b) "evaluation.run event" encoded as decision + agent=evaluation — accepted, per spec parenthetical
- milestone status: **DONE** — 103 tests total, mypy 89 files clean, deps clean

