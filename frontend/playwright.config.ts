import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "../backend/.venv/bin/uvicorn a_share_radar.main:app --app-dir ../backend/src --host 127.0.0.1 --port 18000",
      url: "http://127.0.0.1:18000/api/health",
      env: {
        A_SHARE_FIXTURE_SOURCE: "true",
        A_SHARE_DATA_DIR: "../data/e2e",
        A_SHARE_FRONTEND_PORT: "4173",
      },
      reuseExistingServer: false,
    },
    {
      command: "pnpm dev --host 127.0.0.1 --port 4173",
      url: "http://127.0.0.1:4173",
      env: { VITE_API_BASE_URL: "http://127.0.0.1:18000" },
      reuseExistingServer: false,
    },
  ],
});
