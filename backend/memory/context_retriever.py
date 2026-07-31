"""context_retriever — hybrid retrieval: DiskANN ANN + FTS + reciprocal rank fusion.

The retriever runs two lanes in parallel against the code_chunks table:
  1. Vector lane: cosine similarity via the DiskANN index (pgvector)
  2. FTS lane: full-text search via the GIN index on content_tsv

Results are merged using reciprocal rank fusion (RRF):
  score(d) = sum over lanes of (1 / (k + rank_in_lane(d)))

where k is a constant (default 60, the standard RRF parameter).

This directly supports M5's success criteria:
  - Vector lane finds a renamed helper (semantic match)
  - FTS lane finds an exact identifier (lexical match)
  - Hybrid beats both alone on recall
"""

from __future__ import annotations

from typing import Any

from backend.database.postgres import get_connection
from backend.memory.embedder import Embedder, get_embedder

# RRF constant — standard value from the original paper
RRF_K = 60


class ChunkResult:
    """A retrieved chunk with its metadata and fusion score."""

    def __init__(
        self,
        chunk_id: Any,
        repo: str,
        path: str,
        chunk_index: int,
        content: str,
        score: float,
        vector_rank: int | None = None,
        fts_rank: int | None = None,
    ) -> None:
        self.chunk_id = chunk_id
        self.repo = repo
        self.path = path
        self.chunk_index = chunk_index
        self.content = content
        self.score = score
        self.vector_rank = vector_rank
        self.fts_rank = fts_rank

    def __repr__(self) -> str:
        return (
            f"ChunkResult(path={self.path!r}, chunk_index={self.chunk_index}, "
            f"score={self.score:.4f}, vector_rank={self.vector_rank}, "
            f"fts_rank={self.fts_rank})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ChunkResult):
            return NotImplemented
        return bool(self.chunk_id == other.chunk_id)

    def __hash__(self) -> int:
        return hash(self.chunk_id)


def retrieve_vector(
    query: str,
    repo: str,
    top_k: int = 10,
    *,
    conn: Any = None,
    embedder: Embedder | None = None,
) -> list[ChunkResult]:
    """Vector lane: embed the query, run cosine similarity via DiskANN."""
    emb = embedder or get_embedder()
    query_vec = emb.embed(query)
    vec_str = _vector_to_pgstr(query_vec)

    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    sql = """
        SELECT id, repo, path, chunk_index, content,
               embedding <=> %s::vector AS distance
        FROM code_chunks
        WHERE repo = %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    params = (vec_str, repo, vec_str, top_k)

    results: list[ChunkResult] = []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for rank, row in enumerate(cur.fetchall()):
                results.append(
                    ChunkResult(
                        chunk_id=row[0],
                        repo=row[1],
                        path=row[2],
                        chunk_index=row[3],
                        content=row[4],
                        score=1.0 - float(row[5]),  # convert distance to similarity
                        vector_rank=rank,
                    )
                )
    finally:
        if own_conn:
            conn.close()

    return results


def retrieve_fts(
    query: str,
    repo: str,
    top_k: int = 10,
    *,
    conn: Any = None,
) -> list[ChunkResult]:
    """FTS lane: full-text search via the GIN index on content_tsv."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    # Build a tsquery from the query terms
    # Replace spaces with & for AND search
    tsquery = " & ".join(query.split())

    sql = """
        SELECT id, repo, path, chunk_index, content,
               ts_rank(content_tsv, plainto_tsquery('english', %s)) AS rank
        FROM code_chunks
        WHERE repo = %s AND content_tsv @@ plainto_tsquery('english', %s)
        ORDER BY rank DESC
        LIMIT %s
    """
    params = (query, repo, query, top_k)

    results: list[ChunkResult] = []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for rank, row in enumerate(cur.fetchall()):
                results.append(
                    ChunkResult(
                        chunk_id=row[0],
                        repo=row[1],
                        path=row[2],
                        chunk_index=row[3],
                        content=row[4],
                        score=float(row[5]),
                        fts_rank=rank,
                    )
                )
    finally:
        if own_conn:
            conn.close()

    return results


def retrieve_hybrid(
    query: str,
    repo: str,
    top_k: int = 10,
    *,
    conn: Any = None,
    embedder: Embedder | None = None,
    rrf_k: int = RRF_K,
) -> list[ChunkResult]:
    """Hybrid retrieval: run vector + FTS in parallel, merge via reciprocal rank fusion.

    RRF score(d) = 1/(k + rank_vector) + 1/(k + rank_fts)
    where ranks are 0-indexed and missing lanes contribute 0.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    try:
        vec_results = retrieve_vector(query, repo, top_k=top_k, conn=conn, embedder=embedder)
        fts_results = retrieve_fts(query, repo, top_k=top_k, conn=conn)
    finally:
        if own_conn:
            conn.close()

    # Build merge map: chunk_id → ChunkResult (with fused score)
    by_id: dict[Any, ChunkResult] = {}

    for res in vec_results:
        score = 1.0 / (rrf_k + (res.vector_rank or 0))
        by_id[res.chunk_id] = ChunkResult(
            chunk_id=res.chunk_id,
            repo=res.repo,
            path=res.path,
            chunk_index=res.chunk_index,
            content=res.content,
            score=score,
            vector_rank=res.vector_rank,
            fts_rank=None,
        )

    for res in fts_results:
        fts_score = 1.0 / (rrf_k + (res.fts_rank or 0))
        if res.chunk_id in by_id:
            by_id[res.chunk_id].score += fts_score
            by_id[res.chunk_id].fts_rank = res.fts_rank
        else:
            by_id[res.chunk_id] = ChunkResult(
                chunk_id=res.chunk_id,
                repo=res.repo,
                path=res.path,
                chunk_index=res.chunk_index,
                content=res.content,
                score=fts_score,
                vector_rank=None,
                fts_rank=res.fts_rank,
            )

    # Sort by fused score descending, take top_k
    merged = sorted(by_id.values(), key=lambda r: r.score, reverse=True)
    return merged[:top_k]


def _vector_to_pgstr(vec: Any) -> str:
    """Convert a numpy array (or list) to PostgreSQL vector string '[v1,v2,...]'."""
    import numpy as np

    if isinstance(vec, np.ndarray):
        floats = vec.tolist()
    else:
        floats = list(vec)
    return "[" + ",".join(f"{v:.8f}" for v in floats) + "]"