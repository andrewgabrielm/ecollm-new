# Adaptive Local-First LLM Broker — Implementation Plan

**Version 4.0 | Simplified MVP | Single developer | Full-day work sessions**

Simplified from v3.0 (retained in git history). Cuts semantic cache, adaptive portfolio, drift detection, live retraining, and multi-system research evaluation. Keeps the ML layer: a LightGBM router trained on synthetic benchmark data. `AGENTS.md` governs security and engineering invariants.

## 0. Product and priorities

**Product:** single-user, local-first chat workspace with two installed SLMs; routes requests using rules (baseline) and a trained LightGBM classifier (learned); masks sensitive entities with Presidio; conditionally proposes authorized cloud fallback; avoids redundant inference via exact cache; provides a simple analytics dashboard. Hardware-aware runtime choices are a measured engineering decision.

| Priority | What to deliver |
|---|---|
| P0: working product | Steps 01–10 (foundation through exact cache), Steps 11–14 (benchmark → train → ship learned router), Steps 15, 17, 19 (dashboard, model lifecycle, integrated UI), Steps 24–25 (security, release). |
| Deferred | Semantic cache, adaptive portfolio/drift, live retraining, S1–S7 research evaluation, ablation experiments, production roadmap. Mentioned in README as future work. |

Build in the order below. Stop after each step for owner review.

### Architecture

```text
React UI (loopback) ─────> FastAPI API
                            │
                            ▼
                  Request orchestrator
       validate → analyze PII → trusted sanitized payload
          → task features + snapshot of eligible local models
          → rules / LightGBM ranking
          → scoped exact cache
          → policy gate immediately before each text generation
          → local SLM 1 → local SLM 2 on failure, when eligible
          → conditional cloud (same gate, approval if required)
          → validate / inspect / restore request-local placeholders
          → eligible local cache + allowlisted telemetry → UI
```

Two separate decisions: request router ranks installed models on every request; model manager handles manual install/remove. Neither can override policy. Cache lookup happens after routing. Only successful, eligible local answers can be reused; no cloud outputs in cache.

### Repository and local state

```text
backend/
  api/                 main.py schemas.py routes.py security.py
  broker/              context.py orchestrator.py policy_gate.py
                       classifier.py validators.py router_interface.py
                       rule_router.py feature_extractor.py lightgbm_router.py
  privacy/             analyzer.py sanitizer.py inspector.py recognisers.py
                       sanitized_payload.py placeholder_map.py
  providers/           base.py ollama_slm.py cloud_openai_compat.py mock.py
  model_management/    registry.py hardware.py lifecycle_manager.py
  profiles/            preferences.py profile_store.py
  cache/               eligibility.py key.py exact_cache.py
  learning/            dataset_builder.py trainer.py evaluator.py router_registry.py
  telemetry/           fields.py writer.py aggregate.py
  config/              loader.py *.yaml
  storage/             connection.py migrations/
frontend/
evaluation/benchmark/   # only public or invented examples and split manifests
evaluation/scripts/
evaluation/results/     # ignored run artifacts
var/                    # ignored SQLite stores, model operation state, router artifacts
scripts/                # stable CLI entrypoints
tests/
docs/
AGENTS.md plan.md BUILD_STATUS.md .env.example Makefile pyproject.toml uv.lock README.md
```

Single process/worker. SQLite with WAL and foreign keys. Real model weights stay in Ollama's store, never in Git.

### Stable command contract

