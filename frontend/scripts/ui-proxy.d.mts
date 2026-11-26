import type { IncomingHttpHeaders } from "node:http";
import type { Plugin } from "vite";

export const UI_ORIGIN: "http://127.0.0.1:5173";
export function readBridge(): Promise<{
  token: string;
  backendPort: number;
  uiOrigin: typeof UI_ORIGIN;
}>;
export function safeUiRequest(request: {
  method?: string;
  headers: IncomingHttpHeaders;
}): boolean;
export function localProxy(): Plugin;
