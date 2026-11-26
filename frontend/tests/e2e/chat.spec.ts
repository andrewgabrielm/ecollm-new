import { test, expect, type Page } from "@playwright/test";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { mkdirSync } from "node:fs";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const artifact = `${root}/evaluation/results/ui-smoke`;
mkdirSync(artifact, { recursive: true });

async function mockBroker(page: Page) {
  const driver = spawn(
    `${root}/.venv/bin/python`,
    ["scripts/ui_mock_driver.py"],
    {
      cwd: root,
      env: { ...process.env, PYTHONPATH: root },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  const lines = createInterface({ input: driver.stdout });
  let queue = Promise.resolve();
  let complete: ((value: string) => void) | undefined;
  lines.on("line", (value) => {
    complete?.(value);
    complete = undefined;
  });
  driver.stderr.on("data", () => {
    /* Never copy content-bearing process output into reports. */
  });
  const received: { status: number; body: Record<string, unknown> }[] = [];
  const observed: string[] = [];
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== "http://127.0.0.1:5174") {
      await route.abort();
      throw new Error("External browser request blocked");
    }
    if (!url.pathname.startsWith("/api/")) {
      await route.continue();
      return;
    }
    observed.push(url.pathname);
    const job = queue.then(async () => {
      const line = new Promise<string>((resolve, reject) => {
        complete = resolve;
        const timer = setTimeout(() => {
          complete = undefined;
          reject(new Error("In-process mock transport timed out"));
        }, 10000);
        timer.unref();
        driver.stdin.write(
          JSON.stringify({
            path: url.pathname,
            method: route.request().method(),
            body: route.request().postDataJSON(),
          }) + "\n",
        );
      });
      const result = JSON.parse(await line) as {
        status: number;
        body: Record<string, unknown>;
      };
      received.push(result);
      await route.fulfill({
        status: result.status,
        contentType: "application/json",
        body: JSON.stringify(result.body),
      });
    });
    queue = job.catch(() => {});
    await job;
  });
  return {
    received,
    observed,
    close: async () => {
      await queue;
      driver.stdin.end();
      await new Promise<void>((resolve) => {
        driver.once("exit", () => resolve());
        setTimeout(() => {
          driver.kill();
          resolve();
        }, 2000).unref();
      });
      lines.close();
    },
  };
}