| Target / CLI | Available after | Responsibility |
|---|---|---|
| `make install`, `make lint test`, `make test-privacy`, `make help` | 01 | Codex/CI |
| `make smoke` | 07 | Codex; deterministic mock-only E2E |
| `MOCK_MODE=1 make run-backend`, `make run-frontend`, `make test-ui` | 08 | Owner starts servers |
| `make profile-hardware`, `make catalog-status` | 09 | Owner |
| `make model-install MODEL=<id>` | 09 | Owner, explicit approval |
| `make real-smoke`, `make feasibility` | 09 | Owner; synthetic local prompts |
| `make fixtures`, `make verify-splits` | 11 | Codex; deterministic offline |
| `make benchmark-plan SPLIT=dev LIMIT=20` | 12 | Dry-run, count planned calls |
| `make bench-dev SPLIT=dev_train LIMIT=all` | 12 | Owner; real local data collection |
| `make train-router TRAIN_RUN=<id> VAL_RUN=<id>` | 13 | Codex offline with data |
| `make promote-router VERSION=<id>` | 13 | Owner reviews promotion |
| `make real-router-smoke` | 14 | Owner; local-only |
| `make release-check` | 25 | Codex offline; owner repeats |

### Shared contracts

- `ChatRequest`: prompt (8,000 chars), mode, optional task hint, options, sensitivity scope. Default `local_only`.
- `ChatResponse`: ID, status, answer, route/model/version/reason, privacy type counts, cache status, timings, estimated cost. `ApprovalRequiredResponse` with sanitized preview.
- Task categories: `qa`, `summarise`, `extract`, `classify`, `reason`, `format`, `code`.
- Budget: local 60s, cloud 30s, overall 120s, approval 120s. One local retry max. Health 10s TTL.
- Default weights: `quality=1`, `latency=.3`, `load=.1`. Model roster max 3 installed; start 2.
- Exact cache: opt-in, off by default. Store schema/model/policy versions.

---

# PHASE A — Working product (mock before real)

## STEP 01 — Repository, environment and test harness [DONE]

Python package skeletons, `pyproject.toml`, uv lock, Ruff/mypy/pytest config, Makefile, `.env.example`, `.gitignore`, `BUILD_STATUS.md`, README. `make help lint test` green.

## STEP 02 — Typed contracts, storage and config [DONE]

Shared contracts, Pydantic request limits, `ModelManifest`, roster snapshot, `PreferenceProfile`, validated YAML catalog with unverified placeholders, canonical hashes, SQLite migrations.

## STEP 03 — Privacy engine [DONE]

Presidio/spaCy preload, synthetic ID recognizers, deterministic overlap resolution, immutable sanitized payload, request-only placeholder map, output inspection.

## STEP 04 — Provider adapters and mock runtime [DONE]

Ollama HTTP adapter, OpenAI-compatible cloud adapter, deterministic MockProvider. Separated control APIs for install/unload.

## STEP 05 — Policy gate and dispatch control [DONE]

Single-use cloud proposal/approval, hard-block entity types, guarded dispatch, local retry gate.

## STEP 06 — Minimal registry, rules router and validators [DONE]

Mock roster, task classifier, structural validators, pure rules router (top-two ranking), shared feature extraction.

## STEP 07 — Mock-first end-to-end backend [DONE]

Full orchestration: validate → privacy → features/roster → rules plan → gated local call → validation → cascade → cloud consideration → inspect/restore. Four protected API endpoints. OpenAPI export.

## STEP 08 — Minimal usable React chat [DONE]

Vite/React/TS, generated API types, typed client, approval dialog, privacy mode, local UI origin matching.

## STEP 09 — Real hardware, catalog and two-model local smoke [DONE — closing out]

Safe hardware probe, catalog with verified qwen2.5:1.5b + qwen2.5-coder:1.5b, bound initial install, real broker wiring, gated smoke/feasibility. Both models installed and smoke passed 4/4.

## STEP 10 — Onboarding, exact cache and persistence

Implement preference/profile store and onboarding UI: task interests, speed/balanced/quality, privacy choice, caching consent (independent, off by default). Implement exact cache in SQLite (bounded, TTL, safe writes), lookup after router. Eligibility: opted-in + `non_sensitive` + no-PII scan + eligible output inspection. Key uses canonical scope, task, model/version, full prompt, params, mode/policy. Store under actual producing model. Never cache cloud, failed-validation or limitation output. Cache failure falls back to inference. Add delete/reset, invalidate on consent/policy changes.

**Tests/gate:** migrations, repeat hit, whitespace distinction, model/version provenance, PII/confidential bypass, expiry, delete/opt-out. `make lint test test-privacy smoke test-ui`.

