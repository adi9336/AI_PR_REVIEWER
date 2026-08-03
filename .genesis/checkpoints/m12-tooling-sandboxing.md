# M12 — Tooling & Sandboxing: tool registry + capability scope + Docker sandbox + model router

## G0 · 2026-08-03
- wiki pages read: [[security-engineering/concepts/Distributed-Architecture-Security]] (TOCTTOU, least privilege, purpose-scoped identifiers), [[llmops-ai-agents/concepts/Production-Hardening]] (general hardening checklist — sandbox/capability scoping)
- implementation-notes searched for: "sandbox", "tool_registry", "capability_scope", "model_router" — found: 4 stubs listed in live table ("Evaluation… wip" row; tools/** not yet live)
- codebase grep: `tool_registry|sandbox|capability_scope|model_router` — 4 typed stubs (6 lines each); `tests/` has no tooling tests; agents call no tools today (LLM-only) → unbuilt
- decisions that bind us: 0002 (model routing — router must default to OpenAI-served models; gpt-4o-mini), INV-3 (untrusted diff never reaches execution untraced), INV-4 (every outbound call has explicit timeout), INV-6 (every tool.call emits an append-only event)
- environment: Docker 29.2.0 available (no images pulled yet; python:3.11-slim pulling for live tests)
- verdict: **UNBUILT** → proceed to L1 BUILD

## Micro-plan (files to touch / tests to add / demo cmd)
- `backend/tools/capability_scope.py` — per-specialist tool allowlists (security/quality/docs read-only; tests may run the sandboxed runner; none write) + CapabilityError
- `backend/tools/tool_registry.py` — ToolSpec catalog, register (reject duplicates), call() with: unknown-tool check → capability check → timeout backstop (ThreadPoolExecutor, INV-4) → tool.call event (INV-6)
- `backend/tools/sandbox.py` — policy layer (scrub_env pure function: keep SAFE_ENV_VARS, drop secret-looking keys) + Docker layer (--rm --network none --memory --cpus, scrubbed env inline, subprocess timeout + container kill → SandboxTimeoutError)
- `backend/tools/model_router.py` — resolve_model(step): MODEL_<STEP> env override → step default → global default (gpt-4o-mini)
- `tests/test_tooling.py` — scope matrix, registry accept/reject + event emission (monkeypatched emit, no DB), registry timeout, scrub_env unit, docker-gated live tests (secret masking, network block, timeout kill), model routing
- Demo command: `pytest tests/test_tooling.py -q` (docker-gated tests skipif docker unavailable)
- G4: mypy backend/tools, check_deps, full pytest

## Freeze boundary
`backend/tools/**`, `tests/test_tooling.py`

## iter log (append-only)

## iter 1 · 2026-08-03
- gate G1: pass · skills considered [agentic-swe-master, coding-orchestrator, tdd, security-engineering, production-readiness]; chose canon + tdd + security-engineering + production-readiness
- gate G2: pass · +4 modules backend/tools/ (capability_scope, tool_registry, sandbox, model_router), +18 tests tests/test_tooling.py
- gate G3: pass · single BUILD pass, no research needed (wiki had Distributed-Architecture-Security + Production-Hardening)
- gate G4: pass · demo `pytest tests/test_tooling.py -q` = 18 passed, 4 skipped; mypy backend/tools = 0 errors; check_deps 89 files clean
- gate G5: pending — L4 VERIFY with fresh context next
- decision: five gates per tool call (registered → capability-scoped → timed → traced → sandboxed); policy layer (scrub_env) always on and unit-tested; Docker layer = hard isolation (--network none, --memory, --cpus, scrubbed env, unique --name + docker rm -f on timeout); registry timeout via ThreadPoolExecutor with shutdown(wait=False) — the context-manager form blocks on exit and does NOT cut the call (real bug found by the timeout test)
- environment note (UPDATED): Docker daemon came up mid-session → all 4 live isolation tests ran and caught a REAL bug: the sandbox forwarded the host's Windows PATH into the Linux container (`--env PATH=C:\...` from os.environ), breaking command resolution (python → exit 127) and false-passing the network test. Fix: only caller-provided env crosses into the container — never os.environ. After fix: 22/22 passed, 0 skipped (secret masked in-container, network blocked, 30s payload killed at 2s timeout, known command runs). Policy layer still fully unit-tested.
- next: L4 VERIFY → on APPROVE mark M12 done + update spine + commit

## iter 1 · L4 VERIFY #1 · 2026-08-03 — REJECT → fixed → re-submitted
- verdict: REJECT (fresh-context subagent, verification-audit skill). Two blocking findings:
  - F1 (integrity): the audited sandbox.py mutated mid-audit — the PATH-clobbering defect I fixed (see environment note) landed while the verifier was running. Legit fix, bad timing; verifier certified the CURRENT state green but cannot certify the original submission. Resolved by re-submitting the final state.
  - F2 (HIGH, real defect): `ToolSpec.sandboxed` was never read — gate 5 (sandboxed execution) was unimplemented dead code; `run_tests` executed on the host. FIXED: `call()` now routes sandboxed tools through `Sandbox().run()` (fn must return a list[str] command), fails closed when Docker is down (SandboxError → ToolError), rejects non-command returns, maps non-zero exit to an error event.
  - F4 (LOW): denials (unknown tool / out of scope) emitted no event. FIXED: best-effort status=error tool.call event with denied=True when a review context exists.
  - F3 (LOW, accepted): timed-out tool thread keeps running until process exit — documented; hard kill happens at the sandbox layer. F5 (INFO): docker_available() transiently True right after engine start — environment race, accepted.
- after fix: `pytest tests/test_tooling.py -q` = 26 passed (incl. live in-container sandboxed-tool test, fail-closed, denial events); mypy 6 files clean; check_deps 89 clean
- next: L4 VERIFY #2 on the final state → on APPROVE mark M12 done + commit

## iter 1 · L4 VERIFY #2 · 2026-08-03 — APPROVE
- verdict: **APPROVE** (fresh-context subagent, verification-audit skill; standalone probes + canonical suite)
  - F2 confirmed fixed: sandboxed tool executes INSIDE a container (probe stdout `dockerenv: True` — host is Windows); docker-absent → ToolError "refusing to run untrusted code" + one status=error event; non-command return rejected
  - F4 confirmed fixed: out-of-scope + unknown-tool denials emit 2 tool.call events, denied=True, reasons [out of scope, unknown tool]
  - INV-4 both layers: sandbox kills sleep(30) at 2s (2.5s elapsed, zero container leaks); registry cuts 0.2s timeout, exactly one error event
  - canonical: pytest tests/test_tooling.py -q = 26 passed, 0 skipped; mypy backend/tools 0 errors; check_deps 89 clean
  - integrity: git status byte-identical pre/post audit; no repo files modified; no containers left
  - defects: none
- milestone status: **DONE** — 129 tests total, mypy 89 files clean, deps clean

