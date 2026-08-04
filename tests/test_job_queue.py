"""M17 gate — ARQ async worker: webhook → queue → worker → pipeline.

Tests:
  1. enqueue_review publishes a pipeline_job with the right args
     (monkeypatched arq pool captures them); ConnectionError when Redis
     is down.
  2. pipeline_job runs run_review_pipeline with the right args; an
     exception → anchored error event emitted, no raise (worker survives).
  3. Webhook router: enqueue after claim → 202 with queued/job_id;
     fail-soft: Redis down → 202 queued=false, review still claimed.
  4. Live (redis container, docker-gated): enqueue + worker job → the
     review leaves 'pending' (auto-pipeline, no manual /run).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / "backend" / ".env")

TIGER_URL = os.getenv("TIGER_DATABASE_URL", "")


# ── 1. enqueue_review ───────────────────────────────────────────────────


def test_enqueue_review_publishes_job(monkeypatch):
    from backend.job_queue.arq_worker import enqueue_review

    captured: dict = {}

    class _FakeJob:
        job_id = "job-123"

    class _FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            captured["name"] = name
            captured["args"] = args
            return _FakeJob()

        async def close(self):
            pass

    class _FakeRedisSettings:
        @classmethod
        def from_dsn(cls, url):
            return url

    async def _fake_create_pool(settings):
        captured["dsn"] = settings
        return _FakePool()

    monkeypatch.setattr("backend.job_queue.arq_worker.RedisSettings", _FakeRedisSettings)
    monkeypatch.setattr("backend.job_queue.arq_worker.create_pool", _fake_create_pool)

    rid = uuid.uuid4()
    job_id = asyncio.run(
        enqueue_review(rid, "diff-here", "owner/repo", 42, "abc123",
                       redis_url="redis://x:6379/0")
    )
    assert job_id == "job-123"
    assert captured["name"] == "pipeline_job"
    assert captured["args"][0] == str(rid)
    assert captured["args"][1] == "diff-here"
    assert captured["args"][2] == "owner/repo"
    assert captured["args"][3] == 42
    assert captured["args"][4] == "abc123"
    assert captured["dsn"] == "redis://x:6379/0"


def test_enqueue_review_connection_error_propagates(monkeypatch):
    from backend.job_queue.arq_worker import enqueue_review

    class _FakeRedisSettings:
        @classmethod
        def from_dsn(cls, url):
            return url

    async def _fake_create_pool(settings):
        raise ConnectionError("redis is down")

    monkeypatch.setattr("backend.job_queue.arq_worker.RedisSettings", _FakeRedisSettings)
    monkeypatch.setattr("backend.job_queue.arq_worker.create_pool", _fake_create_pool)

    with pytest.raises(ConnectionError):
        asyncio.run(enqueue_review(uuid.uuid4(), "", "r", 1))


def test_enqueue_review_normalizes_real_redis_exceptions(monkeypatch):
    """L4 round-1 catch: redis-py raises redis.exceptions.ConnectionError /
    TimeoutError (subclasses of RedisError, NOT the builtin). enqueue_review
    must normalize them to the builtin ConnectionError so the webhook's
    fail-soft branch actually fires in production."""
    from redis.exceptions import TimeoutError as RedisTimeoutError

    from backend.job_queue.arq_worker import enqueue_review

    class _FakeRedisSettings:
        @classmethod
        def from_dsn(cls, url):
            return url

    async def _fake_create_pool(settings):
        raise RedisTimeoutError("Timeout connecting to server")

    monkeypatch.setattr("backend.job_queue.arq_worker.RedisSettings", _FakeRedisSettings)
    monkeypatch.setattr("backend.job_queue.arq_worker.create_pool", _fake_create_pool)

    with pytest.raises(ConnectionError, match="redis unavailable"):
        asyncio.run(enqueue_review(uuid.uuid4(), "", "r", 1))


def test_enqueue_review_normalizes_enqueue_job_failure(monkeypatch):
    """L4 round-2 catch (TOCTOU): redis can die between create_pool's eager
    ping and the enqueue_job publish. A raw redis exception from
    enqueue_job must ALSO normalize to the builtin ConnectionError — the
    router's fail-soft + INV-6 error event depend on it."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    from backend.job_queue.arq_worker import enqueue_review

    class _FakeRedisSettings:
        @classmethod
        def from_dsn(cls, url):
            return url

    class _FakePool:
        async def enqueue_job(self, *args, **kwargs):
            raise RedisConnectionError("connection lost during publish")

        async def close(self):
            pass

    async def _fake_create_pool(settings):
        return _FakePool()

    monkeypatch.setattr("backend.job_queue.arq_worker.RedisSettings", _FakeRedisSettings)
    monkeypatch.setattr("backend.job_queue.arq_worker.create_pool", _fake_create_pool)

    with pytest.raises(ConnectionError, match="redis unavailable"):
        asyncio.run(enqueue_review(uuid.uuid4(), "", "r", 1))


