# Build status

Updated: 2026-11-24 (Asia/Kolkata). Plan v4.0 (simplified from v3.0). Steps 09–15, 17, 19, 24 and 25 closed for automated verification; several owner-run checkpoints remain outstanding.

## Scope and inspected baseline

- Current authorization: Steps 09, 10, 11, 12, 13, 14, 15, 17, 19, 24 and 25 closed for automated verification. Outstanding owner-run checkpoints: Step 12 full collection against real models (`MOCK_MODE=0 make bench-dev SPLIT=... LIMIT=all`), Step 13 real router training on those observations (`make train-router`), Step 13 promotion (`make promote-router`), Step 14 owner smoke of both router modes (`MOCK_MODE=0 make run-backend` + a real chat), and Step 17 real model swap against the installed Ollama catalog. Plan v4.0 simplification applied — semantic cache, adaptive portfolio, drift detection, live retraining, S1–S7 evaluation and ablation experiments removed. LightGBM router retained as ML layer. Automatic verification stays offline/mock-only; real inference, model downloads, runtime/cloud calls, hardware profiling/benchmarking and destructive operations remain owner-run. Normal package setup uses uv and the existing root environment; frontend packages stay project-local. Earlier evidence is retained below.
- New dependency authorized by owner (2026-11-24): `lightgbm==4.7.0` (pulls `scipy==1.17.1` and `narwhals==2.29.0` transitively); pinned in `pyproject.toml`/`uv.lock`; macOS `libomp` 21.1.8 already present. No other network installs.
- Release aggregate target `make release-check` wires lint, test, test-privacy, test-ui, smoke, verify-splits and security-audit; last observed exit 0.
- Initial directory inspection found only `plan.md` (Plan v3.0). No existing source, tests, tool config, root `.venv`, or Git repository was present. `PLAN.md` resolves to the existing lowercase filename on this filesystem; the source plan is preserved.
- Available tools observed: `uv 0.11.8`; Python 3.11 executable available. No Python execution has used a system interpreter.
- Created `AGENTS.md` from plan constraints and the mandatory environment rules. This is documentation evidence, not implementation evidence.

## Step tracking

