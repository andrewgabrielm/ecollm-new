import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CloudSettings } from "../src/components/CloudSettings";
import type { ChatApi, CloudDestinations } from "../src/api/client";
import { fakeApi } from "./fixtures";

function withDestinations(destinations: CloudDestinations): ChatApi {
  const api = fakeApi();
  api.getCloudDestinations = vi.fn().mockResolvedValue(destinations);
  return api;
}

const grokUnkeyed: CloudDestinations = {
  destinations: [
    {
      model_id: "grok-cloud",
      model_version: "grok-4-fast-2026-11",
      display_name: "xAI Grok",
      endpoint: "https://api.x.ai/v1/chat/completions",
      runtime_model: "grok-4-fast-reasoning",
      key_available: false,
      key_source: "none",
    },
  ],
};

const grokKeyed: CloudDestinations = {
  destinations: [
    {
      model_id: "grok-cloud",
      model_version: "grok-4-fast-2026-11",
      display_name: "xAI Grok",
      endpoint: "https://api.x.ai/v1/chat/completions",
      runtime_model: "grok-4-fast-reasoning",
      key_available: true,
      key_source: "runtime",
    },
  ],
};

describe("CloudSettings", () => {
  it("offers the add-provider form when no destinations are declared", async () => {
    const api = withDestinations({ destinations: [] });
    render(<CloudSettings api={api} connection="ready" />);
    expect(await screen.findByText("Add a provider")).toBeInTheDocument();
  });

  it("shows unkeyed destination with a password field", async () => {
    const api = withDestinations(grokUnkeyed);
    render(<CloudSettings api={api} connection="ready" />);
    expect(await screen.findByText("xAI Grok")).toBeInTheDocument();
    expect(screen.getByText(/no key set/i)).toBeInTheDocument();
    const input = screen.getByPlaceholderText(/paste api key/i);
    expect(input).toHaveAttribute("type", "password");
  });

  it("saves a pasted key and clears the input", async () => {
    const api = withDestinations(grokUnkeyed);
    api.setCloudKey = vi.fn().mockResolvedValue(grokKeyed);
    render(<CloudSettings api={api} connection="ready" />);
    const input = await screen.findByPlaceholderText(/paste api key/i);
    await userEvent.type(input, "xai-synthetic-test-key");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(api.setCloudKey).toHaveBeenCalledWith(
      "grok-cloud",
      "xai-synthetic-test-key",
    );
    // The key must never appear in the DOM after save.
    expect(screen.queryByText(/xai-synthetic-test-key/)).toBeNull();
  });

  it("renders a clear button only when a key is set, and hides it after clear", async () => {
    const api = withDestinations(grokKeyed);
    api.clearCloudKey = vi.fn().mockResolvedValue(grokUnkeyed);
    render(<CloudSettings api={api} connection="ready" />);
    const clearButton = await screen.findByRole("button", { name: /clear/i });
    await userEvent.click(clearButton);
    expect(api.clearCloudKey).toHaveBeenCalledWith("grok-cloud");
    expect(await screen.findByText(/no key set/i)).toBeInTheDocument();
  });

  it("does not render while connection is not ready", () => {
    const api = withDestinations(grokUnkeyed);
    const { container } = render(
      <CloudSettings api={api} connection="checking" />,
    );
    expect(container.textContent).toBe("");
  });
});

describe("CloudSettings key then model", () => {
  const unkeyed: CloudDestinations = {
    destinations: [
      {
        model_id: "groq",
        model_version: "groq-v1",
        display_name: "Groq",
        endpoint: "https://api.groq.com/openai/v1/chat/completions",
        runtime_model: null,
        key_available: false,
        key_source: "none",
      },
    ],
  };
  const keyed: CloudDestinations = {
    destinations: [
      {
        ...unkeyed.destinations[0]!,
        key_available: true,
        key_source: "runtime",
      },
    ],
  };

  it("asks only for the key before one is set", async () => {
    const api = withDestinations(unkeyed);
    render(<CloudSettings api={api} connection="ready" />);
    expect(
      await screen.findByPlaceholderText(/paste api key/i),
    ).toBeInTheDocument();
    // The preset picker is also a combobox, so scope to the destination row.
    expect(
      within(screen.getByTestId("cloud-groq")).queryByRole("combobox"),
    ).toBeNull();
    expect(api.listCloudModels).not.toHaveBeenCalled();
  });

  it("lists the models that key can reach once it is set", async () => {
    const api = withDestinations(keyed);
    vi.mocked(api.listCloudModels).mockResolvedValue({
      model_id: "groq",
      runtime_model: null,
      models: ["llama-3.3-70b-versatile", "mixtral-8x7b"],
      reachable: true,
    });
    render(<CloudSettings api={api} connection="ready" />);
    expect(
      await screen.findByRole("option", { name: "mixtral-8x7b" }),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/paste api key/i)).toBeNull();
  });

  it("saves the chosen model", async () => {
    const api = withDestinations(keyed);
    vi.mocked(api.listCloudModels).mockResolvedValue({
      model_id: "groq",
      runtime_model: null,
      models: ["llama-3.3-70b-versatile"],
      reachable: true,
    });
    const user = userEvent.setup();
    render(<CloudSettings api={api} connection="ready" />);
    await screen.findByRole("option", { name: "llama-3.3-70b-versatile" });
    await user.selectOptions(
      within(screen.getByTestId("cloud-groq")).getByRole("combobox"),
      "llama-3.3-70b-versatile",
    );
    expect(api.setCloudModel).toHaveBeenCalledWith(
      "groq",
      "llama-3.3-70b-versatile",
    );
  });

  it("adds a provider from an endpoint the owner supplies", async () => {
    const api = withDestinations({ destinations: [] });
    const user = userEvent.setup();
    render(<CloudSettings api={api} connection="ready" />);
    await screen.findByText("Add a provider");
    await user.selectOptions(screen.getByRole("combobox"), "other");
    await user.type(screen.getByPlaceholderText(/provider name/i), "My Host");
    await user.type(
      screen.getByPlaceholderText(/https:\/\/api\.example\.com/i),
      "https://api.example.com/v1/chat/completions",
    );
    await user.click(screen.getByRole("button", { name: "Add provider" }));
    expect(api.addCloudDestination).toHaveBeenCalledWith({
      model_id: "my-host",
      display_name: "My Host",
      endpoint: "https://api.example.com/v1/chat/completions",
      health_endpoint: "https://api.example.com/v1/models",
    });
  });
});
