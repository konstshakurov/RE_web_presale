/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/api/, "") } } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    // тяжёлые e2e-стиль тесты (App целиком) на медленных раннерах: запас по таймаутам,
    // последовательный запуск файлов - параллельная нагрузка давала флаки (matching/note)
    testTimeout: 20000,
    hookTimeout: 20000,
    fileParallelism: false,
  },
});
