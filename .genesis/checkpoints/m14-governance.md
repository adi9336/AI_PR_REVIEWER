# M14 — Governance: queryable audit + per-finding explainability + RBAC

## G0 · 2026-08-03
- wiki pages read: [[llmops-ai-agents/concepts/Observability-and-Cost-Control]] (audit spine), [[security-engineering/concepts/Access-Control]] (RBAC on API routes, least privilege)
- implementation-notes searched for: "audit", "RBAC", "masking", "explain" — found: audit.py/alerting.py/logging.py/auth/dependencies.py/masking.py/api/** all stubs; main.py serves /reviews, /hitl/queue, /health, /webhook/github, /reviews/{id}/run (no auth on any)
- codebase grep: `query_audit|require_api_key|mask_secrets|explain_finding` — 0 hits → unbuilt; reuse: get_events_for_review, get_findings_for_review, get_review_record, prompt_version
- decisions that bind us: INV-6 (agent_events append-only — audit reads are inherently immutable), INV-3 (masking before serving untrusted content), 0002 (env-driven config)
- environment: GOVERNANCE_API_KEY must come from env (fail-closed if absent) — add to backend/.env locally (gitignored)
- verdict: **UNBUILT** → proceed to L1 BUILD

## Micro-plan (files to touch / tests to add / demo cmd)
- `backend/security/masking.py` — mask_secrets(text) + mask_payload(dict): redact sk-*, ghp_/gho_*, postgres DSNs, secret=value pairs; pure, deterministic
- `backend/auth/dependencies.py` — require_governance_key FastAPI dependency: no env key → 503, missing/wrong header → 401 (hmac.compare_digest), valid → pass; fail closed
- `backend/observability/audit.py` — query_audit (filter by review_id/agent/event_type/since, limit, time-ordered, payloads masked), audit_summary (counts + total cost), explain_finding (finding + review + events trace + prompt_versions + decision)
- `backend/api/audit.py` — APIRouter: GET /audit/events, GET /audit/reviews/{id}/explain, GET /audit/reviews/{id}/summary, all Depends(require_governance_key)
- `backend/main.py` — mount the audit router
- `tests/test_governance.py` — masking unit tests; RBAC unit tests (env monkeypatch); DB-gated: query_audit filters + masks, audit_summary, explain_finding; TestClient: 401 without key, 200 with key
- Demo command: `pytest tests/test_governance.py -q` (DB-gated skipif TIGER unset)

## Freeze boundary
`backend/observability/audit.py`, `backend/auth/dependencies.py`, `backend/security/masking.py`, `backend/api/audit.py`, `backend/main.py`, `tests/test_governance.py`

## iter log (append-only)

## iter 1 · 2026-08-03
- gate G1: pass · skills considered [agentic-swe-master, coding-orchestrator, tdd, security-engineering, production-readiness]; chose canon + tdd + security-engineering + production-readiness
- gate G2: pass · +4 modules (masking, auth/dependencies, observability/audit, api/audit), +1 router mounted in main.py, +15 tests tests/test_governance.py
- gate G3: pass · single BUILD pass, no research needed (wiki had Access-Control + Observability-and-Cost-Control)
- gate G4: pass · demo `pytest tests/test_governance.py -q` = 15 passed (3 live-DB); mypy 91 files clean; check_deps 91 clean
- gate G5: pending — L4 VERIFY with fresh context next
- decision: audit reads are inherently tamper-proof (INV-6 append-only); masking happens at the READ boundary (spine stores verbatim, servers redact); RBAC = fail-closed API key (503 unconfigured / 401 mismatch, constant-time compare); explain_finding reconstructs finding + trace + prompt_version + decision — the article's "explainability per finding"; DSN regex keeps the scheme when redacting
- fixes this iter: DSN regex consumed the postgres:// scheme (kept now); query_audit filter tests scoped by review_id (append-only spine holds historical rows for every agent/type)
- next: L4 VERIFY → on APPROVE mark M14 done + update spine + commit + push

## iter 1 · L4 VERIFY #1 · 2026-08-04 — REJECT → fixed → re-submitted
- verdict: REJECT (fresh-context subagent, verification-audit skill). Three defects:
  - F1 (MEDIUM — masking.py:42-45): mask_payload recursed into dicts but list elements that
    were DICTS passed through unmasked — a payload shaped {"findings": [{"evidence": "sk-…"}]}
    leaked at the read boundary (both query_audit and explain_finding serve through mask_payload)
    → criterion (1) unmet, INV-3 violatable. FIXED: list branch now recurses dict elements.
  - F2 (LOW — auth/dependencies.py:24): hmac.compare_digest(str, str) raised TypeError on
    non-ASCII header values (Starlette decodes raw bytes latin-1) → 500 instead of 401
    (unauthenticated attacker can force 500s). FIXED: ascii-encode both sides, UnicodeEncodeError
    → match=False → 401.
  - F3 (LOW — api/audit.py): not-a-uuid path params → unhandled ValueError → 500 (only KeyError
    caught). FIXED: ValueError → 400 (matches main.py's other routes).
  - Test blind spot the verifier broke: list-of-dicts never tested → added
    test_mask_payload_recurses_into_list_of_dicts; non-ASCII header path (raw bytes via httpx —
    str headers are refused at encode time) → test_rbac_non_ascii_key_returns_401_not_500 +
    test_audit_api_non_ascii_key_is_401_not_500; invalid UUID → test_audit_api_invalid_uuid_is_400.
- after fix: 19/19 governance tests (15 + 4 regressions); mypy 91 clean; deps 91 clean
- next: L4 VERIFY #2 on the final state → on APPROVE mark M14 done + commit + push

## iter 1 · L4 VERIFY #2 · 2026-08-04 — APPROVE
- verdict: **APPROVE** (fresh-context subagent, verification-audit skill; atomic ad-hoc probe + live commands)
  - FIX1 masking recursion (list-of-dicts): OK · FIX2 non-ASCII key 401 (direct + raw-ASGI): OK ·
    FIX3 invalid UUID 400 / KeyError 404: OK
  - read-only audit (SELECT-only, parameterized, LIMIT [1,1000]): OK · INV-6 triggers armed live: 2/2
  - suite: governance 19 passed · full 162 passed · mypy 91 files clean · deps 91 clean
  - defects: none · no repo files created/modified · zero residue
- milestone status: **DONE** — 162 tests total, mypy 91 clean, deps 91 clean; pushed to GitHub


