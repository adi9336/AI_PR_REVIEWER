# Threat Model — AI PR Review Agent

Written at M18 from what is actually shipped (no aspirational controls).
Every mitigation maps to a real module and its test suite.

## Assets

| Asset | Where | Worst case |
|---|---|---|
| LLM API keys / secrets | `backend/.env` (gitignored), Docker env | Unauthorized spend, data exfiltration |
| The audit spine (`agent_events`) | Tiger Cloud (append-only by trigger + REVOKE) | Tampering erases accountability |
| Review findings + HITL queue | `finding_records`, `hitl_reviews` | Forged findings, poisoned queue |
| Webhook secret (`GITHUB_WEBHOOK_SECRET`) | `backend/.env` | Forged reviews injected via webhook |
| Golden dataset / prompts | `fixtures/golden/`, `backend/prompts/templates/` | Poisoned eval → degraded reviews look "fine" |
| LLM budget | `backend/economics/budget.py` | Runaway spend |

## Trust boundaries

1. **Internet → webhook receiver** — HMAC-verified (sha256), idempotent per delivery UUID.
2. **Untrusted PR content → agents** — the diff is DATA (INV-3): injection-guarded before it
   reaches any prompt, and any execution of PR code happens inside the Docker sandbox.
3. **Reviewer/API client → HITL/audit API** — governance API key (fail-closed, constant-time).
4. **Host → sandboxed container** — no network, scrubbed env, resource limits, hard timeout,
   ephemeral (`docker rm -f`).

## Threats → mitigations (STRIDE-ish)

| # | Threat | Mitigation (shipped) | Verified by |
|---|---|---|---|
| T1 | Prompt injection via malicious diff (INV-3) | `security/injection_guard.py` sanitizes before any prompt | test_security_agent.py + e2e SQLi run |
| T2 | PR code executed on the host (RCE) | `tools/sandbox.py` Docker isolation: `--network none`, scrubbed env (host env NEVER crosses), `--rm`, `-m/--cpus` limits, per-call timeout, fail-closed without Docker | test_tooling.py (live container proofs) |
| T3 | Tool-call abuse / privilege escalation | `tools/tool_registry.py` closed catalog + `tools/capability_scope.py` per-specialist least privilege (fail closed), every call traced (INV-6) | test_tooling.py gates |
| T4 | Audit tampering | `agent_events` append-only: BEFORE UPDATE/DELETE/TRUNCATE triggers + `REVOKE` from `app_rw` (INV-6); reads are SELECT-only | test_governance.py + live trigger checks |
| T5 | Secret leakage via API/logs | `security/masking.py` redacts at the read boundary (recursive, incl. lists of dicts); `observability/logging.py` masks log messages | test_governance.py masking suite |
| T6 | Unauthenticated admin access | `auth/dependencies.py` fail-closed governance key: 503 unconfigured, 401 missing/wrong/non-ASCII (constant-time) | test_governance.py RBAC suite |
| T7 | Forged webhooks | `webhook_receiver/validator.py` HMAC-SHA256; malformed/missing signature → 401 JSON before any work | test_webhook paths |
| T8 | Duplicate/retried deliveries double-processing | `reliability/idempotency.py` claim-once + delivery UUID | e2e idempotency test |
| T9 | LLM runaway cost | `economics/budget.py` BudgetGuard (daily cap, hard block); `economics/routing_advisor.py` cost-pressure suggestions | test_budget + test_m18 advisor |
| T10 | Degraded review quality passing as clean (silent regression) | M11 regression gate + M13 canary (real agents vs golden set, live LLM) + M16 drift detection (findings collapse = quality decay) | test_evaluation, test_canary, test_drift |
| T11 | Lost reviews on queue failure | M17 fail-soft: Postgres claim is durable; Redis down → 202 + anchored error event, review stays runnable | test_job_queue (live fail-soft repro) |
| T12 | Slow/poisoned upstream (GitHub API, LLM) | `reliability/circuit_breaker.py` + `retry.py` + explicit timeouts everywhere (INV-4, incl. `WorkerSettings.job_timeout=300`) | test_retry/circuit tests |
| T13 | Secrets in logs | `logging.py` masks every message; audit payloads masked at read | test_m18 logging, test_governance |
| T14 | Disputed/incorrect findings uncorrectable | `hitl/dispute.py` + `feedback.py` append-only events (finding_id + reason) — the correction loop | test_m18 |

## Residual risks (accepted, documented)

- The sandbox is ONE boundary (bulkhead per-specialist containers = optional escalation).
- Logging is server-side only; the dashboard never streams raw secrets (masked at the API).
- The governance key protects admin routes; the review/hitl read APIs are public by design
  (the dashboard consumes them server-side — no CORS exposure).
- CI canary needs a live LLM key (skips when absent — the no-secret gate is the sanity check).
