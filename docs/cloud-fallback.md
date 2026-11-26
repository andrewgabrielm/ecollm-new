# Cloud fallback — setup, status and test coverage

**Default behaviour.** Local-only. Every request runs through the eligible
local models (one primary + at most one cascade) and never leaves the device
unless the owner has explicitly opted the request in through the approval
flow.

**Current implementation status (2026-11-25).** The cloud *adapter*, the
policy gate, the mock destination, the `ask_before_cloud` approval flow and
the `auto_cloud` silent fallback path are all implemented and covered by
automated mock-HTTP tests. `build_real_broker` now wires a `CloudProvider`
automatically when (a) [backend/config/settings.yaml](../backend/config/settings.yaml)
declares a `cloud_destination` block and (b) the referenced environment
variable supplies a non-empty API key. A missing destination or missing key
keeps the broker local-only — identical to earlier behaviour.

Real network traffic to a cloud destination still requires owner action:
declare the destination, export the key, start `MOCK_MODE=0`. The
automated gate stays offline-only.

## Where the pieces live

| Concern | File | What it does |
|---|---|---|
| Destination contract | [backend/providers/cloud_openai_compat.py:46](../backend/providers/cloud_openai_compat.py#L46) | `CloudDestination` — exact HTTPS completion URL, optional same-origin health URL, API-key *env variable name*, pinned `model_id`/`model_version`/`runtime_model`. |
| Adapter | [backend/providers/cloud_openai_compat.py:98](../backend/providers/cloud_openai_compat.py#L98) | `CloudProvider` — reused async HTTPX client; ignores proxy/TLS env overrides; rejects redirects; no internal retries; reads the key only from the environment; never logs the key. |
| Response contract | same file | `_Completion` / `_Choice` / `_Message` — strict Pydantic; rejects unknown fields; one-choice completions only; token usage optional. |
| Mock destination | [backend/broker/orchestrator.py:574](../backend/broker/orchestrator.py#L574) | `mock-cloud` registered with `route="cloud"`. Used for every automated test. |
| Real wiring | [backend/broker/orchestrator.py:630](../backend/broker/orchestrator.py#L630) | `build_real_broker` — **does not construct a cloud provider today**. |
| Policy gate | [backend/broker/policy_gate.py](../backend/broker/policy_gate.py) | The only authority allowed to permit a cloud call. Enforces mode, approval state, hard-block entity types, allowed fallback reasons, deadlines. |
| Approval flow | [backend/broker/chat.py](../backend/broker/chat.py) | Builds the atomic one-use sanitized preview + approval binding; consumed exactly once. |
| Request mode | [backend/contracts.py:19](../backend/contracts.py#L19) | `ExecutionMode = local_only | ask_before_cloud | auto_cloud`. `auto_cloud` is reserved — not implemented. |
| Config shape | [backend/config/loader.py](../backend/config/loader.py) | `PolicyConfig` — `hard_block_entity_types`, `allowed_fallback_reasons`, `allow_no_local_model_cloud`, `cloud_preferred_categories`. |
| Policy file | [backend/config/policy.yaml](../backend/config/policy.yaml) | Current defaults: hard-block `CREDIT_CARD`, `US_SSN`; allow fallback on `local_error`, `validation_failure`; no-local → cloud: `false`; cloud-preferred categories: empty. |
| Settings file | [backend/config/settings.yaml](../backend/config/settings.yaml) | No cloud destination declared. |

## Request flow, with the cloud branch highlighted

```
ChatRequest  →  validate  →  sanitize (PII mask)  →  classify + features
                                                             │
                                                             ▼
                                                        Routing picks up
                                                        to 2 eligible local
                                                        candidates
                                                             │
                                             (gate)      ────▼────       (gate)
                                             local primary → ok? ──► return
                                                             │ fail/invalid
                                             local cascade → ok? ──► return
                                                             │ still failing
                                                             ▼
                                                     Cloud branch
                                                             │
                                                 ┌───────────┼───────────┐
                                                 ▼           ▼           ▼
                                           local_only  ask_before_   auto_cloud
                                                         cloud      (reserved;
                                                 return     │       not wired)
                                                 error      ▼
                                                     Return 202 +
                                                     sanitized preview
                                                             │
                                                             ▼
                                                     Owner approves once
                                                     (120 s window)
                                                             │
                                                             ▼
                                                     CloudProvider.generate
                                                     (only if a real
                                                     CloudProvider is wired —
                                                     else synthetic mock-cloud)
                                                             │
                                                             ▼
                                                     Output inspection +
                                                     local restoration
                                                     (cloud answers are
                                                     NEVER cached)
```

The whole cloud branch runs through `GuardedDispatcher.dispatch` + the
policy gate — the orchestrator is still the only generation dispatcher. See
[tests/test_dispatch_boundary.py](../tests/test_dispatch_boundary.py) for
the static audit that enforces this.

## Quick setup — xAI Grok free tier (testing)

The repo is a BYO-key broker now: the owner declares *which* cloud destination
is permitted, and the user supplies their own API key through the UI at
runtime. Keys live in process memory only — never on disk, never echoed back,
never routed through telemetry. The key clears on process restart.

### 1. Declare the destination in [backend/config/settings.yaml](../backend/config/settings.yaml)

```yaml
cloud_destinations:
  - endpoint: https://api.x.ai/v1/chat/completions
    model_id: grok-cloud
    model_version: grok-4-fast-2026-11
    display_name: xAI Grok
    runtime_model: grok-4-fast-reasoning
    # api_key_variable omitted → key comes from the UI at runtime
```

- `endpoint` must be exact (HTTPS, full completion path).
- `model_id` is the stable id used in logs, metrics, routing and cache keys.
- `model_version` is an opaque version string the owner picks; bump it when
  changing `runtime_model`.
- `runtime_model` is what gets sent in the upstream request body.
- `display_name` is what the UI shows.
- `api_key_variable` is optional. If set to an env-var name, the backend will
  pre-load that key on startup (matching older behaviour). If omitted, the
  destination has no key until the user pastes one into Settings.

Multiple destinations are allowed; the first is the primary cloud fallback,
the rest are addressable by `model_id`.

### 2. (Optional) export an env-provided key, or skip and paste at runtime

Env-loaded path:
```sh
export XAI_API_KEY='xai-...'
```
UI-loaded path: don't export anything; see step 3.

### 3. Pick an execution mode

- `local_only` *(default)* — cloud never used.
- `ask_before_cloud` — each cloud attempt returns 202 + sanitized preview; user approves once.
- `auto_cloud` — cloud dispatches automatically when the policy gate's reasons permit. **Already supported by the policy gate** (line 378 only prompts for `ask_before_cloud`). Use this if you want the "if local fails, try cloud" behaviour.

### 4. Pick policy

[backend/config/policy.yaml](../backend/config/policy.yaml). To get "local
first, else cloud" for every failure, allow more reasons:

```yaml
allowed_fallback_reasons:
  - local_error            # local provider failed
  - validation_failure     # local answer failed structural checks
  - no_local_model         # zero eligible locals
allow_no_local_model_cloud: true
cloud_preferred_categories: []    # or list tasks to try cloud first
hard_block_entity_types: [CREDIT_CARD, US_SSN]
```

`hard_block_entity_types` stays on even in `auto_cloud` mode — Grok will
never see a message with a card number or SSN. Add anything else you want
kept on-device.

### 5. Start the real broker

```sh
# Owner, from the repo root, with Ollama running:
MOCK_MODE=0 make run-backend
make run-frontend
```

Open the UI (http://127.0.0.1:5173). The **Cloud providers** panel in the
sidebar lists every declared destination with its current key status
(`key loaded from environment`, `key set this session`, or `no key set`).

### 6. (UI path) Paste the API key

For each destination, paste the key in the password field and click **Save**.
The server echoes back only the updated `key_available`/`key_source` flags,
never the value. Key state:

- `env` — loaded from the configured environment variable at startup
- `runtime` — set this session via the UI
- `none` — not set; cloud dispatch to this destination will be refused by
  the policy gate

**Clear** removes the key from backend memory. Process restart clears all
runtime keys automatically.

### 7. Send a chat

Send a request with `mode: auto_cloud`. Successful local responses never
touch Grok. A local failure, validation failure, or zero-eligible-local
request routes to Grok and the restored answer returns through the normal
inspector. Cloud answers are **never cached**.

## Setup (when you decide to enable real cloud)

Four changes, strictly in this order.

### 1. Pick a destination and declare it

Edit [backend/config/settings.yaml](../backend/config/settings.yaml). Add,
for example (adapt the exact fields to your provider):

```yaml
cloud_destinations:
  - endpoint: https://api.example.ai/v1/chat/completions
    health_endpoint: https://api.example.ai/v1/models        # optional, same origin
    model_id: provider-small
    model_version: provider-small-2026-11-01
    runtime_model: provider/small
    api_key_variable: PROVIDER_SMALL_KEY
```

Hard requirements the contract already enforces:

- `endpoint` must be HTTPS, must not be a loopback address, and must set an
  exact completion path (bare host is rejected).
- `health_endpoint`, when set, must share scheme + host + port with
  `endpoint` — no mixed-origin trickery.
- `api_key_variable` must be an uppercase `[A-Z][A-Z0-9_]{0,63}` name; the
  **value** is read from the process environment only.
- `model_id` is a stable identifier used in logs/responses; `runtime_model`
  is the vendor-visible name sent in the request body.

### 2. Export the API key, never commit it

```sh
export PROVIDER_SMALL_KEY='sk-live-…'
```

Add the variable name (not the value) to `.env.example`:

```sh
PROVIDER_SMALL_KEY=
```

The key is held in a Pydantic `SecretStr`; the adapter neither logs it nor
includes it in error payloads. Secrets are refused by the local heuristic
scanner (`make scan-secrets`).

### 3. Wire the provider into `build_real_broker`

[backend/broker/orchestrator.py:630](../backend/broker/orchestrator.py#L630)
currently constructs no cloud adapter. The change is:

```python
cloud_destinations = load_settings(environment).cloud_destinations   # new field
cloud_providers = {
    destination.model_id: CloudProvider.from_environment(destination)
    for destination in cloud_destinations
}
providers.update(cloud_providers)
broker = RealLocalBroker(
    GuardedDispatcher({**{m.id: p for m,p in local}, **cloud_providers}),
    …,
    cloud_model_id=cloud_destinations[0].model_id if cloud_destinations else None,
)
```

The orchestrator already carries a `cloud_model_id` slot; it's just `None`
in real mode today.

### 4. Choose the mode + policy

Edit [backend/config/policy.yaml](../backend/config/policy.yaml) if you
want to broaden cloud eligibility beyond the current defaults:

```yaml
allowed_fallback_reasons: [local_error, validation_failure, no_local_model]
allow_no_local_model_cloud: true            # cloud ok when 0 eligible locals
cloud_preferred_categories: [code, reason]  # always try cloud for these
```

Edit [backend/config/settings.yaml](../backend/config/settings.yaml) /
[frontend default](../frontend/src/App.tsx) if you want the request mode to
default to `ask_before_cloud` (per-call owner approval). Keep sensitive
work on `local_only`.

`auto_cloud` is **reserved and not yet implemented**. See the Gap list.

### 5. Verify

```sh
make lint test test-privacy                 # offline — must stay green
MOCK_MODE=0 make run-backend                # starts the broker with real adapters
curl -H "X-Local-Token: $LOCAL_WRITE_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"prompt":"Hi","mode":"ask_before_cloud"}' \
     http://127.0.0.1:8000/api/chat
```

Expect either a 200 (if the local primary answered) or a 202 with a
sanitized preview you can approve once. A 500/403 means the adapter is
refusing the configured destination — check logs for the safe error code
(no values are echoed).

## What the cloud branch guarantees (every scenario below is covered by a test)

1. **No cloud call before a successful sanitizer pass.** A sanitizer
   failure aborts the request, local and cloud alike.
2. **PII mask survives the hop.** Only the sanitized preview is sent; the
   placeholder map stays in the local request context and never leaves the
   process.
3. **One-use approval binding.** Each cloud proposal binds request-id,
   digest, target model/version, execution mode, policy version and
   expiry. Consumption is atomic and single-use.
4. **Replay / tamper / policy change rejected.** A proposal whose
   destination, mode, policy or digest has changed is rejected with a safe
   409.
5. **Hard-block categories.** `CREDIT_CARD` and `US_SSN` (configurable)
   suppress cloud proposals before they're returned to the client.
6. **Budgets.** Cloud dispatch uses the separate cloud deadline
   (default 30 s), not the local one. `cloud_retries = 0`: no internal
   retry.
7. **Credential safety.** Error payloads never echo the key or the
   request body.
8. **Output inspection.** Cloud answers run through the same PII inspector
   as local answers before reaching the user, and are **never cached**.
9. **Telemetry record.** When analytics are on, the typed row records
   `route=cloud`, the reason, timings, token counts — never prompt or
   answer text.

## Test cases (all automated, mock HTTP only)

| # | File | Test | What it exercises |
|---|---|---|---|
| 1 | [tests/test_providers.py](../tests/test_providers.py) | `test_cloud_destination_rejects_non_https` | Config contract — http:// and loopback destinations rejected. |
| 2 | " | `test_cloud_destination_requires_completion_path` | Bare-host endpoints rejected; exact path required. |
| 3 | " | `test_cloud_destination_health_must_share_origin` | Mixed-origin health URL rejected. |
| 4 | " | `test_cloud_destination_api_key_variable_pattern` | Env-variable name pattern enforced. |
| 5 | " | `test_cloud_provider_describe_reports_key_availability` | `describe()` reflects whether the env var is set, with no value echo. |
| 6 | " | `test_cloud_provider_generate_mocked_success` | respx mock — successful completion is parsed and returned. |
| 7 | " | `test_cloud_provider_rejects_unknown_fields` | Upstream payload with unexpected keys fails validation. |
| 8 | " | `test_cloud_provider_timeouts_and_errors` | Timeout, 401, 429, malformed JSON → typed safe errors; no retries. |
| 9 | " | `test_cloud_provider_rejects_redirects` | 3xx from the destination is a hard failure. |
| 10 | " | `test_cloud_provider_ignores_proxy_env` | `HTTPS_PROXY`/`SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` do not leak into the client. |
| 11 | [tests/privacy/test_policy_gate.py](../tests/privacy/test_policy_gate.py) | `test_local_only_blocks_cloud_proposal` | Mode `local_only` → 403 with no proposal, zero provider calls. |
| 12 | " | `test_ask_before_cloud_returns_proposal_and_preview` | Local failures with permitted reason → 202 + sanitized preview + binding. |
| 13 | " | `test_cloud_proposal_includes_sanitized_preview_only` | Proposal preview contains placeholders, never the raw prompt. |
| 14 | " | `test_hard_block_entity_suppresses_cloud_proposal` | Presence of `CREDIT_CARD` / `US_SSN` suppresses proposal entirely. |
| 15 | " | `test_proposal_bound_to_digest_and_policy` | Changed digest / policy version → consume refused. |
| 16 | " | `test_proposal_one_use_atomic` | Two concurrent consumes → exactly one succeeds, one 409. |
| 17 | " | `test_proposal_expires_after_deadline` | 120-second TTL expiry + safe cleanup. |
| 18 | " | `test_cloud_hint_requires_policy_permission` | `cloud_preferred` category request without permission → local-only. |
| 19 | " | `test_no_local_model_requires_allow_flag` | 0 eligible locals + `allow_no_local_model_cloud=false` → error, not cloud. |
| 20 | " | `test_cloud_retry_count_zero` | Budgets reject any retry; a failing cloud call is a final error. |
| 21 | [tests/test_chat_lifetime.py](../tests/test_chat_lifetime.py) | `test_decline_cancels_approval_and_releases_state` | Decline path releases request-context, roster lease, placeholder map. |
| 22 | " | `test_cloud_outputs_pass_through_inspector` | Cloud answer with new unmatched PII → masked warning, no cache. |
| 23 | " | `test_cloud_outputs_never_cached` | Successful cloud answer is not written to the exact cache. |
| 24 | [tests/integration/test_chat_api.py](../tests/integration/test_chat_api.py) | `test_approval_exact_preview_map_lifetime_resume_and_replay` | End-to-end API: 202 proposal → approval → 200 restored answer → replay 409. |
| 25 | " | `test_tampered_approval_does_not_consume_a_valid_pending_request` | Mutated proposal id / digest / preview / mode → rejection, pending stays intact. |
| 26 | " | `test_concurrent_approval_consumes_one_record_and_dispatches_once` | Thread race → one consume, one dispatch. |
| 27 | " | `test_decline_cancel_cleanup_and_zero_cloud_calls` | Decline/cancel paths → zero cloud provider invocations. |
| 28 | " | `test_pending_expiry_clears_map_lease_and_both_stores_without_resume` | Expired approval → safe cleanup, no late dispatch. |
| 29 | " | `test_policy_change_invalidates_pending_approval_and_cleans_context` | Policy change mid-approval → proposal invalidated, state cleaned. |
| 30 | " | `test_hard_blocks_and_modes_never_leak_to_cloud` | Every hard-block / mode combination → zero cloud calls. |
| 31 | [tests/integration/test_policy_egress.py](../tests/integration/test_policy_egress.py) | `test_cloud_endpoints_cannot_be_loopback` | Non-loopback contract enforced at startup. |
| 32 | " | `test_cloud_adapter_never_uses_proxy_or_cert_bundle_env` | End-to-end proxy/cert env isolation. |
| 33 | " | `test_write_token_required_for_approval` | `X-Local-Token` absent → 403 before any cloud call. |
| 34 | " | `test_write_token_mismatch_rejected` | Wrong token → 403 before any cloud call. |
| 35 | " | `test_hostile_host_header_rejected` | Non-loopback `Host` → 400 before any cloud call. |

Run the whole cloud-adjacent suite with:

```sh
make test test-privacy
```

(It's part of the default `make release-check`.)

## Manual scenarios to run once a real destination is wired

Not automated — these need a real API key and intentional network egress.
Each is a separate owner decision.

| # | Scenario | Expected outcome |
|---|---|---|
| M1 | `mode=local_only`, simple prompt | 200 from local primary. Zero cloud calls (verify via `/api/metrics` and the destination's own dashboard). |
| M2 | `mode=ask_before_cloud`, prompt that forces local validation failure | 202 with sanitized preview; approve once → 200 from cloud. Retry the same approval → 409 replay. |
| M3 | `mode=ask_before_cloud`, prompt with `STU-1234-5678` (synthetic PII) | Preview shows placeholder; cloud receives the placeholder; final answer restores the ID locally. Confirm request body on the destination has no raw ID. |
| M4 | `mode=ask_before_cloud`, prompt with a card number | Hard-block suppresses the proposal; UI shows a safe error, no cloud call. |
| M5 | Zero eligible local models, `allow_no_local_model_cloud=true` | 202 proposal; approval → 200 cloud answer. Flip the flag off and retry → 503 `unavailable`. |
| M6 | Flip `cloud_preferred_categories` to include `code`; ask a coding question | Direct cloud proposal (not a local failure); still requires approval under `ask_before_cloud`. |
| M7 | Set `CLOUD_API_KEY=` empty, restart | `describe()` reports key unavailable; proposals suppressed; `/api/health` surfaces the config issue. |
| M8 | Exceed `cloud_seconds` deadline (network stall) | Request fails with `timeout`; no retry, no cached entry, approval state released. |
| M9 | Destination returns 429 | Safe `unavailable` error; no retry; approval consumed; no cache. |
| M10 | Rotate the API key while a proposal is pending | Pending proposal still binds to the policy version; verify approval consumption still works or fails cleanly depending on whether the pre-rotation key is still accepted upstream. |

## Gap list

| # | Gap | Where | Blocks real cloud? | Status |
|---|---|---|---|---|
| G1 | No cloud destination declared | [backend/config/settings.yaml](../backend/config/settings.yaml) | Yes | **Owner config.** Declare a `cloud_destination` block; schema supported. |
| G2 | `build_real_broker` constructs no cloud provider | [backend/broker/orchestrator.py](../backend/broker/orchestrator.py) | Yes | **Done.** Conditional on declared destination + available key. |
| G3 | `CloudDestination` not part of `AppSettings` | [backend/config/loader.py](../backend/config/loader.py) | Yes | **Done.** `CloudDestinationConfig` + `AppConfig.cloud_destination`. |
| G4 | `ExecutionMode.AUTO_CLOUD` | [backend/broker/policy_gate.py](../backend/broker/policy_gate.py) | Blocks silent fallback | **Done.** Gate approves cloud without proposal when mode is `auto_cloud`; hard-blocks, allowed reasons and key check still apply. |
| G5 | Frontend `auto_cloud` selector + cloud status tile | [frontend/src/App.tsx](../frontend/src/App.tsx) | UX gap | **Done.** `CloudSettings` panel exposes per-destination key paste / clear; mode selector still needs `auto_cloud` entry. |
| G6 | `pricing.yaml` has no rates → cost field stays `null` | [backend/config/pricing.yaml](../backend/config/pricing.yaml) | Cosmetic | Pending. |
| G7 | No tests for a real destination | *(by design)* | Owner-run | Covered by manual scenarios M1–M10. |

G2–G4 are the ones closed in this session. Row 5 is purely UX polish.
Row 6 only matters if you need cost telemetry. Row 7 is an owner-run
checkpoint that AGENTS.md reserves to you.

## Things that are already handled and do **not** need new work

- Content redaction (sanitizer + inspector + request-local placeholder map).
- Approval binding to request/digest/mode/policy/expiry.
- Policy gate + guarded dispatcher — the single generation chokepoint.
- `cloud_retries = 0` budget enforcement.
- Credential handling — env-only, never logged, redacted from reprs, held
  in `SecretStr`.
- Loopback + CORS + write-token + host/origin guards on the API boundary.
- Cache correctness: cloud answers never written, never read.
- Telemetry allowlist: no prompt or answer content, ever.
- Static audit preventing any file outside the orchestrator from invoking
  a provider's `generate`/`warm_up`/`health` method.

If you want the gap list closed — say so and name a provider/endpoint,
whether default mode should be `ask_before_cloud` or `auto_cloud`, and
which fallback reasons permit automatic cloud dispatch. I will not pick
those for you.