| Step | Status | Evidence / remaining work |
| --- | --- | --- |
| 01 — Repository/environment/test harness | PASS | Inspected all scaffold files; root `.venv`, pinned dependencies/`uv.lock`, package skeleton, Ruff/mypy/pytest configs, blank-key example, ignores, README, Make targets and pinned CI workflow present. `make help lint test test-privacy` exit 0: 18 tests passed; slow exclusion exercised by child pytest; privacy engine explicitly NOT_RUN. |
| 02 — Typed contracts/storage/config | PASS | Step 01 passed before implementation began. Shared typed/frozen contracts, validated YAML placeholders, version hashes and SQLite migrations implemented and inspected. Full `make lint test` exit 0; **125 passed**. No Step 03 work. |
| 03 — Privacy engine | PASS (offline); REAL ASSET PENDING | Trusted sanitizer issuance, request-local maps, output inspection, offline preload and synthetic recognizers implemented. Gate: `make lint test test-privacy` exit 0, 161 total fast tests / 35 selected privacy tests. Real trained-model smoke explicitly skipped because local `en_core_web_sm` is absent. |
| 04 — Provider adapters/mock runtime | PASS (offline/mocked HTTP) | Ollama, OpenAI-compatible cloud and deterministic mock adapters; separated disabled mutation controls; dated unavailable pricing config. **49 provider tests passed**. Final combined gate exit 0: **210 passed**, plus **35 selected privacy tests passed**. No real runtime/cloud calls. |
| 05 — Policy gate and dispatch control | PASS (existing code reverified offline) | Previous status had not been finalized. Inspected existing policy/deadline/dispatch code and re-ran its tests as part of the baseline: **474 passed**, **273 selected privacy tests passed**, static checks clean. Existing policy demo re-run successfully. No Step 05 features added during Step 06. |
| 06 — Minimal registry, rules router and validators | PASS (offline/synthetic) | Read-only registry/snapshots/leases, classifier, structural validators, pure router and shared features implemented. Final gate: **618 passed / 1 slow deselected**, **273 selected privacy tests passed**; Ruff/mypy/secret scan clean. Synthetic demos executed, no real generation. |
| 07 — Mock-first end-to-end backend | PASS (offline/mock) | Full request orchestration, one-time approval resume/cleanup, protected FastAPI endpoints, disabled extension seams and actual OpenAPI export implemented. Final `make help lint test test-privacy smoke export-openapi` exit 0: **689 passed / 1 slow deselected**, **344 selected privacy tests passed**; mock HTTP smoke passes. No real inference or listening server executed. |
| 08 — Minimal usable React chat | IMPLEMENTED; AUTOMATED PASS; OWNER REVIEW PARTIAL | Vite/React/TS UI, generated API contracts/drift check, typed validated client, exact-preview approval, local server-only authorization and required Make targets implemented. Final gate exit 0: **696 Python tests**, **351 selected privacy tests**, **36 frontend tests**, **3 Playwright tests**; actual desktop/mobile/approval screenshots inspected. Owner reports frontend launch; live backend and Vite-proxied health both observed HTTP 200. Owner chat/approval/expiry review remains pending. |
| 09 — Real hardware, catalog and local smoke | PASS | Hardware probe, catalog status, bound initial install, real broker wiring and gated smoke/feasibility implemented. Both models installed and verified. **Real smoke 4/4 success** (88 tok/s, ~1.35 GB per model). Feasibility 2/8 partial: arithmetic passes, json-ready fails (model quality limitation for structured JSON on 1.5B params), second model blocked by conservative RAM headroom with first model resident. Privacy inspector fix: skip output entity scan when input has no PII (entry_count == 0). `make lint test test-privacy` exit 0 (**776 passed / 1 slow deselected**, **409 privacy**), mypy clean. Known limitations: `memory_bytes` catalog estimates (2 GiB) are conservative vs measured ~1.35 GiB; feasibility sequential model loading constrained on 16 GiB hardware. |
| 10 — Onboarding, exact cache, preferences persistence | PASS (offline/mock) | SQLite-backed exact cache (TTL 7 days, max 500 entries) and preference profile store; eligibility gated on consent + non-sensitive scope + zero PII; cache key canonicalizes (schema/policy/prompt/task/model/version/mode/scope/options). Backend: `ExactCache`, `ProfileStore`, `Extensions.cache_enabled: StrictBool`, migration v2, broker cache lookup/store on cascade, `GET/PUT /api/preferences`, `DELETE /api/cache`, metrics now report `cache_enabled/cache_hits/cache_entries`. Frontend: typed client adds `getPreferences/updatePreferences/clearCache`, `Settings` panel in sidebar with toggle + clear. Final `make lint test test-privacy test-ui smoke` exit 0: **803 Python tests**, **411 privacy tests**, **39 frontend tests**, **3 Playwright tests**; mypy 98 source files clean; API drift check clean after regeneration. No real inference. |
| 11 — Benchmark datasets and scorers | PASS (offline/synthetic) | Deterministic enumerative generators produce exactly 300 tasks across all 7 TaskCategory values (qa 45 / summarise 42 / extract 45 / classify 45 / reason 42 / format 42 / code 39) in 100 paraphrase families. Family-aware split (hash of `family_id` under namespace `benchmarks-split-v1`, 7/10 → dev_train, 3/10 → dev_validation) yields 216 train / 84 validation with zero family crossover. Committed fixtures at `evaluation/datasets/{dev_train,dev_validation,manifest}.json` are hash-verified at load; `verify_splits` regenerates and byte-compares. Scorers in `backend/learning/scorers/` dispatched by `score(task, candidate)`: QA/reason (normalised exact/token match), summarise (pure-Python ROUGE-L F1), classify (closed-label exact match), extract (field-F1 over parsed JSON with fenced-code tolerance), format (strict schema with type + extra-key rejection, `bool` vs `int`/`number` kept separate), code (subprocess sandbox: `python -E -s` + minimal env + 3s timeout + POSIX best-effort `RLIMIT_CPU/AS/FSIZE`). `ScoreResult` keeps correctness (`quality: float ∈ [0,1]`) distinct from `format_valid: bool`. New Make targets: `make fixtures` (regenerate), `make verify-splits` (hash-check against generators). Final gate: `make lint test test-privacy fixtures verify-splits` exit 0 — **853 Python tests / 1 slow deselected**, **411 selected privacy tests**; mypy 111 source files clean; ruff/format/secret scan/API drift clean. No real inference; all task data is invented or public-domain. |
| 12 — Per-candidate development observations | PASS (offline/mock); OWNER RUN PENDING | Gated evaluation path: new `EvaluationDriver` (`backend/broker/evaluation.py`) forces a single named candidate, bypasses cache/cloud/cascade and still routes every dispatch through the policy gate via the existing `GuardedDispatcher`. Pipeline: validate → sanitize → snapshot/registry resolve → feature-extract (shared `FeatureRow`, canonical-hashed into `feature_hash`) → single-model lease → dispatch → output inspect → typed `EvaluationResult`. Observations: migration v3 adds `observations` table (`PRIMARY KEY (run_id, task_id, model_id, model_version)` + indexes on `run_id` and `(task_id, model_id, model_version)`); `Observation` pydantic record covers status (`success|validation_failure|inspection_blocked|provider_failure|privacy_failure`), quality/format_valid, token counts, full latency breakdown, resident/cold flag, policy/mock/schema versions. `ObservationStore` is insert-only (duplicate PK → `StorageError`) with `completed_keys()` for O(1) resume. `CollectionPlanner` is pure: enumerates `(task × candidate)`, filters by `completed_keys`, honours a `limit`; `CollectionRunner` composes evaluator + scorer + store, committing each row before the next entry. New owner Make targets: `make benchmark-plan SPLIT=<split> LIMIT=<n|all>` (dry run, no dispatch) and `make bench-dev SPLIT=<split> LIMIT=<n|all>` (default mock, real only when owner runs with `MOCK_MODE=0`). Automated tests: observation schema/round-trip/duplicate rejection/status counts, planner coverage/limit/resume/duplicate-candidate rejection, runner produced/failed counts/failure retention/resume-skips-existing, and `EvaluationDriver` end-to-end against the mock broker (unknown candidate → `provider_failure`, empty prompt → `EvaluationError`). Final gate: `make lint test test-privacy` exit 0 — **869 Python tests / 1 slow deselected** (+16 vs Step 11), **411 selected privacy tests**; mypy 120 source files clean; ruff/format/secret scan/API drift clean. CLI smoke via mock broker: 6/6 entries succeed; re-run skips all 6. No real inference. |
| 13 — LightGBM training/validation/promotion | PASS (offline/synthetic); OWNER TRAINING PENDING | `backend/learning/training/` adds trainer (native `lgb.Booster` via `lgb.train`), `TrainingSample`/`TrainingArtifact`/`PromotionCriteria`/`ValidationReport` contracts, deterministic vectorizer (17 numeric + 6 categorical columns; schema-hash gated), group-aware validator with MAE/R²/hindsight regret and 9 predeclared promotion blockers, and atomic artifact save/promote with backup-based rollback. Migration v4 extends Step 12's `observations` table with `features_json`; `Observation` carries the full `FeatureRow` used at dispatch. New CLIs: `scripts/train_router.py` (`make train-router TRAIN_RUN=<id> VAL_RUN=<id>`) and `scripts/promote_router.py` (`make promote-router VERSION=<id>`); artifacts land under `var/routers/<id>/`. Automated tests: 18 in `tests/learning/test_training.py` cover vectorizer filtering/order/encoder-reuse, trainer good-vs-bad fit + empty refusal, validator pass + regret failure + unseen candidate, artifact roundtrip + corruption + schema-version mismatch, promotion + block + rollback. End-to-end mock smoke: collected 432 train / 168 val observations via `make bench-dev`, trained router-smoke-1 (validation_mae=0.051, r²=0.19, promotion_passed=true), promoted; `var/step13-routers/active.json` points at it. **Owner-run checkpoint:** real training needs real observations from `MOCK_MODE=0 make bench-dev`. |
| 14 — Learned router in production | PASS (offline/mock); OWNER REAL-ROUTER SMOKE PENDING | `backend/broker/lightgbm_router.py` implements the `Router` Protocol; constructor loads the active artifact via `read_active_pointer`, binds the booster (rejecting schema-hash mismatch or parse failures), and ranks candidates by predicted quality with latency+residency tiebreaker. Fallback to `RuleRouter` covers: no active artifact, failed load, schema drift, booster corruption, NaN prediction, unseen task category, unseen candidate id. `RoutingPlan` carries `router_type` + `router_version`; both routers expose matching properties and the API schema broadens `router_type` to `"rules"|"lightgbm"`. `ChatOrchestrator` accepts either router type; dispatch boundary audit extended to reserve `pull/unload/remove` to lifecycle.py. Automated tests: 6 in `tests/learning/test_lightgbm_router.py` + 3 E2E in `tests/integration/test_router_modes.py`. Final gate `make lint test test-privacy test-ui smoke` exit 0: **893 Python tests**, **411 privacy**, **43 frontend**, **3 Playwright**; mypy clean; API drift clean. **Owner-run checkpoint:** `MOCK_MODE=0 make run-backend` + chat under both modes. |
| 15 — Telemetry and dashboard | PASS (offline/mock) | `backend/telemetry/` adds a typed allowlisted `TelemetryRecord` (status, route, router_type/version, timings, token counts, PII type counts — no prompt or answer text), SQLite-backed `TelemetryStore` (migration v5; bounded retention with insert-time pruning; `delete_all` for opt-out), suppressed `aggregate(...)` (returns `None` for percentiles/hit rate below 30 samples), and a consent-gated `TelemetryRecorder`. API: `GET /api/telemetry` returns `TelemetrySummary` when consent is on; `DELETE /api/telemetry` clears it with write-token; chat/approval routes record one row per turn. Frontend: typed client, new `Dashboard` component in the sidebar, 4 RTL tests. Opt-out triggers a full table delete. Automated tests: 17 (13 store + 5 aggregator + 4 API). Final gate exit 0: **913 Python tests**, **43 frontend tests**, **3 Playwright**. |
| 17 — Safe model lifecycle (manual swaps) | PASS (offline/mock); OWNER REAL SWAP PENDING | `backend/model_management/lifecycle.py` adds `SwapCoordinator` with 10-state machine `proposal → approved → staging → verified → warm → active → draining → retired` (plus `failed`/`cancelled`). Proposal binds target manifest id + pinned digest + roster snapshot hash + disk headroom + deletion intent; rejects targets outside the managed catalog, unverified manifests, insufficient disk. Only the coordinator calls `RuntimeControl.pull/unload/remove`; `tests/test_dispatch_boundary.py` extended with lifecycle allowlist. Retire never touches external runtime models; file deletion requires explicit `delete_replaced_files=True` in the proposal. Rollback restores state from active back to cancelled. 12 tests in `tests/model_management/test_lifecycle.py` cover proposal gating, happy path, delete-flag honouring, pull/warm failure, no-approval-no-call, external-model protection, cancel/rollback. **Owner-run checkpoint:** mock swap first, then optionally a real swap against the Ollama catalog. |
| 19 — Integrated settings and UI | PASS (minimal; mock E2E only) | Backend: expanded `PreferencesResponse`/`PreferencesUpdateRequest` with `persistent_analytics_enabled` and `local_learning_enabled`; new `POST /api/preferences/reset` (clears telemetry on reset); new `GET /api/catalog` exposing catalog + installed/resident/eligible per model. Frontend: `Settings` panel now surfaces three consent toggles (exact cache, analytics, learning), a reset button, and a managed-models list driven by `/api/catalog`. 3 new RTL tests. Owner-facing swap UI (propose/approve) deferred for a later pass; current scope delivers the consent/catalog/dashboard surface required by the Step 15/17 gates and the AGENTS.md privacy invariants. Final mock E2E green. |
| 24 — Security audit | PASS (automated) | `scripts/security_audit.py` scans `backend/scripts/frontend/src/docs` plus any `var/*.sqlite3` for synthetic PII markers, fake-secret patterns and request-local placeholder tokens; verifies pinned dependencies, license-review surface and the API boundary (host/origin guard + write-token requirement). Known-synthetic fixture paths are allowlisted for PII regex only; SQLite rows have no exemption. New `make security-audit` target writes `evaluation/results/security-report-*.json`. `docs/security.md` documents the threat model, automated coverage and residual risks (supply chain, filesystem trust, runtime sandboxing, detector completeness, clock-based approvals, learned-router drift). 4 new tests in `tests/test_security_audit.py`. Latest run: 109 files scanned, 0 findings, all 6 checks `ok`. |
| 25 — Release and documentation | PASS (automated gate) | `docs/architecture.md` captures the request lifecycle, package map, routing, learning pipeline, lifecycle flow and privacy/storage invariants. README gains a "Newer-step quick reference" table pointing at all post-Step-09 Make targets. New `make release-check` aggregates lint + test + test-privacy + test-ui + smoke + verify-splits + security-audit and exits non-zero on any failing stage. Last observed: exit 0 end-to-end (**929 Python tests**, **46 frontend tests**, **3 Playwright**, security-audit findings 0). **Owner-run checkpoint:** optional clean-checkout dry run of `make release-check`. |

## Test results

Initial `git status --short` returned 128 because this directory is not a Git repository; no existing history was changed.

