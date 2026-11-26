import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatApi, TelemetrySummary } from "../api/client";

type Status = "idle" | "loading" | "refreshing" | "error" | "ready";

export function Dashboard({
  api,
  connection,
}: {
  api: ChatApi;
  connection: "checking" | "ready" | "offline";
}) {
  const [summary, setSummary] = useState<TelemetrySummary | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [notice, setNotice] = useState("");
  const [clearing, setClearing] = useState(false);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    if (connection !== "ready") return;
    setStatus((previous) => (previous === "ready" ? "refreshing" : "loading"));
    try {
      const data = await api.getTelemetry();
      if (mounted.current) {
        setSummary(data);
        setStatus("ready");
      }
    } catch {
      if (mounted.current) {
        setStatus("error");
      }
    }
  }, [api, connection]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  async function reset() {
    if (clearing) return;
    setClearing(true);
    setNotice("");
    try {
      const removed = await api.clearTelemetry();
      if (mounted.current) {
        setNotice(
          removed > 0
            ? `Cleared ${removed} telemetry records.`
            : "Telemetry already empty.",
        );
      }
      await load();
    } catch {
      if (mounted.current) setNotice("Could not clear telemetry.");
    } finally {
      if (mounted.current) setClearing(false);
    }
  }

  if (connection !== "ready") return null;
  if (status === "loading") {
    return (
      <section className="dashboard-panel" aria-busy="true">
        <span className="eyebrow">Telemetry</span>
        <p className="field-note">Loading…</p>
      </section>
    );
  }
  if (status === "error" || !summary) {
    return (
      <section className="dashboard-panel">
        <span className="eyebrow">Telemetry</span>
        <p className="field-note">Could not load telemetry.</p>
      </section>
    );
  }
  if (!summary.enabled) {
    return (
      <section className="dashboard-panel">
        <span className="eyebrow">Telemetry</span>
        <p className="field-note">
          Opt in via <strong>persistent analytics</strong> to see route, latency
          and cache numbers over your recent chats. Nothing is sent off-device.
        </p>
      </section>
    );
  }
  return (
    <section className="dashboard-panel">
      <span className="eyebrow">Telemetry</span>
      <p className="field-note">
        {summary.sample_count} records (retention {summary.retention_days}{" "}
        days).
        {summary.sample_count < summary.suppression_threshold
          ? ` Latency/cache rates suppressed below ${summary.suppression_threshold} samples.`
          : ""}
      </p>
      <dl className="dashboard-grid">
        <div>
          <dt>Route mix</dt>
          <dd>{formatCounts(summary.route_counts)}</dd>
        </div>
        <div>
          <dt>Outcomes</dt>
          <dd>{formatCounts(summary.outcome_counts)}</dd>
        </div>
        <div>
          <dt>Router</dt>
          <dd>{formatCounts(summary.router_type_counts)}</dd>
        </div>
        <div>
          <dt>Median latency</dt>
          <dd>{formatLatency(summary.latency_overall?.median_ms)}</dd>
        </div>
        <div>
          <dt>P90 latency</dt>
          <dd>{formatLatency(summary.latency_overall?.p90_ms)}</dd>
        </div>
        <div>
          <dt>Cache hit rate</dt>
          <dd>
            {summary.cache.hit_rate == null
              ? "—"
              : `${(summary.cache.hit_rate * 100).toFixed(1)}%`}
          </dd>
        </div>
      </dl>
      {summary.model_usage.length > 0 && (
        <ul className="model-usage">
          {summary.model_usage.map((row) => (
            <li key={`${row.model_id}@${row.model_version}`}>
              {row.model_id}
              <span className="meta">{` × ${row.count}`}</span>
            </li>
          ))}
        </ul>
      )}
      <button
        className="quiet small"
        onClick={() => void reset()}
        disabled={clearing}
      >
        {clearing ? "Clearing…" : "Reset telemetry"}
      </button>
      {notice && (
        <p className="settings-notice" role="status">
          {notice}
        </p>
      )}
    </section>
  );
}

function formatCounts(counts: Record<string, number>): string {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return "—";
  return entries.map(([key, value]) => `${key} (${value})`).join(", ");
}

function formatLatency(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${Math.round(value)} ms`;
}
