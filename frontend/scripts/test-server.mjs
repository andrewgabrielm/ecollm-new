// Static built-UI fixture server only. It has no API proxy, secret or provider.
import http from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = fileURLToPath(new URL("../dist/", import.meta.url));
const types = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
};
http
  .createServer(async (request, response) => {
    if (request.headers.host !== "127.0.0.1:5174") {
      response.writeHead(403);
      response.end();
      return;
    }
    const pathname = new URL(request.url, "http://127.0.0.1:5174").pathname;
    if (pathname.startsWith("/api/")) {
      response.writeHead(503);
      response.end();
      return;
    }
    const file = path.resolve(
      root,
      `.${decodeURIComponent(pathname === "/" ? "/index.html" : pathname)}`,
    );
    if (!file.startsWith(root)) {
      response.writeHead(403);
      response.end();
      return;
    }
    try {
      const content = await readFile(file);
      response.writeHead(200, {
        "Content-Type": types[path.extname(file)] ?? "application/octet-stream",
        "Cache-Control": "no-store",
      });
      response.end(content);
    } catch {
      response.writeHead(404);
      response.end();
    }
  })
  .listen(5174, "127.0.0.1");
