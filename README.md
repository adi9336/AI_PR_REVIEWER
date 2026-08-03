# AI PR Review Agent

Production-grade AI code review: a GitHub PR webhook fans out to four specialist agents (security, quality, tests, docs) that reason over the diff in parallel, then an aggregator merges, scores, and routes findings through a confidence-weighted human-in-the-loop gate — with every action written to an append-only audit trail.

Built from the [antern.co production-grade AI PR review agent study](https://www.antern.co/blogs/production-grade-ai-pr-review-agent) (L0-L9 derivation, ADR-001..004).

## Features

- **Webhook ingress** — HMAC-SHA256 verified, idempotent per `X-GitHub-Delivery` UUID
- **Four specialist agents** — security / quality / tests / docs, LangGraph parallel fan-out
- **Hybrid retrieval** — DiskANN ANN + full-text search fused with reciprocal rank fusion (Tiger Cloud pgvectorscale)
- **HITL gate** — auto-post / approval queue / escalate, driven by finding confidence (CRITICAL always escalates)
- **Audit spine** — every span, LLM call, tool call and decision is one append-only `agent_events` row (INV-6)
- **BudgetGuard** — hard-blocks LLM spend past the daily cap from continuous aggregates
- **Evaluation** — golden dataset + judge (precision/recall/F1) + regression gate + live canary
- **Sandboxed tooling** — Docker-isolated execution: no network, scrubbed secrets, hard timeouts
- **Prompt versioning** — every LLM call records which prompt bytes produced it (disputed-finding traceability)
- **CI/CD for AI** — pytest + mypy + dependency checks + eval gate on every push; canary blocks prompt regressions

## Stack

Python 3.11 · FastAPI · LangGraph · Tiger Cloud (TimescaleDB + pgvector) · OpenAI (gpt-4o-mini) · Docker · GitHub Actions

## Quickstart

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
cp backend/.env.example backend/.env   # fill in API keys
./.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8000
```

Endpoints: `POST /webhook/github` · `GET /health` · `GET /reviews/{id}` · `GET /hitl/queue` · `POST /reviews/{id}/run`

Send a mock PR webhook:

```bash
./.venv/Scripts/python.exe test_webhook.py
```

Run the gates:

```bash
./.venv/Scripts/python.exe -m pytest -q            # 143 tests
./.venv/Scripts/python.exe scripts/ci_check.py     # pytest + mypy + deps + eval gate
./.venv/Scripts/python.exe -m backend.evaluation.canary   # live agents vs golden set
```

## Project status

Milestones M1-M13 complete (143 tests green, mypy strict clean, dependency invariants clean):

| Milestone | What |
|---|---|
| M1-M10 | Core spine: architecture, webhook, Tiger schema, events, retrieval, agents, fan-out, HITL, e2e, BudgetGuard |
| M11 | Evaluation: golden dataset + judge + regression gate |
| M12 | Tooling & Sandboxing: tool registry, capability scope, Docker sandbox, model router |
| M13 | CI/CD for AI: prompt versioning, CI gates, live canary |

Roadmap: dashboard (M14+), governance, continuous learning. Milestones and decisions live in `.genesis/` (the development backbone).

## Invariants

- Dependencies point inward only (INV-1); LangGraph stays behind the orchestrator (INV-2)
- Untrusted diffs are data, never instructions (INV-3)
- Every outbound call has a timeout (INV-4)
- Nothing posts without the confidence gate (INV-5)
- Every action emits one append-only event (INV-6)
