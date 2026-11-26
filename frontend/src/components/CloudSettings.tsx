import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ChatApi,
  CloudDestinationInfo,
  CloudDestinations,
  CloudModelOptions,
  CloudPreset,
} from "../api/client";

type Status = "idle" | "loading" | "ready" | "error";

export function CloudSettings({
  api,
  connection,
}: {
  api: ChatApi;
  connection: "checking" | "ready" | "offline";
}) {
  const [summary, setSummary] = useState<CloudDestinations | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [notice, setNotice] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [options, setOptions] = useState<Record<string, CloudModelOptions>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [adding, setAdding] = useState(false);
  const [presets, setPresets] = useState<CloudPreset[]>([]);
  const [chosen, setChosen] = useState("");
  const [form, setForm] = useState({ display_name: "", endpoint: "" });
  const mounted = useRef(true);

  const load = useCallback(async () => {
    if (connection !== "ready") return;
    setStatus("loading");
    try {
      const [data, available] = await Promise.all([
        api.getCloudDestinations(),
        api.listCloudPresets().catch(() => [] as CloudPreset[]),
      ]);
      if (mounted.current) {
        setSummary(data);
        setPresets(available);
        setStatus("ready");
      }
    } catch {
      if (mounted.current) setStatus("error");
    }
  }, [api, connection]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  // Asking the destination which models a key can reach spends that key, so it
  // only runs once a key is actually present.
  const loadModels = useCallback(
    async (modelId: string) => {
      setBusy((previous) => ({ ...previous, [modelId]: true }));
      try {
        const data = await api.listCloudModels(modelId);
        if (mounted.current) {
          setOptions((previous) => ({ ...previous, [modelId]: data }));
          if (!data.reachable) {
            setNotice(
              `Could not list models for ${modelId}. Check the key, then retry.`,
            );
          }
        }
      } catch {
        if (mounted.current) setNotice(`Could not list models for ${modelId}.`);
      } finally {
        if (mounted.current) {
          setBusy((previous) => ({ ...previous, [modelId]: false }));
        }
      }
    },
    [api],
  );

  useEffect(() => {
    for (const row of summary?.destinations ?? []) {
      if (row.key_available && options[row.model_id] === undefined) {
        void loadModels(row.model_id);
      }
    }
  }, [summary, options, loadModels]);

  async function saveKey(modelId: string) {
    const draft = drafts[modelId] ?? "";
    if (!draft) {
      setNotice("Paste an API key before saving.");
      return;
    }
    setBusy((previous) => ({ ...previous, [modelId]: true }));
    setNotice("");
    try {
      const data = await api.setCloudKey(modelId, draft);
      if (mounted.current) {
        setSummary(data);
        setDrafts((previous) => ({ ...previous, [modelId]: "" }));
        setOptions((previous) => {
          const next = { ...previous };
          delete next[modelId];
          return next;
        });
        setNotice(`Key saved for ${modelId}. Now choose a model.`);
      }
    } catch {
      if (mounted.current) setNotice("Could not save key.");
    } finally {
      if (mounted.current) {
        setBusy((previous) => ({ ...previous, [modelId]: false }));
      }
    }
  }

  async function chooseModel(modelId: string, runtimeModel: string) {
    if (!runtimeModel) return;
    setBusy((previous) => ({ ...previous, [modelId]: true }));
    setNotice("");
    try {
      const data = await api.setCloudModel(modelId, runtimeModel);
      if (mounted.current) {
        setSummary(data);
        setNotice(`${modelId} will use ${runtimeModel}.`);
      }
    } catch {
      if (mounted.current) setNotice("Could not select that model.");
    } finally {
      if (mounted.current) {
        setBusy((previous) => ({ ...previous, [modelId]: false }));
      }
    }
  }

  async function clearKey(modelId: string) {
    setBusy((previous) => ({ ...previous, [modelId]: true }));
    setNotice("");
    try {
      const data = await api.clearCloudKey(modelId);
      if (mounted.current) {
        setSummary(data);
        setOptions((previous) => {
          const next = { ...previous };
          delete next[modelId];
          return next;
        });
        setNotice(`Cleared key for ${modelId}.`);
      }
    } catch {
      if (mounted.current) setNotice("Could not clear key.");
    } finally {
      if (mounted.current) {
        setBusy((previous) => ({ ...previous, [modelId]: false }));
      }
    }
  }

  async function addDestination() {
    const preset = presets.find((item) => item.model_id === chosen);
    const name = preset ? preset.display_name : form.display_name.trim();
    const endpoint = preset ? preset.endpoint : form.endpoint.trim();
    if (!name || !endpoint) {
      setNotice("A provider needs a name and an endpoint.");
      return;
    }
    const slug = preset
      ? preset.model_id
      : name
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "")
          .slice(0, 48);
    if (!slug) {
      setNotice("Use a name with letters or numbers.");
      return;
    }
    setAdding(true);
    setNotice("");
    try {
      const data = await api.addCloudDestination({
        model_id: slug,
        display_name: name,
        endpoint,
        health_endpoint: preset
          ? preset.health_endpoint
          : endpoint.replace(/\/chat\/completions$/, "/models"),
      });
      if (mounted.current) {
        setSummary(data);
        setForm({ display_name: "", endpoint: "" });
        setChosen("");
        setPresets((previous) =>
          previous.filter((item) => item.model_id !== slug),
        );
        setNotice(`Added ${name}. Paste its key to continue.`);
      }
    } catch {
      if (mounted.current) {
        setNotice("Could not add that provider. It must be an https endpoint.");
      }
    } finally {
      if (mounted.current) setAdding(false);
    }
  }

  async function removeDestination(modelId: string) {
    setBusy((previous) => ({ ...previous, [modelId]: true }));
    try {
      const data = await api.removeCloudDestination(modelId);
      if (mounted.current) setSummary(data);
    } catch {
      if (mounted.current) {
        setNotice("That provider is built in and cannot be removed.");
      }
    } finally {
      if (mounted.current) {
        setBusy((previous) => ({ ...previous, [modelId]: false }));
      }
    }
  }

  if (connection !== "ready" || status === "idle" || status === "loading") {
    return null;
  }
  if (status === "error" || !summary) {
    return (
      <section className="cloud-panel">
        <span className="eyebrow">Cloud providers</span>
        <p className="field-note">Could not load cloud destinations.</p>
      </section>
    );
  }
  return (
    <section className="cloud-panel">
      <span className="eyebrow">Cloud providers</span>
      <p className="field-note">
        Paste a key, then pick from the models that key can actually reach. Keys
        live in process memory only, are never written to disk or echoed back,
        and clear on restart. Local models answer first; cloud only runs on
        local failure when the mode allows it.
      </p>
      <ul className="cloud-list">
        {summary.destinations.map((row) => (
          <li key={row.model_id} data-testid={`cloud-${row.model_id}`}>
            <div className="cloud-header">
              <strong>{row.display_name}</strong>
              <span className="meta">
                {row.runtime_model ?? "no model chosen"}
              </span>
            </div>
            <KeyRow
              row={row}
              draft={drafts[row.model_id] ?? ""}
              options={options[row.model_id]}
              busy={busy[row.model_id] === true}
              onDraft={(value) =>
                setDrafts((previous) => ({
                  ...previous,
                  [row.model_id]: value,
                }))
              }
              onSave={() => void saveKey(row.model_id)}
              onClear={() => void clearKey(row.model_id)}
              onChoose={(value) => void chooseModel(row.model_id, value)}
              onReload={() => void loadModels(row.model_id)}
              onRemove={() => void removeDestination(row.model_id)}
            />
          </li>
        ))}
      </ul>
      <details className="add-provider">
        <summary>Add a provider</summary>
        <p className="field-note">
          Pick a provider, or choose Other for any OpenAI-compatible
          <code> /chat/completions</code> endpoint. Adding one widens where
          sanitized text may be sent, so only add endpoints you trust.
        </p>
        <label className="cloud-input">
          <span className="sr-only">Provider</span>
          <select
            value={chosen}
            onChange={(event) => setChosen(event.target.value)}
            disabled={adding}
          >
            <option value="">Choose a provider…</option>
            {presets.map((item) => (
              <option key={item.model_id} value={item.model_id}>
                {item.display_name}
              </option>
            ))}
            <option value="other">Other…</option>
          </select>
        </label>
        {presets.find((item) => item.model_id === chosen)?.note && (
          <p className="field-note">
            {presets.find((item) => item.model_id === chosen)?.note}
          </p>
        )}
        {chosen === "other" && (
          <>
            <label className="cloud-input">
              <span className="sr-only">Provider name</span>
              <input
                type="text"
                placeholder="Provider name"
                value={form.display_name}
                onChange={(event) =>
                  setForm((previous) => ({
                    ...previous,
                    display_name: event.target.value,
                  }))
                }
                maxLength={64}
                disabled={adding}
              />
            </label>
            <label className="cloud-input">
              <span className="sr-only">Chat completions endpoint</span>
              <input
                type="url"
                placeholder="https://api.example.com/v1/chat/completions"
                value={form.endpoint}
                onChange={(event) =>
                  setForm((previous) => ({
                    ...previous,
                    endpoint: event.target.value,
                  }))
                }
                maxLength={2048}
                disabled={adding}
              />
            </label>
          </>
        )}
        <button
          className="quiet small"
          onClick={() => void addDestination()}
          disabled={
            adding ||
            !chosen ||
            (chosen === "other" &&
              (!form.display_name.trim() || !form.endpoint.trim()))
          }
        >
          {adding ? "Adding…" : "Add provider"}
        </button>
      </details>
      {notice && (
        <p className="settings-notice" role="status">
          {notice}
        </p>
      )}
    </section>
  );
}

