"""M14 gate — Governance: masking + RBAC + queryable audit + explainability.

Tests:
  1. masking: sk- keys, GitHub tokens, postgres DSNs and k=v pairs are
     redacted; plain text untouched; payloads masked recursively.
  2. RBAC: no server key → 503; missing/wrong X-API-Key → 401; valid → pass.
  3. API: /audit/events and /audit/reviews/... reject without a key
     (401/503) — never reach the DB unauthenticated.
  4. DB-gated (Tiger): query_audit filters + masks, audit_summary rollup,
     explain_finding reconstructs finding + trace + prompt versions.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from dotenv import load_dotenv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / "backend" / ".env")

TIGER_URL = os.getenv("TIGER_DATABASE_URL", "")

from backend.security.masking import mask_payload, mask_secrets


# ── 1. Masking ──────────────────────────────────────────────────────────


def test_mask_secrets_redacts_api_keys():
    assert "sk-secret" not in mask_secrets("key=sk-abcdefghijklmnopqrstuvwxyz123456")
    assert "[REDACTED]" in mask_secrets("sk-abcdefghijklmnopqrstuvwxyz123456")


def test_mask_secrets_redacts_github_tokens():
    assert "[REDACTED]" in mask_secrets("token ghp_abcdefghijklmnopqrstuvwxyz1234567890")
    assert "[REDACTED]" in mask_secrets("token gho_abcdefghijklmnopqrstuvwxyz1234567890")


def test_mask_secrets_redacts_dsn():
    out = mask_secrets("postgres://admin:hunter2@db.example.com:5432/tiger")
    assert "hunter2" not in out
    assert "postgres://[REDACTED]" in out


def test_mask_secrets_redacts_kv_pairs():
    assert "hunter2" not in mask_secrets("password=hunter2")
    assert "[REDACTED]" in mask_secrets("secret: s3cr3t-value")
    assert "[REDACTED]" in mask_secrets("api_key=abc123")


def test_mask_secrets_leaves_plain_text():
    text = "the diff introduces a sql-injection risk in src/db.py line 10"
    assert mask_secrets(text) == text


def test_mask_payload_recursive():
    payload = {
        "tool": "read_file",
        "path": "sk-abcdefghijklmnopqrstuvwxyz123456",
        "meta": {"dsn": "postgres://u:p@h/db"},
        "list": ["ok", "ghp_abcdefghijklmnopqrstuvwxyz1234567890"],
        "n": 42,
    }
    out = mask_payload(payload)
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in out["path"]
    assert "[REDACTED]" in out["path"]
    assert "[REDACTED]" in out["meta"]["dsn"]
    assert "[REDACTED]" in out["list"][1]
    assert out["n"] == 42
    assert mask_payload(None) is None


def test_mask_payload_recurses_into_list_of_dicts():
    # L4 round-1 catch: list elements that are dicts passed through unmasked,
    # so a payload shaped {"findings": [{"evidence": "sk-..."}]} leaked.
    payload = {
        "findings": [
            {"evidence": "sk-abcdefghijklmnopqrstuvwxyz123456", "ok": "clean"},
            {"evidence": "ghp_abcdefghijklmnopqrstuvwxyz1234567890"},
            "plain",
        ]
    }
    out = mask_payload(payload)
    assert "[REDACTED]" in out["findings"][0]["evidence"]
    assert out["findings"][0]["ok"] == "clean"
    assert "[REDACTED]" in out["findings"][1]["evidence"]
    assert out["findings"][2] == "plain"


# ── 2. RBAC dependency ──────────────────────────────────────────────────


def _make_request(headers: dict[str, str] | None = None):
    from fastapi import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: dict[str, Any] = {
        "type": "http", "method": "GET", "path": "/audit/events",
        "query_string": b"", "headers": raw, "server": ("test", 80),
    }
    return Request(scope)


def test_rbac_no_server_key_returns_503(monkeypatch):
    from fastapi import HTTPException

    from backend.auth.dependencies import require_governance_key

    monkeypatch.delenv("GOVERNANCE_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_governance_key(_make_request({"X-API-Key": "anything"}))
    assert exc.value.status_code == 503


def test_rbac_missing_key_returns_401(monkeypatch):
    from fastapi import HTTPException

    from backend.auth.dependencies import require_governance_key

    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")
    with pytest.raises(HTTPException) as exc:
        require_governance_key(_make_request({}))
    assert exc.value.status_code == 401


def test_rbac_wrong_key_returns_401(monkeypatch):
    from fastapi import HTTPException

    from backend.auth.dependencies import require_governance_key

    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")
    with pytest.raises(HTTPException) as exc:
        require_governance_key(_make_request({"X-API-Key": "wrong-key"}))
    assert exc.value.status_code == 401


def test_rbac_valid_key_passes(monkeypatch):
    from backend.auth.dependencies import require_governance_key

    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")
    require_governance_key(_make_request({"X-API-Key": "test-key"}))  # must not raise


def test_rbac_non_ascii_key_returns_401_not_500(monkeypatch):
    # L4 round-1 catch: Starlette decodes raw header bytes as latin-1; the
    # old compare_digest(str, str) raised TypeError on non-ASCII → 500.
    from fastapi import HTTPException

    from backend.auth.dependencies import require_governance_key

    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")
    with pytest.raises(HTTPException) as exc:
        require_governance_key(_make_request({"X-API-Key": "\u00ff\u00fe"}))
    assert exc.value.status_code == 401, "non-ASCII key must be 401, never 500"


# ── 3. API auth (no DB touched on rejection) ────────────────────────────


def test_audit_api_rejects_without_key():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    r = client.get("/audit/events")
    assert r.status_code in (401, 503), "governance endpoints must never be open"
    r2 = client.get("/audit/reviews/00000000-0000-0000-0000-000000000000/summary")
    assert r2.status_code in (401, 503)


def test_audit_api_accepts_valid_key():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    os.environ["GOVERNANCE_API_KEY"] = "test-key"
    try:
        r = client.get("/audit/events", headers={"X-API-Key": "test-key"})
    finally:
        os.environ.pop("GOVERNANCE_API_KEY", None)
    assert r.status_code == 200


def test_audit_api_non_ascii_key_is_401_not_500():
    # L4 round-1 catch, API level: latin-1 header bytes must 401, never 500.
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    os.environ["GOVERNANCE_API_KEY"] = "test-key"
    try:
        # Raw latin-1 bytes header — exactly the raw-ASGI path the verifier
        # probed (httpx refuses non-ASCII str headers, so send bytes).
        r = client.get("/audit/events", headers={"X-API-Key": b"\xff\xfe"})
    finally:
        os.environ.pop("GOVERNANCE_API_KEY", None)
    assert r.status_code == 401


def test_audit_api_invalid_uuid_is_400(monkeypatch):
    # L4 round-1 catch: not-a-uuid path params raised ValueError → 500.
    # Only KeyError was caught; now ValueError → 400 (matches main.py).
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    monkeypatch.setenv("GOVERNANCE_API_KEY", "test-key")
    assert client.get(
        "/audit/reviews/not-a-uuid/summary", headers={"X-API-Key": "test-key"}
    ).status_code == 400
    assert client.get(
        "/audit/reviews/not-a-uuid/explain/not-a-uuid",
        headers={"X-API-Key": "test-key"},
    ).status_code == 400


# ── 4. DB-gated: audit queries (needs Tiger Cloud) ──────────────────────


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live audit tests"
)
def test_query_audit_filters_and_masks():
    from backend.database.postgres import get_connection
    from backend.models.enums import EventType
    from backend.observability.audit import query_audit
    from backend.observability.events import emit_agent_event
    from backend.reliability.idempotency import claim_delivery

    delivery = str(uuid.uuid4())
    with get_connection() as conn:
        review_id = claim_delivery(delivery, "test-gov", 6001, conn=conn)
        emit_agent_event(
            str(review_id), "security", EventType.LLM_CALL,
            model="gpt-4o-mini", tokens_in=10, tokens_out=5,
            cost_usd=0.0001, latency_ms=50,
            payload={"prompt_version": "abc12345", "leak": "sk-abcdefghijklmnopqrstuvwxyz123456"},
            conn=conn,
        )
        emit_agent_event(
            str(review_id), "aggregator", EventType.DECISION,
            outcome="approved", confidence=0.9, conn=conn,
        )

        # Filters must combine with review scope — the spine is append-only
        # and holds historical rows for every agent/type.
        by_agent = query_audit(review_id=review_id, agent="security", conn=conn)
        assert len(by_agent) == 1
        assert by_agent[0]["agent"] == "security"

        by_type = query_audit(review_id=review_id, event_type="decision", conn=conn)
        assert len(by_type) == 1
        assert by_type[0]["event_type"] == "decision"

        # payloads must be secret-masked at the read boundary
        sec = query_audit(review_id=review_id, conn=conn)
        leak = next(e for e in sec if e["agent"] == "security")
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in str(leak["payload"])
        assert "[REDACTED]" in str(leak["payload"])

        with conn.cursor() as cur:
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live audit tests"
)
def test_audit_summary_rollup():
    from backend.database.postgres import get_connection
    from backend.models.enums import EventType
    from backend.observability.audit import audit_summary
    from backend.observability.events import emit_agent_event
    from backend.reliability.idempotency import claim_delivery

    delivery = str(uuid.uuid4())
    with get_connection() as conn:
        review_id = claim_delivery(delivery, "test-gov", 6002, conn=conn)
        emit_agent_event(str(review_id), "security", EventType.LLM_CALL,
                         model="gpt-4o-mini", tokens_in=10, tokens_out=5,
                         cost_usd=0.0002, latency_ms=50, conn=conn)
        emit_agent_event(str(review_id), "quality", EventType.LLM_CALL,
                         model="gpt-4o-mini", tokens_in=20, tokens_out=10,
                         cost_usd=0.0003, latency_ms=60, conn=conn)
        emit_agent_event(str(review_id), "aggregator", EventType.DECISION,
                         outcome="approved", confidence=0.9, conn=conn)

        s = audit_summary(review_id, conn=conn)
        assert s["events"] == 3
        assert s["llm_calls"] == 2
        assert s["by_agent"]["security"] == 1
        assert s["by_agent"]["aggregator"] == 1
        assert s["total_cost_usd"] == pytest.approx(0.0005)
        assert s["by_event_type"]["llm.call"] == 2

        with conn.cursor() as cur:
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))


@pytest.mark.skipif(
    not TIGER_URL, reason="TIGER_DATABASE_URL not set — skipping live audit tests"
)
def test_explain_finding_reconstructs_evidence():
    from backend.database.postgres import get_connection
    from backend.database.repository import insert_finding
    from backend.models.enums import EventType
    from backend.observability.audit import explain_finding
    from backend.observability.events import emit_agent_event
    from backend.reliability.idempotency import claim_delivery

    delivery = str(uuid.uuid4())
    with get_connection() as conn:
        review_id = claim_delivery(delivery, "test-gov", 6003, conn=conn)
        emit_agent_event(str(review_id), "security", EventType.LLM_CALL,
                         model="gpt-4o-mini", tokens_in=10, tokens_out=5,
                         cost_usd=0.0001, latency_ms=50,
                         payload={"prompt_version": "abc12345"}, conn=conn)
        emit_agent_event(str(review_id), "aggregator", EventType.DECISION,
                         outcome="escalated", confidence=0.95, conn=conn)
        fid = insert_finding(review_id, {
            "agent_type": "security", "severity": "CRITICAL",
            "category": "sql-injection", "summary": "SQLi",
            "file_path": "src/db.py", "line_start": 10, "line_end": 12,
            "suggestion": "parameterize", "confidence": 0.95, "rationale": "concat",
        }, conn=conn)

        ex = explain_finding(review_id, fid, conn=conn)
        assert ex["finding"]["category"] == "sql-injection"
        assert ex["finding"]["severity"] == "CRITICAL"
        assert ex["review"]["repo"] == "test-gov"
        assert "abc12345" in ex["prompt_versions"], (
            "explainability must show which prompt version produced the finding"
        )
        assert any(e["event_type"] == "decision" for e in ex["decision_events"])
        assert len(ex["trace"]) >= 2

        # unknown finding → KeyError
        with pytest.raises(KeyError):
            explain_finding(review_id, uuid.uuid4(), conn=conn)

        with conn.cursor() as cur:
            cur.execute("DELETE FROM finding_records WHERE review_id = %s", (str(review_id),))
            cur.execute("DELETE FROM pr_review_records WHERE id = %s", (str(review_id),))
