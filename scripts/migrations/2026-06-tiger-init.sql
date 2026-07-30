-- ═══════════════════════════════════════════════════════════════════════
-- 2026-06-tiger-init.sql — the single data spine (ADR-003 / ADR-0001)
-- Tiger Cloud = managed Postgres + TimescaleDB + pgvector + pgvectorscale.
-- Three lanes in ONE store:
--   memory  → code_chunks            (VECTOR(256) + DiskANN + FTS GIN)
--   truth   → pr_review_records, finding_records, hitl_reviews, hitl_feedback
--   time    → agent_events           (hypertable, partitioned by 1 day)
--             + continuous aggregates agent_health_1m, pr_cost_hourly
-- Idempotent: safe to run repeatedly (IF NOT EXISTS / if_not_exists => TRUE).
-- ═══════════════════════════════════════════════════════════════════════

-- ── 0. Extensions (already present on Tiger Cloud; harmless to assert) ──
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS vectorscale;

-- ═══ LANE 1 — MEMORY: code_chunks ═══════════════════════════════════════
CREATE TABLE IF NOT EXISTS code_chunks (
  id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  repo         TEXT         NOT NULL,
  path         TEXT         NOT NULL,
  symbol       TEXT,                                  -- function/class name (nullable)
  chunk_index  INT          NOT NULL,                 -- order within file
  content      TEXT         NOT NULL,
  embedding    VECTOR(256)  NOT NULL,                 -- text-embedding-3-large, 256 dims
  token_count  INT,
  updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- DiskANN ANN index (pgvectorscale)
CREATE INDEX IF NOT EXISTS code_chunks_emb_idx
  ON code_chunks USING diskann (embedding vector_cosine_ops);

-- Full-text search lane (exact identifiers: fn names, error codes, config keys)
ALTER TABLE code_chunks
  ADD COLUMN IF NOT EXISTS content_tsv TSVECTOR
  GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX IF NOT EXISTS code_chunks_fts_idx
  ON code_chunks USING GIN (content_tsv);

-- Upsert target for incremental re-embedding (overwrite stale chunks)
CREATE UNIQUE INDEX IF NOT EXISTS code_chunks_unique_idx
  ON code_chunks (repo, path, chunk_index);

-- Freshness tracking — ingestion re-embeds only files that changed
CREATE TABLE IF NOT EXISTS repo_file_index (
  id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  repo            TEXT         NOT NULL,
  path            TEXT         NOT NULL,
  content_hash    TEXT         NOT NULL,
  last_indexed_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE (repo, path)
);

-- ═══ LANE 2 — TIME: agent_events (hypertable) ═══════════════════════════
CREATE TABLE IF NOT EXISTS agent_events (
  ts           TIMESTAMPTZ   NOT NULL,
  review_id    UUID          NOT NULL,
  agent        TEXT          NOT NULL,   -- security|quality|tests|docs|aggregator
  span_id      UUID          NOT NULL DEFAULT gen_random_uuid(),
  parent_span  UUID,
  event_type   TEXT          NOT NULL,   -- span.start|span.end|llm.call|tool.call|decision|escalation
  model        TEXT,
  tokens_in    INT,
  tokens_out   INT,
  cost_usd     NUMERIC(10,6),
  latency_ms   INT,
  outcome      TEXT,                     -- approved|request_changes|critical_block|escalated
  confidence   NUMERIC(4,3),
  payload      JSONB
);

SELECT create_hypertable(
  'agent_events',
  by_range('ts', INTERVAL '1 day'),
  if_not_exists => TRUE
);

-- Append-only by construction (INV-6): revoke mutation rights from the app role path.
-- (Owner can still TRUNCATE/DROP; the application never gets UPDATE/DELETE.)
-- Enforced here as a guardrail for any non-owner role.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
    REVOKE UPDATE, DELETE, TRUNCATE ON agent_events FROM app_rw;
  END IF;
END $$;

-- ═══ LANE 2b — ROLLUPS: continuous aggregates ═══════════════════════════
-- Per-agent health: calls, cost, p95 latency, rejection rate (1-minute buckets)
CREATE MATERIALIZED VIEW IF NOT EXISTS agent_health_1m
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('1 minute', ts)                         AS bucket,
  agent,
  count(*) FILTER (WHERE event_type = 'llm.call')     AS llm_calls,
  sum(cost_usd)                                       AS cost_usd,
  approx_percentile(0.95, percentile_agg(latency_ms)) AS p95_ms,
  count(*) FILTER (WHERE outcome = 'rejected')::float
    / NULLIF(count(*) FILTER (WHERE outcome IS NOT NULL), 0) AS rejection_rate
FROM agent_events
GROUP BY bucket, agent
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
  'agent_health_1m',
  start_offset      => INTERVAL '2 hours',
  end_offset        => INTERVAL '1 minute',
  schedule_interval => INTERVAL '1 minute',
  if_not_exists     => TRUE
);

