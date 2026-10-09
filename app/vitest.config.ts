import { availableParallelism } from "node:os";

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
    // Не больше 8 процессов (0.5): по процессу jsdom на ядро (19 на 20 ядрах) при занятой
    // памяти машины (локальная модель, WSL) падали «JavaScript heap out of memory».
    // И не больше ядер: на 4-ядерном CI-раннере 8 процессов вдвое перегружали машину.
    maxWorkers: Math.max(1, Math.min(8, availableParallelism())),
  },
});
