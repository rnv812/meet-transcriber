/**
 * Готовность агента к вставке после запуска, который начала сама просьба
 * «Спросить агента», — на настоящем выводе Claude Code 2.1.288 и Codex 0.159
 * (fixtures/*.json: [мс от запуска, кусок вывода псевдоконсоли], пути
 * обезличены), через настоящий xterm.js, тем же правилом, что во вкладке:
 * `coldReadiness` + экран не менялся QUIET_MS (опрос каждые 100 мс).
 *
 * Что показал вывод: оба агента включают режим вставки (ESC[?2004h) в первые
 * полсекунды — ещё до диалога и до поля ввода — и потом молчат 0,7–1,3 с.
 * Прежнее правило «режим вставки + тишина 800 мс + нет фраз диалога в нижних
 * 12 строках» вставляло ссылку в эту паузу (Claude Code её теряет), в диалог
 * «доверять ли папке» (он вверху экрана, внизу фраз не видно) и в вопрос Codex
 * об обновлении (цифра ссылки выбирает пункт «Update now»).
 */
import { Terminal } from "@xterm/xterm";
import { QUIET_MS, coldReadiness, ownTitle, screenOutput, screenRows } from "./AgentTab";
import claudeTrusted from "./fixtures/claude-trusted.json";
import claudeTrustDialog from "./fixtures/claude-trust-dialog.json";
import claudeTrustConfirmed from "./fixtures/claude-trust-confirmed.json";
import codexUpdateDialog from "./fixtures/codex-update-dialog.json";
import codexTrustDialog from "./fixtures/codex-trust-dialog.json";
import codexTrusted from "./fixtures/codex-trusted.json";

type Fixture = Array<[number, string]>;
type Provider = "claude-code" | "codex";

const write = (t: Terminal, data: string) => new Promise<void>((done) => t.write(data, done));

/**
 * Проиграть вывод по его времени. Возвращает, когда правило вкладки вставило
 * бы ссылку (null — не вставило до конца записи + 3 с), что было на экране и
 * когда прежнее правило (режим вставки + тишина в выводе) вставило бы её.
 */
async function replay(fx: Fixture, provider: Provider, size = { cols: 120, rows: 30 }) {
  const t = new Terminal({ ...size, allowProposedApi: true });
  let titled = false;
  t.onTitleChange((title) => { if (ownTitle(title)) titled = true; });
  const end = fx[fx.length - 1]![0] + 3000;
  let i = 0;
  let lastOutput = 0;
  let lastBytes = 0;
  let readyAt: number | null = null;
  let oldRuleAt: number | null = null;
  let confirmSeen = false;
  let screenAtReady: string[] = [];
  for (let now = 0; now <= end && readyAt === null; now += 100) {
    while (i < fx.length && fx[i]![0] <= now) {
      const [at, data] = fx[i]!;
      await write(t, data);
      lastBytes = at;
      if (screenOutput(data)) lastOutput = at;
      i++;
    }
    const rows = screenRows(t);
    const bracketed = t.modes.bracketedPasteMode;
    if (oldRuleAt === null && bracketed && now - lastBytes >= QUIET_MS) oldRuleAt = now;
    const state = coldReadiness(rows, { bracketed, provider, titled });
    if (state === "confirm") confirmSeen = true;
    if (state === "ready" && now - lastOutput >= QUIET_MS) {
      readyAt = now;
      screenAtReady = rows;
    }
  }
  t.dispose();
  return { readyAt, oldRuleAt, confirmSeen, screenAtReady };
}

/** Когда в выводе впервые встретилось `needle` (мс от запуска). */
const firstAt = (fx: Fixture, needle: string) => fx.find(([, data]) => data.includes(needle))?.[0] ?? null;

test("оба агента включают режим вставки сразу при старте — по нему одному готовность не понять", () => {
  for (const fx of [claudeTrusted, claudeTrustDialog, codexUpdateDialog, codexTrusted] as Fixture[]) {
    expect(firstAt(fx, "\x1b[?2004h")).toBeLessThan(700);
  }
});

test("Claude Code, папка уже доверенная: пауза перед полем ввода — ждём; вставка — когда видно поле ввода", async () => {
  const fx = claudeTrusted as Fixture;
  const repl = firstAt(fx, "\x1b[?1049h")!; // экран разговора
  const r = await replay(fx, "claude-code");
  // Прежнее правило вставило бы ссылку в паузу до поля ввода — Claude Code её теряет.
  expect(r.oldRuleAt).not.toBeNull();
  expect(r.oldRuleAt!).toBeLessThan(repl);
  expect(r.readyAt).not.toBeNull();
  expect(r.readyAt!).toBeGreaterThan(repl);
  expect(r.readyAt!).toBeLessThan(15_000);
  expect(r.screenAtReady.some((row) => row.startsWith("❯\u00a0"))).toBe(true);
  expect(r.confirmSeen).toBe(false);
});

