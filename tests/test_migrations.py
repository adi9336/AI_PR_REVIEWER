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
    with psycopg.connect(TIGER_URL, connect_timeout=30) as c:
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
    cols = dict(
        zip(
            _scalars(
                conn,
                "select column_name from information_schema.columns "
                "where table_name='code_chunks' order by ordinal_position",
            ),
            _scalars(
                conn,
                "select data_type from information_schema.columns "
                "where table_name='code_chunks' order by ordinal_position",
            ),
        )
    )
    for required in ("id", "repo", "path", "chunk_index", "content", "embedding", "content_tsv"):
        assert required in cols, f"code_chunks missing column {required!r}"


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
    """INV-5: a retried webhook delivery must not be able to create a second review."""
    con = _scalars(
        conn,
        "select conname from pg_constraint c "
        "join pg_class t on t.oid = c.conrelid "
        "where t.relname='pr_review_records' and c.contype='u'",
    )
    assert con, "pr_review_records has no UNIQUE constraint for delivery idempotency"


def test_findings_cascade_from_review(conn):
    """Deleting a review must not orphan its findings."""
    rule = _scalars(
        conn,
        "select confdeltype from pg_constraint c "
        "join pg_class t on t.oid = c.conrelid "
        "where t.relname='finding_records' and c.contype='f'",
    )
    assert "c" in rule, f"finding_records FK is not ON DELETE CASCADE (got {rule})"
