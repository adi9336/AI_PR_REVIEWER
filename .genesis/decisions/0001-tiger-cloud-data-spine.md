# ADR 0001 — Use Tiger Cloud as the single data spine (adopt the study's ADR-003)

- **Date:** 2026-07-31
- **Status:** accepted
- **Phase / milestone:** genesis → M3 (Infra & Data Engineering, Tiger)

## Context
The study's Part II derives one Postgres-compatible store for the three data shapes — memory
(`code_chunks` vectors), truth (review/finding/hitl rows), and time (`agent_events` hypertable) —
instead of three separate durable stores. Tiger Cloud adds TimescaleDB + pgvector + pgvectorscale
to a managed Postgres. The user confirmed to go with Tiger given the free-trial credit.

## Decision
We adopt Tiger Cloud as the single durable data spine for this project, per ADR-003 of the study.
Redis stays for the queue only (judgment, not dogma).

## Consequences
- Positive: one backup/connection/query story; vector + relational + time-series in one store;
  continuous aggregates power BudgetGuard (ADR-004) and the dashboard without scanning raw events.
- Negative / cost: a managed external dependency; needs `TIGER_DATABASE_URL` provisioned before M3.
- **Invariant added to context-graph.json:** none new — this decision is the reason INV-6's
  `agent_events` and the M3 migration target Tiger, but INV-1..6 are unchanged.

## Alternatives rejected
- Split stores (Qdrant + Postgres + a time-series DB) — splits memory from review truth and audit.
- Plain local Postgres only — fine for the truth lane, weak for vector memory and time-series rollups;
  the user chose not to split M3 into local-first + Tiger-later.

## Setup (credentials live in `backend/.env`, never in source)
- Sign up at https://www.tigerdata.com (Tiger Cloud). New accounts get $1,000 credit / 30 days, no card.
- Create a service, copy the Postgres connection string.
- Save as `TIGER_DATABASE_URL=postgres://USER:PASS@HOST:5432/DB?sslmode=require` in `backend/.env`.
- Also needed later: `OPENAI_API_KEY` (embeddings + LLM), `GITHUB_APP_ID`, `GITHUB_WEBHOOK_SECRET`,
  `GITHUB_PRIVATE_KEY_PATH`.
