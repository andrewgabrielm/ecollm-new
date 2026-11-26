import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// Node-only configuration; the IPC credential never enters the client bundle.
import { localProxy } from "./scripts/ui-proxy.mjs";

export default defineConfig({
  plugins: [react(), localProxy()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    allowedHosts: ["127.0.0.1"],
    cors: false,
    proxy: {},
  },
  build: { sourcemap: false },
});
