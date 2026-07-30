# Wiki Index — ai-pr-review-agent

The project knowledge base. Same schema as the agentic-swe-kit wiki: concept pages in `concepts/`,
each with frontmatter and ≥2 `[[wikilinks]]`. The L3 RESEARCH loop writes here; G0 reads here first.

> **Read this file before any milestone (G0 step 1).** Pick candidate pages by name-matching the
> milestone's nouns, then drill in. The wiki is what prevents rebuilding work that already exists.

**Design source of truth:** https://www.antern.co/blogs/production-grade-ai-pr-review-agent
(L0–L9 first-principles derivation, ADR-001 LangGraph vs Temporal, ADR-002 modular monolith,
ADR-003 Tiger Cloud one-store data layer, ADR-004 cost control.) A local text extract of the full
article is at `/tmp/antern.txt` — re-fetch if lost.

## Entities (the things this system has)
<!-- fill as they are built -->
- Finding — the object on the arrows: `agent_type, severity, category, summary, file_path, line_start/line_end, suggestion, confidence, rationale`
- Review — one PR review: `pr_review_records` + child `finding_records`, carries `overall_confidence`
- AgentEvent — one append-only row in the `agent_events` hypertable (span/llm.call/tool.call/decision)
- CodeChunk — `code_chunks` row: repo, path, symbol, content, `VECTOR(256)` embedding, `content_tsv`

## Concepts (how it works)
<!-- fill as they are learned -->
- The four specialists — security, quality, tests, docs — parallel fan-out, not one prompt (L1/L3)
- Confidence-weighted HITL gate — post / queue / escalate (L7)
- Hybrid retrieval — DiskANN ANN + FTS GIN, merged by reciprocal rank fusion (3.5)
- Three data shapes, one store — memory / truth / time on Tiger Cloud (Part II)

## Sources (research distilled by L3)
- antern.co "Designing an AI Pull-Request Review Agent" — the full derivation | filed 2026-07-31

## Seeded from agentic-swe-kit
Relevant global concept pages for this project's phases (pointers only — read on demand).
Root: `$AGENTIC_SWE_WIKI_ROOT` = `~/.agentic-swe-kit/wiki`

### Architecture & module boundaries (ADR-002, INV-1) — read before M1/M2
- clean-architecture/concepts/Boundary-Lines.md — where to draw the module seams
- clean-architecture/concepts/Clean-Architecture-Pattern.md — the inward dependency rule itself
- clean-architecture/concepts/Component-Coupling-Principles.md — acyclic dependencies; validates INV-1
- clean-architecture/concepts/Decoupling-Modes.md — monolith→service extraction path (3.1 scaling answer)
- clean-architecture/concepts/Business-Rules.md — what belongs in `core/` vs outer modules
- pragmatic-programmer/concepts/Orthogonality.md — why deleting an outer module must not break inner
- pragmatic-programmer/concepts/Design-by-Contract.md — the Finding contract as a real contract

### Agent orchestration & the fan-out (L1/L3, ADR-001) — read before M3/M4
- llmops-ai-agents/concepts/Agentic-Design-Patterns.md — the pattern catalog
- llmops-ai-agents/concepts/Orchestrator-Worker-Architecture.md — orchestrator + 4 workers, exactly this shape
- llmops-ai-agents/concepts/Parallel-and-Fan-Out-Agents.md — the LangGraph Send fan-out + join
- llmops-ai-agents/concepts/Multi-Agent-Orchestration.md — aggregation and conflict between agents
- llmops-ai-agents/concepts/Agent-Fundamentals.md — baseline vocabulary

### Grounding / retrieval (L4/L5) — read before M5 (memory)
- llmops-ai-agents/concepts/RAG-Architecture.md — retrieve exactly what the reasoner lacks
- designing-data-intensive-applications/concepts/Data-Models-and-Query-Languages.md — vector + relational in one store
- designing-data-intensive-applications/concepts/Storage-Engines.md — why DiskANN keeps the index on SSD
- designing-data-intensive-applications/concepts/OLTP-vs-OLAP-and-Columnar-Storage.md — hypertable + rollup reasoning
- designing-data-intensive-applications/concepts/Polyglot-Persistence.md — interrogates ADR-003 "one store, not three"
- designing-data-intensive-applications/concepts/Encoding-and-Schema-Evolution.md — migration discipline

### Proof: events, cost, evaluation (L6, ADR-004) — read before M6/M7
- llmops-ai-agents/concepts/Observability-and-Cost-Control.md — the events spine + BudgetGuard
- llmops-ai-agents/concepts/Evaluation-Frameworks.md — golden dataset, LLM-as-judge, regression gate
- llmops-ai-agents/concepts/LLMOps-Essentials.md — prompt versioning, model routing
- llmops-ai-agents/concepts/Production-Hardening.md — the general hardening checklist

### Reliability (L8, INV-4) — read before M8
- release-it/concepts/Circuit-Breaker.md — the breaker around LLM + GitHub calls
- release-it/concepts/Integration-Points.md — every outbound call is a failure point
- release-it/concepts/Fail-Fast.md — degrade to slower-but-correct
- release-it/concepts/Bulkheads.md — isolate one stalled specialist from the join
- release-it/concepts/Cascade-and-Chain-of-Failure.md — why a hung agent must not hang the aggregator
- release-it/concepts/Recovery-Patterns.md — retry/resume from LangGraph checkpoint
- release-it/concepts/Design-for-Production.md — the production posture

### Security & the trust boundary (INV-3) — read before M2 and M9
- security-engineering/concepts/Protocol-Security.md — HMAC-SHA256 webhook verification
- security-engineering/concepts/Access-Control.md — RBAC on API routes; least-privilege GitHub token
- security-engineering/concepts/Secure-Development-and-Assurance.md — threat model as an artifact
- security-engineering/concepts/Privacy-and-Inference-Control.md — secret masking, no repo leakage
- security-engineering/concepts/Distributed-Architecture-Security.md — sandbox + capability scoping

### Distributed concerns (only if 3.1 scaling trigger fires)
- distributed-systems/concepts/Fault-Tolerance.md
- distributed-systems/concepts/Communication-Models.md — queue decoupling at ingress
- distributed-systems/concepts/Scalability.md — the "10k PRs/min" interrogating question
