"""M3 gate — the Tiger data spine is provisioned correctly.

Asserts the three lanes from ADR-0001 / the study's Part II exist in one store:
  memory -> code_chunks (VECTOR(256) + DiskANN + FTS GIN)
  time   -> agent_events hypertable + continuous aggregates
  truth  -> pr_review_records / finding_records / hitl_reviews / hitl_feedback

Requires TIGER_DATABASE_URL (backend/.env). Skips cleanly if unset so the suite
stays runnable on a machine without the credential.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / "backend" / ".env")

TIGER_URL = os.getenv("TIGER_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live Tiger checks"
)


@pytest.fixture(scope="module")
def conn():
    """Shared connection. autocommit=True so SELECTs don't hold open
    transactions that would lock-block a concurrent migration re-run.

    Tests that need a transaction wrap themselves in
    ``with conn.transaction(force_rollback=True):`` which works correctly
    regardless of the connection's autocommit setting.
    """
    with psycopg.connect(TIGER_URL, connect_timeout=30, autocommit=True) as c:
        yield c


def _scalars(conn, sql: str, params: tuple = ()) -> list:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


# ── 1. extensions ───────────────────────────────────────────────────────
def test_required_extensions_installed(conn):
    """timescaledb + pgvector + pgvectorscale are what make one store carry three shapes."""
    found = _scalars(
        conn,
        "select extname from pg_extension "
        "where extname in ('timescaledb','vector','vectorscale') order by 1",
    )
    assert found == ["timescaledb", "vector", "vectorscale"], f"missing extensions: {found}"


# ── 2. memory lane ──────────────────────────────────────────────────────
def test_code_chunks_table_shape(conn):
    """Every expected column must exist AND have the correct type.

    The original zip of two ORDER BY ordinal_position queries assumed row
    alignment, which is fragile. We build a single "col:type" dict instead.
    """
    raw = _scalars(
        conn,
        "select column_name || ':' || data_type from information_schema.columns "
        "where table_name='code_chunks' order by ordinal_position",
    )
    cols = dict(x.split(":", 1) for x in raw)
    required = {
        "id": "uuid",
        "repo": "text",
        "path": "text",
        "chunk_index": "integer",
        "content": "text",
        "content_tsv": "tsvector",
    }
    for col, expected_type in required.items():
        actual = cols.get(col)
        assert actual == expected_type, (
            f"code_chunks.{col}: expected {expected_type}, got {actual}"
        )
    assert "embedding" in cols, "code_chunks missing column 'embedding'"


def test_code_chunks_embedding_is_256_dim(conn):
    dim = _scalars(
        conn,
        "select a.atttypmod from pg_attribute a "
        "join pg_class c on c.oid = a.attrelid "
        "where c.relname='code_chunks' and a.attname='embedding'",
    )
    assert dim and dim[0] == 256, f"embedding must be VECTOR(256), got atttypmod={dim}"


def test_code_chunks_has_diskann_and_fts_indexes(conn):
    idx = _scalars(
        conn, "select indexname from pg_indexes where tablename='code_chunks'"
    )
    defs = " ".join(
        _scalars(conn, "select indexdef from pg_indexes where tablename='code_chunks'")
    ).lower()
    assert "code_chunks_emb_idx" in idx, "DiskANN ANN index missing"
    assert "diskann" in defs, "index exists but is not a diskann index"
    assert "code_chunks_fts_idx" in idx, "FTS GIN index missing"
    assert "gin" in defs, "FTS index is not GIN"


def test_code_chunks_upsert_key_exists(conn):
    """(repo, path, chunk_index) unique — lets ingestion overwrite stale chunks."""
    idx = _scalars(
        conn, "select indexname from pg_indexes where tablename='code_chunks'"
    )
    assert "code_chunks_unique_idx" in idx


# ── 3. time lane ────────────────────────────────────────────────────────
def test_agent_events_is_a_hypertable(conn):
    ht = _scalars(
        conn,
        "select hypertable_name from timescaledb_information.hypertables "
        "where hypertable_name='agent_events'",
    )
    assert ht == ["agent_events"], "agent_events is not a hypertable"


def test_continuous_aggregates_exist(conn):
    views = _scalars(
        conn,
        "select view_name from timescaledb_information.continuous_aggregates "
        "where view_name in ('agent_health_1m','pr_cost_hourly') order by 1",
    )
    assert views == ["agent_health_1m", "pr_cost_hourly"], f"missing aggregates: {views}"


def test_agent_events_carries_cost_and_latency(conn):
    """L6: every action row must be able to answer 'what did it cost, how slow was it'."""
    cols = _scalars(
        conn,
        "select column_name from information_schema.columns where table_name='agent_events'",
    )
    for required in (
        "ts", "review_id", "agent", "span_id", "parent_span",
        "event_type", "cost_usd", "latency_ms", "confidence", "payload",
    ):
        assert required in cols, f"agent_events missing column {required!r}"


def test_agent_events_rejects_update(conn):
    """INV-6: the audit trail is immutable *by construction*, not by convention.

    A GRANT-based guard is not enough — the app connects as the table owner and
    owners bypass privileges. This proves the trigger actually fires.
    Everything runs inside a transaction that is rolled back.
    """
    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            cur.execute(
                "insert into agent_events (ts, review_id, agent, event_type, cost_usd, latency_ms)"
                " values (now(), gen_random_uuid(), 'security', 'llm.call', 0.01, 100)"
            )
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                cur.execute("update agent_events set cost_usd = 999 where agent = 'security'")


def test_agent_events_rejects_delete(conn):
    """INV-6: history cannot be erased either."""
    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            cur.execute(
                "insert into agent_events (ts, review_id, agent, event_type)"
                " values (now(), gen_random_uuid(), 'aggregator', 'decision')"
            )
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                cur.execute("delete from agent_events where agent = 'aggregator'")


def test_agent_events_still_accepts_insert(conn):
    """The guard must block mutation without breaking the append path."""
    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            cur.execute(
                "insert into agent_events (ts, review_id, agent, event_type, cost_usd)"
                " values (now(), gen_random_uuid(), 'docs', 'span.start', 0.002) returning span_id"
            )
            assert cur.fetchone()[0] is not None, "append path is broken"


def test_agent_events_rejects_truncate(conn):
    """INV-6: a FOR EACH ROW trigger cannot fire on TRUNCATE.

    Without a statement-level trigger, TRUNCATE silently erases the entire
    audit trail — the exact thing INV-6 forbids.
    """
    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                cur.execute("truncate table agent_events")


def test_llm_call_must_carry_cost_and_latency(conn):
    """INV-6: an llm.call with no cost/latency is an unaccountable action."""
    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "insert into agent_events (ts, review_id, agent, event_type)"
                    " values (now(), gen_random_uuid(), 'security', 'llm.call')"
                )


def test_non_llm_events_may_omit_cost(conn):
    """The accountability check must not block legitimate span/decision rows."""
    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            cur.execute(
                "insert into agent_events (ts, review_id, agent, event_type)"
                " values (now(), gen_random_uuid(), 'security', 'span.start') returning span_id"
            )
            assert cur.fetchone()[0] is not None


def test_rejection_rate_filter_matches_real_outcomes(conn):
    """The drift signal must filter on outcome values the system actually writes.

    The schema documents outcome as approved|request_changes|critical_block|
    escalated. A filter on a literal 'rejected' would never match, pinning
    rejection_rate at 0.0 forever and silently killing the calibration signal.
    """
    definition = _scalars(
        conn,
        "select view_definition from timescaledb_information.continuous_aggregates"
        " where view_name = 'agent_health_1m'",
    )[0]
    assert "'rejected'" not in definition, (
        "agent_health_1m filters on 'rejected', which the outcome column never "
        "takes — rejection_rate is dead and will always report 0.0"
    )
    assert "request_changes" in definition, (
        "rejection_rate does not count request_changes as a rejection"
    )


# ── 4. truth lane ───────────────────────────────────────────────────────
def test_truth_tables_exist(conn):
    tables = _scalars(
        conn, "select tablename from pg_tables where schemaname='public' order by 1"
    )
    for required in (
        "pr_review_records", "finding_records", "hitl_reviews",
        "hitl_feedback", "repo_file_index",
    ):
        assert required in tables, f"truth-lane table {required!r} missing"


def test_idempotency_key_is_unique(conn):
    """INV-5: a retried webhook delivery must not be able to create a second review.

    The test must prove the UNIQUE constraint covers exactly the idempotency key
    (repo, pr_number, delivery_uuid) — not just that *some* UNIQUE exists.
    """
    con = _scalars(
        conn,
        "select conname, pg_get_constraintdef(c.oid) as def from pg_constraint c "
        "join pg_class t on t.oid = c.conrelid "
        "where t.relname='pr_review_records' and c.contype='u'",
    )
    assert con, "pr_review_records has no UNIQUE constraint for delivery idempotency"
    # The auditor found that asserting 'con' exists but not WHICH columns is weak.
    # We now verify the specific columns the idempotency key requires.
    defs = _scalars(
        conn,
        "select pg_get_constraintdef(c.oid) from pg_constraint c "
        "join pg_class t on t.oid = c.conrelid "
        "where t.relname='pr_review_records' and c.contype='u'",
    )
    assert any(
        "repo" in d and "pr_number" in d and "delivery_uuid" in d for d in defs
    ), f"no UNIQUE covers (repo, pr_number, delivery_uuid) — found: {defs}"


def test_findings_cascade_from_review(conn):
    """Deleting a review must not orphan its findings.

    The specific foreign key is finding_records.review_id → pr_review_records(id).
    If a second FK were added later, a broad "c in any cascade" check would still
    pass even if the *right* one didn't cascade. We target the exact constraint.
    """
    condef = _scalars(
        conn,
        "select pg_get_constraintdef(c.oid) from pg_constraint c "
        "join pg_class t on t.oid = c.conrelid "
        "join pg_class rt on rt.oid = c.confrelid "
        "where t.relname='finding_records' "
        "  and rt.relname='pr_review_records' "
        "  and c.contype='f'",
    )
    assert condef, "no FK from finding_records to pr_review_records found"
    assert any("ON DELETE CASCADE" in d for d in condef), (
        f"FK exists but does not cascade on delete: {condef}"
    )


# ── 5. idempotency (M3 success criterion #4) ────────────────────────────
def test_migration_is_idempotent():
    """PLAN.md M3 claims 're-running exits 0'. Nothing asserted it until now.

    Runs the real migration file against the real database a second time and
    requires a clean exit — this is the criterion, executed rather than trusted.
    """
    import shutil
    import subprocess

    psql = shutil.which("psql") or r"C:\Program Files\PostgreSQL\16\bin\psql.exe"
    if not Path(psql).exists():
        pytest.skip(f"psql not found at {psql}")

    migration = REPO_ROOT / "scripts" / "migrations" / "2026-06-tiger-init.sql"
    assert migration.exists(), f"migration file missing: {migration}"

    # psql quirk on this machine: flags MUST precede the connection URL.
    # On Windows, subprocess.PIPE can deadlock with large NOTICE output.
    # Redirect both streams to a temp file and read it back.
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        with open(tmp_path, "w") as out:
            proc = subprocess.run(
                [psql, "-v", "ON_ERROR_STOP=1", "-f", str(migration), TIGER_URL],
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=60,
                env={**os.environ, "PGCONNECT_TIMEOUT": "30"},
            )
        log = Path(tmp_path).read_text(encoding="utf-8", errors="replace")
    finally:
        os.unlink(tmp_path)

    assert proc.returncode == 0, (
        f"migration re-run failed (exit {proc.returncode}):\n{log[-2000:]}"
    )
    lowered = log.lower()
    assert "error" not in lowered, f"migration re-run emitted errors:\n{log[-2000:]}"
