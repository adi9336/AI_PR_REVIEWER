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

-- INV-6: an llm.call that cannot answer "what did it cost / how slow was it"
-- is an unaccountable action. Cost and latency are required on those rows
-- specifically (a span.start legitimately has neither yet).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'agent_events_llm_call_accountable'
  ) THEN
    ALTER TABLE agent_events ADD CONSTRAINT agent_events_llm_call_accountable
      CHECK (
        event_type <> 'llm.call'
        OR (cost_usd IS NOT NULL AND latency_ms IS NOT NULL)
      );
  END IF;
END $$;

SELECT create_hypertable(
  'agent_events',
  by_range('ts', INTERVAL '1 day'),
  if_not_exists => TRUE
);

-- The primary read path (M4) is "all events for one review, in time order",
-- and pr_cost_hourly groups by review_id. Without this it is a full scan
-- across every chunk.
CREATE INDEX IF NOT EXISTS agent_events_review_ts_idx
  ON agent_events (review_id, ts DESC);

-- Append-only by construction (INV-6).
--
-- NOTE: a GRANT/REVOKE-based guard is NOT sufficient here — the application
-- connects as the table owner (tsdbadmin), and owners bypass table privileges.
-- A rule/trigger is the only thing that holds regardless of role, so the audit
-- trail is immutable *by construction* rather than by convention.
--
-- ORDERING: the triggers themselves are (re)created at the END of this file.
-- DROP MATERIALIZED VIEW ... CASCADE on a continuous aggregate cascades into
-- triggers on the underlying hypertable, so creating them here would let a
-- re-run silently disarm INV-6.
CREATE OR REPLACE FUNCTION agent_events_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'agent_events is append-only (INV-6): % is not permitted on the audit trail',
    TG_OP
    USING HINT = 'Emit a new corrective event row instead of mutating history.';
END;
$$;

-- Defence in depth: also revoke from any non-owner app role if one exists.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
    REVOKE UPDATE, DELETE, TRUNCATE ON agent_events FROM app_rw;
  END IF;
END $$;

-- ═══ LANE 2b — ROLLUPS: continuous aggregates ═══════════════════════════
-- CREATE ... IF NOT EXISTS will NOT update an existing view, so an older
-- definition would survive a re-run forever. Drop it only when it is stale.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM timescaledb_information.continuous_aggregates
    WHERE view_name = 'agent_health_1m'
      AND view_definition LIKE '%''rejected''%'
  ) THEN
    DROP MATERIALIZED VIEW agent_health_1m CASCADE;
    RAISE NOTICE 'dropped stale agent_health_1m (dead rejection_rate filter)';
  END IF;
END $$;

-- Per-agent health: calls, cost, p95 latency, rejection rate (1-minute buckets)
--
-- NOTE on rejection_rate: it must match values the `outcome` column actually
-- takes (approved|request_changes|critical_block|escalated). Filtering on a
-- literal 'rejected' — which is never written — would make this metric a
-- permanent 0.0 and silently kill the drift signal it exists to provide.
CREATE MATERIALIZED VIEW IF NOT EXISTS agent_health_1m
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('1 minute', ts)                         AS bucket,
  agent,
  count(*) FILTER (WHERE event_type = 'llm.call')     AS llm_calls,
  sum(cost_usd)                                       AS cost_usd,
  approx_percentile(0.95, percentile_agg(latency_ms)) AS p95_ms,
  count(*) FILTER (WHERE outcome IN ('request_changes','critical_block','escalated'))::float
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

-- ═══ INV-6 GUARDS — created LAST, deliberately ══════════════════════════
-- These must be the final statements in the file. DROP MATERIALIZED VIEW ...
-- CASCADE (used above to replace a stale continuous aggregate) cascades into
-- triggers on the underlying hypertable. Creating these earlier means a
-- re-run of this migration silently leaves the audit trail mutable — the
-- exact failure L4 VERIFY caught.
DROP TRIGGER IF EXISTS agent_events_no_update ON agent_events;
CREATE TRIGGER agent_events_no_update
  BEFORE UPDATE OR DELETE ON agent_events
  FOR EACH ROW EXECUTE FUNCTION agent_events_reject_mutation();

-- A FOR EACH ROW trigger cannot fire on TRUNCATE, so TRUNCATE would silently
-- erase the whole audit trail. Close that hole with a statement-level trigger.
DROP TRIGGER IF EXISTS agent_events_no_truncate ON agent_events;
CREATE TRIGGER agent_events_no_truncate
  BEFORE TRUNCATE ON agent_events
  FOR EACH STATEMENT EXECUTE FUNCTION agent_events_reject_mutation();

-- Fail loudly if the guards are not actually armed when this file finishes.
DO $$
DECLARE
  n INT;
BEGIN
  SELECT count(*) INTO n
    FROM pg_trigger
   WHERE tgrelid = 'agent_events'::regclass
     AND NOT tgisinternal
     AND tgname IN ('agent_events_no_update', 'agent_events_no_truncate');
  IF n <> 2 THEN
    RAISE EXCEPTION 'INV-6 guards not armed: expected 2 triggers, found %', n;
  END IF;
  RAISE NOTICE 'INV-6 guards armed (% triggers)', n;
END $$;

-- ── done ────────────────────────────────────────────────────────────────
