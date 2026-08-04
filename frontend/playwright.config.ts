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
      command: "../backend/.venv/bin/python ../scripts/e2e_backend.py",
      url: "http://127.0.0.1:18000/api/health",
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
