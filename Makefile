SHELL := /bin/sh
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHON := $(ROOT)/.venv/bin/python
export UV_PROJECT_ENVIRONMENT := $(ROOT)/.venv
export UV_CACHE_DIR := $(ROOT)/.uv-cache
export UV_PYTHON_DOWNLOADS := never
export PLAYWRIGHT_BROWSERS_PATH := $(ROOT)/.playwright
# LightGBM and presidio both load libomp on macOS. Allow their duplicate loads;
# this is a loader workaround only, no numerical correctness is affected.
export KMP_DUPLICATE_LIB_OK := TRUE
export MODEL MODEL_STORE OLLAMA_ENDPOINT

.PHONY: help check-env check-ui install lint test test-privacy test-ui ui-build check-api generate-api run-backend run-frontend app-start app-stop app-status app-reset scan-secrets demo-policy demo-routing smoke export-openapi profile-hardware catalog-status model-install real-smoke feasibility fixtures verify-splits benchmark-plan bench-dev train-router promote-router security-audit release-check

help:
	@echo "Implemented targets (Python 3.11 in root .venv; uv 0.11.8):"
	@echo "  install       Install locked dependencies into the existing root .venv (network may be needed)"
	@echo "  lint          Offline Ruff format/lint, strict mypy and repository secret scan"
	@echo "  test          Fast offline pytest; sockets disabled; slow tests excluded"
	@echo "  test-privacy  Fast offline privacy tests; real NLP model checks are explicitly slow"
	@echo "  scan-secrets  Scan repository text without printing secret values"
	@echo "  demo-policy   Offline Step 05 mock approval/replay/hard-block demonstration"
	@echo "  demo-routing  Offline Step 06 synthetic classification/ranking/lease demonstration"
	@echo "  smoke         Offline mock API and browser smoke; no real inference"
	@echo "  export-openapi Export schema from the actual routes; no server starts"
	@echo "  generate-api  Generate TypeScript contracts from actual backend routes"
	@echo "  check-api     Offline check for generated API contract drift"
	@echo "  test-ui       Offline TypeScript/RTL tests and production build"
	@echo "  run-backend   Owner: one-worker loopback API (default mock; verified real with MOCK_MODE=0)"
	@echo "  run-frontend  Owner: loopback Vite UI; start run-backend first"
	@echo "  app-start     One-shot: backend + frontend together, port/socket checks, Ctrl-C stops both"
	@echo "  app-stop      Stop anything 'app-start' started; clean up stale UI bridge socket"
	@echo "  app-reset     Clean slate before starting: stop, clear stale pid/socket, report blockers"
	@echo "  app-status    Show running pids, port state, and bridge-socket state"
	@echo "  profile-hardware Owner: read actual CPU/RAM/disk/accelerator metadata"
	@echo "  catalog-status Owner: read local runtime metadata; no generation"
	@echo "  model-install Owner: MODEL=<reviewed-id>; interactive bound download approval"
	@echo "  real-smoke    Owner: plan and approve small synthetic local-only inference"
	@echo "  feasibility   Owner: plan and approve bounded local measurements"
	@echo "  fixtures      Regenerate Step 11 benchmark fixtures under evaluation/datasets/"
	@echo "  verify-splits Verify committed fixtures match the generators and manifest"
	@echo "  benchmark-plan SPLIT=<dev_train|dev_validation> LIMIT=<n|all>   Step 12 collection plan (no dispatch)"
	@echo "  bench-dev      SPLIT=<dev_train|dev_validation> LIMIT=<n|all>   Owner: Step 12 collection run (mock by default)"
	@echo "  train-router   TRAIN_RUN=<id> VAL_RUN=<id> [ARTIFACT_ID=<id>]   Step 13 fit + validate (offline; no promotion)"
	@echo "  promote-router VERSION=<id>                                     Step 13 atomic activation of a passing artifact"
	@echo "  security-audit                                                  Step 24 offline PII/secret/pin/boundary audit"
	@echo "  release-check                                                   Aggregate all offline gates for Step 25 release"
	@echo "Frontend requires Node 24.11.1/npm 11.6.2 and locked local dependencies."
	@echo "Smoke additionally requires project-local Playwright Chromium (see README)."
	@echo "Prerequisite: uv venv --python 3.11 .venv (only if root .venv is absent)"

