import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Settings } from "../src/components/Settings";
import type { ChatApi, Preferences } from "../src/api/client";
import { defaultCatalog, fakeApi } from "./fixtures";

function makeApi(initial: Partial<Preferences> = {}): ChatApi {
  const api = fakeApi();
  const prefs: Preferences = {
    exact_cache_enabled: false,
    persistent_analytics_enabled: false,
    local_learning_enabled: false,
    workload_persistence_enabled: false,
    mode: "local_only",
    ...initial,
  };
  api.getPreferences = vi.fn().mockResolvedValue(prefs);
  api.updatePreferences = vi
    .fn()
    .mockImplementation(async (update: Partial<Preferences>) => ({
      ...prefs,
      ...update,
    }));
  api.resetPreferences = vi.fn().mockResolvedValue({
    exact_cache_enabled: false,
    persistent_analytics_enabled: false,
    local_learning_enabled: false,
    workload_persistence_enabled: false,
    mode: "local_only",
  });
  api.getCatalog = vi.fn().mockResolvedValue(defaultCatalog);
  return api;
}

describe("Settings", () => {
  it("renders consent toggles and the model catalog", async () => {
    const api = makeApi();
    render(<Settings api={api} connection="ready" />);
    expect(await screen.findByLabelText(/exact cache/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/local analytics/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/local learning/i)).toBeInTheDocument();
    expect(
      await screen.findByTestId("catalog-mock-general"),
    ).toBeInTheDocument();
  });

  it("toggles persistent analytics", async () => {
    const api = makeApi();
    render(<Settings api={api} connection="ready" />);
    const toggle = await screen.findByLabelText(/local analytics/i);
    await userEvent.click(toggle);
    expect(api.updatePreferences).toHaveBeenCalledWith({
      persistent_analytics_enabled: true,
    });
    expect(
      await screen.findByText(/local analytics enabled/i),
    ).toBeInTheDocument();
  });

  it("resets preferences to defaults", async () => {
    const api = makeApi({ persistent_analytics_enabled: true });
    render(<Settings api={api} connection="ready" />);
    const button = await screen.findByRole("button", {
      name: /reset preferences/i,
    });
    await userEvent.click(button);
    expect(api.resetPreferences).toHaveBeenCalled();
    expect(
      await screen.findByText(/preferences reset to defaults/i),
    ).toBeInTheDocument();
  });
});
