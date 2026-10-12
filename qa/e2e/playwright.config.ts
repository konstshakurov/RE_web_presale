import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "@playwright/test";

// Параметры QA-стенда: окружение либо qa/stand/.qa.env (создаётся stand.sh up, вне Git).
function loadEnv(): Record<string, string> {
  const file = resolve(__dirname, "..", "stand", ".qa.env");
  const out: Record<string, string> = {};
  if (existsSync(file)) {
    for (const line of readFileSync(file, "utf-8").split(/\r?\n/)) {
      const m = /^([A-Z0-9_]+)=(.*)$/.exec(line);
      if (m) out[m[1]] = m[2];
    }
  }
  return { ...out, ...(process.env as Record<string, string>) };
}

export const ENV = loadEnv();
export const BASE_URL = ENV.QA_BASE_URL || "http://127.0.0.1:18080";

// Один воркер: сценарии меняют общий список допущенных. trace/video выключены намеренно:
// в них попали бы вводимые пароли. Скриншоты - только явные, без ввода секретов.
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["json", { outputFile: "artifacts/results.json" }]],
  outputDir: "artifacts/test-results",
  use: {
    baseURL: BASE_URL,
    locale: "ru-RU",
    trace: "off",
    video: "off",
    screenshot: "off",
    viewport: { width: 1280, height: 800 },
  },
});
