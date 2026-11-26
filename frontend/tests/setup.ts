import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
if (typeof HTMLDialogElement !== "undefined") {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
}
// jsdom implements no layout, so the transcript's scroll-to-latest is a no-op here.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function () {};
}

// Unit tests must inject a transport/API; accidental network calls fail immediately.
vi.stubGlobal("fetch", () => {
  throw new Error("Real network is disabled in unit tests");
});