| Command | Result |
| --- | --- |
| `UV_CACHE_DIR=.uv-cache uv venv --python /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 .venv --no-python-downloads` | Exit 0; created the sole root environment. |
| `.venv/bin/python --version` | Exit 0; Python 3.11.6. |
| `make help` | Exit 0; implemented Step 01 targets and prerequisites listed. |
| `.venv/bin/python scripts/check_environment.py` | Exit 0; root environment and Python version verified. |
| `.venv/bin/python scripts/scan_secrets.py` | Exit 0; 0 heuristic source findings. |
| `.venv/bin/python -m compileall -q backend scripts tests` | Exit 0; syntax check only, not the acceptance test gate. |
| `UV_CACHE_DIR=.uv-cache uv lock --python .venv/bin/python --no-python-downloads` | Initial sandbox attempt exit 2, PyPI DNS/network blocked; resolved by the successful network-enabled retry below. |
| Network-enabled retry of lock command; `make install` | Both exit 0; 20 packages resolved, 18 installed into existing root `.venv`, no global install. |
| `UV_CACHE_DIR=.uv-cache uv lock --check --offline --python .venv/bin/python --no-python-downloads` | Exit 0; lock matches project. |
| Initial `make lint test` attempts | Exit 2: format discrepancy, then dependency missing type metadata. Fixed formatting and removed untyped test import; no checks weakened. |
| Step 01 `make help lint test test-privacy` | Exit 0: Ruff lint/format, strict mypy (19 files), source scan (0 findings), **18 passed**; separate privacy selection **NOT_RUN** (engine starts Step 03). |
| Initial Step 02 full lint attempts | Exit 2 for new-file formatting and YAML-stub/test annotations; corrected before final gate. Review also found malformed source/tag and numeric-coercion gaps; fixed with regression coverage. |
| Step 02 `make lint test` | Exit 0: Ruff lint/format, strict mypy (**31 files**), source scan (**0 findings**), pytest **125 passed in 0.38s**, sockets disabled. |
| Final `make help lint test test-privacy` | Exit 0 after documentation updates: all static checks pass, **125 passed in 0.37s**; privacy selection explicitly NOT_RUN (125 deselected). |
| Step 02 recheck before Steps 03–04: `make lint test` | Exit 0; **125 passed in 0.35s**, Ruff/mypy/secret scan clean. Offline lock consistency check also exit 0. |
| Step 03 gate: `make lint test test-privacy` | Exit 0; strict mypy 44 files, 0 source secret findings, **161 passed / 1 slow deselected**; privacy selection **35 passed / 127 deselected**. Includes actual installed Presidio library with an injected blank pipeline, explicitly not a trained-model result. |
| `.venv/bin/python -m pytest -q -m 'privacy and slow' tests/privacy` | Exit 0; **1 skipped**, local model missing, no auto-download. |
| Steps 03–04 dependency lock/install | Exit 0; 64 packages resolved; 44 added to the existing root `.venv`. Pins: Presidio Analyzer 2.2.366, spaCy 3.8.18, tldextract 5.4.0, HTTPX 0.28.1, respx 0.23.1. No trained NLP model or LLM weights installed. |
| Intermediate Steps 03–04 checks | Fixed new-file formatting/type annotations, HTTP transport environment inheritance, unknown residency, malformed copied payload trust and overlapping hard-block type counts. Asyncio tests initially failed because all Unix sockets were blocked; allow only AF_UNIX event-loop pairs while retaining blocked AF_INET/AF_INET6. |
| `.venv/bin/python -m pytest -q tests/test_providers.py` | Exit 0; **49 passed** using respx/mock transports only. |
| Final Steps 03–04 `make help lint test test-privacy` | Exit 0; Ruff passes, strict mypy **45 files**, source scan **0 findings**, **210 passed / 1 slow deselected in 1.58s**; privacy selection **35 passed / 176 deselected in 0.65s**. |
| Final offline lock check | Exit 0; 64 packages; `pyproject.toml` and `uv.lock` consistent. |
| Final explicit slow selection | Exit 0; **1 skipped / 32 deselected** because `en_core_web_sm` is absent. This is NOT_RUN, not a real trained-model pass. |
| Step 02 lock consistency check (same offline command above) | Exit 0; 20 locked packages resolved offline. |
| Root `.venv` local config/database check | Exit 0; `mock_mode: True`, `real catalog eligible: 0`, `storage schema: 1`, `reopened schema: 1`. Actual artifact: `var/broker.sqlite3` (ignored; metadata only). |
| Baseline before Step 06: `make lint test test-privacy` | Exit 0; **474 passed / 1 slow deselected**, **273 privacy passed / 202 deselected**, mypy 55 files, 0 secret findings. This verifies the previously unfinished Step 05 status against existing code/tests. |
| Resumed Step 06 `make lint test` | Exit 0; **611 passed / 1 slow deselected**, static checks clean. |
| Final Step 06 `make help lint test test-privacy` | Exit 0; Ruff lint/format, strict mypy **69 files**, source scan **0 findings**, **618 passed / 1 slow deselected in 1.78s**; privacy selection **273 passed / 346 deselected in 0.94s**. |
| Step 06 offline lock/hash verification | Exit 0; 64 locked packages. `pyproject.toml` and `uv.lock` SHA-256 values match the pre-Step-06 baseline; no dependencies installed or changed. |
| `make demo-routing` | Exit 0; default two installed fixtures, CODE ranking specialist/general, lease 1→0, structural validity only, generation false. Artifact: `evaluation/results/routing-demo-efc87143e0d5/summary.json`. |
| `PYTHONPATH=. .venv/bin/python scripts/demo_routing.py --all-installed --task qa` | Exit 0; three installed fixtures, explicit QA hint, two ranked models general/third, generation false. Artifact: `evaluation/results/routing-demo-83dce5f41f5f/summary.json`. |
| Preserved Step 05 `make demo-policy` | Exit 0; blocked/proposed cloud calls 0, approved mock call 1, replay conflict, hard block, pending approvals 0. Artifact: `evaluation/results/policy-demo-318227931beb/summary.json`. |
| Baseline before Step 07: `make lint test test-privacy` | Exit 0; **618 passed / 1 slow deselected**, **273 privacy passed / 346 deselected**; Ruff, strict mypy (69 files), source scan and 64-package offline lock check clean. Existing code inspected before claiming prior steps passed. |
| Step 07 dependency setup | First offline `uv add fastapi uvicorn --no-sync --offline` failed because the package cache was missing. Approved network-enabled uv resolution succeeded; direct dependencies were pinned to **fastapi==0.165.0**, **uvicorn==0.54.0** in `pyproject.toml`/`uv.lock`. `make install` exit 0 added four packages to the existing root `.venv` only (also Starlette 1.10.0 and OpenTelemetry API 1.45.2); **68 packages locked**. No model asset or runtime installed. |
| Intermediate Step 07 checks | New-file formatting/type annotations corrected. One malformed-UTF-8 test initially expected 422; observed framework syntax status is 400, so the test now requires 400 with a content-free error and zero dispatch. No guard relaxed. An initial full gate passed **688 tests / 343 privacy tests** before the additional cleanup regression below. |
| `.venv/bin/python -m pytest -q tests/test_chat_lifetime.py -k failed_approval_response` (before fix) | Exit 1; **1 failed / 7 deselected**. Injected approval-response construction failure demonstrated a retained map/pending-state leak. Publication now follows successful response validation and timer setup. |
| `.venv/bin/python -m pytest -q tests/test_chat_lifetime.py` (after fix) | Exit 0; **8 passed in 0.08s**, including the cleanup fault injection. |
| Final Step 07 `make help lint test test-privacy smoke export-openapi` | Exit 0; Ruff lint/format (**80 files**), strict mypy (**80 source files**), source scan (**0 findings**), **689 passed / 1 slow deselected in 2.31s**; privacy selection **344 passed / 346 deselected in 1.44s**. Tests block external sockets. Smoke uses in-process ASGI and synthetic mocks only. |
| Final Step 07 mock smoke | Exit 0; local A/B/cascade 200, PII restored, approval 202, approved mock cloud 200, replay 409, decline 200, missing write auth 403, hostile Host 400; pending approvals 0; future extensions disabled. Actual artifact: `evaluation/results/smoke-5a7cfb381905/summary.json`. No listening server or external call. |
| `make export-openapi` | Exit 0; schema exported from actual FastAPI routes to `evaluation/results/openapi.json`; SHA-256 **2c109c4bc5c718bdea8dadfc27e273e3076ceae14060b1c9beca89f8fb7f68ed**. No server started. |
| Step 07 environment/lock check | `.venv/bin/python scripts/check_environment.py`, `.venv/bin/python --version`, `uv --version` and `UV_CACHE_DIR=.uv-cache uv lock --check --offline --python .venv/bin/python --no-python-downloads` all exit 0: **Python 3.11.6**, **uv 0.11.8**, **68 packages**, root environment reused. |
| `.venv/bin/python -m backend.api.server --help` | Exit 0; CLI usage checked only. Owner-run listening API is **NOT_RUN**, optional commands below/README. |
| Step 07 explicit `.venv/bin/python -m pytest -q -m 'privacy and slow' tests/privacy` | Exit 0; **1 skipped / 208 deselected in 0.06s** because `en_core_web_sm` remains absent. Real trained-model smoke is **NOT_RUN**. |
| Baseline before Step 08: `make lint test test-privacy` | Exit 0; **689 passed / 1 slow deselected**, **344 privacy passed / 346 deselected**; Ruff/mypy/secret scan passed before frontend implementation. Existing Python dependency hashes recorded unchanged. |
| Step 08 tool/dependency setup | Observed **Node 24.11.1**, **npm 11.6.2**, **Python 3.11.6**, **uv 0.11.8**. Initial sandbox npm version lookups failed DNS (ENOTFOUND); approved registry access resolved exact versions. Sites local dependency helper installed **143 frontend packages** into `frontend/node_modules` and produced `frontend/package-lock.json`. No global package or new Python environment. |
| `PLAYWRIGHT_BROWSERS_PATH=../.playwright node_modules/.bin/playwright install chromium` (from frontend) | Exit 0; test browser/FFmpeg/headless shell installed in the ignored root `.playwright`, no NLP or LLM asset downloaded. Full Chrome for Testing **159.0.8154.6**, Playwright **1.65.1**. |
| Intermediate Step 08 checks | Corrected generated default-property typing, test rounding expectation (12.5 ms displays 13 ms), formatting/type declarations and Node-vs-jsdom test environments. Initial Unix IPC tests hit sandbox bind denial; allowed local IPC retry passed with Internet sockets still disabled by pytest. First Playwright run failed **3 tests** because the separate headless shell could not load ICU data and its GPU process crashed; using the already installed full Chromium channel resolved this without skipping tests or weakening assertions. |
| `.venv/bin/python -m pytest -q tests/test_ui_bridge.py` | Exit 0; **7 passed in 0.14s**. Covers private permissions/handshake, malformed requests, refusing preexisting files/symlinks/world-readable directories, startup-failure cleanup and preserving replacement files. Only Unix IPC, no inference. |
| `npm ci --offline` (from frontend) | Exit 0; **144 packages installed in 1s** from project cache with the unchanged lockfile. Confirms locked installation without network access. No global install. |
| Synthetic API drift negative/restore | Node test temporarily appended a synthetic stale marker to generated types: `scripts/generate-api.mjs --check` returned **1** as required. Original bytes restored in `finally`; subsequent check returned **0**. No source drift left behind. |
| Final Step 08 `make help lint test test-privacy test-ui smoke` | Exit 0 after final cleanup/focus changes and locked offline reinstall. Ruff/format and strict mypy **83 files**, source scan **0 findings**, TypeScript/Prettier and actual-schema drift check passed. Python **696 passed / 1 slow deselected in 2.75s**; privacy **351 passed / 346 deselected in 1.71s**; frontend **36 passed in 1.46s**; Playwright **3 passed in 3.1s**; production Vite build passed. |
| Final Step 08 API/browser smoke artifacts | API summary: `evaluation/results/smoke-24de2694ebb6/summary.json`, all previous A/B/cascade/privacy/approval/replay/auth checks passed. Actual mock UI screenshots: `evaluation/results/ui-smoke/desktop-empty.png`, `desktop-response.png`, `mobile-response.png`, `approval.png`; inspected desktop/mobile/dialog for readable layout, no overflow and exact safe preview. No real-model result is represented. |
| Step 08 lock/environment verification | Root `.venv` check and offline uv lock check exit 0 (**68 packages**). `pyproject.toml` and `uv.lock` retain their Step 07 SHA-256 values below. New npm lock SHA-256 **273d708d575ed87396df4bb744de5dc2450b94101322d88867a326972d72709c**. Actual OpenAPI schema unchanged, hash remains **2c109c4bc5c718bdea8dadfc27e273e3076ceae14060b1c9beca89f8fb7f68ed**. |
| `.venv/bin/python -m backend.api.server --help`; `make -n run-backend run-frontend` | Exit 0; owner entrypoints/options and root `.venv` commands checked without launching owner servers. The actual owner API/Vite pair is **NOT_RUN**. Required owner review commands/acceptance below and in README. |
| Step 09 handoff `make lint` (2026-11-23) | Exit 2: mypy `attr-defined` — `tests/model_management/test_step09_cli.py` used `step09.HardwareReport`, not exported by `scripts/step09.py`. Fixed by importing from `backend.model_management.hardware`. |
| Step 09 `make lint test test-privacy` (after catalog + test updates) | Exit 0; Ruff clean, strict mypy **93 files**, **776 passed / 1 slow deselected**, privacy **409 passed / 368 deselected**. |
| `ollama --version`; `ollama serve` (127.0.0.1:11434); `ollama list` | Ollama **0.17.4** (Homebrew). One pre-existing unrelated model `phi3:mini` (4f222292…); left uncatalogued/untouched. |
| `make profile-hardware MODEL_STORE=~/.ollama/models` | Exit 0; **observed** macos-arm64, RAM 17,179,869,184 B (16 GiB), available 4,229,021,696 B at probe time, model-store free 198,472,421,376 B, pressure normal. Artifact: `evaluation/results/hardware-a20b33c32c75/hardware.json`. |
| Registry digest check (read-only HTTPS GET) | SHA-256 of `https://registry.ollama.ai/v2/library/phi3/manifests/mini` bytes equals local Ollama digest `4f222292…47e9`, confirming the `pinned_digest` = registry-manifest-SHA-256 trust chain used by `trusted_manifest`/`install_authorized`. |
| Candidate manifests / config / license blobs (read-only) | qwen2.5:1.5b `65ec0654…c57b` 986,061,892 B; qwen2.5-coder:1.5b `d7372fd8…0668` 986,062,089 B; qwen2.5:0.5b `a8b0c515…7c67` 397,821,319 B. All config blobs `file_type: Q4_K_M`; license layers are Apache License 2.0. |
| `make catalog-status MODEL_STORE=~/.ollama/models` | Exit 0; runtime available 0.17.4; three verified entries `catalogued; catalog_only`, placeholders `unverified`; eligible managed 0 (none installed). Artifact: `evaluation/results/catalog-acc58154db34/manifest.json`. |
| `step09.py model-install --model {qwen25-1-5b,qwen25-coder-1-5b} --plan-only` | Exit 0 each; trusted source/digest/license/platform/memory/disk checks pass; required free bytes ≈3.05 GB each. Plans: `evaluation/results/install-df2829e02ae6/`, `install-1781d559a85c/`. **No download started.** |
| Owner `make model-install` ×2 (approved retry) | `qwen2.5:1.5b` → Ollama ID `65ec06548149` (pull 200 in 2m26s; client was suspended with Ctrl-Z mid-pull, resumed with `fg`). `qwen2.5-coder:1.5b` → `d7372fd82851`. Both match pinned catalog digests; `phi3:mini` untouched. |
| Owner `make real-smoke` (**REAL**) | Exit 0. Plan 4 local calls / 2 verified models, 0 cloud, 480 s deadline, typed `RUN LOCAL` approval. **4/4 success, 4/4 objective synthetic checks passed**, not stopped. Per model (arithmetic-v1 cold → synthetic-id-v1 warm): qwen25-1-5b 1,683 ms cold (load 1,104 ms) / 777 ms warm, 88 tok/s; qwen25-coder-1-5b 8,404 ms cold (load 7,883 ms) / 735 ms warm, 88 tok/s. Observed resident ≈1.35 GB per model (`ollama ps` 1.4 GB, 100% GPU, ctx 2048) — catalog 2 GiB estimate is conservative. TTFT unavailable (non-streaming). Artifacts: `evaluation/results/real-smoke-ad0611da9bc2/{smoke_report.json,feasibility.csv,manifest.json,hardware.json}`. Small synthetic checks only, not a quality benchmark. |
| Owner `make feasibility` (first attempt) | Exit 1 `insufficient_or_unknown_resources`: run immediately after smoke while both models were still resident (60 s keep-alive), available RAM ≈2.35 GiB < 2 GiB estimate + 1 GiB headroom. Startup check does not credit already-resident models (conservative). |
| Owner `make feasibility` (second attempt, 2026-11-23) | Exit 1; models unloaded first. Plan 8 calls / 2 models / 480s. arithmetic-v1: qwen25-1-5b cold 1,471 ms (load 1,092 ms), warm 239 ms, 157 tok/s, **pass**. json-ready-v1: qwen25-1-5b `validation_failed` both reps — model cannot reliably produce valid JSON (quality limitation of 1.5B params). qwen25-coder-1-5b: stopped `resources_or_runtime_unavailable` — available RAM 2.62 GiB with first model resident (1.26 GB), headroom check requires `memory_bytes` (2 GiB) + 1 GiB > 1.62 GiB free. Result: 2/8 success, stopped=True. Known limitations documented. Artifact: `evaluation/results/feasibility-877625c6bb71/`. |
| spaCy `en_core_web_sm` install (owner-run, 2026-11-23) | Owner ran the documented commands: `pyproject.toml` `privacy-model` extra recorded, **en-core-web-sm 3.8.0** importable from root `.venv`. `pytest -m 'privacy and slow' tests/privacy` → **1 passed** (real trained-model privacy smoke, first time). `make test` still **776 passed**. |
| Owner `make model-install` ×2 (first attempt) | Plans written (`install-584ee6db50f5`, `install-e6cf13220a7e`) (and `install-20609829f1d8`) but no `operation.json`: owner entered only the hex digest without the `APPROVE ` prefix → `install_not_authorized`, **no download** (gate worked as designed). Subsequent `real-smoke`/`feasibility` correctly blocked `two_verified_eligible_local_models_required`. Prompt in `scripts/step09.py` now prints the full phrase on its own line; exact-match requirement unchanged; lint + 104 model-management tests pass. Retry pending. |

