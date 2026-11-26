import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  createClient,
  type ChatApi,
  type ChatRequest,
  type ChatResponse,
  type ConversationSummary,
  type Health,
  type Outcome,
  type Proposal,
} from "./api/client";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { Answer } from "./components/Answer";
import { Markdown } from "./components/Markdown";
import { CloudSettings } from "./components/CloudSettings";
import { Dashboard } from "./components/Dashboard";
import { Settings } from "./components/Settings";

const defaultApi = createClient();
type Mode = NonNullable<ChatRequest["mode"]>;
type Scope = NonNullable<ChatRequest["sensitivity_scope"]>;
type Task = NonNullable<ChatRequest["task_hint"]>;
type HistoryTurn = NonNullable<ChatRequest["history"]>[number];

// A restored conversation has no live metadata — it was not generated this
// session — so `response` is absent there rather than filled with invented
// timings, and the transcript renders those turns as plain text.
export type Turn = {
  key: string;
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse;
};

// Mirrors HISTORY_TURN_LIMIT / HISTORY_CHARACTER_BUDGET in backend/api/schemas.py.
// Trimming here keeps a long session sending valid requests instead of collecting
// 422s once the transcript outgrows the server bound.
export const HISTORY_TURN_LIMIT = 20;
export const HISTORY_CHARACTER_BUDGET = 12000;

export function historyFrom(turns: Turn[]): HistoryTurn[] {
  const recent: HistoryTurn[] = [];
  let characters = 0;
  for (const turn of [...turns].reverse()) {
    if (recent.length >= HISTORY_TURN_LIMIT) break;
    if (characters + turn.text.length > HISTORY_CHARACTER_BUDGET) break;
    characters += turn.text.length;
    recent.unshift({ role: turn.role, content: turn.text });
  }
  return recent;
}

const modes: Record<Mode, string> = {
  local_only: "Local only",
  ask_before_cloud: "Ask before cloud",
  auto_cloud: "Allow automatic cloud",
  cloud_only: "Cloud only",
};
const modeNotes: Record<Mode, string> = {
  local_only: "Generation stays with eligible models on this device.",
  ask_before_cloud:
    "A permitted cloud fallback needs your approval of the exact sanitized text.",
  auto_cloud:
    "A permitted fallback may send sanitized text to cloud without another prompt.",
  cloud_only:
    "Skips local models entirely. Every request goes to the configured cloud provider, which needs a key and a chosen model. Hard-blocked identifiers are still refused.",
};
const taskNames: Record<Task, string> = {
  qa: "Question & answer",
  summarise: "Summarise",
  extract: "Extract",
  classify: "Classify",
  reason: "Reason",
  format: "Format",
  code: "Code",
};
const examples = [
  "What is the capital of France?",
  "Write a Python function that reverses a list",
  "Summarise what we discussed",
];

