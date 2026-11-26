# Security review — single-user local-first broker

**Scope.** Single-user, single-process FastAPI service bound to loopback,
with a React UI served from the same machine. All generation decisions flow
through a privacy engine, a policy gate and an orchestrator-only dispatcher.
No multi-tenant trust boundary is assumed; a profile id identifies the local
user, not an authenticated principal.

## Threat model

| Asset | Threat | Mitigation |
|---|---|---|
| Local prompts & PII | Leak via telemetry, cache or logs | Default-off caches; typed allowlisted telemetry (`backend/telemetry/types.py`); redaction via sanitizer + inspector |
| Approval state | Replay, tampering, policy drift | Atomic once-only consumption bound to digest/mode/policy (`backend/broker/policy_gate.py`) |
| Local write endpoints | CSRF from a browser page or malicious origin | `LocalApiBoundary` middleware rejects hostile Host/Origin; `X-Local-Token` required for mutating routes |
| Model lifecycle | Silent install or deletion of external weights | `SwapCoordinator` only operates on managed catalog ids and requires an explicit approval token (`backend/model_management/lifecycle.py`) |
| Cloud fallback | Unauthorized egress | Policy gate hard-blocks cloud paths without explicit per-request approval; cloud is default-off |
| Learned router | Unsafe weight load | `TrainingArtifact` loaded from native text format only; SHA-256 checksum and feature-schema hash gate reuse |

## Boundary checks

Automated verification runs via `make security-audit`:

- Scans `backend/`, `scripts/`, `frontend/src/` and `docs/` for synthetic PII
  markers, known fake-secret patterns and request-local placeholder tokens
  escaping into static files.
- Walks any `var/*.sqlite3` present and inspects stored rows for the same
  markers — observations, telemetry and cache rows must never contain them.
- Confirms `pyproject.toml` has no unpinned dependency pins.
- Confirms the API boundary file wires host/origin guards and the write
  token requirement.

Related automated coverage:

- `tests/integration/test_chat_api.py` exercises Host/Origin guards, write
  token enforcement, approval replay, timing-bound approval expiry and the
  explicit decline path.
- `tests/privacy/test_policy_gate.py` and `tests/privacy/test_engine.py`
  pin the policy-gate contract and the sanitizer invariants.
- `tests/test_guarded_dispatch.py` and `tests/test_dispatch_boundary.py`
  enforce the orchestrator-only generation rule via a static import audit.

## Residual risks (not covered by this release)

- **Supply chain.** We pin direct and transitive versions in `uv.lock`, but
  do not verify publisher signatures or run a dependency vulnerability
  scanner. Owner-driven `uv lock --check --offline` detects lockfile drift
  but not post-publication tampering of a specific version.
- **Local filesystem compromise.** All controls assume the owner's shell and
  file system are trusted. A local attacker can read SQLite rows, model
  weights and configuration directly; disk encryption and user-level
  permissions are the owner's responsibility.
- **Runtime process isolation.** The broker runs in-process without a
  sandbox. Benchmark code execution reuses an isolated subprocess via
  `backend/learning/scorers/code.py`, but non-benchmark operations do not
  sandbox trusted internal code.
- **PII detector completeness.** The sanitizer uses deterministic patterns
  plus optional NLP recognizers. Novel identifier formats or free-form
  content that matches no pattern can pass through unmasked. The policy
  gate's default `local_only` mode limits blast radius: such content is
  never sent to a cloud destination without explicit per-request approval.
- **Clock-based approvals.** Approval expiry uses the local clock; a
  deliberately mis-set clock can either accept stale approvals or reject
  valid ones. The 120-second default window limits exposure.
- **Learned router drift.** The LightGBM artifact is validated at load, but
  predictions themselves are not independently audited at inference. The
  router always falls back to rules on unseen candidates, NaN output or
  schema drift.

## Release blockers

The following findings would block a release even if `make lint test
test-privacy smoke` passes:

- Any PII, placeholder or fake-secret marker in a SQLite row (telemetry,
  cache, observations).
- Any failing assertion in `tests/privacy/`.
- Any auth-boundary bypass surfaced by `tests/integration/test_chat_api.py`
  (host/origin guard, write token).
- Any non-rules-fallback behavior from `LightGBMRouter` when its artifact is
  missing or invalid.
