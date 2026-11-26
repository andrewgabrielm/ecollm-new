// @vitest-environment node
import { EventEmitter } from "node:events";
import net from "node:net";
import { describe, expect, it, vi } from "vitest";
import {
  localProxy,
  readBridge,
  safeUiRequest,
  UI_ORIGIN,
} from "../scripts/ui-proxy.mjs";

const token = "synthetic-local-write-" + "x".repeat(40);
class Socket extends EventEmitter {
  write = vi.fn();
  destroy = vi.fn();
  setTimeout = vi.fn();
}
function fakeSocket() {
  const socket = new Socket();
  vi.spyOn(net, "createConnection").mockReturnValue(
    socket as unknown as net.Socket,
  );
  return socket;
}
describe("server-only UI authorization", () => {
  it.each([
    {
      method: "POST",
      headers: { host: "evil.invalid:5173", origin: UI_ORIGIN },
    },
    { method: "POST", headers: { host: "127.0.0.1:5173" } },
    {
      method: "POST",
      headers: { host: "127.0.0.1:5173", origin: "http://evil.invalid" },
    },
    { method: "POST", headers: { host: "127.0.0.1:5173", origin: "null" } },
    {
      method: "POST",
      headers: { host: "127.0.0.1:5173", origin: [UI_ORIGIN, UI_ORIGIN] },
    },
    {
      method: "GET",
      headers: { host: "127.0.0.1:5173", "sec-fetch-site": "cross-site" },
    },
    { method: "GET", headers: { host: "127.0.0.1:9999" } },
  ])("blocks hostile/missing local authorization", (request) => {
    expect(
      safeUiRequest(request as unknown as Parameters<typeof safeUiRequest>[0]),
    ).toBe(false);
  });
  it("allows the exact local UI origin and safe initial navigation", () => {
    expect(
      safeUiRequest({
        method: "POST",
        headers: {
          host: "127.0.0.1:5173",
          origin: UI_ORIGIN,
          "sec-fetch-site": "same-origin",
        },
      }),
    ).toBe(true);
    expect(
      safeUiRequest({ method: "GET", headers: { host: "127.0.0.1:5173" } }),
    ).toBe(true);
  });
  it("fails closed and safely on truncated IPC", async () => {
    const socket = fakeSocket();
    const promise = readBridge();
    socket.emit("close");
    await expect(promise).rejects.toThrow("Start MOCK_MODE=1");
  });
  it("validates IPC destination and secret before proxy construction", async () => {
    const socket = fakeSocket();
    const promise = readBridge();
    socket.emit(
      "data",
      Buffer.from(
        JSON.stringify({ token, backendPort: 999999, uiOrigin: UI_ORIGIN }) +
          "\n",
      ),
    );
    await expect(promise).rejects.toThrow("Start MOCK_MODE=1");
  });
  it("keeps the credential inside the proxy closure and injects it server-side", async () => {
    const socket = fakeSocket();
    const server = {
      middlewares: { use: vi.fn() },
      config: {
        server: {
          proxy: {} as Record<
            string,
            { configure: (proxy: unknown) => void; target: string }
          >,
        },
      },
    };
    const configure = localProxy().configureServer as (
      server: unknown,
    ) => Promise<void>;
    const ready = configure(server);
    socket.emit("connect");
    socket.emit(
      "data",
      Buffer.from(
        JSON.stringify({ token, backendPort: 8000, uiOrigin: UI_ORIGIN }) +
          "\n",
      ),
    );
    await ready;
    expect(socket.write).toHaveBeenCalledWith("ECOLM_UI_V1\n");
    expect(server.config.server.proxy["/api"]!.target).toBe(
      "http://127.0.0.1:8000",
    );
    expect(JSON.stringify(server.config)).not.toContain(token);
    const handlers = new Map<
      string,
      (request: { setHeader: ReturnType<typeof vi.fn> }) => void
    >();
    server.config.server.proxy["/api"]!.configure({
      on: (
        event: string,
        handler: (request: { setHeader: ReturnType<typeof vi.fn> }) => void,
      ) => handlers.set(event, handler),
    });
    const request = { setHeader: vi.fn() };
    handlers.get("proxyReq")!(request);
    expect(request.setHeader).toHaveBeenCalledWith("X-Local-Token", token);
  });
});
