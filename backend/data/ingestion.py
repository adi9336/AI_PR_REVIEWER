"""ingestion — chunk + embed repo files into code_chunks.

Reads files from a local repo path, chunks them, embeds each chunk,
and upserts into the code_chunks table. Uses freshness tracking to
skip unchanged files (zero re-embeds on re-ingest).

Chunking strategy: split on blank lines, group into ~500-token chunks.
Each chunk gets a sequential chunk_index within its file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.data.freshness import (
    delete_stale_chunks,
    file_hash,
    is_file_changed,
    update_file_index,
)
from backend.database.postgres import get_connection
from backend.memory.embedder import Embedder, get_embedder

# File extensions to index
SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".kt", ".scala", ".sh", ".yaml", ".yml", ".json", ".toml",
    ".md", ".txt", ".sql", ".html", ".css", ".vue", ".svelte",
}

# Chunking parameters
MAX_CHUNK_LINES = 50
MAX_CHUNK_CHARS = 2000


def chunk_file(content: str, path: str) -> list[tuple[int, str]]:
    """Split file content into chunks.

    Returns a list of (chunk_index, chunk_text) pairs.
    Strategy: split on blank lines, accumulate into chunks up to
    MAX_CHUNK_LINES or MAX_CHUNK_CHARS, whichever is hit first.
    """
    lines = content.split("\n")
    chunks: list[tuple[int, str]] = []
    current_lines: list[str] = []
    current_chars = 0
    idx = 0

    for line in lines:
        if current_lines and (
            len(current_lines) >= MAX_CHUNK_LINES
            or current_chars + len(line) + 1 > MAX_CHUNK_CHARS
        ):
            chunks.append((idx, "\n".join(current_lines)))
            idx += 1
            current_lines = []
            current_chars = 0

        current_lines.append(line)
        current_chars += len(line) + 1

    if current_lines:
        chunks.append((idx, "\n".join(current_lines)))

    return chunks


def _should_index(path: Path) -> bool:
    """True if the file extension is supported and the file is not hidden."""
    return (
        path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not any(part.startswith(".") for part in path.parts[:-1] if part != ".")
    )


def collect_files(repo_path: str | Path) -> list[Path]:
    """Walk a repo path and return all indexable files."""
    root = Path(repo_path)
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # Skip hidden dirs, __pycache__, .git, node_modules, .venv
        parts = p.relative_to(root).parts
        if any(
            part in (".git", "__pycache__", "node_modules", ".venv", ".mypy_cache",
                     ".pytest_cache", "venv", "dist", "build")
            or part.startswith(".")
            for part in parts[:-1]
        ):
            continue
        if _should_index(p):
            files.append(p)
    return sorted(files)


def ingest_repo(
    repo_path: str | Path,
    repo_name: str | None = None,
    *,
    conn: Any = None,
    embedder: Embedder | None = None,
) -> dict[str, int]:
    """Ingest a repo: chunk, embed, and upsert into code_chunks.

    Returns a dict with keys: files_total, files_changed, files_skipped,
    chunks_embedded, chunks_upserted.

    Uses freshness tracking: unchanged files are skipped entirely.
    """
    root = Path(repo_path)
    if not root.exists():
        raise FileNotFoundError(f"repo path not found: {root}")

    repo = repo_name or root.name
    emb = embedder or get_embedder()
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    stats = {
        "files_total": 0,
        "files_changed": 0,
        "files_skipped": 0,
        "chunks_embedded": 0,
        "chunks_upserted": 0,
    }

    try:
        files = collect_files(root)
        stats["files_total"] = len(files)

        for fp in files:
            rel_path = str(fp.relative_to(root)).replace("\\", "/")
            content = fp.read_text(encoding="utf-8", errors="replace")
            fhash = file_hash(content)

            if not is_file_changed(conn, repo, rel_path, fhash):
                stats["files_skipped"] += 1
                continue

            stats["files_changed"] += 1

            # Delete stale chunks for this file
            delete_stale_chunks(conn, repo, rel_path)

            # Chunk the file
            chunks = chunk_file(content, rel_path)
            if not chunks:
                continue

            # Embed all chunks
            chunk_texts = [c[1] for c in chunks]
            embeddings = emb.embed_batch(chunk_texts)
            stats["chunks_embedded"] += len(embeddings)

            # Upsert into code_chunks
            for (chunk_idx, chunk_text), embedding in zip(chunks, embeddings):
                _upsert_chunk(conn, repo, rel_path, chunk_idx, chunk_text, embedding)
                stats["chunks_upserted"] += 1

            # Update freshness index
            update_file_index(conn, repo, rel_path, fhash)
    finally:
        if own_conn and conn is not None:
            conn.close()

    return stats


def _upsert_chunk(
    conn: Any,
    repo: str,
    path: str,
    chunk_index: int,
    content: str,
    embedding: Any,
) -> None:
    """Upsert a single chunk into code_chunks."""
    # Convert numpy array to PostgreSQL vector format: '[0.1,0.2,...]'
    vec_str = _vector_to_pgstr(embedding)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO code_chunks (id, repo, path, chunk_index, content, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (repo, path, chunk_index)
            DO UPDATE SET content = EXCLUDED.content,
                          embedding = EXCLUDED.embedding,
                          updated_at = now()
            """,
            (str(uuid4()), repo, path, chunk_index, content, vec_str),
        )


def _vector_to_pgstr(vec: Any) -> str:
    """Convert a numpy array (or list) to PostgreSQL vector string '[v1,v2,...]'."""
    import numpy as np

    if isinstance(vec, np.ndarray):
        floats = vec.tolist()
    else:
        floats = list(vec)
    # Truncate to reasonable precision to keep the string compact
    return "[" + ",".join(f"{v:.8f}" for v in floats) + "]"


def main() -> int:
    """CLI entry point: python -m backend.data.ingestion --repo ./path"""
    import argparse

    parser = argparse.ArgumentParser(description="Ingest a repo into code_chunks")
    parser.add_argument("--repo", required=True, help="Path to the repo to ingest")
    parser.add_argument("--name", default=None, help="Repo name (default: dir name)")
    args = parser.parse_args()

    stats = ingest_repo(args.repo, args.name)
    print(
        f"Done: {stats['files_total']} files, "
        f"{stats['files_changed']} changed, "
        f"{stats['files_skipped']} skipped, "
        f"{stats['chunks_embedded']} chunks embedded"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())