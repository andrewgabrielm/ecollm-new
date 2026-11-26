import { useCallback, useEffect, useRef, useState } from "react";
import type { CatalogSummary, ChatApi, Preferences } from "../api/client";

type PreferenceKey =
  | "exact_cache_enabled"
  | "persistent_analytics_enabled"
  | "local_learning_enabled"
  | "workload_persistence_enabled";

export function Settings({
  api,
  connection,
}: {
  api: ChatApi;
  connection: "checking" | "ready" | "offline";
}) {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [catalog, setCatalog] = useState<CatalogSummary | null>(null);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);

  const load = useCallback(async () => {
    if (connection !== "ready") return;
    try {
      const [loadedPrefs, loadedCatalog] = await Promise.all([
        api.getPreferences(),
        api.getCatalog(),
      ]);
      if (mounted.current) {
        setPrefs(loadedPrefs);
        setCatalog(loadedCatalog);
      }
    } catch {
      /* will retry on next connection change */
    }
  }, [api, connection]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  async function togglePreference(key: PreferenceKey, label: string) {
    if (!prefs || saving) return;
    setSaving(true);
    setNotice("");
    try {
      const updated = await api.updatePreferences({ [key]: !prefs[key] });
      if (mounted.current) {
        setPrefs(updated);
        setNotice(`${label} ${updated[key] ? "enabled" : "disabled"}.`);
      }
    } catch {
      if (mounted.current) setNotice("Could not update preference.");
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  async function clearCache() {
    if (clearing) return;
    setClearing(true);
    setNotice("");
    try {
      const deleted = await api.clearCache();
      if (mounted.current)
        setNotice(
          deleted > 0
            ? `Cleared ${deleted} cached entries.`
            : "Cache is empty.",
        );
    } catch {
      if (mounted.current) setNotice("Could not clear cache.");
    } finally {
      if (mounted.current) setClearing(false);
    }
  }

  async function resetPreferences() {
    if (resetting) return;
    setResetting(true);
    setNotice("");
    try {
      const defaults = await api.resetPreferences();
      if (mounted.current) {
        setPrefs(defaults);
        setNotice("Preferences reset to defaults.");
      }
    } catch {
      if (mounted.current) setNotice("Could not reset preferences.");
    } finally {
      if (mounted.current) setResetting(false);
    }
  }

  if (connection !== "ready" || !prefs) return null;

  return (
    <div className="settings-panel">
      <span className="eyebrow">Workspace settings</span>
      <label className="toggle-row">
        <input
          type="checkbox"
          checked={prefs.exact_cache_enabled}
          onChange={() => void togglePreference("exact_cache_enabled", "Cache")}
          disabled={saving}
        />
        <span>Exact cache</span>
      </label>
      <p className="field-note">
        Cache identical non-sensitive requests locally. PII requests are never
        cached.
      </p>
      {prefs.exact_cache_enabled && (
        <button
          className="quiet small"
          onClick={() => void clearCache()}
          disabled={clearing}
        >
          {clearing ? "Clearing..." : "Clear cache"}
        </button>
      )}
      <label className="toggle-row">
        <input
          type="checkbox"
          checked={prefs.persistent_analytics_enabled}
          onChange={() =>
            void togglePreference(
              "persistent_analytics_enabled",
              "Local analytics",
            )
          }
          disabled={saving}
        />
        <span>Local analytics</span>
      </label>
      <p className="field-note">
        Keep a small, typed, allowlisted record of each chat turn for the local
        dashboard. Prompt and answer text never land here.
      </p>
      <label className="toggle-row">
        <input
          type="checkbox"
          checked={prefs.local_learning_enabled}
          onChange={() =>
            void togglePreference("local_learning_enabled", "Local learning")
          }
          disabled={saving}
        />
        <span>Local learning</span>
      </label>
      <p className="field-note">
        Enable the learned router's use of your local observations. Turning this
        off falls back to the rules router.
      </p>
      <label className="toggle-row">
        <input
          type="checkbox"
          checked={prefs.workload_persistence_enabled}
          onChange={() =>
            void togglePreference("workload_persistence_enabled", "Saved chats")
          }
          disabled={saving}
        />
        <span>Save chat history</span>
      </label>
      <p className="field-note">
        Keeps conversations on this device so they survive a reload. This is the
        only setting that writes your prompts and answers to disk. Turning it
        off deletes every saved conversation immediately.
      </p>
      <button
        className="quiet small"
        onClick={() => void resetPreferences()}
        disabled={resetting}
      >
        {resetting ? "Resetting..." : "Reset preferences"}
      </button>
      {catalog && (
        <section className="catalog-panel" aria-label="Model catalog">
          <span className="eyebrow">Managed models</span>
          <ul className="catalog-list">
            {catalog.models.map((row) => (
              <li
                key={`${row.model_id}@${row.manifest_version}`}
                data-testid={`catalog-${row.model_id}`}
              >
                <span className="model-name">{row.model_id}</span>
                <span className="meta">
                  {row.verified ? "verified" : "unverified"} ·{" "}
                  {row.installed ? "installed" : "not installed"}
                  {row.resident === true ? " · resident" : ""}
                  {row.eligible ? "" : " · ineligible"}
                </span>
              </li>
            ))}
          </ul>
          {!catalog.hardware_known && (
            <p className="field-note">
              Hardware probe has not been run; some eligibility checks are
              unknown.
            </p>
          )}
        </section>
      )}
      {notice && (
        <p className="settings-notice" role="status">
          {notice}
        </p>
      )}
    </div>
  );
}
