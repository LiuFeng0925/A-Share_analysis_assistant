import { spawn } from "node:child_process";
import { constants } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const unsupportedPlatformMessage =
  "E2E 测试 runner 当前仅支持 macOS 和 Linux，暂不支持 Windows。";

export function resolveRuntimePlatform(actualPlatform, testPlatform) {
  if (actualPlatform === "win32") return actualPlatform;
  return testPlatform ?? actualPlatform;
}

const runtimePlatform = resolveRuntimePlatform(
  process.platform,
  process.env.A_SHARE_E2E_TEST_PLATFORM,
);

if (runtimePlatform === "win32") {
  console.error(unsupportedPlatformMessage);
  process.exitCode = 1;
} else {
  const scriptsDirectory = path.dirname(fileURLToPath(import.meta.url));
  const projectDirectory = path.dirname(scriptsDirectory);
  const virtualEnvironmentDirectory = path.join(
    projectDirectory,
    "backend",
    ".venv",
  );
  const pythonExecutable = process.env.A_SHARE_E2E_PYTHON ?? path.join(
    virtualEnvironmentDirectory,
    "bin",
    "python",
  );
  const runner = path.join(scriptsDirectory, "run_e2e.py");
  const child = spawn(pythonExecutable, [runner], {
    detached: true,
    env: process.env,
    stdio: "inherit",
  });

  for (const signalName of ["SIGTERM", "SIGINT"]) {
    process.on(signalName, () => {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill(signalName);
      }
    });
  }

  child.on("error", (error) => {
    console.error(`无法启动 E2E Python 进程：${error.message}`);
    process.exitCode = 1;
  });

  child.on("exit", (code, signalName) => {
    if (code !== null) {
      process.exitCode = code;
      return;
    }
    const signalNumber = signalName ? constants.signals[signalName] : undefined;
    process.exitCode = 128 + (signalNumber ?? 1);
  });
}
