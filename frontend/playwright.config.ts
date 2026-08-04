import { defineConfig } from "@playwright/test";

const runtimeProcess = (globalThis as typeof globalThis & {
  process?: { env: Record<string, string | undefined>; platform: string };
}).process;
const e2eDataDir = runtimeProcess?.env.A_SHARE_E2E_DATA_DIR;
const e2ePython = runtimeProcess?.env.A_SHARE_E2E_PYTHON;
if (!e2eDataDir || !e2ePython) {
  throw new Error("缺少由 E2E 父进程创建的临时运行环境");
}

const quotedPython = runtimeProcess?.platform === "win32"
  ? `"${e2ePython.replaceAll('"', '""')}"`
  : `'${e2ePython.replaceAll("'", "'\\''")}'`;

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
      command: `${quotedPython} -m uvicorn a_share_radar.main:app --app-dir ../backend/src --host 127.0.0.1 --port 18000`,
      url: "http://127.0.0.1:18000/api/health",
      env: {
        A_SHARE_FIXTURE_SOURCE: "true",
        A_SHARE_DATA_DIR: e2eDataDir,
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