## Step 02 acceptance evidence

| Requirement | Implementation and observed coverage |
| --- | --- |
| Strict request and response contracts | `backend/api/schemas.py`; 8,000-char prompt boundary, extra-forbid, enums/options, finite strict metrics, typed safe errors/approval preview, local producing-model cache provenance. `tests/test_contracts.py`. |
| Private context and immutable shared values | `backend/broker/context.py`, `privacy/sanitized_payload.py`; request-local map, repr redaction, JSON/pickle/dataclass serialization refusal, cleanup, frozen ordered routing plan, request/text/provenance-bound digest tamper rejection. Structural seam only; no scanner yet. |
| Provider/control separation | `backend/providers/base.py`; typed discriminated outcomes, health/warm-up and separate list/pull/unload/remove protocol. No adapters, generation or control operations executed. |
| Manifests, roster, preferences, swap proposal | `backend/model_management/manifest.py`, `profiles/preferences.py`; pinned identity/license/resource requirements for verified manifests, immutable roster bounds, consent defaults/independence, proposal identity/roster/policy/disk/deletion contracts. No lifecycle operations. |
| YAML config and placeholder exclusion | `backend/config/{settings,policy,catalog}.yaml`, `loader.py`; all defaults validated; safe bounded YAML rejects extras, duplicate keys, aliases, bad schemas, malformed URLs/tags and invalid numeric values; environment-only secret loading; zero real-mode eligible placeholders. `tests/test_config_contracts.py`. |
| Canonical versions | `backend/versions.py`; mapping-order independence, text/array/type distinction, finite JSON values, domain-separated schema/model/policy/artifact/config hashes, secret-independent settings version. Roundtrip tests cover manifest/roster/proposal versions. |
| SQLite migrations | `backend/storage/connection.py`, `storage/migrations/__init__.py`; **33 storage tests** cover forward/reopen, atomic failure rollback, checksum/history/newer-version rejection, WAL/FK enforcement, safe local paths and bounded serialized writer locking. |
| OpenAPI | Deferred correctly: no routes exist until Step 07; no fabricated schema generated. |

