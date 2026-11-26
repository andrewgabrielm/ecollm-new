import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  App,
  historyFrom,
  HISTORY_CHARACTER_BUDGET,
  HISTORY_TURN_LIMIT,
  type Turn,
} from "../src/App";
import { ApiError, type Outcome } from "../src/api/client";
import { answer, fakeApi, proposal, health } from "./fixtures";

async function ready(api = fakeApi()) {
  render(<App api={api} />);
  await screen.findByText("Mock broker connected");
  return { api, user: userEvent.setup() };
}
async function send(
  user: ReturnType<typeof userEvent.setup>,
  prompt = "A synthetic question",
) {
  await user.type(screen.getByLabelText("Your message"), prompt);
  await user.click(screen.getByRole("button", { name: "Send message" }));
}
// Request options moved into the settings dialog, so tests open it to reach them.
async function openSettings(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Settings" }));
}
async function closeSettings(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Close settings" }));
}
describe("minimal local chat", () => {
  it("shows real-local mode honestly and leaves cloud modes selectable", async () => {
    const api = fakeApi();
    vi.mocked(api.health).mockResolvedValue({ ...health, mode: "real" });
    vi.mocked(api.chat).mockResolvedValue({
      ...answer,
      mock: false,
      generation_metrics: {
        input_tokens: 10,
        output_tokens: 5,
        output_tokens_per_second: 1000.0,
      },
    });
    render(<App api={api} />);
    await screen.findByText("Broker connected");
    expect(screen.getByText("Local runtime")).toBeVisible();
    const user = userEvent.setup();
    await openSettings(user);
    expect(
      screen.getByRole("option", { name: "Ask before cloud" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("option", { name: "Allow automatic cloud" }),
    ).toBeEnabled();
    await closeSettings(user);
    await send(user);
    await screen.findByText(answer.answer);
    await user.click(screen.getByText("Request details"));
    expect(screen.getByText("1000.0 token/s")).toBeVisible();
    expect(screen.getByText("10 / 5")).toBeVisible();
  });
  it("labels unavailable real runtime fallback as a synthetic session", async () => {
    const api = fakeApi();
    vi.mocked(api.health).mockResolvedValue({
      ...health,
      notice: "ollama_unavailable_using_mock",
    });
    await ready(api);
    expect(
      screen.getByText(
        "Ollama is unavailable. This session is using synthetic mock models.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Synthetic demo")).toBeVisible();
  });
  it("uses existing structured constraints and rejects malformed field names locally", async () => {
    const { api, user } = await ready();
    await openSettings(user);
    await user.selectOptions(screen.getByLabelText("Task hint"), "format");
    await user.type(screen.getByLabelText("Required JSON fields"), "result");
    await closeSettings(user);
    await send(user);
    expect(api.chat).toHaveBeenCalledWith(
      expect.objectContaining({
        constraints: { response_format: "json", required_fields: ["result"] },
      }),
      expect.any(AbortSignal),
    );
    await openSettings(user);
    await user.clear(screen.getByLabelText("Required JSON fields"));
    await user.type(
      screen.getByLabelText("Required JSON fields"),
      "invalid field",
    );
    await closeSettings(user);
    await send(user, "Another synthetic question");
    expect(api.chat).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alert")).toBeVisible();
  });
  it("defaults to local-only, unspecified scope and auto task; displays honest result metadata", async () => {
    const { api, user } = await ready();
    await openSettings(user);
    expect(screen.getByLabelText("Privacy mode")).toHaveValue("local_only");
    await closeSettings(user);
    await send(user);
    expect(api.chat).toHaveBeenCalledWith(
      {
        prompt: "A synthetic question",
        mode: "local_only",
        sensitivity_scope: "unspecified",
      },
      expect.any(AbortSignal),
    );
    expect(await screen.findByText(answer.answer)).toBeVisible();
    expect(screen.getAllByText("mock-general").length).toBeGreaterThanOrEqual(
      1,
    );
    expect(screen.getByText("13 ms")).toBeVisible();
    await user.click(screen.getByText("Request details"));
    expect(screen.getByText("Unavailable")).toBeVisible();
  });
  it("honors sensitivity, explicit task and privacy selectors", async () => {
    const { api, user } = await ready();
    await openSettings(user);
    await user.selectOptions(
      screen.getByLabelText("Sensitivity"),
      "confidential",
    );
    await user.selectOptions(screen.getByLabelText("Task hint"), "code");
    await user.selectOptions(
      screen.getByLabelText("Privacy mode"),
      "ask_before_cloud",
    );
    await closeSettings(user);
    await send(user);
    expect(api.chat).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: "ask_before_cloud",
        sensitivity_scope: "confidential",
        task_hint: "code",
      }),
      expect.any(AbortSignal),
    );
  });
  it("shows the exact sanitized preview and best local answer; submits it unchanged once", async () => {
    const api = fakeApi(),
      pending = proposal();
    vi.mocked(api.chat).mockResolvedValue(pending);
    const { user } = await ready(api);
    await send(user);
    expect(await screen.findByRole("dialog")).toBeVisible();
    expect(screen.getByLabelText("Exact sanitized preview").textContent).toBe(
      pending.sanitized_preview,
    );
    expect(screen.getByText(pending.best_local_answer!)).toBeVisible();
    expect(screen.getByRole("button", { name: "Keep local" })).toHaveFocus();
    await user.dblClick(screen.getByRole("button", { name: "Approve once" }));
    await waitFor(() => expect(api.approval).toHaveBeenCalledTimes(1));
    expect(api.approval).toHaveBeenCalledWith(pending.request_id, {
      proposal_id: pending.proposal_id,
      decision: "approve",
      sanitized_preview: pending.sanitized_preview,
      digest: pending.digest,
    });
  });
  it("declines without sending a preview or approving cloud", async () => {
    const api = fakeApi(),
      pending = proposal();
    vi.mocked(api.chat).mockResolvedValue(pending);
    vi.mocked(api.approval).mockResolvedValue({
      request_id: pending.request_id,
      status: "cancelled",
      reason: "decline",
    });
    const { user } = await ready(api);
    await send(user);
    await user.click(await screen.findByRole("button", { name: "Keep local" }));
    expect(api.approval).toHaveBeenCalledWith(pending.request_id, {
      proposal_id: pending.proposal_id,
      decision: "decline",
    });
    expect(await screen.findByText(/Cloud request declined/)).toBeVisible();
  });
  it("expires the dialog and cancels pending state with zero approvals", async () => {
    const api = fakeApi(),
      pending = proposal();
    vi.mocked(api.chat).mockResolvedValue({
      ...pending,
      expires_at: new Date(Date.now() - 1000).toISOString(),
    });
    const { user } = await ready(api);
    await send(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Approval expired",
    );
    expect(api.approval).toHaveBeenCalledWith(pending.request_id, {
      proposal_id: pending.proposal_id,
      decision: "cancel",
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("displays broker errors safely, then allows a retry", async () => {
    const api = fakeApi();
    vi.mocked(api.chat).mockRejectedValueOnce(new ApiError("privacy_failed"));
    const { user } = await ready(api);
    await send(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Generation was blocked",
    );
    await send(user, "A retried synthetic question");
    expect(await screen.findByText(answer.answer)).toBeVisible();
  });
  it("discards late output after stopping and cancels a late cloud proposal", async () => {
    const api = fakeApi();
    let resolve!: (value: Outcome) => void;
    vi.mocked(api.chat).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const { user } = await ready(api);
    await send(user);
    await user.click(screen.getByRole("button", { name: "Stop waiting" }));
    const pending = proposal();
    await act(async () => {
      resolve(pending);
    });
    expect(api.approval).toHaveBeenCalledWith(pending.request_id, {
      proposal_id: pending.proposal_id,
      decision: "cancel",
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("clears session content and does not render provider HTML", async () => {
    const api = fakeApi();
    vi.mocked(api.chat).mockResolvedValue({
      ...answer,
      answer: "<img src=x onerror=alert(1)>",
    });
    const { user } = await ready(api);
    await send(user);
    expect(
      await screen.findByText("<img src=x onerror=alert(1)>"),
    ).toBeVisible();
    expect(document.querySelector('img[src="x"]')).toBeNull();
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.getByLabelText("Your message")).toHaveValue("");
    expect(
      screen.queryByText("<img src=x onerror=alert(1)>"),
    ).not.toBeInTheDocument();
  });
  it("has a bounded, accessible prompt and no browser persistence", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const { user } = await ready();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(screen.getByLabelText("Your message")).toHaveAttribute(
      "maxlength",
      "8000",
    );
    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: " ".repeat(3) },
    });
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "What is the capital of France?" }),
    );
    expect(screen.getByLabelText("Your message")).toHaveValue(
      "What is the capital of France?",
    );
    expect(storage).not.toHaveBeenCalled();
  });
  it("clears the composer on send and keeps both turns in the transcript", async () => {
    const { user } = await ready();
    await send(user, "First question");
    expect(await screen.findByText(answer.answer)).toBeVisible();
    expect(screen.getByLabelText("Your message")).toHaveValue("");
    expect(screen.getByText("First question")).toBeVisible();
  });
  it("sends prior turns as history so a follow-up has context", async () => {
    const { api, user } = await ready();
    await send(user, "First question");
    await screen.findByText(answer.answer);
    await send(user, "Second question");
    await waitFor(() => expect(api.chat).toHaveBeenCalledTimes(2));
    expect(api.chat).toHaveBeenLastCalledWith(
      expect.objectContaining({
        prompt: "Second question",
        history: [
          { role: "user", content: "First question" },
          { role: "assistant", content: answer.answer },
        ],
      }),
      expect.any(AbortSignal),
    );
  });
  it("trims history to the turn bound the server accepts", () => {
    const many: Turn[] = Array.from(
      { length: HISTORY_TURN_LIMIT + 10 },
      (_, index) => ({
        key: String(index),
        role: "user",
        text: "hi",
      }),
    );
    expect(historyFrom(many)).toHaveLength(HISTORY_TURN_LIMIT);
  });
  it("trims history to the character budget the server accepts", () => {
    const each = HISTORY_CHARACTER_BUDGET / 4;
    const heavy: Turn[] = Array.from({ length: 10 }, (_, index) => ({
      key: String(index),
      role: "user",
      text: "x".repeat(each),
    }));
    expect(historyFrom(heavy)).toHaveLength(4);
  });
  it("does not save conversations while the preference is off", async () => {
    const { api, user } = await ready();
    await send(user, "First question");
    await screen.findByText(answer.answer);
    expect(api.saveConversation).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Saved conversations")).toBeNull();
  });
  it("saves the whole exchange once the owner opts in", async () => {
    const api = fakeApi();
    vi.mocked(api.listConversations).mockResolvedValue({
      enabled: true,
      conversations: [],
    });
    vi.mocked(api.saveConversation).mockResolvedValue({
      enabled: true,
      conversations: [
        {
          id: "f18ec088-906b-47fe-aef1-057588e2ea2d",
          title: "First question",
          created_at: 1,
          updated_at: 2,
          message_count: 2,
        },
      ],
    });
    render(<App api={api} />);
    await screen.findByText("Mock broker connected");
    const user = userEvent.setup();
    await send(user, "First question");
    await screen.findByText(answer.answer);
    await waitFor(() => expect(api.saveConversation).toHaveBeenCalled());
    const [, messages] = vi.mocked(api.saveConversation).mock.calls[0]!;
    expect(messages).toEqual([
      { role: "user", content: "First question", model_id: null },
      { role: "assistant", content: answer.answer, model_id: "mock-general" },
    ]);
    expect(await screen.findByLabelText("Saved conversations")).toBeVisible();
  });
  it("opens a saved conversation back into the transcript", async () => {
    const api = fakeApi();
    vi.mocked(api.listConversations).mockResolvedValue({
      enabled: true,
      conversations: [
        {
          id: "f18ec088-906b-47fe-aef1-057588e2ea2d",
          title: "Earlier chat",
          created_at: 1,
          updated_at: 2,
          message_count: 2,
        },
      ],
    });
    vi.mocked(api.readConversation).mockResolvedValue([
      { role: "user", content: "an earlier question", model_id: null },
      {
        role: "assistant",
        content: "an earlier answer",
        model_id: "mock-general",
      },
    ]);
    render(<App api={api} />);
    await screen.findByText("Mock broker connected");
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Earlier chat" }),
    );
    expect(await screen.findByText("an earlier question")).toBeVisible();
    expect(screen.getByText("an earlier answer")).toBeVisible();
  });
  it("reports unavailable health and reconnects", async () => {
    const api = fakeApi();
    vi.mocked(api.health).mockRejectedValueOnce(new ApiError("network"));
    render(<App api={api} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Reconnect" }),
    );
    expect(await screen.findByText("Mock broker connected")).toBeVisible();
  });
});
