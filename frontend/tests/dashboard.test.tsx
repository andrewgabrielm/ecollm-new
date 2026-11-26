import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Dashboard } from "../src/components/Dashboard";
import type { ChatApi, TelemetrySummary } from "../src/api/client";
import { defaultTelemetry, fakeApi } from "./fixtures";

function telemetryApi(summary: TelemetrySummary): ChatApi {
  const api = fakeApi();
  api.getTelemetry = vi.fn().mockResolvedValue(summary);
  return api;
}

describe("Dashboard", () => {
  it("prompts the user to opt in when disabled", async () => {
    const api = telemetryApi(defaultTelemetry);
    render(<Dashboard api={api} connection="ready" />);
    expect(
      await screen.findByText(/persistent analytics/i),
    ).toBeInTheDocument();
  });

  it("renders aggregates when enabled", async () => {
    const summary: TelemetrySummary = {
      ...defaultTelemetry,
      enabled: true,
      sample_count: 45,
      route_counts: { local: 40, cloud: 5 },
      outcome_counts: { success: 42, local_failure: 3 },
      router_type_counts: { rules: 40, lightgbm: 5 },
      reason_counts: { rules_ranked: 45 },
      model_usage: [
        { model_id: "mock-general", model_version: "v1", count: 30 },
        { model_id: "mock-specialist", model_version: "v1", count: 15 },
      ],
      latency_overall: {
        task: "qa",
        sample_count: 45,
        median_ms: 120,
        p90_ms: 310,
      },
      latency_per_task: [],
      cache: { total_records: 45, cache_hits: 9, hit_rate: 0.2 },
    };
    const api = telemetryApi(summary);
    render(<Dashboard api={api} connection="ready" />);
    expect(await screen.findByText(/local \(40\)/)).toBeInTheDocument();
    expect(screen.getByText("120 ms")).toBeInTheDocument();
    expect(screen.getByText("310 ms")).toBeInTheDocument();
    expect(screen.getByText("20.0%")).toBeInTheDocument();
  });

  it("suppresses latency and hit rate below threshold", async () => {
    const summary: TelemetrySummary = {
      ...defaultTelemetry,
      enabled: true,
      sample_count: 5,
      route_counts: { local: 5 },
      outcome_counts: { success: 5 },
      router_type_counts: { rules: 5 },
      reason_counts: { rules_ranked: 5 },
      latency_overall: {
        task: "qa",
        sample_count: 5,
        median_ms: null,
        p90_ms: null,
      },
      cache: { total_records: 5, cache_hits: 1, hit_rate: null },
    };
    const api = telemetryApi(summary);
    render(<Dashboard api={api} connection="ready" />);
    await screen.findByText(/latency\/cache rates suppressed/i);
    const emDashes = screen.getAllByText("—");
    expect(emDashes.length).toBeGreaterThanOrEqual(3);
  });

  it("clears telemetry when the owner resets it", async () => {
    const summary: TelemetrySummary = {
      ...defaultTelemetry,
      enabled: true,
      sample_count: 40,
      latency_overall: {
        task: "qa",
        sample_count: 40,
        median_ms: 100,
        p90_ms: 250,
      },
      cache: { total_records: 40, cache_hits: 4, hit_rate: 0.1 },
    };
    const api = telemetryApi(summary);
    api.clearTelemetry = vi.fn().mockResolvedValue(40);
    render(<Dashboard api={api} connection="ready" />);
    const button = await screen.findByRole("button", {
      name: /reset telemetry/i,
    });
    await userEvent.click(button);
    expect(api.clearTelemetry).toHaveBeenCalled();
    expect(
      await screen.findByText(/cleared 40 telemetry records/i),
    ).toBeInTheDocument();
  });
});
