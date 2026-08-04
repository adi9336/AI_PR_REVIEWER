# M17 — ARQ Async Worker (Phase 8 completion — the production webhook path)

## G0 · 2026-08-04
- environment: arq 0.28.0 + redis-py >= 5.0 ALREADY in the venv (pyproject deps — installed from the start); Docker UP; redis image NOT pulled yet (pulling redis:7-alpine); no REDIS_URL in backend/.env
- codebase: backend/job_queue/ = __init__.py + arq_worker.py (stub); webhook router claims the delivery then returns 202 WITHOUT running the pipeline (manual POST /reviews/{id}/run?diff= is the only trigger today — the M15 live demo exposed this gap)
- reuse: run_review_pipeline (webhook_receiver/router.py) — persists findings/status/events already (M9); claim_delivery/is_duplicate_delivery; emit_agent_event for worker error events
- decisions that bind us: INV-4 (job_timeout on the worker — no unbounded runs), INV-6 (worker failures emit anchored error events, never silent), fail-soft queue (Redis down ≠ review lost — claim persists in Postgres)
- verdict: **UNBUILT** → proceed to L1 BUILD

## Micro-plan (files to touch / tests to add / demo cmd)
- `backend/job_queue/arq_worker.py`:
  - `pipeline_job(ctx, review_id, diff, repo, pr_number, head_sha)` — runs run_review_pipeline; on exception emits an anchored error event (agent=job_queue) and re-raises nothing (arq logs it); returns the pipeline result dict
  - `WorkerSettings` — functions=[pipeline_job], job_timeout=300 (INV-4), max_jobs=10
  - `enqueue_review(review_id, diff, repo, pr_number, head_sha, redis_url=None)` — create_pool + enqueue_job('pipeline_job', ...); returns job id; raises ConnectionError when Redis is down