---

# PHASE B — Train and ship the learned router

## STEP 11 — Benchmark datasets and scorers

Build ~300 synthetic/public-licensed tasks across all seven categories with known reference answers. Split: ~200 dev_train, ~100 dev_validation. Group by source/template family before splitting so paraphrases cannot cross boundaries. Freeze split hashes; fit tools cannot read validation.

Scorers: QA/reason exact or normalized answer; extraction field F1; classification accuracy; strict format pass; summarization ROUGE-L; code unit tests in isolated offline sandbox only. Normalize quality [0,1]. Keep objective correctness vs format separate.

**Tests/gate:** deterministic generation, family disjointness, duplicate detection, hashes, scorer known cases. `make fixtures`, `make verify-splits`, `make lint test`.

**Owner:** review random sample of tasks/reference quality.

## STEP 12 — Per-candidate development observations

Collector runs synthetic requests through orchestrator's gated evaluation dispatch (force single candidate, no cache/cloud/cascade). One row per `(task_id, model_id, model_version, run_id)` with scored quality, latency, token counts, resident/cold status. Record failures explicitly. Shared feature extraction. Resumable, no duplicate rows. Collect each candidate sequentially if RAM-limited.

**Tests/gate:** fixture coverage, resume, failed run retained, model/version schema mismatch rejects. `make lint test`.

**Owner run:**
```sh
make benchmark-plan SPLIT=dev_train LIMIT=20     # smoke first
make bench-dev SPLIT=dev_train LIMIT=20
make benchmark-plan SPLIT=dev_validation LIMIT=20
make bench-dev SPLIT=dev_validation LIMIT=20
# once sound, full runs:
make bench-dev SPLIT=dev_train LIMIT=all
make bench-dev SPLIT=dev_validation LIMIT=all
```
At 88 tok/s with 2 models and ~300 tasks, full collection takes ~15–20 minutes.

## STEP 13 — LightGBM training, validation and promotion

`LGBMRegressor` over `(request features, candidate features) → quality`. Fit on dev_train; validate on dev_validation (group-aware). Candidate utility: `U = Q_hat − alpha * latency − beta * load`. Compare learned vs rules on validation prompts: quality regret vs hindsight best, per-task quality, route choice. Define promotion criteria before tuning. Save versioned artifact, encoder, feature schema, training hashes, metrics. Atomic activation with rollback; missing/unseen candidate → rules fallback.

**Tests/gate:** tiny offline synthetic train and reload, unknown category/model handling, invalid artifact fallback, promotion gates. `make lint test`.

**Owner run:**
```sh
make train-router TRAIN_RUN=<id> VAL_RUN=<id>
# inspect validation evidence, then:
make promote-router VERSION=<id>
```

## STEP 14 — Learned router in production

`lightgbm_router.py` behind same Router protocol; feature flag `rules|lightgbm`. Shared feature schema, only eligible verified models, exact compatible versions. Fall back on missing artifact/features, new model, NaN, corruption. Show router type/version/reason in UI. Cache remains model/version-specific.

**Tests/gate:** known fixture selection, unseen versions, schema, both router modes E2E. `make lint test test-privacy smoke test-ui`.

**Owner run:**
```sh
make real-router-smoke
```
Small synthetic prompts, both modes, cloud/cache off; report routing outcomes and overhead. If no promoted artifact, report rules fallback.

---

# PHASE C — Polish and ship

## STEP 15 — Telemetry and dashboard

Typed allowlisted numeric/categorical records: timestamp, opaque request ID, task, model/version, router type/version, route/reason, PII type counts, durations, token counts, outcome, cache status. No request/response content. SQLite bounded retention. Aggregate: sample counts, median, p90 (suppress below 30 observations). UI shows route distribution, model usage, latency, cache hit rate. Unmeasured/disabled shown explicitly. Add delete/reset for opted-in stats.

**Tests/gate:** reject unknown fields, fixture aggregation, retention/delete, no prompt in SQLite. `make lint test test-ui`.

