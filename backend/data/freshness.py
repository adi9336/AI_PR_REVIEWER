"""freshness — track file content hashes to skip unchanged files on re-ingest.

The repo_file_index table stores (repo, path, content_hash, last_indexed_at).
On ingestion, we compare the current file hash against the stored hash.
If they match, the file is skipped (zero re-embeds). If they differ or
the file is new, it's re-chunked and re-embedded.

This directly supports M5's success criterion: "re-ingesting an unchanged
file re-embeds 0 chunks (freshness works)."
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from backend.database.postgres import get_connection


def file_hash(content: str) -> str:
    """SHA-256 hash of file content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_stored_hash(conn: Any, repo: str, path: str) -> str | None:
    """Return the stored content_hash for a file, or None if not indexed."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT content_hash FROM repo_file_index WHERE repo = %s AND path = %s",
            (repo, path),
        )
        row = cur.fetchone()
    return row[0] if row else None


def is_file_changed(conn: Any, repo: str, path: str, current_hash: str) -> bool:
    """True if the file is new or its hash differs from the stored one."""
    stored = get_stored_hash(conn, repo, path)
    return stored != current_hash


def update_file_index(conn: Any, repo: str, path: str, content_hash: str) -> None:
    """Upsert the file's hash into repo_file_index."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO repo_file_index (repo, path, content_hash, last_indexed_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (repo, path)
            DO UPDATE SET content_hash = EXCLUDED.content_hash,
                          last_indexed_at = now()
            """,
            (repo, path, content_hash),
        )


def get_outdated_files(conn: Any, repo: str, files: list[tuple[str, str]]) -> list[str]:
    """Given (path, content) pairs, return paths whose hash has changed.

    This is the batch check: ingestion calls this to decide which files
    need re-embedding.
    """
    outdated: list[str] = []
    for path, content in files:
        h = file_hash(content)
        if is_file_changed(conn, repo, path, h):
            outdated.append(path)
    return outdated


def delete_stale_chunks(conn: Any, repo: str, path: str) -> None:
    """Delete all chunks for a file before re-inserting (stale chunk cleanup)."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM code_chunks WHERE repo = %s AND path = %s",
            (repo, path),
        )