test("Claude Code, новая папка: диалог «доверять ли папке» вверху высокого экрана — не вставляем", async () => {
  for (const rows of [30, 40, 50]) {
    const r = await replay(claudeTrustDialog as Fixture, "claude-code", { cols: 120, rows });
    expect(r.oldRuleAt).not.toBeNull(); // прежнее правило вставило бы в диалог
    expect(r.readyAt).toBeNull();
    expect(r.confirmSeen).toBe(true);
  }
});

test("Claude Code: человек подтвердил доверие — вставка после поля ввода, не раньше", async () => {
  const fx = claudeTrustConfirmed as Fixture;
  const answered = firstAt(fx, "\x1b[?2004l")!; // диалог закрыт
  const r = await replay(fx, "claude-code");
  expect(r.confirmSeen).toBe(true);
  expect(r.readyAt).not.toBeNull();
  expect(r.readyAt!).toBeGreaterThan(answered);
  expect(r.screenAtReady.join("\n")).not.toMatch(/trust this folder/i);
});

test("Codex: вопрос об обновлении — не вставляем (цифра ссылки выбрала бы «Update now»)", async () => {
  const r = await replay(codexUpdateDialog as Fixture, "codex");
  expect(r.oldRuleAt).not.toBeNull();
  expect(r.readyAt).toBeNull();
  expect(r.confirmSeen).toBe(true);
});

test("Codex, новая папка: поле ввода видно до вопроса о папке — ждём, вопрос — не вставляем", async () => {
  const r = await replay(codexTrustDialog as Fixture, "codex");
  expect(r.readyAt).toBeNull();
  expect(r.confirmSeen).toBe(true);
});

test("Codex, папка доверенная: вставка после того, как сеанс поставил свой заголовок", async () => {
  const fx = codexTrusted as Fixture;
  const titled = fx.find(([, d]) => /\x1b\]0;[^\\\x07]*\x07/.test(d) && !d.includes("codex.exe"))![0];
  const r = await replay(fx, "codex");
  expect(r.oldRuleAt!).toBeLessThan(titled);
  expect(r.readyAt).not.toBeNull();
  expect(r.readyAt!).toBeGreaterThan(titled);
  expect(r.readyAt!).toBeLessThan(15_000);
  expect(r.screenAtReady.some((row) => /^›/.test(row))).toBe(true);
});

test("поле ввода и диалоги: признаки из вывода агентов", () => {
  const ready = (rows: string[], provider: Provider = "claude-code", titled = true) =>
    coldReadiness(rows, { bracketed: true, provider, titled });
  const rule = "─".repeat(40);
  expect(ready([rule, "❯\u00a0Try \"edit <filepath> to...\"", rule])).toBe("ready");
  expect(ready([rule, "❯ ", rule])).toBe("ready");
  expect(ready(["╭" + "─".repeat(30) + "╮", "│ > Try something │", "╰" + "─".repeat(30) + "╯"])).toBe("ready");
  // Указатель выбора в диалоге Claude Code — не поле ввода.
  expect(ready(["", "❯ No, exit", "  Yes, I trust this folder"])).toBe("confirm");
  expect(ready(["", "❯ No, exit", "  Something else"])).toBe("waiting");
  // Codex: поле ввода, пункты выбора — нет; без своего заголовка — ждём.
  expect(ready(["› Ask Codex to do anything"], "codex")).toBe("ready");
  expect(ready(["› Ask Codex to do anything"], "codex", false)).toBe("waiting");
  expect(ready(["› 1. Trust and continue", "  2. Quit"], "codex")).toBe("waiting");
  expect(ready(["› 1. Update now (runs `powershell …`)", "  2. Skip", "  3. Skip until next version"], "codex"))
    .toBe("confirm");
  // Баннер об обновлении в истории Codex — не диалог.
  expect(ready(["✨ Update available! 0.159.0 -> 0.160.0", "Run powershell … to update.", "› Ask Codex"], "codex"))
    .toBe("ready");
  expect(coldReadiness([rule, "❯\u00a0", rule], { bracketed: false, provider: "claude-code", titled: true }))
    .toBe("waiting");
});

test("заголовок окна и вывод, меняющий экран", () => {
  expect(ownTitle("C:\\agent\\bin\\codex.exe")).toBe(false);
  expect(ownTitle("")).toBe(false);
  expect(ownTitle("2026-01-01_10-00")).toBe(true);
  expect(ownTitle("✳ Claude Code")).toBe(true);
  expect(screenOutput("\x1b]0;⠼ 2026-01-01_10-00\x07")).toBe(false);
  expect(screenOutput("\x1b]0;x\x07\x1b[27;3H")).toBe(true);
});

afterEach(() => vi.useRealTimers());

// --- запуск кнопкой: ссылка тоже ждёт готовности (сеанс agentSessions, настоящий xterm) ----

