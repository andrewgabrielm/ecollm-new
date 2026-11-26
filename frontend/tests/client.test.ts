import { describe, expect, it, vi } from "vitest";
import { ApiError, createClient } from "../src/api/client";
import { answer, health, proposal } from "./fixtures";

function transport(body: unknown, status = 200) {
  return vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}
describe("typed local client", () => {
  it("accepts observed real-local metrics without inventing missing values", async () => {
    const response = await createClient(
      transport({
        ...answer,
        mock: false,
        generation_metrics: {
          input_tokens: 10,
          output_tokens: null,
          output_tokens_per_second: null,
        },
      }),
    ).chat({ prompt: "Synthetic fixture" });
    expect(response.status).toBe("success");
    if (response.status === "success")
      expect(response.generation_metrics?.output_tokens).toBeNull();
    await expect(
      createClient(
        transport({ ...answer, generation_metrics: { input_tokens: -1 } }),
      ).chat({ prompt: "Synthetic fixture" }),
    ).rejects.toHaveProperty("code", "invalid_response");
  });
  it("sends a single same-origin request with no credentials or retries", async () => {
    const fetcher = transport(answer);
    expect(
      await createClient(fetcher).chat({ prompt: "Synthetic fixture" }),
    ).toEqual(answer);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/chat",
      expect.objectContaining({
        method: "POST",
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("X-Local-Token");
  });
  it("validates health and exact approval previews", async () => {
    expect(await createClient(transport(health)).health()).toEqual(health);
    expect(
      await createClient(transport(proposal(), 202)).chat({
        prompt: "Synthetic fixture",
      }),
    ).toHaveProperty("status", "approval_required");
  });
  it.each([
    {},
    { ...answer, answer: 12 },
    { ...answer, timings: { total_ms: -1 } },
    { ...proposal(), proposal_id: undefined },
    { ...answer, secret: "synthetic-secret" },
  ])("rejects malformed or extra response fields safely", async (body) => {
    await expect(
      createClient(transport(body)).chat({ prompt: "Synthetic fixture" }),
    ).rejects.toThrow("invalid response");
  });
  it("rejects mismatched HTTP statuses", async () => {
    await expect(
      createClient(transport(proposal())).chat({ prompt: "Synthetic fixture" }),
    ).rejects.toHaveProperty("code", "invalid_response");
  });
  it("does not echo upstream content or validation detail in errors", async () => {
    const fetcher = transport(
      { status: "error", detail: "SYNTHETIC-PRIVATE-CONTENT" },
      500,
    );
    try {
      await createClient(fetcher).chat({ prompt: "fixture" });
      expect.fail("Must reject");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect(String(error)).not.toContain("SYNTHETIC-PRIVATE-CONTENT");
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("renders typed safe errors", async () => {
    await expect(
      createClient(
        transport(
          {
            request_id: answer.request_id,
            status: "error",
            error_code: "privacy_failed",
            retryable: false,
          },
          503,
        ),
      ).chat({ prompt: "fixture" }),
    ).rejects.toThrow("Generation was blocked");
  });
  it("rejects unsafe approval request paths without a call", async () => {
    const fetcher = transport(answer);
    await expect(
      createClient(fetcher).approval("../evil", {
        proposal_id: proposal().proposal_id,
        decision: "cancel",
      }),
    ).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("maps aborted/network/HTML failures to safe messages", async () => {
    const controller = new AbortController();
    controller.abort();
    const fail = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new Error("SYNTHETIC-SECRET"));
    await expect(
      createClient(fail).health(controller.signal),
    ).rejects.toHaveProperty("code", "cancelled");
    await expect(createClient(fail).health()).rejects.toHaveProperty(
      "code",
      "network",
    );
    const html = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("SYNTHETIC-SECRET", {
        headers: { "Content-Type": "text/html" },
      }),
    );
    await expect(createClient(html).health()).rejects.toHaveProperty(
      "code",
      "invalid_response",
    );
  });
});