check-env:
	@test -x "$(PYTHON)" || { echo "Missing root .venv: run uv venv --python 3.11 .venv"; exit 1; }
	@"$(PYTHON)" scripts/check_environment.py

install: check-env
	uv sync --frozen --group dev --python "$(PYTHON)" --no-python-downloads
	cd frontend && npm ci

check-ui: check-env
	@node -e 'if(process.versions.node !== "24.11.1") { console.error("Use Node 24.11.1"); process.exit(1); }'
	@test -x frontend/node_modules/.bin/vite || { echo "Missing frontend dependencies: run make install"; exit 1; }

lint: check-ui
	"$(ROOT)/.venv/bin/ruff" check .
	"$(ROOT)/.venv/bin/ruff" format --check .
	"$(ROOT)/.venv/bin/mypy"
	"$(PYTHON)" scripts/scan_secrets.py
	cd frontend && npm run lint
	cd frontend && npm run api:check

test: check-env
	"$(PYTHON)" -m pytest

test-privacy: check-env
	"$(PYTHON)" -m pytest -m "privacy and not slow"

scan-secrets: check-env
	"$(PYTHON)" scripts/scan_secrets.py

demo-policy: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/demo_policy.py

demo-routing: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/demo_routing.py

smoke: check-env ui-build
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/smoke.py
	cd frontend && npm run test:e2e

export-openapi: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/export_openapi.py

generate-api: check-ui
	cd frontend && npm run api:generate

check-api: check-ui
	cd frontend && npm run api:check

ui-build: check-ui
	cd frontend && npm run build

test-ui: check-ui check-api ui-build
	cd frontend && npm test

run-backend: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" -m backend.api.server --ui

run-frontend: check-ui
	cd frontend && npm run dev

app-start: check-env check-ui
	$(if $(BACKEND_PORT),BACKEND_PORT="$(BACKEND_PORT)") $(if $(FRONTEND_PORT),FRONTEND_PORT="$(FRONTEND_PORT)") scripts/app.sh start

app-reset:
	scripts/app.sh reset

app-stop:
	scripts/app.sh stop

app-status:
	scripts/app.sh status

profile-hardware: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/step09.py profile-hardware

catalog-status: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/step09.py catalog-status

model-install: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/step09.py model-install

real-smoke: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/step09.py real-smoke

feasibility: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/step09.py feasibility

fixtures: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/fixtures.py generate

verify-splits: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/fixtures.py verify

benchmark-plan: check-env
	PYTHONPATH="$(ROOT)" SPLIT="$(SPLIT)" LIMIT="$(LIMIT)" RUN_ID="$(RUN_ID)" "$(PYTHON)" scripts/bench_dev.py plan

bench-dev: check-env
	PYTHONPATH="$(ROOT)" SPLIT="$(SPLIT)" LIMIT="$(LIMIT)" RUN_ID="$(RUN_ID)" "$(PYTHON)" scripts/bench_dev.py run

train-router: check-env
	PYTHONPATH="$(ROOT)" TRAIN_RUN="$(TRAIN_RUN)" VAL_RUN="$(VAL_RUN)" ARTIFACT_ID="$(ARTIFACT_ID)" "$(PYTHON)" scripts/train_router.py

promote-router: check-env
	PYTHONPATH="$(ROOT)" VERSION="$(VERSION)" "$(PYTHON)" scripts/promote_router.py

security-audit: check-env
	PYTHONPATH="$(ROOT)" "$(PYTHON)" scripts/security_audit.py

release-check: check-env check-ui
	$(MAKE) lint
	$(MAKE) test
	$(MAKE) test-privacy
	$(MAKE) test-ui
	$(MAKE) smoke
	$(MAKE) verify-splits
	$(MAKE) security-audit
	@echo "release-check: all offline gates passed"