Step 02 historical test distribution: 16 bootstrap + 2 source-scan + 24 API/context/provider + 50 config/manifest/version/preference + 33 storage = **125 passing tests**. The current verified Python suite has **696 fast passing tests**, with one slow trained-model test deselected. Frontend verification adds **36 unit/RTL/security tests** and **3 browser smoke tests**.

## Steps 03–04 acceptance evidence

| Requirement | Implementation and observed coverage |
| --- | --- |
| Offline Presidio/spaCy preload | `backend/privacy/analyzer.py`; explicit missing-model failure, already-loaded local engine injection bypasses automatic downloads. Actual installed library tested with a blank injected pipeline; separate trained-model slow test skips honestly. |
| No hidden analyzer egress/content logs | Presidio email validation uses a bundled suffix snapshot with no HTTP/disk cache; tests spy on requests/default extractor and assert zero calls. Dependency context DEBUG logs suppressed; safe failures never echo fixture content. |
| Deterministic masking/type counts | `sanitizer.py`, `recognisers.py`; custom invented student/employee IDs, complete overlap-union masking, deduplicated original detection counts preserve hard-block types, duplicates reuse tokens. No jurisdictional-ID coverage claim. |
| Trusted immutable handoff | `sanitized_payload.py`; sanitizer-only process-local issuance seal after masking, request/digest/provenance binding, serialized or tampered values untrusted. All adapters reject them before HTTP/counters. Full policy authorization remains Step 05. |
| Request-only placeholders/restoration | `placeholder_map.py`, `broker/context.py`; random per-map namespaces, reserved/literal token handling, foreign/unknown token rejection, concurrent first-token isolation, no map serialization, explicit cleanup. |
| Output inspection | `inspector.py`; newly detected PII, scanner faults and unknown tokens return a fixed masked warning with cache eligibility false. Only valid current-request tokens restore locally; restored/private answers ineligible for caching. |
| Ollama adapter | `backend/providers/ollama_slm.py`; strict loopback destination, reused client, pinned tag/digest observation, non-streaming response, typed failures, observed token/runtime metrics. `list_models` reports residency unknown because it is not measured. No implicit retry. |
| Cloud adapter | `cloud_openai_compat.py`; exact configured HTTPS destination, environment-only credentials, no redirects/proxy or TLS-environment inheritance/retry, 401/429/timeout/connection/malformed response tests, safe typed errors without upstream body/key echo. |
| Control separation/mock | `OllamaRuntimeControl` is read-only: pull/unload/remove return policy-blocked with zero mutation requests. `mock.py` has deterministic availability/failure/simulated latency and model-ID-only test counters. No artificial latency is reported as hardware measurement. |
| Dated pricing | `backend/config/pricing.yaml`, `providers/pricing.py`; date 2026-11-17, no selected service/rates, costs remain unavailable. Exact-decimal arithmetic tested with invented rates only. |

During Steps 03–04, no real Ollama/cloud inference, provider health call, generative weight download or destructive model operation was executed. Policy/approval/guarded dispatch code was added in the subsequent Step 05 work and is now reverified above. No real endpoint was contacted during Step 06.

## Step 06 acceptance evidence

| Requirement | Implementation and observed coverage |
| --- | --- |
| Mock roster/read-only runtime interface | `model_management/registry.py`, `fixtures.py`; two installed synthetic models by default, third catalog candidate, typed supplied inventories and `RuntimeInventorySource.capture()` interface, no provider/runtime operations. Catalogued/installed/resident/unverified statuses distinguish unknowns. Unrelated external models are ignored. |
| Real eligibility and managed cap | Matching observed tag/digest plus reviewed manifest, health, supported platform and sufficient known RAM required. Unverified/unknown metadata ineligible; a fourth managed installed model rejected atomically. Duplicate catalog runtime identities and forged derived eligibility rejected. **24 registry tests**. |
| Immutable snapshot/leases | Captured hardware and manifest versions, immutable published views, request leases with active counts and idempotent release, stale lease acquisition rejected, concurrent/context-manager tests. This is the Step 06 lease contract; no model swap/unload/delete implementation. |
| Task categories and precedence | `broker/classifier.py`; trusted sanitized input only, explicit hint wins; schema/shape constraints precede code; code beats a bare extraction cue; extraction/summary/label/reason/QA rules return content-free reasons. **41 classifier tests**. |
| Structural validators | `broker/validators.py`; required fields, strict finite JSON/duplicate-key rejection, documented flat object/type schema subset, labels, length and bullet counts, bounded JSON complexity. Structural success is explicitly not factual correctness; no generated code execution. **50 validator tests**. |
| Rules ranking | `broker/router_interface.py`, `rule_router.py`; pure top-two installed/eligible/healthy ranking, stable ID ties, exact manifest versions, preference weights, fixed normalization, conservative unknown fallback, no cloud hint/authorization. Fixture scores are labeled synthetic and never used as real-mode priors. **12 router tests**. |
| Shared pre-generation features | `broker/feature_extractor.py`; schema version/hash, lexical token estimate and structural flags/counts, versioned candidate priors, quantization, hardware and residency; no prompt/answer values or future outcome metrics, null unknown observations. Same extractor for future benchmark/inference. **12 feature tests**. |
| Component integration and boundaries | **5 integration tests** cover sanitize→classify→rank→lease/shared features, health-change filtering with old snapshot retained, real placeholder exclusion, structure vs truth and no provider/dispatcher imports. No Step 07 request handling or generation cascade. |
| Demo | `scripts/demo_routing.py`, `make demo-routing`; executed default two-model and explicit three-model QA examples. All scores/data are synthetic; summaries include `generation_performed: false`. No frontend exists yet. |

Step 06 adds **144 tests** to the verified 474-test baseline. Issues found and fixed before completion: derived eligibility could be forged independently of hardware, two catalog IDs could alias one runtime tag, malformed metadata could cause serializer warnings, and extraction wording could outrank a code request. Regression tests cover these cases. Existing default false residency remains compatible; an explicit unknown `None` is now supported.

Dependency file hashes preserved through Step 06:

- `pyproject.toml`: `4bb480f6dbd401f3a80d9dabacb018d5bcf11b8048deccdc6094473e1ca9c67d`
- `uv.lock`: `103e8bf84436367e5242ca11cef07ea49a63014077cd7df61a7d6799dea1f1dc`

## Step 07 acceptance evidence