# ── 2. pipeline_job ─────────────────────────────────────────────────────


def test_pipeline_job_runs_pipeline_with_args(monkeypatch):
    from backend.job_queue.arq_worker import pipeline_job

    called: dict = {}

    def _fake_run(review_id, diff, repo, pr_number, head_sha):
        called.update(
            review_id=str(review_id), diff=diff, repo=repo,
            pr_number=pr_number, head_sha=head_sha,
        )
        return {"decision": "escalate", "findings_count": 1}

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("backend.job_queue.arq_worker.asyncio.to_thread", _to_thread)
    monkeypatch.setattr(
        "backend.webhook_receiver.router.run_review_pipeline", _fake_run
    )

    rid = uuid.uuid4()
    result = asyncio.run(
        pipeline_job({}, rid, "the-diff", "owner/repo", 7, "sha1")
    )
    assert result["ok"] is True
    assert result["decision"] == "escalate"
    assert called == {
        "review_id": str(rid), "diff": "the-diff", "repo": "owner/repo",
        "pr_number": 7, "head_sha": "sha1",
    }


def test_pipeline_job_failure_emits_error_event_and_survives(monkeypatch):
    from backend.job_queue.arq_worker import pipeline_job

    emitted: list[dict] = []

    def _fake_emit(review_id, agent, event_type, **kwargs):
        emitted.append({"review_id": review_id, "agent": agent,
                        "event_type": event_type, **kwargs})
        return uuid.uuid4()

    def _boom(*args, **kwargs):
        raise RuntimeError("pipeline exploded")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("backend.job_queue.arq_worker.asyncio.to_thread", _to_thread)
    monkeypatch.setattr(
        "backend.webhook_receiver.router.run_review_pipeline", _boom
    )
    monkeypatch.setattr(
        "backend.job_queue.arq_worker.emit_agent_event", _fake_emit
    )

    rid = uuid.uuid4()
    result = asyncio.run(pipeline_job({}, rid, "", "r", 1))
    assert result["ok"] is False
    assert "exploded" in result["error"]
    assert len(emitted) == 1
    assert emitted[0]["agent"] == "job_queue"
    assert emitted[0]["payload"]["status"] == "error"


# ── 3. Webhook router: enqueue + fail-soft ──────────────────────────────


def test_webhook_enqueues_after_claim_and_fails_soft(monkeypatch):
    """Redis down → 202 accepted with queued=false; the claim stands."""
    from fastapi.testclient import TestClient

    from backend.main import app

    payload = {
        "action": "opened",
        "diff": "--- a/src/db.py\n+++ b/src/db.py\n+query = 'SELECT * FROM users WHERE id = ' + user_id",
        "repository": {"name": "test-repo", "full_name": "owner/test-repo"},
        "pull_request": {"number": 99, "title": "T", "body": "B",
                         "head": {"sha": "abc123", "ref": "f", "label": "u:f"},
                         "base": {"sha": "m", "ref": "main", "label": "r:main"}},
    }
    import json, hmac, hashlib
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / "backend" / ".env")

    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(payload)
    sig = "sha256=" + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    async def _enqueue_ok(*args, **kwargs):
        return "job-1"

    async def _enqueue_down(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(
        "backend.job_queue.arq_worker.enqueue_review", _enqueue_ok
    )
    r = TestClient(app).post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 202
    assert r.json()["queued"] is True
    assert r.json()["job_id"] == "job-1"

    monkeypatch.setattr(
        "backend.job_queue.arq_worker.enqueue_review", _enqueue_down
    )
    r2 = TestClient(app).post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert r2.status_code == 202, "queue down must still accept the claim"
    assert r2.json()["queued"] is False


def test_ping_event_acknowledged_no_claim(monkeypatch):
    """GitHub pings on webhook creation — the router must 200 pong and
    never claim a delivery for a ping body (no pull_request)."""
    import hashlib
    import hmac

    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.database.postgres import get_connection

    secret = "ping-test-secret"
    payload = {"zen": "Keep it logically awesome.", "hook_id": 42}
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    delivery = str(uuid.uuid4())


    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", secret)
    r = TestClient(app).post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pong"

    # no review row must exist for this delivery
    if os.getenv("TIGER_DATABASE_URL"):
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM pr_review_records WHERE delivery_uuid = %s",
                    (delivery,),
                )
                assert cur.fetchone()[0] == 0