## STEP 17 — Safe model lifecycle (manual swaps)

Extend registry with operation state: `proposal → approved → staging → verified → warm → activate → drain → retire/failed`. Approval binds manifest/digest/roster/disk headroom. Download pinned models only; verify identity/license; gated warm-up; at least one known-good model; atomic roster publish. No silent removal, never touch external weights. Unload memory without deleting files. Progress API; no arbitrary tag/URL from UI.

**Tests/gate:** staged success, no confirmation no download, digest failure, low disk, rollback, external model preserved. `make lint test test-privacy smoke`.

**Owner:** mock swap first; optionally approve one real replacement if space allows.

## STEP 19 — Integrated settings and UI

Complete API: preferences get/update/reset, catalog/status, propose/approve swap, operation progress, metrics. UI: onboarding with hardware report, privacy/retention explanation, catalog vs installed vs resident, proposals/approval/progress/rollback, route/model/version display, exact cache status, latency, consent settings. No keys, prompt logs, or silent installs.

**Tests/gate:** RTL onboarding, consent opt-outs, model proposal/reject/approve, cache indicators, Playwright mock E2E. `make lint test test-privacy test-ui smoke`.

**Owner run:** two terminals with `MOCK_MODE=0` backend and frontend. Verify onboarding, chat with real models, PII masking, exact cache controls, model status display.

## STEP 24 — Security audit

Audit storage with synthetic PII and fake API-key markers. Full policy invariants under failures/timing. Approval replay, concurrent resume, placeholder tampering, cloud endpoint/host/origin. Local API write authorization/CSRF. Pin dependencies, audit licenses. Document supply-chain verification limitations and residual risks. Coverage target ≥80% broker/privacy.

**Tests/gate:** `make lint test test-privacy smoke test-ui`; security report with actual checks and blockers. Any critical authorization/PII bug blocks release.

## STEP 25 — Release and documentation

Finalize README (setup, Ollama startup, model selection, running/testing, limitations). `docs/architecture.md`, threat model, API docs. `make release-check` for all offline checks, mock E2E and artifact integrity. Demo script: onboarding, two models, rule vs learned route, PII masking, exact cache hit, approval flow. Show what's implemented; don't overpromise.

**Tests/gate:** `make release-check`; owner optionally runs clean checkout. `make eval-report` uses existing artifacts.

---

## Release acceptance matrix

| Requirement | Evidence |
|---|---|
| Two actual local SLMs, managed cap | Step 09 hardware/real-smoke + Step 17 swap tests |
| Policy gate, zero calls on privacy failure | Steps 05, 07, 24 fault tests |
| Usable local app with consent/approval | Steps 08, 10, 19 browser and integration tests |
| Rules baseline and learned routing | Steps 12–14 observed dataset, trained artifact, validation |
| Exact cache safe by default | Step 10 tests and provenance |
| Manual model lifecycle | Step 17 swap tests, real swap status |
| Security and release docs | Steps 24–25 clean checks, threat model, known gaps |

## Deferred (future work, not in this release)

- Guarded semantic cache (embedding model + vector search)
- Adaptive portfolio optimization and workload drift detection
- Live metadata collection and feedback-driven retraining
- Multi-system S1–S7 research evaluation
- Performance ablation experiments
- Alternative runtimes (MLX, llama.cpp)
- Multi-user deployment, RAG, agents

## LightGBM training data strategy

No external data required. The pipeline is:

1. **Generate** ~300 synthetic tasks across 7 categories with known reference answers (Step 11)
2. **Run each task through each model** independently via the gated orchestrator — 2 models × ~300 tasks ≈ 600 inference calls (Step 12)
3. **Auto-score** each response — exact match, F1, format pass, ROUGE-L depending on category
4. **Result:** `(task_features, model_id, quality_score, latency)` table — the training set
5. **Train LightGBM** to predict quality given (task features + candidate model) → router picks highest predicted quality (Step 13)

At 88 tok/s, full collection takes ~15–20 minutes on the current hardware.