function KeyRow({
  row,
  draft,
  options,
  busy,
  onDraft,
  onSave,
  onClear,
  onChoose,
  onReload,
  onRemove,
}: {
  row: CloudDestinationInfo;
  draft: string;
  options: CloudModelOptions | undefined;
  busy: boolean;
  onDraft: (value: string) => void;
  onSave: () => void;
  onClear: () => void;
  onChoose: (value: string) => void;
  onReload: () => void;
  onRemove: () => void;
}) {
  const stateLabel =
    row.key_source === "env"
      ? "key loaded from environment"
      : row.key_source === "runtime"
        ? "key set this session"
        : "no key set";
  return (
    <div className="cloud-key-row">
      <span className="cloud-state" data-source={row.key_source}>
        {stateLabel}
      </span>
      {!row.key_available ? (
        <div className="cloud-key-controls">
          <label className="cloud-input">
            <span className="sr-only">API key for {row.display_name}</span>
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="Paste API key"
              value={draft}
              onChange={(event) => onDraft(event.target.value)}
              disabled={busy}
            />
          </label>
          <button
            className="quiet small"
            onClick={onSave}
            disabled={busy || !draft}
          >
            {busy ? "Saving…" : "Save key"}
          </button>
          <button className="quiet small danger" onClick={onRemove}>
            Remove
          </button>
        </div>
      ) : (
        <div className="cloud-key-controls">
          <label className="cloud-input">
            <span className="sr-only">Model for {row.display_name}</span>
            <select
              value={row.runtime_model ?? ""}
              onChange={(event) => onChoose(event.target.value)}
              disabled={busy || !options?.models.length}
            >
              <option value="" disabled>
                {busy
                  ? "Loading models…"
                  : options?.models.length
                    ? "Choose a model"
                    : "No models listed"}
              </option>
              {options?.models.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <button className="quiet small" onClick={onReload} disabled={busy}>
            Refresh
          </button>
          <button className="quiet small danger" onClick={onClear}>
            Clear key
          </button>
        </div>
      )}
    </div>
  );
}
