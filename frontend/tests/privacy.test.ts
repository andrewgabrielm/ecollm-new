// @vitest-environment node
import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("browser privacy boundary", () => {
  it("does not include server credentials, bridge code or content persistence in browser source", () => {
    const files = [
      "App.tsx",
      "api/client.ts",
      "components/ApprovalDialog.tsx",
      "components/Answer.tsx",
      "main.tsx",
    ];
    const source = files
      .map((file) =>
        readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8"),
      )
      .join("\n");
    for (const forbidden of [
      "LOCAL_WRITE_TOKEN",
      "CLOUD_API_KEY",
      "X-Local-Token",
      "ECOLM_UI_V1",
      "localStorage.",
      "sessionStorage.",
      "console.log(",
      "console.error(",
      "dangerouslySetInnerHTML",
      "indexedDB",
      "sendBeacon",
    ])
      expect(source).not.toContain(forbidden);
  });
  it("keeps credentials and IPC markers out of built JavaScript", () => {
    const directory = new URL("../dist/assets/", import.meta.url);
    const bundle = readdirSync(directory)
      .filter((file) => file.endsWith(".js"))
      .map((file) => readFileSync(new URL(file, directory), "utf8"))
      .join("\n");
    expect(bundle.length).toBeGreaterThan(0);
    for (const forbidden of [
      "LOCAL_WRITE_TOKEN",
      "CLOUD_API_KEY",
      "X-Local-Token",
      "ECOLM_UI_V1",
      "bridge.sock",
    ])
      expect(bundle).not.toContain(forbidden);
  });
});
