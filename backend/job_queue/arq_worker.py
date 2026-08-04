"""arq_worker — the production async webhook path (M17, Phase 8).

Webhook claims a delivery (Postgres — the durable record), then enqueues
a pipeline job here; the worker runs the M9 pipeline (agents → aggregator
→ decide → persist). Fail-soft: if Redis is down the webhook still
accepts the claim and the review stays manually runnable — a queue
failure never loses a review (the Postgres claim is the source of truth).

Run the worker:
    arq backend.job_queue.arq_worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / "backend" / ".env")

from arq.connections import RedisSettings, create_pool  # noqa: E402

from backend.observability.events import emit_agent_event  # noqa: E402

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_JOB_NAME = "pipeline_job"


async def pipeline_job(
    ctx: dict[str, Any],
    review_id: UUID | str,
    diff: str,
    repo: str,
    pr_number: int,
    head_sha: str | None = None,
) -> dict[str, Any]:
    """Run the review pipeline for a claimed review. Never raises.

    Failures emit an anchored error event (agent=job_queue, payload
    status=error — the same shape the drift error_events metric counts)
    and return a result dict with errors, so arq marks the job done
    without killing the worker.
    """
    from backend.webhook_receiver.router import run_review_pipeline

    rid = str(review_id)
    try:
        # GitHub webhooks don't carry diffs — fetch via the integration
        # when the payload didn't embed one (best-effort; local runs have
        # no app credentials and fall back to the embedded/empty diff).
        if not diff:
            try:
                from backend.integrations.github_client import GitHubClient

                diff = GitHubClient().get_pr_diff(repo, int(pr_number))
            except Exception:  # noqa: BLE001 — no credentials / network
                diff = ""
        result = await asyncio.to_thread(
            run_review_pipeline,
            rid, diff, repo, pr_number, head_sha,
        )
        return {"review_id": rid, "ok": True, **result}
    except Exception as exc:  # noqa: BLE001 — the worker must not die
        emit_agent_event(
            rid, "job_queue", "tool.call",
            payload={"status": "error", "error": str(exc)[:200]},
        )
        return {"review_id": rid, "ok": False, "error": str(exc)[:200]}


async def enqueue_review(
    review_id: UUID | str,
    diff: str,
    repo: str,
    pr_number: int,
    head_sha: str | None = None,
    *,
    redis_url: str | None = None,
) -> str:
    """Enqueue a pipeline job.

    Public contract: raises the BUILTIN ConnectionError on any queue
    outage. Real redis-py failures (redis.exceptions.ConnectionError /
    TimeoutError — subclasses of RedisError, NOT the builtin) and raw
    OSErrors are normalized here so callers only ever catch one class.
    """
    from redis.exceptions import (
        ConnectionError as RedisConnectionError,
        TimeoutError as RedisTimeoutError,
    )

    settings = RedisSettings.from_dsn(redis_url or REDIS_URL)
    try:
        pool = await create_pool(settings)
        try:
            job = await pool.enqueue_job(
                _JOB_NAME, str(review_id), diff, repo, int(pr_number), head_sha
            )
        finally:
            try:
                await pool.close()
            except (RedisConnectionError, RedisTimeoutError, OSError):
                # Closing a dead pool must never mask the original failure.
                pass
    except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
        # Covers BOTH create_pool and enqueue_job (TOCTOU: redis can die
        # between arq's eager ping and the publish) — any queue outage
        # surfaces as the builtin ConnectionError.
        raise ConnectionError(f"redis unavailable: {exc}") from exc
    if job is None:
        raise ConnectionError("arq enqueue_job returned None")
    return job.job_id


class WorkerSettings:
    """arq worker config — run: arq backend.job_queue.arq_worker.WorkerSettings."""

    functions: list[Any] = [pipeline_job]
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    # INV-4: every execution has an explicit timeout — a runaway review
    # pipeline must be killed, not left running.
    job_timeout: int = 300
    max_jobs: int = 10
    health_check_interval: int = 30