export function App({ api = defaultApi }: { api?: ChatApi }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [connection, setConnection] = useState<
    "checking" | "ready" | "offline"
  >("checking");
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<Mode>("local_only");
  const [scope, setScope] = useState<Scope>("unspecified");
  const [task, setTask] = useState<Task | "">("");
  const [jsonFields, setJsonFields] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState<Proposal | null>(null);
  const [phase, setPhase] = useState<"idle" | "sending" | "approving">("idle");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [configOpen, setConfigOpen] = useState(false);
  const [saved, setSaved] = useState<ConversationSummary[]>([]);
  const [savingEnabled, setSavingEnabled] = useState(false);
  const [conversationId, setConversationId] = useState<string>(() =>
    crypto.randomUUID(),
  );
  const generation = useRef(0);
  const mounted = useRef(true);
  const controllers = useRef(new Set<AbortController>());
  const pendingRef = useRef<Proposal | null>(null);
  const turnsRef = useRef<Turn[]>([]);
  const input = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busy = phase !== "idle";
  const locked = busy || pending !== null;

  useEffect(() => {
    turnsRef.current = turns;
  }, [turns]);
  useEffect(() => {
    if (!locked) input.current?.focus();
  }, [locked]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, busy, pending]);

  const refreshSaved = useCallback(async () => {
    try {
      const data = await api.listConversations();
      if (!mounted.current) return;
      setSavingEnabled(data.enabled);
      setSaved(data.conversations);
    } catch {
      /* The list is a convenience; chat still works without it. */
    }
  }, [api]);

  const cancelProposal = useCallback(
    async (proposal: Proposal) => {
      try {
        await api.approval(proposal.request_id, {
          proposal_id: proposal.proposal_id,
          decision: "cancel",
        });
      } catch {
        /* Server expiry remains the bounded fallback. */
      }
    },
    [api],
  );
  const reconnect = useCallback(async () => {
    setConnection("checking");
    const controller = new AbortController();
    controllers.current.add(controller);
    try {
      const value = await api.health(controller.signal);
      if (mounted.current) {
        setHealth(value);
        setConnection(value.status === "ok" ? "ready" : "offline");
      }
    } catch {
      if (mounted.current) {
        setConnection("offline");
        setHealth(null);
      }
    } finally {
      controllers.current.delete(controller);
    }
  }, [api]);
  useEffect(() => {
    if (connection === "ready") void refreshSaved();
  }, [connection, refreshSaved, configOpen]);
  useEffect(() => {
    mounted.current = true;
    void reconnect();
    const active = controllers.current;
    return () => {
      mounted.current = false;
      generation.current++;
      for (const controller of active) controller.abort();
      if (pendingRef.current) void cancelProposal(pendingRef.current);
    };
  }, [reconnect, cancelProposal]);

  const updatePending = (proposal: Proposal | null) => {
    pendingRef.current = proposal;
    setPending(proposal);
  };
  const appendAnswer = (response: ChatResponse) => {
    setTurns((previous) => {
      const next: Turn[] = [
        ...previous,
        {
          key: `${response.request_id}-assistant`,
          role: "assistant",
          text: response.answer,
          response,
        },
      ];
      void persist(next);
      return next;
    });
  };
  // Saving is a side effect of a completed exchange, never a precondition for
  // showing it: a storage failure must not lose the answer on screen.
  const persist = (all: Turn[]) => {
    if (!savingEnabled || all.length === 0) return;
    void api
      .saveConversation(
        conversationId,
        all.map((item) => ({
          role: item.role,
          content: item.text,
          model_id:
            item.role === "assistant"
              ? (item.response?.model_id ?? null)
              : null,
        })),
      )
      .then((data) => {
        if (!mounted.current) return;
        setSavingEnabled(data.enabled);
        setSaved(data.conversations);
      })
      .catch(() => undefined);
  };

  async function openSaved(id: string) {
    if (locked) return;
    try {
      const messages = await api.readConversation(id);
      if (!mounted.current || messages.length === 0) return;
      generation.current++;
      setConversationId(id);
      setTurns(
        messages.map((item, index) => ({
          key: `${id}-${index}`,
          role: item.role,
          text: item.content,
        })),
      );
      setNotice("");
      setError("");
      setConfigOpen(false);
    } catch {
      setError("Could not open that conversation.");
    }
  }

  async function removeSaved(id: string) {
    try {
      await api.deleteConversation(id);
      await refreshSaved();
      if (id === conversationId) clear();
    } catch {
      setError("Could not delete that conversation.");
    }
  }
  function receive(outcome: Outcome) {
    if (outcome.status === "approval_required") {
      updatePending(outcome);
      setNotice("Review the cloud request before continuing.");
    } else if (outcome.status === "success") {
      updatePending(null);
      appendAnswer(outcome);
      setNotice("");
    } else {
      updatePending(null);
      setNotice("Cloud request declined. Your message was not sent onward.");
    }
  }

  async function send() {
    const message = prompt.trim();
    if (!message || message.length > 8000 || locked) return;
    const current = ++generation.current;
    const history = historyFrom(turnsRef.current);
    setError("");
    setNotice("");
    setTurns((previous) => [
      ...previous,
      { key: `${current}-user`, role: "user", text: message },
    ]);
    setPrompt("");
    setPhase("sending");
    const controller = new AbortController();
    controllers.current.add(controller);
    try {
      const requiredFields = jsonFields
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean);
      if (
        task === "format" &&
        (requiredFields.length > 16 ||
          requiredFields.some(
            (value) => !/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(value),
          ) ||
          new Set(requiredFields).size !== requiredFields.length)
      )
        throw new ApiError("invalid_request");
      const outcome = await api.chat(
        {
          prompt: message,
          mode,
          sensitivity_scope: scope,
          ...(history.length ? { history } : {}),
          ...(task ? { task_hint: task } : {}),
          ...(task === "format" && requiredFields.length
            ? {
                constraints: {
                  response_format: "json",
                  required_fields: requiredFields,
                },
              }
            : {}),
        },
        controller.signal,
      );
      if (!mounted.current || generation.current !== current) {
        if (outcome.status === "approval_required")
          await cancelProposal(outcome);
        return;
      }
      receive(outcome);
    } catch (failure) {
      if (mounted.current && generation.current === current) {
        setError(
          failure instanceof ApiError
            ? failure.message
            : new ApiError("network").message,
        );
        setNotice("");
      }
    } finally {
      controllers.current.delete(controller);
      if (mounted.current && generation.current === current) setPhase("idle");
    }
  }

  async function decide(decision: "approve" | "decline" | "cancel") {
    if (!pending || busy) return;
    const proposal = pending;
    const current = ++generation.current;
    setPhase("approving");
    setError("");
    try {
      const outcome = await api.approval(proposal.request_id, {
        proposal_id: proposal.proposal_id,
        decision,
        ...(decision === "approve"
          ? {
              sanitized_preview: proposal.sanitized_preview,
              digest: proposal.digest,
            }
          : {}),
      });
      if (mounted.current && generation.current === current) receive(outcome);
    } catch (failure) {
      if (mounted.current && generation.current === current) {
        updatePending(null);
        void cancelProposal(proposal);
        setError(
          failure instanceof ApiError
            ? failure.message
            : new ApiError("network").message,
        );
        setNotice("");
      }
    } finally {
      if (mounted.current && generation.current === current) {
        setPhase("idle");
        input.current?.focus();
      }
    }
  }

  const expire = useCallback(() => {
    const proposal = pendingRef.current;
    if (!proposal) return;
    pendingRef.current = null;
    setPending(null);
    setError("Approval expired. Send another message to try again.");
    setNotice("");
    void cancelProposal(proposal);
    input.current?.focus();
  }, [cancelProposal]);

  function clear() {
    generation.current++;
    if (pendingRef.current) void cancelProposal(pendingRef.current);
    updatePending(null);
    setTurns([]);
    setPrompt("");
    setPhase("idle");
    setError("");
    setConversationId(crypto.randomUUID());
    setNotice("Started a new conversation.");
    input.current?.focus();
  }

  return (
    <div className="workspace">
      <aside className="sidebar" aria-label="Workspace information">
        <a className="brand" href="/" aria-label="Ecolm home">
          <img src="/icon.svg" width="36" height="36" alt="" />
          ecolm<span>LOCAL WORKSPACE</span>
        </a>
        <div className="workspace-label">WORKSPACE</div>
        <div className="nav-current">
          <span aria-hidden="true">◯</span> Local chat
        </div>
        {savingEnabled && (
          <nav className="saved-list" aria-label="Saved conversations">
            <div className="workspace-label">SAVED</div>
            {saved.length === 0 ? (
              <p className="saved-empty">No saved conversations yet.</p>
            ) : (
              <ul>
                {saved.map((row) => (
                  <li
                    key={row.id}
                    className={
                      row.id === conversationId ? "current" : undefined
                    }
                  >
                    <button
                      className="saved-open"
                      onClick={() => void openSaved(row.id)}
                      disabled={locked}
                      title={row.title}
                    >
                      {row.title}
                    </button>
                    <button
                      className="saved-delete"
                      onClick={() => void removeSaved(row.id)}
                      aria-label={`Delete ${row.title}`}
                      disabled={locked}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </nav>
        )}
        <div className="sidebar-bottom">
          <div className="device-icon" aria-hidden="true">
            ⌘
          </div>
          <strong>On your terms.</strong>
          <p>
            Privacy choices for every request. Your chat stays in this session.
          </p>
          <span className="sidebar-tag">Single-user workspace</span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <span className="subtle">Workspace /</span> Local chat
          </div>
          <div className="topbar-actions">
            <div className="connection">
              <span
                className={`connection-dot ${connection}`}
                aria-hidden="true"
              />
              {connection === "ready"
                ? `${health?.mode === "mock" ? "Mock broker" : "Broker"} connected`
                : connection === "checking"
                  ? "Connecting…"
                  : "Broker unavailable"}
              {connection === "offline" && (
                <button onClick={() => void reconnect()}>Reconnect</button>
              )}
            </div>
            <button
              className="quiet"
              onClick={() => setConfigOpen(true)}
              aria-haspopup="dialog"
            >
              Settings
            </button>
            <button
              className="quiet"
              onClick={clear}
              disabled={phase === "approving" || turns.length === 0}
            >
              New chat
            </button>
          </div>
        </header>
        <div className="main-layout">
          <section className="chat-area" aria-label="Local chat">
            <div className="demo-banner">
              <span className="pill">
                {health?.mode === "real" ? "Local runtime" : "Synthetic demo"}
              </span>
              <p>
                {health?.mode === "real"
                  ? "Verified local models. Responses are inspected and restored on this device."
                  : health?.notice === "ollama_unavailable_using_mock"
                    ? "Ollama is unavailable. This session is using synthetic mock models."
                    : health?.mode === "injected"
                      ? "Injected broker. Verify its configured providers before use."
                      : "Mock models and a fixture privacy scanner. Use invented examples."}
              </p>
            </div>
            <div className="conversation" aria-busy={busy}>
              {turns.length === 0 && !busy && !pending ? (
                <div className="empty-state">
                  <div className="empty-symbol" aria-hidden="true">
                    ✳
                  </div>
                  <h2>What's on your mind?</h2>
                  <p>
                    Ask anything. Local models answer first; cloud only runs if
                    you allow it and local cannot.
                  </p>
                  <div className="examples">
                    {examples.map((example) => (
                      <button
                        key={example}
                        onClick={() => {
                          setPrompt(example);
                          input.current?.focus();
                        }}
                      >
                        {example}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <ol className="transcript">
                  {turns.map((turn) =>
                    turn.role === "user" ? (
                      <li key={turn.key} className="turn turn-user">
                        <span className="turn-role">You</span>
                        <div className="bubble">{turn.text}</div>
                      </li>
                    ) : (
                      <li key={turn.key} className="turn turn-assistant">
                        <span className="turn-role">
                          {turn.response?.mock ? "Mock model" : "Assistant"}
                        </span>
                        {turn.response ? (
                          <Answer result={turn.response} />
                        ) : (
                          <article className="answer">
                            <Markdown text={turn.text} />
                          </article>
                        )}
                      </li>
                    ),
                  )}
                </ol>
              )}
              {busy && (
                <div className="loading">
                  <span className="spinner" aria-hidden="true" />
                  <p>
                    {phase === "sending"
                      ? "Working through the local broker…"
                      : "Finishing your decision…"}
                  </p>
                </div>
              )}
              <p className="live-status" role="status">
                {notice}
              </p>
              {error && (
                <div className="error" role="alert">
                  {error}
                </div>
              )}
              <div ref={bottom} />
            </div>
            <form
              className="composer"
              onSubmit={(event) => {
                event.preventDefault();
                void send();
              }}
            >
              <label htmlFor="prompt">Your message</label>
              <textarea
                ref={input}
                id="prompt"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
                maxLength={8000}
                rows={3}
                placeholder="Ask anything…  (Enter to send, Shift+Enter for a new line)"
                disabled={locked}
                aria-describedby="prompt-help"
              />
              <div className="composer-footer">
                <span id="prompt-help">
                  {prompt.length.toLocaleString()} / 8,000 characters · Session
                  memory only
                </span>
                {phase === "sending" ? (
                  <button
                    type="button"
                    onClick={() => {
                      generation.current++;
                      setPhase("idle");
                      setNotice(
                        "Stopped waiting. Running work remains deadline-limited.",
                      );
                    }}
                  >
                    Stop waiting
                  </button>
                ) : (
                  <button
                    className="primary"
                    type="submit"
                    disabled={
                      locked || !prompt.trim() || connection !== "ready"
                    }
                  >
                    Send message
                  </button>
                )}
              </div>
            </form>
            <p className="below-composer">
              Local-first by default. Responses can be inaccurate; structure
              checks do not verify facts.
            </p>
          </section>
        </div>
        <footer className="page-footer">
          <span>ecolm · Local-first LLM broker</span>
          <span>
            {health?.mode === "real" ? "Local workspace" : "Mock workspace"} ·
            Session only
          </span>
        </footer>
      </main>
      {configOpen && (
        <div
          className="modal-backdrop"
          onClick={() => setConfigOpen(false)}
          role="presentation"
        >
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="config-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <h2 id="config-title">Settings</h2>
              <button
                className="quiet"
                onClick={() => setConfigOpen(false)}
                aria-label="Close settings"
              >
                Close
              </button>
            </header>
            <div className="modal-body">
              <section className="config-group">
                <span className="eyebrow">For each request</span>
                <label htmlFor="mode">Privacy mode</label>
                <select
                  id="mode"
                  value={mode}
                  onChange={(event) => setMode(event.target.value as Mode)}
                  disabled={locked}
                >
                  {Object.entries(modes).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <p className="field-note">{modeNotes[mode]}</p>
                <label htmlFor="scope">Sensitivity</label>
                <select
                  id="scope"
                  value={scope}
                  onChange={(event) => setScope(event.target.value as Scope)}
                  disabled={locked}
                >
                  <option value="unspecified">Unspecified</option>
                  <option value="non_sensitive">Non-sensitive</option>
                  <option value="confidential">Confidential</option>
                </select>
                <p className="field-note">
                  A request label. Scanning and policy checks still apply.
                </p>
                <label htmlFor="task">Task hint</label>
                <select
                  id="task"
                  value={task}
                  onChange={(event) => setTask(event.target.value as Task | "")}
                  disabled={locked}
                >
                  <option value="">Detect automatically</option>
                  {(Object.keys(taskNames) as Task[]).map((value) => (
                    <option key={value} value={value}>
                      {taskNames[value]}
                    </option>
                  ))}
                </select>
                <p className="field-note">
                  Guide the router without changing privacy policy.
                </p>
                {task === "format" && (
                  <>
                    <label htmlFor="json-fields">Required JSON fields</label>
                    <input
                      id="json-fields"
                      value={jsonFields}
                      onChange={(event) => setJsonFields(event.target.value)}
                      maxLength={256}
                      placeholder="e.g. answer, result"
                      disabled={locked}
                    />
                    <p className="field-note">
                      Optional comma-separated field names. Checks structure
                      only.
                    </p>
                  </>
                )}
              </section>
              <Settings api={api} connection={connection} />
              <CloudSettings api={api} connection={connection} />
              <Dashboard api={api} connection={connection} />
              <div className="session-note">
                <strong>Just this session</strong>
                <p>
                  Conversation lives in this browser tab only. No chat history
                  is written to disk or browser storage.
                </p>
                <span>
                  {health
                    ? `${health.eligible_local_models} eligible local models`
                    : "Model status unavailable"}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
      {pending && (
        <ApprovalDialog
          proposal={pending}
          busy={phase === "approving"}
          onDecision={(decision) => void decide(decision)}
          onExpire={expire}
        />
      )}
    </div>
  );
}
