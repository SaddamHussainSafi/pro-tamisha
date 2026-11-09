import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = process.env.API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    // xfwd: the API sees the browser's real address (X-Forwarded-For; uvicorn trusts it from
    // 127.0.0.1), so dev sign-in and per-IP limits still work if Vite is opened to the LAN.
    proxy: {
      "/api": { target: api, changeOrigin: true, xfwd: true },
      "/health": { target: api, changeOrigin: true, xfwd: true },
      // Order Connect customer pages (served by the API, framed by the company website).
      "/embed": { target: api, changeOrigin: true, xfwd: true },
    },
  },
  test: { include: ["src/**/*.test.ts"] },
});
