// «Прокликивание» окна (0.5, пункт 23): каждый экран — снимок, ошибки консоли
// и неудачные запросы к резиденту — в отчёт. Запускает scripts/clickthrough.py
// (он поднимает резидента с временной папкой данных и Vite); вручную:
//
//   node scripts/clickthrough.mjs http://127.0.0.1:5179 ../.superpowers/clickthrough
//
// Браузер — системный Edge (`CLICK_CHANNEL`, по умолчанию msedge): скачивать
// Chromium не нужно. Опасного не нажимает: удаление, запись, «Стоп» и
// отправку наружу — нет; только переходы, вкладки, раскрытие и поиск.

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const [base = "http://127.0.0.1:5179", out = "clickthrough"] = process.argv.slice(2);
mkdirSync(out, { recursive: true });

const issues = [];
const expected = [];
const shots = [];
// Штатные ответы «нет данных» — не ошибки (адрес → причина).
const EXPECTED = [
  [/^404 GET \/api\/recordings\/[^/]+\/summary$/, "у записи ещё нет итогов"],
  [/^404 GET \/api\/recordings\/[^/]+\/live-draft$/, "у записи нет черновика ассистента"],
  [/^409 GET \/api\/live\/events$/, "встреча с ассистентом не идёт"],
];
const browser = await chromium.launch({ channel: process.env.CLICK_CHANNEL || "msedge", headless: true });

async function run(theme) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 820 }, colorScheme: theme });
  const page = await context.newPage();
  let where = "старт";
  const note = (kind, text) => {
    const item = { theme, where, kind, text: String(text).slice(0, 500) };
    const why = EXPECTED.find(([re]) => re.test(item.text));
    if (why) expected.push({ ...item, why: why[1] });
    else issues.push(item);
  };
  // «Failed to load resource» без адреса — его покрывает запись ответов ниже.
  page.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) note("console", m.text()); });
  page.on("pageerror", (e) => note("pageerror", e.message));
  page.on("requestfailed", (r) => { if (r.url().includes("/api/")) note("request", `${r.method()} ${r.url()} — ${r.failure()?.errorText}`); });
  page.on("response", (r) => { if (r.status() >= 400) note("http", `${r.status()} ${r.request().method()} ${r.url().replace(base, "")}`); });

  let n = 0;
  const shot = async (name) => {
    await page.waitForTimeout(350);
    const file = `${theme}-${String(++n).padStart(2, "0")}-${name.replace(/[^\p{L}\p{N}]+/gu, "_")}.png`;
    await page.screenshot({ path: join(out, file) });
    shots.push({ theme, name, file });
  };
  const step = async (name, fn) => {
    where = name;
    try {
      await fn();
      await shot(name);
    } catch (e) {
      note("step", e.message.split("\n")[0]);
    }
  };
  const nav = (label) => page.getByRole("navigation", { name: "Разделы" }).getByRole("button", { name: label });

  await step("Записи", async () => {
    await page.goto(`${base}/`, { waitUntil: "networkidle" });
    await nav("Записи").waitFor({ timeout: 15000 });
  });
  const items = page.locator(".rec-item__main");
  const count = Math.min(await items.count(), 3);
  for (let i = 0; i < count; i++) {
    await step(`Запись ${i + 1}`, async () => { await items.nth(i).click(); });
    const tabs = page.getByRole("tablist", { name: "Содержимое записи" }).getByRole("tab");
    const names = await tabs.allInnerTexts().catch(() => []);
    for (const [t, label] of names.entries()) {
      await step(`Запись ${i + 1} — ${label}`, async () => { await tabs.nth(t).click(); });
    }
  }
  await step("Голоса", async () => { await nav("Голоса").click(); });
  await step("Настройки", async () => { await nav("Настройки").click(); });
  const menu = page.getByRole("navigation", { name: "Разделы настроек" }).getByRole("button");
  const sections = await menu.allInnerTexts();
  for (const [i, title] of sections.entries()) {
    await step(`Настройки — ${title.trim()}`, async () => {
      await menu.nth(i).click();
      const fine = page.getByRole("button", { name: "Тонкая настройка" });
      if (await fine.count()) {
        const open = await fine.first().getAttribute("aria-expanded");
        if (open === "false") await fine.first().click();
      }
    });
  }
  await step("Поиск по настройкам", async () => {
    const search = page.getByRole("combobox", { name: "Поиск по настройкам" });
    await search.fill("тема");
    await page.getByRole("listbox", { name: "Найденные настройки" }).waitFor();
  });
  await page.keyboard.press("Escape");
  await step("Окно ассистента (без встречи)", async () => {
    await page.goto(`${base}/live.html`, { waitUntil: "networkidle" });
  });
  await step("Панель записи (macOS)", async () => {
    await page.goto(`${base}/tray.html`, { waitUntil: "networkidle" });
  });
  await context.close();
}

for (const theme of ["dark", "light"]) await run(theme);
await browser.close();
writeFileSync(join(out, "report.json"), JSON.stringify({ base, shots, issues, expected }, null, 2), "utf8");
console.log(`снимков: ${shots.length}, замечаний: ${issues.length} → ${out}`);
process.exit(issues.length ? 1 : 0);
