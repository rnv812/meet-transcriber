import { readFileSync } from "node:fs";
import { join } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * Адрес и токен резидента — тот же `daemon.json`, что читает оболочка Tauri.
 *
 * Нужно только dev-серверу: в собранном приложении endpoint приходит из Rust
 * (команда `endpoint`), а в браузере файл прочитать нельзя, поэтому dev-сервер
 * проксирует `/api` и сам подставляет заголовок с токеном.
 *
 * Читается один раз при старте dev-сервера: если резидент поднялся позже,
 * dev-сервер надо перезапустить.
 */
function daemonEndpoint(): { port: number; token: string } | null {
  const dir =
    process.env.MEET_DATA_DIR || join(process.env.LOCALAPPDATA ?? "", "meet");
  try {
    const raw = readFileSync(join(dir, "daemon.json"), "utf8");
    const data = JSON.parse(raw);
    return data?.port ? data : null;
  } catch {
    return null; // резидента нет — панель покажет это состояние сама
  }
}

const daemon = daemonEndpoint();

export default defineConfig({
  plugins: [react()],
  build: {
    target: "chrome110", // WebView2 на Win10/11; лишние полифилы не нужны
    // Две страницы: окно приложения и плавающая панель ассистента (окно
    // `live` оболочки грузит live.html и в dev, и в сборке).
    rollupOptions: { input: { main: "index.html", live: "live.html" } },
  },
  // Вывод cargo не должен затираться очисткой экрана Vite.
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    watch: {
      // IMPORTANT: не следить за src-tauri. Там cargo держит свои файлы
      // открытыми, и watcher Vite падает на них с EBUSY, роняя весь запуск.
      ignored: ["**/src-tauri/**"],
    },
    proxy: daemon
      ? {
          "/api": {
            target: `http://127.0.0.1:${daemon.port}`,
            changeOrigin: false,
            rewrite: (path: string) => path.replace(/^\/api/, ""),
            configure: (proxy: any) => {
              proxy.on("proxyReq", (proxyReq: any) => {
                proxyReq.setHeader("Authorization", `Bearer ${daemon.token}`);
              });
            },
          },
        }
      : undefined,
  },
});