const shellMock = vi.hoisted(() => ({
  listeners: [] as Array<(e: { id: string; data?: string; code?: number | null }) => void>,
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => true,
  agentSpawn: vi.fn(async () => "agent-1"),
  agentWrite: vi.fn(async () => {}),
  agentResize: vi.fn(async () => {}),
  agentKill: vi.fn(async () => {}),
  onAgentData: vi.fn(async (cb: (e: { id: string; data: string }) => void) => {
    shellMock.listeners.push(cb as never);
    return () => {};
  }),
  onAgentExit: vi.fn(async () => () => {}),
}));

/**
 * Агента запустили кнопкой (сама просьба его не запускала), он вывел `fx`;
 * просьба «Спросить агента» приходит в `askAt` мс. Возвращает, что и когда
 * вставлено в терминал и что видно в полосе ожидания во время диалога.
 */
async function manualStart(fx: Fixture, provider: Provider, askAt: number) {
  const { agentSession } = await import("./agentSessions");
  shellMock.listeners = [];
  const host = document.createElement("div");
  document.body.appendChild(host);
  const s = agentSession(`manual-${provider}-${askAt}`);
  // Терминал создаётся при первом показе; jsdom не знает matchMedia, которую зовёт xterm.
  window.matchMedia ??= ((query: string) => ({
    matches: false, media: query, onchange: null, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  })) as typeof window.matchMedia;
  const detach = s.attach(host);
  await vi.waitFor(() => expect(s.term).not.toBeNull());
  // Дальше — без экрана, как сеанс записи, открытой не сейчас.
  detach();
  s.term!.resize(120, 30); // размер, в котором записан вывод
  vi.useFakeTimers();
  const pasted: Array<[number, string]> = [];
  let now = 0;
  vi.spyOn(s.term!, "paste").mockImplementation((text: string) => { pasted.push([now, text]); });
  expect(await s.start(provider)).toBe(true);
  const views: string[] = [];
  let asked = false;
  const end = fx[fx.length - 1]![0] + 3000;
  let i = 0;
  for (; now <= end; now += 50) {
    while (i < fx.length && fx[i]![0] <= now) {
      const data = fx[i]![1];
      shellMock.listeners.forEach((cb) => cb({ id: "agent-1", data }));
      i++;
    }
    if (!asked && now >= askAt) {
      s.request("Про реплику: [12:34] Анна: «Сдаём отчёт.»");
      asked = true;
    }
    await vi.advanceTimersByTimeAsync(50);
    if (asked && s.snapshot().pending) views.push(s.snapshot().pending!.view);
  }
  vi.useRealTimers();
  host.remove();
  return { pasted, views };
}

test("запуск кнопкой, на экране вопрос Codex об обновлении — ссылку не вставляем", async () => {
  const r = await manualStart(codexUpdateDialog as Fixture, "codex", 500);
  expect(r.pasted).toEqual([]);
  expect(r.views).toContain("confirm");
});

test("запуск кнопкой, на экране вопрос Codex о папке — ссылку не вставляем", async () => {
  const r = await manualStart(codexTrustDialog as Fixture, "codex", 2400);
  expect(r.pasted).toEqual([]);
  expect(r.views).toContain("confirm");
});

test("запуск кнопкой, на экране вопрос Claude Code о папке — ссылку не вставляем", async () => {
  const r = await manualStart(claudeTrustDialog as Fixture, "claude-code", 600);
  expect(r.pasted).toEqual([]);
  expect(r.views).toContain("confirm");
});

test("запуск кнопкой: ссылка, пришедшая во время вопроса о папке, вставляется один раз — когда видно поле ввода", async () => {
  const fx = claudeTrustConfirmed as Fixture;
  const answered = firstAt(fx, "\x1b[?2004l")!;
  const r = await manualStart(fx, "claude-code", 1000);
  expect(r.views).toContain("confirm");
  expect(r.pasted).toHaveLength(1);
  expect(r.pasted[0]![0]).toBeGreaterThan(answered);
  expect(r.pasted[0]![1]).toBe("Про реплику: [12:34] Анна: «Сдаём отчёт.»");
});

test("фразы диалога, перенесённые узким терминалом или самим агентом, всё равно узнаются", async () => {
  const { dialogShown, screenText } = await import("./agentReady");
  const t = new Terminal({ cols: 20, rows: 6, allowProposedApi: true });
  await write(t, "\x1b[2;1H❯ No, exit\r\n  Yes, I trust this folder");
  expect(screenRows(t).some((row) => /trust this folder/i.test(row))).toBe(false);
  expect(dialogShown(screenText(t))).toBe(true);
  t.dispose();
  // Агент сам переносит текст на новую строку с отступом.
  expect(dialogShown(["Quick safety check: Is this a project you created or one you", "  trust?"].join(" "))).toBe(true);
});
