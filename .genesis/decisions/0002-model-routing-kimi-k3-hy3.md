# ADR 0002 — Model routing: kimi-k3 for reasoning/orchestration, hy3 for code generation + verify

- **Date:** 2026-07-31
- **Status:** accepted
- **Phase / milestone:** genesis → all loops (router configuration)

## Context
Two models are available on the project's provider (`opencode-go`, base_url
https://opencode.ai/zen/go/v1, one API key for both): `kimi-k3` (Moonshot Kimi K3 — a
reasoning-optimised model) and `hy3` (Tencent Hunyuan 3 — a code-generation model). The user wants
reasoning/thinking on kimi-k3 and code generation on hy3. The genesis-kit already routes work via a
cheap/driver model (runs the loop) and a flagship/checker model (hard hops + L4 VERIFY).

## Decision
Route reasoning, planning, orchestration, gate-evaluation and the driver loop to **kimi-k3**, and
route code generation + L4 VERIFY to **hy3**. Hermes `model.default` is set to `kimi-k3` (the loop
driver). hy3 is the flagship/checker and the code-generation target.

## Consequences
- Positive: each model does what it is built for; hy3 is already the verified default in the env;
  both ride the same provider key, so no new credentials are needed.
- Negative / cost: a runtime router must pick the right model per step (reasoning vs codegen);
  if the router is absent, the default (kimi-k3) handles both — acceptable degradation, not a failure.
- **Invariant added to context-graph.json:** none — this is a routing config, not a dependency rule.

## Alternatives rejected
- kimi-k3 for everything — wastes hy3's code-generation strength on the most code-heavy steps.
- hy3 for everything — wastes kimi-k3's reasoning on the orchestration-heavy steps.

## Where this is recorded
- Hermes `config.yaml`: `model.default = kimi-k3` (provider opencode-go).
- Loop router: kimi-k3 = cheap/driver + thinking; hy3 = flagship/checker + code generation + L4 VERIFY.
