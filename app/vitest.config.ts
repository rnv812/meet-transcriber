import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.ts?(x)"],
    css: false,
    // Набор и под нагрузкой (параллельная сборка, медленный CI-раннер) должен
    // быть зелёным: тесты с вводом userEvent идут 1–1,5 с, под нагрузкой — в
    // разы дольше; таймаут по умолчанию (5 с) рвал их, и недописанный ввод
    // попадал в следующий тест.
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
});