| Requirement | Implementation and observed coverage |
| --- | --- |
| End-to-end request workflow | `broker/chat.py`, re-exported by `broker/orchestrator.py`; revalidates inputs, sanitizes, classifies and extracts shared pre-generation features through the existing pure rules router and immutable snapshot, leases up to two candidates, then calls only `GuardedDispatcher`. Actual A/B metadata is returned. Valid open-ended output passes; typed errors distinguish unavailable, timeout, policy, validation, privacy, inspection and internal failures. |
| Local cascade and bounded compute | Eligible second model runs on provider error or structural failure, never to invent a quality judgment. Existing one-time local connection retry remains reauthorized; no cloud retry. Tests cover structured success on B, local timeout, exhausted total budget before B/cloud, privacy time counted in compute, cancellation and no deadline reset. Mock wall-clock timings are not hardware benchmarks. |
| Masking and output inspection | RequestContext retains an empty raw-prompt field and the request-only sanitizer map. Injected scanner/sanitizer failure causes zero generation. Unknown tokens, new synthetic PII and inspector faults return the fixed masked warning. Restored structured output is validated again so quoted source values cannot silently corrupt JSON. Tests check safe errors/logs and state cleanup. |
| Minimal secure approval lifetime | `broker/pending.py`; in-memory, redacted, serialization-blocked state contains sanitized session/proposal, map/context, constraints, bounded timings/lease and optional inspected best local answer. Expiry timer, decline/cancel, dispatch completion, failures, active cancellation and shutdown clear maps/receipts/leases. Response construction is validated before pending-state publication. No raw ChatRequest is retained. |
| Approval and cloud policy | Exact preview/digest/proposal/request/policy binding, atomic pop before resume, one-use gate receipt, concurrent approvals dispatch once, wrong preview/ID leaves valid pending state intact, policy change/expiry/replay rejected. Hard blocks precede proposals; no-local-model fallback needs explicit policy opt-in. Local-only, forbidden reason, missing key, no model, cloud timeout and unauthorized cloud are covered. A previously inspected valid local answer can be returned on decline/failure of a separately allowed cloud-preferred attempt. |
| Four protected API endpoints | `api/main.py`, `routes.py`, `security.py`, `server.py`; FastAPI factory starts nothing on import. Strict extra-forbid transport models, content-free validation/HTTP/framework errors, exact local Host/Origin, loopback client checks and environment-only constant-time write authorization. JSON-only writes have bounded size, declared-length validation and total upload timeout. Single-worker owner entrypoint disables proxy headers/access logs and binds numeric loopback. No wildcard CORS or browser CDN assets. |
| Honest health/metrics and disabled seams | Health reports mock/injected analyzer/roster status without runtime probes. Metrics contain essential ephemeral counts only; no history or persistent analytics, unknown cost remains null. `broker/extensions.py` reserves cache/persistence/learned-routing interfaces with immutable false flags; existing Router is the ranking seam. No cache, learning, persistence or future-step behavior is enabled. |
| Mock factory and privacy fixture | `orchestrator.build_mock_broker()` constructs only deterministic MockProviders plus a fixture scanner, with two installed synthetic locals and configured mock cloud. Model IDs/overrides are validated. Cloud has no transport/real credential. `MOCK_MODE=0` app creation fails explicitly until Step 09. Fixture scanning covers invented IDs, simple emails and card markers only; real NER quality is unverified. |
| Tests and evidence | **63 new API integration tests + 8 new lifetime tests = 71 added tests**, all marked privacy; previous 618 tests preserved. Full gate and deterministic in-process smoke executed after the cleanup fix. Actual OpenAPI exported with the four routes, typed responses and write-token security metadata. CI command now includes smoke; hosted CI execution remains NOT_RUN. |

Current dependency-file SHA-256 values after the necessary Step 07 FastAPI/Uvicorn additions:

- `pyproject.toml`: `0dccd01806effb520de79ae2ea3adde0667f9d88961ba1acdd5c2520337af138`
- `uv.lock`: `db36f33709aeb2efb3506f519f4275abea083a9e21ae4e480307d30a4edc71e1`

Step 07 is complete against its offline/mock acceptance gate. Optional owner API execution was not run. Step 08 implementation and evidence follow; Step 09 remains out of scope.

## Step 08 acceptance evidence

| Requirement | Implementation and observed coverage |
| --- | --- |
| Minimal Vite/React/TypeScript workspace | `frontend/src/App.tsx`, `components/Answer.tsx`, `components/ApprovalDialog.tsx`, local CSS/SVG. Responsive single-chat workspace with local-only default, unspecified sensitivity, task hint, optional existing JSON-field constraints, bounded input, route/model/version/privacy/timing/cache/cost/status display and connection retry. No dashboard, model manager, history or future feature implementation. |
| Actual generated API types and typed client | `src/api/generated.ts` is generated by pinned openapi-typescript **7.13.0** from the actual FastAPI export using the root `.venv`. `make generate-api`, `make check-api` and CI drift check present; synthetic drift actually rejected. Client uses generated types plus strict runtime Zod decoding, fixed same-origin paths, bounded waits, safe errors, no redirects/credentials/retries. No upstream validation/error input is shown. |
| Exact one-time approval UX | Native modal dialog displays unchanged sanitized preview, target/reason/expiry and optional best local answer. Decline receives initial focus; approve sends exact preview/digest/proposal once. Countdown/expiry disables approval and cancels; Escape cancels; completion restores input focus. Backend policy/atomic receipt remains authoritative. RTL and actual broker/Playwright tests cover approve/decline/expiry/replay; duplicate UI approval is prevented. |
| Loading, cancellation and in-memory privacy | App state has no browser persistence, logging, beacon or HTML execution. Clear session drops visible content; Stop waiting discards late results and cancels late proposals rather than authorizing them. Active server compute remains deadline-limited; unload/expiry provide bounded cleanup. Network/approval failures show safe messages. Unit and browser checks assert empty local/session storage and escaped provider HTML. |
| Protected local UI connection | `api/ui_bridge.py`, optional `server.py --ui`, Node-only `frontend/scripts/ui-proxy.mjs`. Backend generates a process-only local write credential if needed and shares it with the local Vite process over an owner-private Unix socket (0700 directory/0600 socket). No credential file, new HTTP auth endpoint, frontend key or browser credential header. Vite accepts exact loopback Host/Origin and rejects cross-site writes before injecting the credential server-side. Backend also validates the UI origin. Tests cover auth negatives, credential confinement, IPC failures and owned cleanup; no OS isolation claim. |
| Stable commands and reproducible setup | Implemented `make run-backend`, `make run-frontend`, `make test-ui`, `make generate-api`, `make check-api`. `make install` retains uv locked root-env sync and adds project-local npm ci; no dependency installation in lint/test/smoke. Direct packages pinned (React **19.3.1**, Vite **8.3.8**, TypeScript **5.9.3**, Vitest **5.1.1**, Playwright **1.65.1**, Zod **3.25.76**). Existing Python lock/project files preserved byte-for-byte. CI adds pinned Node setup and mock UI/type checks; hosted run NOT_RUN. |
| Unit/integration/browser evidence | **7 added Python privacy/IPC tests** preserve the 689-test baseline. **36 frontend tests** include RTL, client, proxy and browser-source/bundle credential negatives. **3 Playwright tests** serve built static UI locally, intercept API requests and forward them to the actual FastAPI/broker via a private stdio ASGI driver with mock providers. External browser requests are blocked; no real provider/client/listening API runtime is started. Expiry/malformed-response cases are explicitly synthetic fault injection. |
| Visual demonstration and owner checkpoint | Actual desktop, mobile and approval screenshots created by passing Playwright tests and inspected. No fabricated screenshot/model output. Owner subsequently reported frontend launch; read-only checks of the live backend and Vite proxy return HTTP 200 with `status: ok`, `mode: mock`, two eligible locals. The owner chat/approval/expiry walkthrough is **PARTIAL / PENDING**; commands, expected outcomes and requested feedback below. Implementation and automated gate pass; owner review must be recorded before treating this checkpoint as fully signed off. |

Step 08 implementation and automated checks are complete. Owner visual review remains pending. **Next implementation step is Step 09; not started and not authorized automatically.**

Owner follow-up (2026-11-20): reported `invalid_request` JSON after the first command and that the frontend appeared. Read-only live checks using root `.venv` HTTPX (no inference) observed `GET http://127.0.0.1:8000/` **404**, `GET http://127.0.0.1:8000/api/health` **200**, and `GET http://127.0.0.1:5173/api/health` **200**. Both health bodies report `ok`, `mock`, `synthetic_fixture`, two eligible local models. The API root has no page and uses the generic safe error shape; if that was the URL opened, the shown error is expected. Backend/Vite startup and proxy connection are verified; owner chat, approval and expiry outcomes are still pending. Initial sandbox read attempts could not reach the loopback services; the authorized read-only retry succeeded. No server was started/stopped and no chat/generation request was made for this diagnosis.

## Step 09 acceptance evidence

