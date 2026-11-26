import net from "node:net";
import { fileURLToPath } from "node:url";

export const UI_ORIGIN = "http://127.0.0.1:5173";
const path = fileURLToPath(
  new URL("../../var/ui/bridge.sock", import.meta.url),
);

export function readBridge() {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(path);
    let buffer = "";
    let settled = false;
    const fail = () => {
      if (settled) return;
      settled = true;
      socket.destroy();
      reject(
        new Error("Start MOCK_MODE=1 make run-backend before the frontend."),
      );
    };
    socket.setTimeout(1500, fail);
    socket.on("error", fail);
    socket.on("end", fail);
    socket.on("close", fail);
    socket.on("connect", () => socket.write("ECOLM_UI_V1\n"));
    socket.on("data", (chunk) => {
      buffer += chunk.toString("ascii");
      if (buffer.length > 2048) return fail();
      if (!buffer.includes("\n")) return;
      try {
        const result = JSON.parse(buffer);
        if (
          !/^[A-Za-z0-9_-]{32,256}$/.test(result.token) ||
          !Number.isInteger(result.backendPort) ||
          result.backendPort < 1024 ||
          result.backendPort > 65535 ||
          result.uiOrigin !== UI_ORIGIN
        )
          return fail();
        settled = true;
        socket.destroy();
        resolve(result);
      } catch {
        fail();
      }
    });
  });
}

export function safeUiRequest(request) {
  const origin = request.headers.origin;
  const write = !["GET", "HEAD", "OPTIONS"].includes(request.method ?? "");
  return (
    request.headers.host === "127.0.0.1:5173" &&
    (!origin || origin === UI_ORIGIN) &&
    (!write || origin === UI_ORIGIN) &&
    request.headers["sec-fetch-site"] !== "cross-site"
  );
}

export function localProxy() {
  return {
    name: "ecolm-local-proxy",
    async configureServer(server) {
      const bridge = await readBridge();
      server.middlewares.use((request, response, next) => {
        if (!safeUiRequest(request)) {
          response.writeHead(403);
          response.end("Local UI authorization rejected.");
          return;
        }
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("X-Content-Type-Options", "nosniff");
        response.setHeader("Referrer-Policy", "no-referrer");
        next();
      });
      server.config.server.proxy = {
        "/api": {
          target: `http://127.0.0.1:${bridge.backendPort}`,
          changeOrigin: true,
          configure(proxy) {
            proxy.on("proxyReq", (request) => {
              request.setHeader("X-Local-Token", bridge.token);
            });
            proxy.on("error", () => {
              /* Never log request objects or headers. */
            });
          },
        },
      };
    },
  };
}