-- Per-PR cost + token rollup (hourly buckets) — feeds BudgetGuard (ADR-004)
CREATE MATERIALIZED VIEW IF NOT EXISTS pr_cost_hourly
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('1 hour', ts)   AS bucket,
  review_id,
  sum(cost_usd)               AS total_cost_usd,
  count(DISTINCT agent)       AS agents_used,
  max(confidence)             AS max_confidence
FROM agent_events
GROUP BY bucket, review_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
  'pr_cost_hourly',
  start_offset      => INTERVAL '1 day',
  end_offset        => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour',
  if_not_exists     => TRUE
);

-- ═══ LANE 3 — TRUTH: relational review tables ═══════════════════════════
CREATE TABLE IF NOT EXISTS pr_review_records (
  id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  repo               TEXT         NOT NULL,
  pr_number          INT          NOT NULL,
  delivery_uuid      TEXT         NOT NULL,            -- X-GitHub-Delivery idempotency key
  head_sha           TEXT,
  overall_confidence NUMERIC(4,3),
  status             TEXT         NOT NULL DEFAULT 'pending',  -- pending|posted|queued|escalated|failed
  github_review_id   BIGINT,
  created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  posted_at          TIMESTAMPTZ,
  UNIQUE (repo, pr_number, delivery_uuid)              -- idempotency: one review per delivery
);

CREATE TABLE IF NOT EXISTS finding_records (
  id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  review_id   UUID         NOT NULL REFERENCES pr_review_records(id) ON DELETE CASCADE,
  agent_type  TEXT         NOT NULL,        -- security|quality|tests|docs
  severity    TEXT         NOT NULL,        -- CRITICAL|HIGH|MEDIUM|LOW|INFO
  category    TEXT,
  summary     TEXT         NOT NULL,
  file_path   TEXT,
  line_start  INT,
  line_end    INT,
  suggestion  TEXT,
  confidence  NUMERIC(4,3),
  rationale   TEXT,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS finding_records_review_idx ON finding_records (review_id);
CREATE INDEX IF NOT EXISTS finding_records_sev_idx    ON finding_records (severity);

-- HITL: approval queue (L7) + feedback (anti feedback-loop-poisoning)
CREATE TABLE IF NOT EXISTS hitl_reviews (
  id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  review_id    UUID         NOT NULL REFERENCES pr_review_records(id) ON DELETE CASCADE,
  reason       TEXT         NOT NULL,        -- low_confidence|critical_finding|dispute
  state        TEXT         NOT NULL DEFAULT 'queued',  -- queued|approved|rejected|escalated
  assigned_to  TEXT,
  decided_at   TIMESTAMPTZ,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hitl_reviews_state_idx ON hitl_reviews (state);

CREATE TABLE IF NOT EXISTS hitl_feedback (
  id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  finding_id  UUID         NOT NULL REFERENCES finding_records(id) ON DELETE CASCADE,
  reviewer    TEXT,
  verdict     TEXT         NOT NULL,         -- agreed|disputed|false_positive
  note        TEXT,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hitl_feedback_finding_idx ON hitl_feedback (finding_id);

-- ── done ────────────────────────────────────────────────────────────────