test("local-only chat, PII restoration, structured routing and responsive visual demo", async ({
  page,
}) => {
  const broker = await mockBroker(page);
  const errors: string[] = [];
  page.on("pageerror", () => errors.push("Browser error"));
  try {
    await page.goto("/");
    await expect(page.getByText("Mock broker connected")).toBeVisible();
    await page.getByRole("button", { name: "Settings" }).click();
    await expect(page.getByLabel("Privacy mode")).toHaveValue("local_only");
    await page.getByRole("button", { name: "Close settings" }).click();
    await page.screenshot({
      path: `${artifact}/desktop-empty.png`,
      fullPage: true,
    });
    await page.getByLabel("Your message").fill("Hello STU-1234-5678");
    await page.getByRole("button", { name: "Send message" }).click();
    // The echoed payload now also carries the composed date header, so match
    // the restored identifier within it rather than the whole answer.
    await expect(page.locator(".answer-text").last()).toContainText(
      "Hello STU-1234-5678",
    );
    // Exact match picks the always-visible chip, not the same words inside the
    // collapsed request-details list.
    await expect(
      page.getByText("restored locally", { exact: true }).last(),
    ).toBeVisible();
    const modelLabels = page.getByText("mock-general", { exact: true });
    await expect(modelLabels.first()).toBeVisible();
    expect(
      broker.received.find((result) => result.body.status === "success")?.body
        .mock,
    ).toBe(true);
    await page.screenshot({
      path: `${artifact}/desktop-response.png`,
      fullPage: true,
    });
    await page.getByLabel("Your message").fill("Return JSON with an answer");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(
      page.getByText("local cascade", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("mock-specialist", { exact: true }).first(),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      page.getByRole("button", { name: "Send message" }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `${artifact}/mobile-response.png`,
      fullPage: true,
    });
    expect(
      await page.evaluate(() => ({
        local: localStorage.length,
        session: sessionStorage.length,
      })),
    ).toEqual({ local: 0, session: 0 });
    expect(errors).toEqual([]);
  } finally {
    await broker.close();
  }
});

test("exact sanitized approval, one-use replay protection and explicit decline", async ({
  page,
}) => {
  const broker = await mockBroker(page);
  try {
    await page.goto("/");
    await expect(page.getByText("Mock broker connected")).toBeVisible();
    await page.getByRole("button", { name: "Settings" }).click();
    await page.getByLabel("Privacy mode").selectOption("ask_before_cloud");
    await page.getByLabel("Task hint").selectOption("format");
    await page.getByLabel("Required JSON fields").fill("result");
    await page.getByRole("button", { name: "Close settings" }).click();
    // The actual default locals lack the requested result field; mock cloud supplies it.
    const prompt = "Return JSON about STU-1234-5678";
    await page.getByLabel("Your message").fill(prompt);
    await page.getByRole("button", { name: "Send message" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const proposal = broker.received.find(
      (result) => result.status === 202,
    )!.body;
    await expect(page.getByLabel("Exact sanitized preview")).toHaveText(
      proposal.sanitized_preview as string,
      { useInnerText: false },
    );
    expect(proposal.sanitized_preview).not.toContain("STU-1234-5678");
    await expect(
      page.getByRole("button", { name: "Keep local" }),
    ).toBeFocused();
    await page.screenshot({ path: `${artifact}/approval.png`, fullPage: true });
    await page.getByRole("button", { name: "Approve once" }).click();
    await expect(dialog).not.toBeVisible();
    await expect(page.locator(".answer-text")).toContainText("STU-1234-5678");
    await expect(page.getByText("mock-cloud", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Your message")).toBeFocused();
    const replay = await page.evaluate(async (proposal) => {
      const response = await fetch(
        `/api/chat/${proposal.request_id}/approval`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            proposal_id: proposal.proposal_id,
            decision: "approve",
            sanitized_preview: proposal.sanitized_preview,
            digest: proposal.digest,
          }),
        },
      );
      return response.status;
    }, proposal);
    expect(replay).toBe(409);
    await page.getByLabel("Your message").fill(prompt);
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(dialog).toBeVisible();
    await page.getByRole("button", { name: "Keep local" }).click();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByRole("status")).toContainText(
      "Cloud request declined.",
    );
    expect(
      await page.evaluate(() => localStorage.length + sessionStorage.length),
    ).toBe(0);
  } finally {
    await broker.close();
  }
});

test("expired approval blocks dispatch and errors never render upstream content", async ({
  page,
}) => {
  const broker = await mockBroker(page);
  try {
    await page.goto("/");
    await expect(page.getByText("Mock broker connected")).toBeVisible();
    const identity = "f18ec088-906b-47fe-aef1-057588e2ea2d";
    await page.route("**/api/chat", (route) =>
      route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          request_id: identity,
          status: "approval_required",
          sanitized_preview: "Synthetic masked fixture",
          expires_at: new Date(Date.now() - 1000).toISOString(),
          reason: "validation_failure",
          proposal_id: identity,
          digest: "a".repeat(64),
          mode: "ask_before_cloud",
          model_id: "mock-cloud",
          model_version: "synthetic-v1",
          policy_version: "policy-v1",
        }),
      }),
    );
    await page.getByLabel("Your message").fill("Synthetic expired fixture");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByRole("alert")).toHaveText(
      "Approval expired. Send another message to try again.",
    );
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await page.route("**/api/chat", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "SYNTHETIC-PRIVATE-ERROR" }),
      }),
    );
    await page.getByLabel("Your message").fill("Synthetic error fixture");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByRole("alert")).toContainText("invalid response");
    await expect(page.getByText("SYNTHETIC-PRIVATE-ERROR")).not.toBeVisible();
  } finally {
    await broker.close();
  }
});