| Requirement | Implementation and observed coverage |
| --- | --- |
| Safe hardware probe | `model_management/hardware.py`; platform/arch, psutil RAM/available, disk for project and owner-declared model store (path hashed, not exported), allowlisted Metal/CUDA facts; unknowns stay null. Real probe observed above. Low-memory/unknown fixtures in `tests/model_management/test_hardware.py`. |
| Catalog and model selection | `backend/config/catalog.yaml`: placeholders retained (unverified), plus owner-selected verified `qwen25-1-5b` (general), `qwen25-coder-1-5b` (specialist) and catalog-only third `qwen25-0-5b` (not downloaded). Source must be the official library registry manifest URL with revision == pinned digest. `memory_bytes` (2 GiB / 1 GiB) are conservative estimates, **not measurements**; capability scores unknown. Only macos-arm64 listed. |
| Bound initial install | `model_management/initial_install.py`, `OllamaLocalControl.install_authorized`; plan bound to manifest/digest/inventory/hardware/store/endpoint with 10-minute expiry, typed `APPROVE <digest>` TTY confirmation, single-use, re-fetches registry manifest and re-hashes it before `/api/pull`, post-install digest verification before eligibility; no delete/prune/unload path; existing tag with different digest is a conflict. |
| Real broker and gated smoke | `orchestrator.build_real_broker`, `broker/local_runtime.py`, `broker/real_smoke.py`; requires two verified eligible models, observed hardware and real Presidio; per-call resource preflight; no warm-up/force-unload; smoke/feasibility print planned calls and need typed `RUN LOCAL <digest>`. Mock fallback only when explicitly allowed (API), never in the owner CLI. |
| Tests | Offline only: hardware fixtures, install rejection/unverified digest/conflicts, CLI fixture isolation, mocked smoke/feasibility fields. Catalog tests updated so placeholders stay ineligible and verified entries must pass `trusted_manifest`; a verified tag observed with a wrong digest stays ineligible. |
| Real execution | **WAITING_FOR_OWNER**: model downloads, real smoke, feasibility and real-mode UI need interactive approvals in the owner terminal. |

## Conversational MVP evidence (owner-run real mode, 2026-11-25)

| Requirement | Implementation and observed coverage |
| --- | --- |
| Detection entity allowlist | `privacy/analyzer.DETECTED_ENTITIES` restricts Presidio to identifiers. Presidio's predefined set includes spaCy NER categories that match ordinary vocabulary; before this, observed real-mode `What is the capital of France?` masked `LOCATION` and the model replied `I'm sorry, but I can't assist with that.`, and `What day is it today?` was withheld by the output inspector. After: `The capital of France is Paris.` Email + card prompt still masked `EMAIL_ADDRESS`, `CREDIT_CARD`. **PERSON stays masked**, so prompts naming a real person are still placeholdered; widening or narrowing is a YAML-free edit of that tuple and is deliberately not reachable from the browser API. |
| Multi-turn conversation | `ChatRequest.history` (`ConversationTurn`, ≤20 turns, ≤24000 chars total) composed by `broker/chat.compose_prompt` **before** sanitization, so prior turns are masked on every turn and a repeated value keeps one placeholder. Observed real two-turn exchange: `What are the first 3 prime numbers?` → `Now add them together.` → `2 + 3 + 5 = 10` on `qwen25-1-5b`. |
| Clock in prompt | `compose_prompt` prepends an observed local date/time header. Observed: `Today's date is Wednesday, 25 November 2026.` The header changes each minute, so an enabled exact cache will miss far more often. |
| Conversation UI | `frontend/src/App.tsx` keeps a turn transcript in React state only (no storage), clears the composer on send, trims history to the server bound, Enter sends and Shift+Enter inserts a newline. Request options, consent toggles, cloud keys and telemetry moved into a settings dialog. |
| Cloud destination | `backend/config/settings.yaml` declares xAI Grok with **no** `api_key_variable`; `/api/cloud` observed `key_available: false, key_source: none`. PUT/DELETE key routes observed flipping `runtime`/`none`. No real cloud request was made. |
| Gates | `make release-check` passed: ruff/mypy/secret-scan, **968 passed** pytest, **430 passed** privacy, **55 passed** UI unit, **3 passed** Playwright, split verification, security audit **0 findings**. |
| Not implemented | Live-data questions (weather, news, "what is on my calendar") need tool/function calling, which the plan excludes from MVP scope. The model answers these from training data or declines; it cannot fetch. |

## Request-path audit (2026-11-25)

Walked validate → compose → mask → classify → route → generate → inspect looking
for limits set independently of each other. Six defects fixed, one reported.

| # | Defect | Evidence | Fix |
| --- | --- | --- | --- |
| 1 | Sanitizer capped input at 8000 chars, but it now receives the composed transcript (header + history + prompt). Every multi-turn request past ~8k chars, and any single prompt at the advertised 8000 limit, failed closed as `privacy_failed`. | Observed: prompt 7950 → composed 8016 → `PrivacyError`. | Cap is now `COMPOSED_CHARACTER_LIMIT`, derived from the prompt and history budgets. |
| 2 | `max_output_tokens` defaulted to 512, truncating answers mid-sentence. | Observed on "Generate an essay on world war 2". | Default `DEFAULT_MAX_OUTPUT_TOKENS` = 1024. Observed 606 output tokens, 3245 chars, ends cleanly. |
| 3 | `REAL_CONTEXT_TOKENS` was 2048 while history allowed 24000 chars (~6000 tokens). Ollama truncates past `num_ctx` **silently**, so long conversations lost their start with no error. | Arithmetic; budgets never reconciled. | Window 8192, history budget 12000, composed limit 20480 — one derived chain in `api/schemas.py`, asserted by `test_budget_chain_fits_inside_the_local_context_window`. Observed recall of a codeword across a 5579-char history at 1368 input tokens. |
| 4 | `OllamaProvider` rejected `context_tokens` above 4096, so (3) could not be applied. | 13 step09 failures on construction. | Ceiling raised to 16384; it guards against sizing the KV cache out of memory, and is not the working value. |
| 5 | Exact cache key is built from the prompt alone, ignoring history, so "summarise that" could hit an entry stored for the same words in a different conversation. Wrong answers, not just stale ones. | Key inputs in `cache/key.py`. | Requests carrying history are cache-ineligible until the key covers context. Cache is off by default, so this was latent. |
| 6 | Task classification read the whole composed transcript, so one code question retagged every later turn as `code` for the rest of the session. | Observed: same question classified `qa`, `code` or `summarise` purely from earlier turns. | Classification runs on the new message alone, masked separately so it still only sees trusted text; the extra map is discarded immediately. Observed `qa` restored after a code turn. |

Context cost for (3) is estimated, not measured: ~28 KiB/token KV cache for the
catalog's 1.5B models puts 8192 at roughly +170 MiB over 2048, inside the
catalog's conservative 2 GiB estimate against ~1.35 GiB measured. Confirm with
`make feasibility` before trusting it on different hardware.

**Resolved — task routing now reaches the specialist.** `RuleRouter.rank_details`
passed `task` to feature extraction but never used it: utility is quality − latency
− load, `capability_for()` returns a benchmark only when `provenance == MEASURED`,
and every catalog entry ships `provenance: unknown`. So quality was 0.0 for every
candidate, ties sorted by `model_id`, and `qwen25-coder-1-5b` was never selected —
including with an explicit Code hint.

Ranking now breaks ties on whether the catalog **declares** the task for that model,
read straight from `capability_benchmark` regardless of provenance. This is
owner-reviewed catalog metadata, not a measurement, so it is a tie-break rather
than a score term: utility and the fixed normalization are untouched, a real
utility difference still wins, and once benchmarking supplies quality scores those
decide and the tie-break stops mattering. No fabricated quality value was
introduced and the learned-router baseline is unchanged.

Observed against the live models with both warm: summarise and qa and reason →
`qwen25-1-5b`; two different code prompts → `qwen25-coder-1-5b`.

**Caveat worth knowing.** With quality and latency unknown for every candidate, the
only term that can differ in utility is the residency penalty (weight 0.1), so a
model that is already warm outranks one that is not, and the tie-break only applies
once residency matches. One observed run routed `summarise` to the coder while the
general model was still cold. This is the weights behaving as specified rather than
a defect, but it means routing is not fully stable until Step 12 benchmarking
supplies real per-task quality.

## Manual execution requirements

**Step 09 owner checkpoint (current).** Ollama must be running on 127.0.0.1:11434 (`brew services start ollama`, or `ollama serve` in another terminal). From the project root:

```sh
cd /Users/andrew/Documents/Projects/ecolm
export MODEL_STORE="$HOME/.ollama/models"
# 1. Real NLP asset (needed by real broker); see the Step 03 owner block below for details
UV_CACHE_DIR=.uv-cache UV_PROJECT_ENVIRONMENT=.venv uv add --optional privacy-model --no-sync --python .venv/bin/python --no-python-downloads 'en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl'
UV_CACHE_DIR=.uv-cache UV_PROJECT_ENVIRONMENT=.venv uv sync --frozen --group dev --extra privacy-model --python .venv/bin/python --no-python-downloads
.venv/bin/python -m pytest -q -m 'privacy and slow' tests/privacy
# 2. Downloads (~0.99 GB each); type the printed APPROVE phrase
make model-install MODEL=qwen25-1-5b
make model-install MODEL=qwen25-coder-1-5b
make catalog-status
# 3. Bounded local-only inference; type the printed RUN LOCAL phrase
make real-smoke
make feasibility
# 4. Real-mode UI (two terminals)
MOCK_MODE=0 make run-backend
make run-frontend
```