- `backend/webhook_receiver/router.py` — after claim: try enqueue_review → 202 {"status":"accepted","queued":true,...}; except ConnectionError → 202 {"status":"accepted","queued":false,...} (fail-soft, log via emit_agent_event error event)
- `backend/.env` (+REDIS_URL=redis://localhost:6379/0 — gitignored)
- `tests/test_job_queue.py`:
  - unit: enqueue_review captures job args (monkeypatched create_pool/enqueue_job); ConnectionError propagation
  - unit: pipeline_job calls run_review_pipeline with right args; exception → error event emitted, no raise
  - unit: webhook router fail-soft (monkeypatch enqueue_review to raise ConnectionError → 202 queued=false)
  - live (docker-gated on redis container): enqueue + run pipeline_job via arq against a real redis → review leaves 'pending'
- demo cmd: `pytest tests/test_job_queue.py -q`; live: redis container + worker process → webhook → auto review (no manual /run)

## Freeze boundary
`backend/job_queue/arq_worker.py`, `backend/webhook_receiver/router.py`, `tests/test_job_queue.py`

## iter log (append-only)

## iter 1 · 2026-08-04
- gate G1: pass · skills considered [agentic-swe-master, coding-orchestrator, tdd, production-readiness]; chose canon + tdd + production-readiness
- gate G2: pass · arq_worker.py (stub → real: pipeline_job + enqueue_review + WorkerSettings), webhook router enqueues after claim (fail-soft), PullRequestWebhook.diff field + parser extraction, worker diff-fetch fallback via GitHubClient.get_pr_diff, +6 tests tests/test_job_queue.py
- gate G3: pass · test-driven fixes: to_thread mock pattern; GitHubClient class name; live test switched from docker-py SDK (not installed) to the docker CLI (the codebase's pattern — sandbox.docker_available); M16 drift findings test made robust (demo reviews now share the window — direction+threshold, not magnitude)
- gate G4: pass · demo `pytest tests/test_job_queue.py -q` = 6 passed (incl. live redis container: enqueue → job exists → worker fn → review leaves 'pending'); full suite 185 passed; mypy 92 clean; deps 92 clean
- gate G5: pending — L4 VERIFY with fresh context next
- LIVE PRODUCTION DEMO (the milestone's real proof): redis container (hermes-redis :6379) + `arq backend.job_queue.arq_worker.WorkerSettings` worker process + fresh uvicorn → webhook with embedded golden diff → **202 {"queued": true, job_id}** → worker auto-consumed → review 4ce4d145 **escalated, 3 findings (CRITICAL sql-injection src/db.py:10), 13 events — NO manual /run**
- decision: fail-soft queue (Redis down → claim stands, review stays runnable via /run, error event emitted); diff flow: payload-embedded (tests/demo) else GitHubClient.get_pr_diff (production) else "" (completed, no findings); arq WorkerSettings job_timeout=300 (INV-4)
- next: L4 VERIFY → on APPROVE mark M17 done + update spine + commit + push

## iter 1 · L4 VERIFY #1 · 2026-08-04 — REJECT → fixed → re-submitted
- verdict: REJECT (fresh-context subagent, verification-audit skill; 36-min audit). ONE HIGH defect:
  - F1 (HIGH — router.py:117): `except ConnectionError:` caught only the BUILTIN. Real redis-py
    outages raise redis.exceptions.ConnectionError/TimeoutError — subclasses of RedisError, NOT the
    builtin (MRO proven live). arq 0.28's create_pool eagerly pings → the failure escaped uncaught →
    webhook 500'd instead of 202 queued:false; the fail-soft INV-6 error event never fired (silent).
    The unit test masked it: it raised the builtin ConnectionError — a branch enqueue_review only
    reaches for `job is None`, never on a real outage.
    FIXED: enqueue_review now normalizes (RedisConnectionError, RedisTimeoutError, OSError) → builtin
    ConnectionError ("redis unavailable: …") — the public contract; router keeps except ConnectionError
    + a defense-in-depth except OSError. New regression test: test_enqueue_review_normalizes_real_redis_exceptions
    (create_pool raises redis.exceptions.TimeoutError → builtin ConnectionError raised).
  - HTTP-level LIVE repro of the exact broken scenario: uvicorn with REDIS_URL=redis://localhost:6390/0
    (dead port) → webhook → **202 {"accepted", "queued": false, "job_id": null}** (was 500) ✓
  - REDIS_URL added to backend/.env (INFO note from verifier)
- after fix: 7/7 job_queue tests; mypy 92 clean; deps 92 clean
- next: L4 VERIFY #2 on the final state → on APPROVE mark M17 done + commit + push

## iter 1 · L4 VERIFY #2 · 2026-08-04 — REJECT (same class, new line) → fixed → re-submitted
- verdict: REJECT (fresh-context subagent; round-1 fix CONFIRMED live: dead-port webhook → 202 queued:false + error event + review pending; happy path live-verified again: 202 queued:true → 68a159c8 pending → escalated in <15s, no manual /run). ONE HIGH defect:
  - F2 (HIGH — arq_worker.py:102-108): enqueue_review normalized ONLY create_pool. pool.enqueue_job was try/finally-only — a real redis failure between arq's eager ping and the publish (TOCTOU) propagated RAW redis.exceptions.ConnectionError (MRO proves not builtin, not OSError) → router excepts can't catch → 500 + NO error event → silent outage (INV-6), violating the function's own "any queue outage" contract.
    FIXED: the whole connect-and-publish block is now normalized — pool.close errors swallowed in an inner finally (never mask the original), any (RedisConnectionError, RedisTimeoutError, OSError) from create_pool OR enqueue_job → builtin ConnectionError("redis unavailable: …"). New regression test: test_enqueue_review_normalizes_enqueue_job_failure (pool ok, enqueue_job raises RedisConnectionError → builtin ConnectionError).
- after fix: 8/8 job_queue tests; mypy 92 clean; deps 92 clean
- INFO from verifier: environment has DUPLICATE servers/workers (a system-Python311 uvicorn on :8000 + a native redis on ::1:6379 from earlier sessions coexist with the .venv/docker ones) — reconcile after M17 lands
- next: L4 VERIFY #3 on the final state → on APPROVE mark M17 done + commit + push + env reconciliation

## iter 1 · L4 VERIFY #3 · 2026-08-04 — APPROVE
- verdict: **APPROVE** (fresh-context subagent, verification-audit skill) — the TOCTOU defect class is CLOSED
  - function probe (the exact round-2 hole): create_pool OK, enqueue_job raises REAL redis.exceptions.ConnectionError
    → builtins.ConnectionError, is_builtin=True, 'redis unavailable' in msg, MRO [ConnectionError, OSError, ...]
    (round 2 leaked the raw redis exception); cases 2-6: create_pool TimeoutError → builtin; close-failure preserves
    job_id; close+enqueue both raising → original normalized (no masking); job None → builtin; happy → job_id
  - HTTP fail-soft live: dead-port 6390, 5 real TimeoutError retries in the log, response **202 Accepted
    {queued:false}**; error event anchored (agent=job_queue, payload.status=error — drift shape); claim pending;
    no 500, no silent outage
  - live happy path: webhook → worker auto-pipeline → pending → escalated (13 events, all 4 specialists)
  - suite: 8/8 job_queue (live redis ran) · full 187 passed · mypy 92 clean · deps 92 clean · INV-4 job_timeout=300
  - defects: none · INFO only (router unit test raises builtin — legitimate, the real fn raises exactly that now;
    TOCTOU branch proven at function level — router's single except branch is identical for both; env mixing noted)
- milestone status: **DONE** — 187 tests total; mypy 92 clean; deps 92 clean

