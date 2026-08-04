# Setup — using AI PR Review Agent on your real repositories

This guide covers the production path: connecting the agent to your own
GitHub repos, configuring the environment, and running the stack
(API + worker + dashboard). All 20 roadmap phases are built; this is the
deployment recipe.

## Architecture in one line

```
GitHub webhook → FastAPI (:8000) → claim (Postgres/Tiger) → enqueue (Redis/arq)
   → worker → 4 agents (LLM) → aggregator → HITL gate → findings persisted
   → dashboard (:3000) reads the API server-side
```

## 1. What you need

| Piece | How you get it |
|---|---|
| Postgres (TimescaleDB) | Tiger Cloud (used here) or any Postgres — run `scripts/migrations/2026-06-tiger-init.sql` |
| Redis | Local install (this machine) or `docker run -d -p 6379:6379 redis:7-alpine` |
| OpenAI key | https://platform.openai.com/api-keys |
| GitHub webhook secret | Any strong random string |
| GitHub token for the review bot | A PAT with `repo` scope (or a GitHub App) — used by `GithubClient` to fetch diffs/post reviews |

## 2. Environment (`backend/.env` — gitignored, never commit)

```
OPENAI_API_KEY=sk-...            # LLM (gpt-4o-mini)
MODEL_REASONING=gpt-4o-mini      # per-step model overrides (optional)
MODEL_CODEGEN=gpt-4o-mini
TIGER_DATABASE_URL=postgres://user:pass@host:5432/tiger
GITHUB_WEBHOOK_SECRET=<random>   # must match the GitHub webhook config
GOVERNANCE_API_KEY=<random>      # protects /audit + /hitl admin routes
REDIS_URL=redis://localhost:6379/0
GITHUB_APP_ID=                   # only if using a GitHub App for posting reviews
GITHUB_PRIVATE_KEY_PATH=         # only if using a GitHub App
```

Frontend (`frontend/.env.local`):

```
API_BASE_URL=http://localhost:8000
GOVERNANCE_API_KEY=<same as backend>
```

## 3. Install

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
cd frontend && npm install && cd ..
```

## 4. Run the stack

```bash
# 1. API server
./.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8000

# 2. Worker (auto-runs reviews from the queue)
./.venv/Scripts/python.exe -m arq backend.job_queue.arq_worker.WorkerSettings

# 3. Dashboard (optional)
cd frontend && npm run build && npm start   # http://localhost:3000
```

Check: `curl http://localhost:8000/health` → `{"status":"ok","database":"connected"}`

## 5. Connect GitHub (per repository)

1. Repo → Settings → Webhooks → **Add webhook**
   - **Payload URL:** `https://<your-host>/webhook/github`
   - **Content type:** `application/json`
   - **Secret:** your `GITHUB_WEBHOOK_SECRET`
   - **Events:** select **Pull requests** (opened, synchronize, reopened)
2. The agent receives the event, claims the delivery (durable), enqueues
   the job, and the worker runs the review automatically.
   - GitHub webhooks don't carry diffs — the worker fetches them via
     `GithubClient.get_pr_diff` (needs `GITHUB_APP_ID`/PAT configured) or
     the diff can be embedded in the payload for testing.

## 6. How reviews flow

- **Auto-post:** findings all high-confidence and non-CRITICAL → posted to
  the PR automatically.
- **Approval queue:** low-confidence or agent-failure → sits in
  `/hitl/queue` for a human.
- **Escalated:** any CRITICAL finding → always escalates to a human
  (`/hitl/queue`), never auto-posted.
- Humans can **dispute** a finding or mark it **unhelpful**
  (`POST /hitl/reviews/{id}/findings/{fid}/dispute`) — recorded as
  append-only events that feed the feedback loop.

## 7. Quality gates you can run

```bash
./.venv/Scripts/python.exe -m pytest -q                    # 197 tests
./.venv/Scripts/python.exe scripts/ci_check.py             # pytest+mypy+deps+eval sanity
./.venv/Scripts/python.exe -m backend.evaluation.canary    # real agents vs golden set (needs LLM key)
./.venv/Scripts/python.exe -m backend.observability.drift   # cost/quality drift report
```

CI (GitHub Actions) runs the first three on every push; the canary is a
secrets-gated step.

## 8. Admin

- **Audit trail:** `GET /audit/events` (key: `X-API-Key: $GOVERNANCE_API_KEY`)
- **Explain a finding:** `GET /audit/reviews/{id}/explain/{finding_id}` —
  shows the finding, its events trace, and the exact prompt version that
  produced it.
- **Drift:** `GET /audit/drift` or the dashboard `/drift` page.
- **Cost:** BudgetGuard blocks daily LLM spend past the cap
  (see `backend/economics/budget.py`, `DAILY_BUDGET_CAP`).

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `health` says degraded | `TIGER_DATABASE_URL` wrong or DB unreachable |
| Webhook returns 401 | `GITHUB_WEBHOOK_SECRET` mismatch with the GitHub webhook config |
| Webhook returns 202 `queued:false` | Redis down — the review is still claimed (durable); start Redis or run `POST /reviews/{id}/run?diff=...` manually |
| Review stuck `pending` | Worker not running — start `arq backend.job_queue.arq_worker.WorkerSettings` |
| No findings / `auto_post` empty | The diff was empty (no embedded diff, no GitHub credentials) — check the trace: `GET /audit/reviews/{id}/trace` |
| `sk-...` in dashboard | Never: masking redacts at the read boundary (tested) |
