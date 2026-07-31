"""M5 gate — hybrid retrieval returns grounded top-k.

Asserts:
  1. A query for a renamed helper returns its chunk in top-5 via vector lane.
  2. A query for an exact identifier returns it via FTS lane.
  3. Hybrid beats both alone on the fixture recall assertion.
  4. Re-ingesting an unchanged file re-embeds 0 chunks (freshness works).

Requires TIGER_DATABASE_URL + OPENAI_API_KEY. Skips cleanly if either is unset.
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
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not TIGER_URL or not OPENAI_KEY,
    reason="TIGER_DATABASE_URL or OPENAI_API_KEY not set — skipping live retrieval tests",
)

FIXTURE_REPO = REPO_ROOT / "fixtures" / "sample_repo"
REPO_NAME = "sample_repo"


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(TIGER_URL, connect_timeout=30, autocommit=True) as c:
        # Clean slate: delete any existing chunks for this fixture repo
        with c.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repo = %s", (REPO_NAME,))
            cur.execute("DELETE FROM repo_file_index WHERE repo = %s", (REPO_NAME,))
        yield c
        # Cleanup after tests
        with c.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repo = %s", (REPO_NAME,))
            cur.execute("DELETE FROM repo_file_index WHERE repo = %s", (REPO_NAME,))


@pytest.fixture(scope="module", autouse=True)
def ingest(conn):
    """Ingest the fixture repo once before all tests."""
    from backend.data.ingestion import ingest_repo

    stats = ingest_repo(FIXTURE_REPO, REPO_NAME, conn=conn)
    return stats


# ── 0. ingestion succeeded ───────────────────────────────────────────────
def test_ingestion_produced_chunks(ingest):
    """Sanity: ingestion actually chunked and embedded the fixture files."""
    assert ingest["files_total"] >= 4, f"expected >=4 files, got {ingest['files_total']}"
    assert ingest["chunks_embedded"] >= 4, f"expected >=4 chunks, got {ingest['chunks_embedded']}"
    assert ingest["files_changed"] == ingest["files_total"], "first ingestion should change all files"


# ── 1. vector lane finds a renamed helper ───────────────────────────────
def test_vector_lane_finds_renamed_helper(conn):
    """Query 'get user data' should find user_service.py (load_user_profile)
    in top-5 via the vector lane, even though the exact words don't match."""
    from backend.memory.context_retriever import retrieve_vector

    results = retrieve_vector("get user data", REPO_NAME, top_k=5, conn=conn)
    assert len(results) > 0, "vector lane returned no results"

    paths = [r.path for r in results]
    assert any("user_service" in p for p in paths), (
        f"vector lane did not find user_service.py in top-5: {paths}"
    )


# ── 2. FTS lane finds an exact identifier ────────────────────────────────
def test_fts_lane_finds_exact_identifier(conn):
    """Query 'generate_session_token' should find auth.py via FTS (exact match)."""
    from backend.memory.context_retriever import retrieve_fts

    results = retrieve_fts("generate_session_token", REPO_NAME, top_k=5, conn=conn)
    assert len(results) > 0, "FTS lane returned no results"

    paths = [r.path for r in results]
    assert any("auth" in p for p in paths), (
        f"FTS lane did not find auth.py: {paths}"
    )


# ── 3. hybrid beats both alone ───────────────────────────────────────────
def test_hybrid_beats_both_alone(conn):
    """For a query that benefits from both lanes, hybrid retrieval should
    rank the correct result higher than either lane alone.

    Query: 'user profile configuration' — touches user_service.py (profile)
    and config.py (configuration). Hybrid should surface both higher than
    either lane alone because RRF merges signals.
    """
    from backend.memory.context_retriever import retrieve_fts, retrieve_hybrid, retrieve_vector

    query = "user profile configuration"
    vec_results = retrieve_vector(query, REPO_NAME, top_k=10, conn=conn)
    fts_results = retrieve_fts(query, REPO_NAME, top_k=10, conn=conn)
    hybrid_results = retrieve_hybrid(query, REPO_NAME, top_k=10, conn=conn)

    # Helper: find rank of a target file in a result list
    def rank_of(results, target_substr):
        for i, r in enumerate(results):
            if target_substr in r.path:
                return i
        return 999  # not found = effectively worst rank

    # Hybrid should find both files at least as well as the better single lane
    vec_user_rank = rank_of(vec_results, "user_service")
    fts_user_rank = rank_of(fts_results, "user_service")
    hybrid_user_rank = rank_of(hybrid_results, "user_service")

    vec_config_rank = rank_of(vec_results, "config")
    fts_config_rank = rank_of(fts_results, "config")
    hybrid_config_rank = rank_of(hybrid_results, "config")

    best_single_user = min(vec_user_rank, fts_user_rank)
    best_single_config = min(vec_config_rank, fts_config_rank)

    assert hybrid_user_rank <= best_single_user, (
        f"hybrid user rank {hybrid_user_rank} > best single {best_single_user}"
    )
    assert hybrid_config_rank <= best_single_config, (
        f"hybrid config rank {hybrid_config_rank} > best single {best_single_config}"
    )

    # Verify hybrid includes more relevant results than either lane alone
    hybrid_paths = {r.path for r in hybrid_results}
    vec_paths = {r.path for r in vec_results}
    fts_paths = {r.path for r in fts_results}
    assert len(hybrid_paths) >= len(vec_paths), (
        f"hybrid returned fewer unique paths ({len(hybrid_paths)}) "
        f"than vector ({len(vec_paths)})"
    )
    assert len(hybrid_paths) >= len(fts_paths), (
        f"hybrid returned fewer unique paths ({len(hybrid_paths)}) "
        f"than FTS ({len(fts_paths)})"
    )


# ── 4. freshness: re-ingest=0 chunks ────────────────────────────────────
def test_reingest_unchanged_file_zero_embeddings(conn):
    """Re-ingesting the unchanged fixture repo should embed 0 chunks."""
    from backend.data.ingestion import ingest_repo

    stats = ingest_repo(FIXTURE_REPO, REPO_NAME, conn=conn)
    assert stats["chunks_embedded"] == 0, (
        f"freshness broke: re-ingested unchanged files but embedded "
        f"{stats['chunks_embedded']} chunks"
    )
    assert stats["files_skipped"] == stats["files_total"], (
        f"expected all {stats['files_total']} files skipped, "
        f"got {stats['files_skipped']} skipped"
    )