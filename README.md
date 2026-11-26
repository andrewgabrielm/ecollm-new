# ecolm — a local-first LLM broker

A single-user chat application that answers from **small language models running on your
own machine**, and only reaches a cloud provider when a local model cannot do the job and
you have allowed it. Prompts are scanned for identifiers and masked before they reach any
model; a cloud call shows you the exact text that will leave the machine, and waits for
your approval.

Everything runs on loopback. There is no account, no server component and no telemetry
that leaves the device.

---

## Quick start

### Prerequisites

| Requirement | Version | Notes |
| --- | --- | --- |
| Python | 3.11 | One `.venv` in the project root; never a global install |
| uv | 0.11.8 | Dependency management |
| Node / npm | 24.11.1 / 11.6.2 | Frontend only |
| [Ollama](https://ollama.com) | 0.17+ | Local model runtime |

### First time

```sh
uv venv --python 3.11 .venv --no-python-downloads   # only if .venv is absent
make install
```

Pull the two models the catalog has reviewed and verified:

```sh
ollama pull qwen2.5:1.5b
ollama pull qwen2.5-coder:1.5b
```

### Every time

Ollama must be running first — the broker will not start real models without it. It is
often already running, because the Ollama desktop app starts it on login. Check before
starting another one:

```sh
curl -s http://127.0.0.1:11434/api/version
```

If that prints a version, Ollama is up — skip to the next step. If it prints nothing,
start it and leave it running in its own terminal:

```sh
ollama serve
```

`Error: listen tcp 127.0.0.1:11434: bind: address already in use` just means it was
already running. Nothing is wrong; carry on.

Then, in another terminal:

```sh
MOCK_MODE=0 make app-start
```

Open **<http://127.0.0.1:5173>**.

`app-start` launches the API and the UI together, checks both ports are free, clears a
stale UI socket from a previous crash, and waits for the backend to answer before starting
the frontend. Ctrl-C stops both.

```sh
make app-status   # what is running, which ports are held
make app-stop     # stop both, clean up
make app-reset    # clean slate before starting again
```

### Cleaning up before a fresh start

If a previous run was killed rather than stopped — closed terminal, crash, `kill -9` — it
can leave a PID file, a held port or a stale UI socket behind, and the next `app-start`
then fails with a port or socket error. One command clears all of it:

```sh
make app-reset
```

It stops anything this script started, removes stale PID files and the UI socket, and
prints `Clean. Ready to start.` It is safe to run when nothing is running.

It deliberately **does not kill processes it did not start** — if something else is
holding port 8000 or 5173 it names the process and exits non-zero, so an unrelated server
is never taken down by surprise. Ollama is also left running, since it is slow to reload
and shared with anything else using it.

To clear the app's own local state as well — saved conversations, telemetry, cache — the
whole directory is disposable:

```sh
make app-reset
rm -rf var/         # deletes saved chats, telemetry and cache; not reversible
```

### Without Ollama

Omit `MOCK_MODE=0` and the app runs on synthetic mock models. Nothing is downloaded and no
inference happens — useful for working on the UI, but the answers are fixtures, not a
model. The UI says **Synthetic demo** so the two are never confused.

### Checking it works

`http://127.0.0.1:8000/api/health` reports the truth about the current state:

```json
{"status":"ok","mode":"real","eligible_local_models":2,"worker_limit":1}
```

If Ollama stops while the app is running, this becomes `"status":"degraded"` with
`"eligible_local_models":0` rather than failing silently.

---

## How a request flows

The order is fixed, and the policy gate is the only door to generation. Routing cannot
skip it, and neither can a provider.

```
your message
    │
    ▼
 validate ─────────── reject malformed input, bounded sizes
    │
    ▼
 compose ──────────── current date + prior turns + new message, as one text
    │
    ▼
 mask ─────────────── scan for identifiers, substitute placeholders
    │                 (fails closed: no mask, no generation)
    ▼
 classify ─────────── task category, from the new message only
    │
    ▼
 route ────────────── rank eligible local models into an immutable plan
    │
    ▼
 POLICY GATE ──────── the only path to a model
    │
    ├── local cascade ──── model A, then model B if A fails validation
    │
    └── cloud fallback ─── only if: local failed
                            AND mode allows it
                            AND no hard-blocked identifier is present
                            AND (in ask mode) you approved this exact text
    │
    ▼
 inspect ──────────── check the answer before you see it
    │
    ▼
 restore ──────────── put your real values back, locally
```

**Masking before routing** is deliberate: the sanitized text is the only thing that becomes
trusted input, so no component downstream can ever see the raw prompt.

---

## Features

### Chat

- Multi-turn conversation with real memory — follow-ups like "now add them together" work.
- Enter sends, Shift+Enter for a newline.
- The model is told the current date, so "what day is it" is answered rather than guessed.
- Each answer shows which model produced it, the route taken, why that route, and latency.

### Local models

Models are pinned by digest in `backend/config/catalog.yaml` and verified against that
digest after download. Two are installed by the quick start:

| Model | Declared for | |
| --- | --- | --- |
| `qwen2.5:1.5b` | qa, summarise, reason | installed |
| `qwen2.5-coder:1.5b` | code, format, extract | installed |
| `qwen2.5:0.5b` | qa, classify | reviewed, not downloaded |

If the first model returns structurally invalid output (for example, malformed JSON when
JSON was required), the broker cascades to the second.

### Privacy

Detection is restricted to identifiers matched by **regex or checksum** — email, phone,
credit card (Luhn-validated), SSN, IBAN, passport, driver's licence, bank number, ITIN,
crypto and IP addresses. Those are masked before any model sees them and restored only in
the answer shown to you.

Ordinary words are **not** masked. Presidio's name/place/organisation recognisers are
switched off because, with the small spaCy model, they tag ordinary vocabulary — they
read "Generate" as a person's name and "France" as a location, which replaced the words
the model needed and made it refuse to answer.

**The trade-off this makes:** a person's name typed into a prompt is not masked, so it
reaches an authorized cloud provider as written. The entity list is a single tuple in
`backend/privacy/analyzer.py` and is deliberately not reachable from the browser.

### Cloud providers (optional, off by default)

Settings → Cloud providers. Groq is seeded; any OpenAI-compatible
`/chat/completions` endpoint can be added from the UI.

1. Paste an API key.
2. The panel asks that provider which models the key can reach, and offers them as a list.
3. Pick one.

Keys live in process memory only — never written to disk, never echoed back, cleared on
restart. Cloud is reached only on local failure, and only in a mode that allows it.

Adding a destination widens where your text may travel, so it requires the local write
token, must be `https`, and must use a DNS hostname — address literals are refused, so the
cloud path cannot be pointed at loopback, a private range or a cloud metadata service.

### Saved chats (optional, off by default)

Settings → **Save chat history**. Conversations persist in local SQLite and appear in the
sidebar. This is the only feature that writes prompts and answers to disk. Turning it off
deletes every saved conversation immediately.

### Dashboard

Request counts, routes and latency, opt-in and aggregate-only. Prompt and answer text
never reach it.

---

## What is stored, and where

| Data | Location | Default |
| --- | --- | --- |
| Current conversation | Browser memory | Always |
| Saved conversations | `var/broker.sqlite3` | **Off** |
| Cloud API keys | Process memory | Never persisted |
| Placeholder maps | Process memory, request-scoped | Never persisted |
| Telemetry | `var/broker.sqlite3` | **Off** |
| Answer cache | `var/broker.sqlite3` | **Off** |

Everything under `var/` is git-ignored. Deleting it resets the app.

---

## Project layout

```
backend/
  api/               FastAPI routes, request/response contracts, loopback guards
  broker/            Orchestrator, policy gate, routers, classifier
  privacy/           Analyzer, sanitizer, placeholder map, output inspector
  providers/         Ollama adapter, OpenAI-compatible cloud adapter, mocks
  model_management/  Catalog, hardware probe, registry, install lifecycle
  storage/           SQLite connection, migrations, conversations, destinations
  cache/  telemetry/  profiles/  learning/  config/
frontend/src/        React UI, generated API types, runtime-validating client
evaluation/          Benchmark fixtures and results
var/                 Local state (git-ignored)
```

The TypeScript API client is **generated from the live FastAPI schema**
(`make generate-api`); `make check-api` fails the build on drift, so the frontend cannot
disagree with the backend about a contract.

---

## Development

```sh
make lint            # ruff, strict mypy, secret scan, tsc, prettier, contract drift
make test            # offline pytest, sockets disabled
make test-privacy    # privacy suite
make test-ui         # TypeScript, RTL, production build
make smoke           # mock API + Playwright
make release-check   # all of the above
make help            # every implemented target
```

No test contacts a real endpoint, starts a runtime, or runs inference. Cloud and local
provider behaviour is exercised through intercepted HTTP.

Owner-run commands that touch real hardware or download anything are separate and
interactive: `profile-hardware`, `catalog-status`, `model-install`, `real-smoke`,
`feasibility`.

---

## Known limitations

These are real and currently true. None of them fail silently.

- **Task routing does not pick the specialist model.** The router scores candidates on
  quality, latency and load, but every catalog entry has `quality: null` because
  benchmarking has not been run. With all scores unknown, ranking falls back to model id,
  so `qwen2.5-coder` is never selected — including when you set the Code task hint.
- **No tool calling.** Live data — weather, news, your calendar — cannot be fetched. The
  model answers from training data or declines. A cloud provider does not change this.
- **Small models.** 1.5B parameters. Fast and private, noticeably weaker than a
  frontier model at reasoning and long-form writing.
- **One request at a time.** The real broker runs a single worker.
- **Answer cache skips conversations.** The cache key does not include history, so any
  request carrying prior turns bypasses the cache rather than risk returning an answer
  from a different conversation.
- **Context is 8192 tokens.** Long conversations are trimmed oldest-first by the client,
  within limits that are derived from the window so they cannot silently overflow.

`BUILD_STATUS.md` records observed evidence for each of these, including the commands run
and what they printed.