Expected: slow privacy test **1 passed**; each install prints `Installed identity verified: True; eligible: True`; catalog-status shows `eligible managed: 2`; real-smoke/feasibility write `smoke_report.json` + CSV under `evaluation/results/<run>/`. Close memory-heavy apps first if a resource check reports `memory_insufficient` (probe saw ~4.2 GB free). Share printed artifact paths and summary lines.

**Step 08 owner visual checkpoint (still pending).** Automated mock checks passed; the owner API/Vite pair has not been launched automatically. No runtime, LLM weights, cloud credentials or hardware benchmark is required. Step 03's separate real-NLP asset smoke remains optional and unverified below.

**Step 08 owner visual checkpoint is pending.** Automated mock checks passed; the owner API/Vite pair has not been launched automatically. No runtime, LLM weights, cloud credentials or hardware benchmark is required. Step 03's separate real-NLP asset smoke remains optional and unverified below.

Terminal 1 (project root):

```sh
cd /Users/andrew/Documents/Projects/ecolm
MOCK_MODE=1 make run-backend
```

Expected: `Mock API: http://127.0.0.1:8000 (one worker; no access logs)`. Keep it running. It enables the private UI bridge and creates no credential file.

Terminal 2 (same project root):

```sh
cd /Users/andrew/Documents/Projects/ecolm
make run-frontend
```

Expected: Vite prints `http://127.0.0.1:5173/`; open that exact URL. Confirm **Mock broker connected**, default **Local only**, two eligible local fixtures. Test `Hello STU-1234-5678` (restored local answer from general); `Return JSON with an answer` (specialist/local cascade). For approval, choose **Ask before cloud**, **Format**, required JSON fields **result**, then send `Return JSON about STU-1234-5678`. Confirm the exact dialog preview masks the invented ID, **Approve once** returns inspected/restored mock-cloud JSON, **Keep local** declines, and waiting for the displayed expiry blocks approval. If present, an inspected best local answer is shown. Ctrl-C stops each owner server. `Stop waiting` dismisses a result and cancels any late approval; active compute stays bounded.

Share observed connection/route/privacy/approval/expiry behavior and any safe error codes or screenshots of invented examples. Never share tokens, raw private prompts/answers or maps. The owner feedback is needed to verify this checkpoint. Do not proceed to Step 09 without a new instruction.

Optional exact reproduction commands (reuse the existing root `.venv`):

```sh
cd /Users/andrew/Documents/Projects/ecolm
.venv/bin/python --version
make help lint test test-privacy test-ui smoke export-openapi
UV_CACHE_DIR=.uv-cache uv lock --check --offline --python .venv/bin/python --no-python-downloads
```

Expected: `Python 3.11.6`; Make lists implemented targets; Ruff passes; mypy reports no issues in **83 files**; source scan has **0 findings**; main pytest reports **696 passed / 1 deselected**, privacy target **351 passed / 346 deselected**, frontend **36 passed**, Playwright **3 passed**; lock check exits zero with 68 packages. Smoke reports local A/B/cascade 200, restored PII, approval 202→200, replay 409, decline 200, write guard 403, hostile Host 400, pending approvals 0 and disabled extensions. It prints actual summary/OpenAPI paths. Smoke requires installed project-local Chromium, as below.

Run full mock smoke from any directory with `make -C /Users/andrew/Documents/Projects/ecolm smoke export-openapi`. For only the preserved Step 07 in-process API demo: `cd /Users/andrew/Documents/Projects/ecolm` then `PYTHONPATH=. .venv/bin/python scripts/smoke.py`. The README also retains the optional API-only curl walkthrough. Those owner listening-server demos are NOT_RUN; the in-process API and mock browser evidence above actually ran.

For a future checkout with the existing root environment and Node 24.11.1/npm 11.6.2, install only locked packages and the project-local browser:

```sh
cd /Users/andrew/Documents/Projects/ecolm
make install
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright" frontend/node_modules/.bin/playwright install chromium
make check-api test-ui smoke
```

Expected: uv sync uses the existing root `.venv`; npm ci installs the locked local frontend; Chromium lives under ignored `.playwright`; tests/type drift/build/browser smoke pass. Initial package/browser installation may need network access. No NLP/LLM download, cloud credential, hardware experiment or global dependency installation is involved. In this sandbox only, Unix socket/browser fixture-server binding required normal tool permission escalation; the tests still block external/provider traffic. Ordinary owner shells/CI do not have that tool sandbox.

Optional local Step 06 demonstration, from `/Users/andrew/Documents/Projects/ecolm`:

```sh
make demo-routing
PYTHONPATH=. .venv/bin/python scripts/demo_routing.py --all-installed --task qa
```

Expected: both clearly print `OFFLINE SYNTHETIC FIXTURES ONLY`; default CODE ranks `mock-specialist`, `mock-general`; explicit QA with three installed fixtures ranks `mock-general`, `mock-third`. Lease counts are 1 during use and 0 afterward; `structure_validation: valid_structure`, `factual_correctness_evaluated: False`, `generation_performed: False`. Each command prints the actual ignored summary-artifact path. No results need to be shared unless reproducing a failure; if a command fails, share its full safe terminal output, exit code and printed artifact path.

If dependencies are missing in a future checkout with the root environment already present, run `make install`; expected: `uv sync --frozen` exits zero and installs/synchronizes only the locked packages into the existing root `.venv`. Network access may be required for package downloads. Never create another environment or install globally.

Optional owner asset checkpoint, not executed here (approximately 13 MB wheel; observed platform Darwin arm64 / Python 3.11.6, locked spaCy 3.8.18):

```sh
cd /Users/andrew/Documents/Projects/ecolm
UV_CACHE_DIR=.uv-cache UV_PROJECT_ENVIRONMENT=.venv uv add --optional privacy-model --no-sync --python .venv/bin/python --no-python-downloads 'en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl'
UV_CACHE_DIR=.uv-cache UV_PROJECT_ENVIRONMENT=.venv uv sync --frozen --group dev --extra privacy-model --python .venv/bin/python --no-python-downloads
.venv/bin/python -m pytest -q -m 'privacy and slow' tests/privacy
make lint test test-privacy
```

Expected: first command records the versioned optional asset in `pyproject.toml`/`uv.lock`; second installs **en-core-web-sm 3.8.0** into the same root environment; slow selection reports **1 passed** instead of skipped (other privacy tests deselected). Fast checks remain green. This is a synthetic local privacy smoke, not generation. Retain `--extra privacy-model` for future syncs if keeping the model installed. The [versioned upstream asset](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0) is compatible with spaCy 3.8.x; real preload remains unverified until this command succeeds.

## Unresolved issues

- Step 09 closed. Feasibility partial (2/8): json-ready-v1 is a model quality limitation; sequential second-model loading blocked by conservative RAM headroom on 16 GiB hardware. Real smoke 4/4 success demonstrates both models work. Catalog `memory_bytes` (2 GiB) are conservative vs measured ~1.35 GiB. Step 08 owner visual checkpoint is still pending.
- The previous design reference named in the plan is absent; do not fabricate it.
- No Git repository/remote exists; GitHub Actions can be authored and checked locally but cannot run remotely in this directory.
- GitHub Actions hosted execution is NOT_RUN; local offline checks passed. No repository initialization, remote creation or push was performed.
- Real trained Presidio/spaCy smoke is NOT_RUN: pinned NLP asset absent; owner commands above. The blank test pipeline validates API wiring but cannot establish trained NER quality.
- No real provider endpoint was contacted; all Step 04 evidence is intercepted HTTP or deterministic mocks. No cloud rate is fabricated.
- Policy authorization, guarded dispatch, pure ranking, full mock request handling/local cascade, protected API routes, actual OpenAPI and minimal React UI now exist. Real-runtime wiring/hardware probes, lifecycle operations and ML/cache/persistence behavior remain later-step work. A digest-valid structural payload without the sanitizer issuance seal remains untrusted.
- Step 06 runtime reconciliation was tested with supplied synthetic inventories, not a real runtime. Real inference/quality/speed/hardware behavior is unverified. Unknowns remain explicit; the default latency reference clips all default fixture latencies equally and is a documented configurable ranking hypothesis.
- Owner API/Vite startup is reported and live read-only API/proxy health verified. The full owner chat/approval/expiry walkthrough remains pending. Actual mock in-process HTTP/browser tests and screenshots passed; they do not establish real-model quality, hardware performance or trained NER accuracy. Preserve the safe error boundary when adding routes: raw Pydantic `.errors()`/`.json()` may include input despite safe string representations.
