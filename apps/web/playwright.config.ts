import { defineConfig } from "@playwright/test";

// Smoke E2E against the real API (uvicorn on :8000) and the Vite dev server (:5173).
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  // Reduced motion: axe and screenshots see final colours, not a frame of the page fade-in.
  use: { baseURL: "http://localhost:5173", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" },
  webServer: [
    {
      command: "cd ../api && uv run uvicorn --app-dir src pro_trading.main:app --port 8000",
      url: "http://127.0.0.1:8000/health/live",
      reuseExistingServer: true,
    },
    { command: "npm run dev", url: "http://localhost:5173", reuseExistingServer: true },
  ],
});