def test_real_github_payload_shape_parses():
    """LIVE-DEPLOY catch: real GitHub PR payloads nest OBJECTS in
    pull_request.head/base (user/repo with login/id/...), not strings.
    Parsing must succeed and get_head_sha must still read head.sha."""
    from backend.integrations.github_models import PullRequestWebhook

    head = {
        "label": "adi9336:demo/vulnerable-sqli",
        "ref": "demo/vulnerable-sqli",
        "sha": "deadbeef1234567890",
        "user": {"login": "adi9336", "id": 148680267, "type": "User"},
        "repo": {"id": 1322057409, "name": "AI_PR_REVIEWER", "full_name": "adi9336/AI_PR_REVIEWER"},
    }
    base = {
        "label": "adi9336:master",
        "ref": "master",
        "sha": "c2b50ed",
        "user": {"login": "adi9336", "id": 148680267, "type": "User"},
        "repo": {"id": 1322057409, "name": "AI_PR_REVIEWER", "full_name": "adi9336/AI_PR_REVIEWER"},
    }
    wh = PullRequestWebhook(
        action="opened",
        delivery_uuid=str(uuid.uuid4()),
        repository={"name": "AI_PR_REVIEWER", "full_name": "adi9336/AI_PR_REVIEWER"},
        pull_request={"number": 1, "title": "Demo", "body": "b", "head": head, "base": base},
    )
    assert wh.pull_request.head["sha"] == "deadbeef1234567890"
    assert wh.pull_request.base["ref"] == "master"
    assert isinstance(wh.pull_request.head["user"], dict)


# ── 4. Live: redis + worker job (docker-gated) ──────────────────────────


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live worker test"
)
def test_pipeline_job_live_against_real_redis():
    """Full path with a real redis container: enqueue → job exists → worker
    function runs the pipeline → review leaves 'pending' (auto-review, no
    manual /run). Uses the docker CLI (the codebase's pattern — no SDK)."""
    import shutil
    import subprocess

    from backend.tools.sandbox import docker_available

    if not docker_available():
        pytest.skip("docker unavailable")

    import json
    import time

    import redis as redis_lib

    from backend.database.postgres import get_connection
    from backend.job_queue.arq_worker import enqueue_review, pipeline_job
    from backend.reliability.idempotency import claim_delivery

    golden_diff = json.load(open(REPO_ROOT / "fixtures" / "golden" / "sqli_pr.json"))["diff"]
    container_name = f"hermes-redis-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "-d", "--name", container_name,
         "-p", "6399:6379", "redis:7-alpine"],
        capture_output=True, check=True, timeout=60,
    )
    try:
        # wait for redis to answer
        deadline = time.time() + 30
        up = False
        while time.time() < deadline:
            try:
                r = redis_lib.Redis(host="localhost", port=6399, socket_timeout=1)
                r.ping()
                up = True
                break
            except Exception:
                time.sleep(1)
        assert up, "redis container did not become ready"
        redis_url = "redis://localhost:6399/0"

        with get_connection() as conn:
            review_id = claim_delivery(str(uuid.uuid4()), "arq-live", 9001, conn=conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM pr_review_records WHERE id = %s",
                    (str(review_id),),
                )
                assert cur.fetchone()[0] == "pending"

        # 1. enqueue against the REAL redis — the job must land
        job_id = asyncio.run(enqueue_review(
            review_id, golden_diff, "arq-live", 9001, None,
            redis_url=redis_url,
        ))
        assert job_id

        # 2. the job exists in redis (arq stores jobs keyed by job_id)
        r = redis_lib.Redis(host="localhost", port=6399, socket_timeout=2)
        assert r.exists(f"arq:job:{job_id}"), "enqueued job must exist in redis"

        # 3. the worker function consumes it: auto-pipeline with the diff
        result = asyncio.run(pipeline_job(
            {}, review_id, golden_diff, "arq-live", 9001, None,
        ))
        assert result["ok"] is True

        # 4. the review left 'pending' — the pipeline persisted the outcome
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM pr_review_records WHERE id = %s",
                    (str(review_id),),
                )
                status = cur.fetchone()[0]
        assert status in ("escalated", "queued", "completed", "auto_post"), (
            f"review must leave 'pending' after the worker ran (got {status})"
        )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, timeout=60, check=False,
        )
        # clean the claimed review row (agent_events rows stay — INV-6)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM pr_review_records WHERE repo = 'arq-live'"
                )
