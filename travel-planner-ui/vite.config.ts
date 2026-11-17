/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The UI talks to ADK's API server (`adk api_server`, default port 8000) through the dev-server proxy, so the
// browser sees one origin and no CORS setup is needed. Point it elsewhere with ADK_API=http://host:port.
// The proxy drops the browser's Origin header: the API server refuses requests from origins it was not told
// about (HTTP 403), and here the only client is this local dev server.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.ADK_API || "http://127.0.0.1:8000";
  const adkProxy = {
    "/adk": {
      target,
      changeOrigin: true,
      rewrite: (path: string) => path.replace(/^\/adk/, ""),
      configure: (proxy: { on: (event: "proxyReq", cb: (req: { removeHeader: (h: string) => void }) => void) => void }) => {
        proxy.on("proxyReq", (req) => req.removeHeader("origin"));
      },
    },
  };
  return {
    plugins: [react()],
    server: { port: 5173, proxy: adkProxy },
    preview: { proxy: adkProxy },
    test: { environment: "node", include: ["src/**/*.test.ts"] },
  };
});
