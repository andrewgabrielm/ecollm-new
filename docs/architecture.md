# Architecture

Single-user, local-first broker. One FastAPI process bound to loopback, a React
chat UI served on a sibling port, and a privacy-first dispatch pipeline that
never contacts an outside service without explicit per-request owner approval.

## Request lifecycle

```
HTTP in  →  validate  →  sanitize (PII mask)  →  trusted sanitized payload
         →  feature extraction + immutable roster snapshot
         →  routing (rules or LightGBM, with rules fallback)
         →  model-scoped exact/semantic cache (opt-in)
         →  policy gate  →  eligible local cascade  →  optional approved cloud
         →  output inspection + local restoration
         →  cache eligible answers (opt-in)  →  typed telemetry (opt-in)
```

Every arrow is enforced by code in `backend/broker/`. The policy gate
(`policy_gate.py`) and the `GuardedDispatcher` in `orchestrator.py` are the
only places that may invoke a provider's `generate` or `warm_up` methods; the
`tests/test_dispatch_boundary.py` static audit keeps that invariant.

## Packages

| Path | Role |
|---|---|
| `backend/api/` | FastAPI app factory, Pydantic request/response schemas, loopback security middleware |
| `backend/broker/` | Sanitizer → gate → dispatcher → inspector pipeline, routing (rules + LightGBM), approval state machine |
| `backend/privacy/` | Analyzer, sanitizer, placeholder maps, output inspector, request-local state |
| `backend/model_management/` | Catalog manifests, registry, hardware context, lifecycle state machine (Step 17) |
| `backend/providers/` | Async HTTPX adapters: mock, Ollama local, cloud (reserved); the only files allowed to call generation/health/warmup/pull/unload directly |
| `backend/learning/` | Benchmark datasets (Step 11), per-candidate observations (Step 12), LightGBM training/validation/promotion (Step 13), vectorizer and artifact loader |
| `backend/telemetry/` | Opt-in typed telemetry records, SQLite retention, aggregator with suppression (Step 15) |
| `backend/cache/` | Model/version-specific exact cache (opt-in) |
| `backend/profiles/` | Preference and consent persistence |
| `backend/config/` | Validated YAML settings/policy/catalog loaders |
| `backend/storage/` | SQLite connection, serialized writer, checksum-verified migrations |
| `backend/versions.py` | Canonical content hashing and domain-separated version namespaces |
| `scripts/` | CLIs for demo, fixtures, bench collection, router training/promotion, security audit |
| `frontend/` | React + Vite UI, typed OpenAPI client, Playwright mock E2E |

## Routing

`RuleRouter` scores candidates with
`quality*weight − latency*weight − load*weight` using catalog priors and
hardware context; it is the always-available fallback.

`LightGBMRouter` wraps a promoted `TrainingArtifact`. It loads the booster at
construction, binds the feature-schema hash, and ranks candidates by predicted
quality. On any of the following it falls back to the rules router for the
single request:

- Missing or corrupted artifact.
- Mismatched feature-schema hash.
- NaN prediction.
- Unseen task category or candidate id.

Both routers return a `RoutingPlan` carrying a `router_type` (`rules` or
`lightgbm`) and a `router_version` string so the API response and telemetry
record exactly which router answered each turn.

See `docs/routing.md` for the full routing specification (task classification,
candidate eligibility, feature extraction, `RuleRouter` scoring/tie-breaking,
`LightGBMRouter` training/fallbacks, local cascade, and cloud fallback).

## Learning pipeline

1. `make fixtures` writes ~300 synthetic benchmark tasks across seven
   categories with family-aware train/validation splits.
2. `make bench-dev SPLIT=... LIMIT=...` collects one `Observation` per
   `(task × candidate)` pair through the gated `EvaluationDriver`. The driver
   bypasses cache/cloud/cascade but still routes every call through the policy
   gate. Observations include the full `FeatureRow` used at dispatch so
   training has the exact numeric inputs.
3. `make train-router TRAIN_RUN=<id> VAL_RUN=<id>` fits a LightGBM booster on
   the training run, validates it against the validation run (group-aware),
   and writes a versioned artifact under `var/routers/<artifact_id>/`. It
   never activates automatically.
4. `make promote-router VERSION=<id>` atomically points `active.json` at a
   passing artifact. A backup pointer survives rollback.
5. Starting the backend with `LightGBMRouter.from_root(...)` picks up the
   active artifact on boot; promotion without a backend restart is safe only
   when the artifact is bound at request time (future work).

## Lifecycle

`SwapCoordinator` in `backend/model_management/lifecycle.py` is the only
component that may call `RuntimeControl.pull/unload/remove`. Each swap runs
through `proposal → approved → staging → verified → warm → active →
draining → retired` (or `failed`/`cancelled`). Every transition is driven by
an explicit owner-approved method call; there is no automatic promotion. The
coordinator refuses to operate on model ids that are not in the managed
catalog.

## Privacy and storage invariants

- `RequestContext` and placeholder maps are process-local, redacted from
  repr, serialization and pickle.
- Sanitizer and inspector failures abort before any provider call.
- Approval state is consumed atomically and never replayed.
- SQLite migrations are packaged, checksum-verified, and applied inside a
  single transaction with WAL and foreign keys enabled.
- Cache lookup runs after routing so entries bind to the actual producing
  model/version.
- Telemetry rows are typed and allowlisted; prompt and answer text never land
  there. Consent defaults off; opting out triggers a full table delete.

## Boundaries

- Orchestrator is the only generation dispatcher.
- Routing, portfolio optimization, swap lifecycle and telemetry collection
  may not invoke providers directly.
- The lifecycle coordinator is the one exception for `pull/unload/remove`.
- Caches, learning, workload persistence and analytics have independent
  opt-ins and persist nothing by default.

See `docs/security.md` for the threat model and residual risks, and
`AGENTS.md` for the authoritative engineering rules.
