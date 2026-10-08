import { StrictMode } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  AgentTab, CODEX_SCRIPT_NOTE, CONFIRM_SCREEN, OPENCODE_FALLBACK_QUIET_MS, OPENCODE_QUIET_MS, OPENCODE_SCRIPT_NOTE,
  PASTE_WAIT_MS, QUIET_MS, agentProviders, codexScriptOnly, coldReadiness, opencodePromptVisible, opencodeScriptOnly,
  quietNeeded,
  defaultProvider,
} from "./AgentTab";
import { pasteLine } from "../../lib/agentRef";
import { BUSY_MS } from "./agentSessions";
import { CardTabs } from "./CardTabs";
import * as api from "../../lib/api";
import type { AgentData, AgentExit } from "../../lib/shell";
import type { AssistantInfo } from "../../lib/types";

const h = vi.hoisted(() => {
  const PROMPT = "❯ ";
  class FakeTerminal {
    static all: FakeTerminal[] = [];
    cols = 80;
    rows = 24;
    written: string[] = [];
    selection = "";
    pasted: string[] = [];
    disposed = false;
    resets = 0;
    focused = 0;
    /** Режим вставки, который включает сам агент (Claude Code, Codex). */
    modes = { bracketedPasteMode: true };
    onDataCb: ((d: string) => void) | null = null;
    onResizeCb: ((s: { cols: number; rows: number }) => void) | null = null;
    onTitleCb: ((t: string) => void) | null = null;
    keys: ((e: KeyboardEvent) => boolean) | null = null;
    constructor(public options: unknown) { FakeTerminal.all.push(this); }
    loadAddon() {}
    open() {}
    attachCustomKeyEventHandler(fn: (e: KeyboardEvent) => boolean) { this.keys = fn; }
    onData(cb: (d: string) => void) { this.onDataCb = cb; return { dispose() {} }; }
    onResize(cb: (s: { cols: number; rows: number }) => void) { this.onResizeCb = cb; return { dispose() {} }; }
    onTitleChange(cb: (t: string) => void) { this.onTitleCb = cb; return { dispose() {} }; }
    /** Агент (или псевдоконсоль) поставил заголовок окна. */
    title(text: string) { this.onTitleCb?.(text); }
    write(data: string) { this.written.push(data); }
    reset() { this.written = []; this.resets++; }
    focus() { this.focused++; }
    dispose() { this.disposed = true; }
    hasSelection() { return this.selection !== ""; }
    getSelection() { return this.selection; }
    clearSelection() { this.selection = ""; }
    paste(text: string) { this.pasted.push(text); }
    /** Как будто подгонка под окно поменяла размер. */
    resize(cols: number, rows: number) { this.cols = cols; this.rows = rows; this.onResizeCb?.({ cols, rows }); }
    get text() { return this.written.join(""); }
    /**
     * Последняя строка экрана; по умолчанию — поле ввода Claude Code («❯» и
     * неразрывный пробел): агент показал, куда вставлять.
     */
    screen = PROMPT;
    /** Остальные строки экрана сверху (диалог агента рисует вверху). */
    lines: string[] = [];
    get buffer() {
      const last = this.rows - 1;
      const line = (i: number) => (i === last ? this.screen : this.lines[i] ?? "");
      return { active: { viewportY: 0, baseY: 0, getLine: (i: number) => ({ translateToString: () => line(i) }) } };
    }
  }
  return {
    PROMPT,
    FakeTerminal,
    listeners: { data: [] as Array<(d: AgentData) => void>, exit: [] as Array<(e: AgentExit) => void> },
    shell: {
      inTauri: vi.fn(() => true),
      agentSpawn: vi.fn(),
      agentWrite: vi.fn(async () => {}),
      agentResize: vi.fn(async () => {}),
      agentKill: vi.fn(async () => {}),
      onAgentData: vi.fn(),
      onAgentExit: vi.fn(),
    },
  };
});

vi.mock("@xterm/xterm", () => ({ Terminal: h.FakeTerminal }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("../../lib/shell", () => h.shell);
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getAssistant: vi.fn(),
  getQa: vi.fn(),
  getAgentContext: vi.fn(),
}));

const assistant = (o: Partial<AssistantInfo> = {}): AssistantInfo => ({
  provider: "claude-code", setting: "auto", knowledge_dir: null, checking: false,
  available: {
    "claude-code": { found: true, path: "C:/bin/claude.exe" },
    codex: { found: true, path: "C:/bin/codex.exe" },
    "openai-compatible": { found: true, base_url: "http://127.0.0.1:1234/v1" },
  },
  ...o,
});

const term = () => h.FakeTerminal.all.at(-1)!;
const ep = { base: "/api", token: null };
const data = (id: string, text: string) => act(() => h.listeners.data.forEach((cb) => cb({ id, data: text })));
const exit = (id: string, code: number | null) => act(() => h.listeners.exit.forEach((cb) => cb({ id, code })));

type More = Partial<Pick<Parameters<typeof AgentTab>[0],
  "onOpenSettings" | "endpoint" | "insert" | "onTaken" | "contextVersion" | "textPhase">>;

async function show(info: AssistantInfo | null = assistant(), more: More = {}) {
  const view = render(<AgentTab id="r1" assistant={info} {...more} />);
  if (h.shell.inTauri() && agentProviders(info).length) {
    await waitFor(() => expect(h.FakeTerminal.all.length).toBe(1));
  }
  return view;
}
const startButton = () => screen.getByRole("button", { name: "Запустить" });

beforeEach(() => {
  vi.clearAllMocks();
  h.FakeTerminal.all = [];
  h.listeners.data = [];
  h.listeners.exit = [];
  h.shell.inTauri.mockReturnValue(true);
  h.shell.onAgentData.mockImplementation(async (cb: (d: AgentData) => void) => {
    h.listeners.data.push(cb);
    return () => { h.listeners.data = h.listeners.data.filter((x) => x !== cb); };
  });
  h.shell.onAgentExit.mockImplementation(async (cb: (e: AgentExit) => void) => {
    h.listeners.exit.push(cb);
    return () => { h.listeners.exit = h.listeners.exit.filter((x) => x !== cb); };
  });
  h.shell.agentSpawn.mockResolvedValue("agent-1");
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: ["transcript.md", "summary.md"], live: false });
});

test("агенты — только установленные Claude Code и Codex; по умолчанию выбранный для итогов", () => {
  const both = agentProviders(assistant());
  expect(both.map((p) => p.label)).toEqual(["Claude Code", "Codex"]);
  expect(defaultProvider(assistant({ provider: "codex" }), both)).toBe("codex");
  expect(defaultProvider(assistant({ provider: "openai-compatible" }), both)).toBe("claude-code");
  const onlyCodex = agentProviders(assistant({ available: { codex: { found: true }, "claude-code": { found: false } } }));
  expect(onlyCodex.map((p) => p.id)).toEqual(["codex"]);
  expect(defaultProvider(assistant(), [])).toBeNull();
  expect(agentProviders(null)).toEqual([]);
});

test("Codex только как сценарий npm (codex.cmd) в список не попадает — вместо него подсказка", async () => {
  const npm = assistant({ available: {
    "claude-code": { found: true, path: "C:/bin/claude.exe" },
    codex: { found: true, path: "C:/npm/codex.cmd" },
  } });
  expect(codexScriptOnly(npm)).toBe(true);
  expect(agentProviders(npm).map((p) => p.id)).toEqual(["claude-code"]);
  expect(codexScriptOnly(assistant())).toBe(false);
  await show(npm);
  const select = screen.getByRole("combobox", { name: "Агент" });
  expect([...(select as HTMLSelectElement).options].map((o) => o.text)).toEqual(["Claude Code"]);
  expect(screen.getByText(CODEX_SCRIPT_NOTE)).toBeInTheDocument();
  expect(CODEX_SCRIPT_NOTE).toContain("codex.exe");
});

test("только codex.cmd — агентов нет, подсказка объясняет, как поставить codex.exe", async () => {
  await show(assistant({ available: {
    "claude-code": { found: false },
    codex: { found: true, path: "C:\\npm\\codex.CMD" },
  } }));
  expect(screen.getByText("Подключите Claude Code, Codex или OpenCode в настройках")).toBeInTheDocument();
  expect(screen.getByText(CODEX_SCRIPT_NOTE)).toBeInTheDocument();
});

test("подсказка о базе знаний не обещает того, что решают настройки агента", async () => {
  await show();
  expect(screen.getByText(/База знаний подключена для чтения; права на запись определяются настройками агента/))
    .toBeInTheDocument();
});

test("экран терминала на время исчез (список агентов пуст) — сеанс живёт, терминал возвращается тот же", async () => {
  const view = await show();
  await userEvent.click(startButton());
  await screen.findByText("Работает");
  await data("agent-1", "ответ агента");
  view.rerender(<AgentTab id="r1" assistant={assistant({ available: {
    "claude-code": { found: false }, codex: { found: false } } })} />);
  expect(h.shell.agentKill).not.toHaveBeenCalled();
  view.rerender(<AgentTab id="r1" assistant={assistant()} />);
  expect(await screen.findByText("Работает")).toBeInTheDocument();
  expect(h.FakeTerminal.all).toHaveLength(1);
  expect(term().text).toContain("ответ агента");
  expect(document.querySelector("[data-agent-terminal] .agent__term")).not.toBeNull();
});

test("вкладка «Агент» в карточке готовой записи", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(assistant());
  h.shell.inTauri.mockReturnValue(false);
  render(<CardTabs endpoint={ep} id="r1" folder="C:/r1" jobs={[]} transcript={<p>текст</p>} />);
  await userEvent.click(screen.getByRole("tab", { name: "Агент" }));
  expect(screen.getByRole("tab", { name: "Агент" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Доступно в приложении")).toBeInTheDocument();
});

test("вне приложения — «Доступно в приложении», терминала и запуска нет", async () => {
  h.shell.inTauri.mockReturnValue(false);
  await show();
  expect(screen.getByText("Доступно в приложении")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Запустить" })).not.toBeInTheDocument();
  expect(h.shell.onAgentData).not.toHaveBeenCalled();
  expect(h.FakeTerminal.all).toHaveLength(0);
});

test("без агентов — подсказка и «Открыть настройки»", async () => {
  const onOpenSettings = vi.fn();
  await show(assistant({ available: { "claude-code": { found: false }, codex: { found: false } } }), { onOpenSettings });
  expect(screen.getByText("Подключите Claude Code, Codex или OpenCode в настройках")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Открыть настройки" }));
  expect(onOpenSettings).toHaveBeenCalledWith("models");
});

test("выбор агента: только установленные, запуск выбранного с размером терминала", async () => {
  await show(assistant({ provider: "codex" }));
  const select = screen.getByRole("combobox", { name: "Агент" });
  expect(select).toHaveValue("codex");
  expect([...(select as HTMLSelectElement).options].map((o) => o.text)).toEqual(["Claude Code", "Codex"]);
  await userEvent.selectOptions(select, "claude-code");
  await userEvent.click(startButton());
  expect(h.shell.agentSpawn).toHaveBeenCalledWith("r1", "claude-code", 80, 24, false);
  expect(await screen.findByText("Работает")).toBeInTheDocument();
  expect(screen.getByText(/Агент запущен в папке встречи/)).toBeInTheDocument();
});

test("шапка: модель, с которой запущен агент (--model из настроек); до запуска — нет", async () => {
  h.shell.agentSpawn.mockResolvedValue({ id: "agent-1", model: "opus" });
  await show(assistant());
  expect(screen.queryByText(/модель:/)).toBeNull();
  await userEvent.click(startButton());
  expect(await screen.findByText("модель: opus")).toBeInTheDocument();
});

test("вывод своего сеанса — в терминал, в том числе пришедший до ответа на запуск; чужой — нет", async () => {
  let resolve: (id: string) => void = () => {};
  h.shell.agentSpawn.mockReturnValue(new Promise<string>((r) => { resolve = r; }));
  await show();
  await userEvent.click(startButton());
  expect(screen.getByText("Запуск…")).toBeInTheDocument();
  await data("agent-1", "Добро ");
  await data("agent-0", "чужое");
  await act(async () => resolve("agent-1"));
  await data("agent-1", "пожаловать");
  expect(term().text).toBe("Добро пожаловать");
});

test("клавиатура — агенту, смена размера — agent_resize", async () => {
  await show();
  await userEvent.click(startButton());
  await screen.findByText("Работает");
  act(() => term().onDataCb!("ls\r"));
  expect(h.shell.agentWrite).toHaveBeenCalledWith("agent-1", "ls\r");
  act(() => term().resize(120, 40));
  expect(h.shell.agentResize).toHaveBeenCalledWith("agent-1", 120, 40);
});

test("до запуска ввод и размер никуда не уходят", async () => {
  await show();
  act(() => term().onDataCb!("x"));
  act(() => term().resize(100, 30));
  expect(h.shell.agentWrite).not.toHaveBeenCalled();
  expect(h.shell.agentResize).not.toHaveBeenCalled();
});

test("«Остановить» → agent_kill; конец сеанса — «Завершён» и снова «Запустить»", async () => {
  await show();
  await userEvent.click(startButton());
  await userEvent.click(await screen.findByRole("button", { name: "Остановить" }));
  expect(h.shell.agentKill).toHaveBeenCalledWith("agent-1");
  expect(screen.getByText("Остановка…")).toBeInTheDocument();
  await exit("agent-1", 1);
  expect(screen.getByText("Завершён (код 1)")).toBeInTheDocument();
  expect(term().text).toContain("агент завершил работу");
  expect(startButton()).toBeEnabled();
  act(() => term().onDataCb!("x"));
  expect(h.shell.agentWrite).not.toHaveBeenCalled();
});

test("«Перезапустить» гасит прежний сеанс и запускает новый", async () => {
  await show();
  await userEvent.click(startButton());
  await screen.findByText("Работает");
  h.shell.agentSpawn.mockResolvedValue("agent-2");
  await userEvent.click(screen.getByRole("button", { name: "Перезапустить" }));
  expect(h.shell.agentKill).toHaveBeenCalledWith("agent-1");
  expect(h.shell.agentSpawn).toHaveBeenCalledTimes(2);
  await exit("agent-1", 1);
  expect(screen.getByText("Работает")).toBeInTheDocument();
  await data("agent-2", "новый");
  expect(term().text).toBe("новый");
});

test("ошибка запуска видна", async () => {
  h.shell.agentSpawn.mockRejectedValue("Служба записи не отвечает — агент не может запуститься");
  await show();
  await userEvent.click(startButton());
  expect(await screen.findByRole("alert")).toHaveTextContent("Служба записи не отвечает");
  expect(startButton()).toBeEnabled();
});

test("карточку закрыли (другая запись, «Голоса», «Настройки») — агент работает дальше; вернулись — тот же сеанс", async () => {
  const first = await show();
  await userEvent.click(startButton());
  await screen.findByText("Работает");
  await data("agent-1", "строка 1\r\n");
  const t = term();
  first.unmount();
  expect(h.shell.agentKill).not.toHaveBeenCalled();
  expect(t.disposed).toBe(false);
  // Пока карточки нет, агент продолжает выводить — всё попадает в его терминал.
  await data("agent-1", "строка 2\r\n");
  render(<AgentTab id="r1" assistant={assistant()} />);
  expect(await screen.findByText("Работает")).toBeInTheDocument();
  expect(h.FakeTerminal.all).toHaveLength(1);
  expect(term()).toBe(t);
  expect(t.text).toBe("строка 1\r\nстрока 2\r\n");
  expect(screen.getByRole("button", { name: "Остановить" })).toBeEnabled();
  expect(h.shell.agentSpawn).toHaveBeenCalledTimes(1);
  // Ввод — в тот же сеанс.
  await act(async () => t.onDataCb?.("q"));
  expect(h.shell.agentWrite).toHaveBeenCalledWith("agent-1", "q");
});

test("ответ на запуск пришёл, когда карточку уже закрыли, — сеанс остаётся за записью", async () => {
  let resolve: (id: string) => void = () => {};
  h.shell.agentSpawn.mockReturnValue(new Promise<string>((r) => { resolve = r; }));
  const { unmount } = await show();
  await userEvent.click(startButton());
  unmount();
  await act(async () => resolve("agent-7"));
  expect(h.shell.agentKill).not.toHaveBeenCalled();
  render(<AgentTab id="r1" assistant={assistant()} />);
  expect(await screen.findByText("Работает")).toBeInTheDocument();
});

test("Ctrl+Shift+C копирует выделение, Ctrl+Shift+V вставляет", async () => {
  const writeText = vi.fn(async () => {});
  const readText = vi.fn(async () => "вставка");
  Object.defineProperty(navigator, "clipboard", { value: { writeText, readText }, configurable: true });
  await show();
  const t = term();
  t.selection = "выделено";
  const key = (code: string) => new KeyboardEvent("keydown", { code, ctrlKey: true, shiftKey: true });
  expect(t.keys!(key("KeyC"))).toBe(false);
  expect(writeText).toHaveBeenCalledWith("выделено");
  expect(t.keys!(key("KeyV"))).toBe(false);
  await waitFor(() => expect(t.pasted).toEqual(["вставка"]));
  expect(t.keys!(new KeyboardEvent("keydown", { code: "KeyC", ctrlKey: true }))).toBe(true);
});

// --- вставка ссылок («Спросить агента») -----------------------------------------

const REF = "Про реплику:\n[01:05] Анна: «Сдаём отчёт в пятницу.»\n";
/** Как ссылка уходит в поле ввода: одной строкой, без \r, \n и ESC. */
const LINE = pasteLine(REF);
const SLOW = { timeout: QUIET_MS + 1500 };
// eslint-disable-next-line no-control-regex
const CONTROL = /[\x00-\x1f\x7f-\x9f]/;
const TRUST = "Accessing workspace: … Quick safety check: Is this a project you created or one you trust?";

afterEach(() => vi.useRealTimers());

/** Время — поддельное, но идёт и само (waitFor работает); `skip` — перескочить вперёд. */
const fakeTime = () => vi.useFakeTimers({ shouldAdvanceTime: true });
const skip = (ms: number) => act(async () => { vi.advanceTimersByTime(ms); });
const output = (text: string) => act(async () => { h.listeners.data.forEach((cb) => cb({ id: "agent-1", data: text })); });
const strip = () => screen.queryByRole("status", { name: "Ссылка ждёт вставки" });

/** Просьба при незапущенном агенте: запуск начинает сама просьба (cold). */
async function coldStart(insert: { text: string } = { text: REF }) {
  let resolve: (id: string) => void = () => {};
  h.shell.agentSpawn.mockReturnValue(new Promise<string>((r) => { resolve = r; }));
  const view = await show(assistant(), { insert });
  await waitFor(() => expect(h.shell.agentSpawn).toHaveBeenCalledTimes(1));
  return { view, started: () => act(async () => resolve("agent-1")) };
}

test("агент не запущен — запускается сам, ссылка вставляется одной строкой, без Enter", async () => {
  const onTaken = vi.fn();
  await show(assistant(), { insert: { text: REF }, onTaken });
  expect(onTaken).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(h.shell.agentSpawn).toHaveBeenCalledWith("r1", "claude-code", 80, 24, false));
  expect(strip()).toHaveTextContent("Ссылка будет вставлена в поле ввода, когда агент будет готов.");
  await waitFor(() => expect(term().pasted).toEqual([LINE]), SLOW);
  expect(LINE).toBe("Про реплику: [01:05] Анна: «Сдаём отчёт в пятницу.» ");
  expect(CONTROL.test(term().pasted[0]!)).toBe(false);
  expect(screen.getByText("Работает")).toBeInTheDocument();
  expect(term().focused).toBeGreaterThan(0);
  expect(strip()).toBeNull();
  // Вставка — не нажатие клавиш: напрямую в агента ничего не пишется.
  expect(h.shell.agentWrite).not.toHaveBeenCalled();
});

/** Запустить кнопкой и дождаться, пока сеанс впервые готов (поле ввода, режим вставки, тишина). */
async function readySession() {
  const view = await show();
  await userEvent.click(startButton());
  await screen.findByText("Работает");
  await act(() => new Promise((r) => setTimeout(r, QUIET_MS + 500)));
  return view;
}

test("сеанс уже работает — ссылка вставляется сразу, второго запуска нет; та же просьба — один раз", async () => {
  const view = await readySession();
  // Агент ещё что-то выводит, режим вставки не включён — работающему сеансу всё равно сразу.
  term().modes.bracketedPasteMode = false;
  await output("…");
  const first = { text: REF };
  view.rerender(<AgentTab id="r1" assistant={assistant()} insert={first} />);
  await waitFor(() => expect(term().pasted).toEqual([LINE]));
  expect(h.shell.agentSpawn).toHaveBeenCalledTimes(1);
  view.rerender(<AgentTab id="r1" assistant={assistant()} insert={first} />);
  const next = { text: "Про реплику:\n[00:01] Олег: «да»\n" };
  view.rerender(<AgentTab id="r1" assistant={assistant()} insert={next} />);
  view.rerender(<AgentTab id="r1" assistant={assistant()} insert={next} />);
  await waitFor(() => expect(term().pasted).toHaveLength(2));
  await new Promise((r) => setTimeout(r, 300));
  expect(term().pasted).toHaveLength(2);
});

test("разговор с фразами про вход и подтверждение не мешает готовому сеансу", async () => {
  const view = await readySession();
  term().screen = [
    "login to the portal fails · нужен sign in with Google · Approval for the budget is pending",
    "approval policy: on-request · Run npm install (y/n)? · Quick safety check (из письма заказчика)",
  ].join(" ");
  view.rerender(<AgentTab id="r1" assistant={assistant()} insert={{ text: REF }} />);
  await waitFor(() => expect(term().pasted).toEqual([LINE]));
  expect(screen.queryByText(/Подтвердите запуск агента/)).toBeNull();
});

test("холодный запуск: вывод не затих — ждём; затих на QUIET_MS — вставка", async () => {
  fakeTime();
  const { started } = await coldStart();
  await started();
  // Агент рисует экран: вывод каждые полсекунды, дольше, чем окно тишины.
  for (let i = 0; i < 6; i++) {
    await output("·");
    await skip(QUIET_MS / 2);
  }
  expect(term().pasted).toEqual([]);
  await skip(QUIET_MS + 200);
  expect(term().pasted).toEqual([LINE]);
});

test("холодный запуск: режим вставки включён и вывод затих, но поля ввода ещё нет — ждём, не теряем ссылку", async () => {
  // Claude Code включает режим вставки сразу и молчит до секунды, пока готовит
  // сеанс (fixtures/claude-trusted.json): прежнее правило вставляло в эту паузу.
  fakeTime();
  const { started } = await coldStart();
  term().screen = "";
  await started();
  await output("\x1b[?2004h\x1b[?25l");
  await skip(QUIET_MS * 3);
  expect(term().pasted).toEqual([]);
  expect(strip()).toHaveTextContent("Ссылка будет вставлена в поле ввода, когда агент будет готов.");
  // Поле ввода появилось, экран затих — вставка, один раз.
  term().screen = h.PROMPT;
  await output("\x1b[?1049h❯\u00a0");
  await skip(QUIET_MS / 2);
  expect(term().pasted).toEqual([]);
  await skip(QUIET_MS);
  expect(term().pasted).toEqual([LINE]);
  await skip(PASTE_WAIT_MS);
  expect(term().pasted).toEqual([LINE]);
});

test("диалог «доверять ли папке» вверху высокого экрана — не вставляем, хотя внизу пусто", async () => {
  fakeTime();
  const { started } = await coldStart();
  term().rows = 40;
  term().lines = ["", "", "", "", " Accessing workspace:", "", " C:\\meet\\recordings\\2026-01-01_10-00", "",
    " Quick safety check: Is this a project you created or one you trust?", "", "", "", "", "", "",
    " ❯ No, exit", "   Yes, I trust this folder", "", " Enter to confirm · Esc to cancel"];
  term().screen = "";
  await started();
  await skip(QUIET_MS * 3);
  expect(term().pasted).toEqual([]);
  expect(strip()).toHaveTextContent("Подтвердите запуск агента — ссылка будет вставлена после.");
  // Срок вышел — «Вставить ссылку» видна над терминалом.
  await skip(PASTE_WAIT_MS);
  const strip15 = strip()!;
  expect(within(strip15).getByRole("button", { name: "Вставить ссылку" })).toBeEnabled();
  expect(strip15.compareDocumentPosition(document.querySelector("[data-agent-terminal]")!)
    & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(term().pasted).toEqual([]);
});

test("Codex: поле ввода видно, но сеанс ещё не начался (нет его заголовка окна) — ждём; начался — вставка", async () => {
  // Codex рисует поле ввода сразу, а вопрос о папке — через 0,8–1,3 с
  // (fixtures/codex-trust-dialog.json); свой заголовок окна он ставит, только
  // когда сеанс начался (fixtures/codex-trusted.json).
  fakeTime();
  let resolve: (id: string) => void = () => {};
  h.shell.agentSpawn.mockReturnValue(new Promise<string>((r) => { resolve = r; }));
  await show(assistant({ provider: "codex" }), { insert: { text: REF } });
  await waitFor(() => expect(h.shell.agentSpawn).toHaveBeenCalledWith("r1", "codex", 80, 24, false));
  term().screen = "› Ask Codex to do anything";
  await act(async () => resolve("agent-1"));
  term().title("C:\\agent\\bin\\codex.exe"); // заголовок псевдоконсоли — не в счёт
  await skip(QUIET_MS * 3);
  expect(term().pasted).toEqual([]);
  term().title("2026-01-01_10-00");
  await skip(QUIET_MS + 200);
  expect(term().pasted).toEqual([LINE]);
});

test("Codex спрашивает об обновлении — не вставляем: цифра ссылки выбрала бы пункт", async () => {
  fakeTime();
  let resolve: (id: string) => void = () => {};
  h.shell.agentSpawn.mockReturnValue(new Promise<string>((r) => { resolve = r; }));
  await show(assistant({ provider: "codex" }), { insert: { text: REF } });
  await waitFor(() => expect(h.shell.agentSpawn).toHaveBeenCalledTimes(1));
  term().lines = ["", "  Update available · 0.159.0 → 0.160.0", "", "",
    "› 1. Update now (runs `powershell -ExecutionPolicy Bypass -c '…'`)", "", "  2. Skip",
    "  3. Skip until next version"];
  term().screen = "";
  await act(async () => resolve("agent-1"));
  term().title("2026-01-01_10-00");
  await skip(QUIET_MS * 3);
  expect(term().pasted).toEqual([]);
  expect(strip()).toHaveTextContent("Подтвердите запуск агента");
});

test("первый запуск: диалог «доверять ли папке» — ждём; срок от щелчка не сдвигается; ответили — вставка", async () => {
  fakeTime();
  const { started } = await coldStart();
  term().screen = TRUST;
  await started();
  await skip(QUIET_MS + 200);
  expect(term().pasted).toEqual([]);
  expect(strip()).toHaveTextContent("Подтвердите запуск агента — ссылка будет вставлена после.");
  expect(within(strip()!).getByRole("button", { name: "Вставить сейчас" })).toBeEnabled();
  expect(within(strip()!).getByRole("button", { name: "Копировать" })).toBeInTheDocument();
  // Диалог висит, агент что-то дорисовывает — срок всё равно наступает.
  for (let i = 0; i < 4; i++) {
    await output("·");
    await skip(PASTE_WAIT_MS / 4);
  }
  expect(term().pasted).toEqual([]);
  expect(within(strip()!).getByRole("button", { name: "Вставить ссылку" })).toBeEnabled();
  expect(strip()).toHaveTextContent("Вставьте ссылку, когда поле ввода будет видно");
  // Человек ответил на вопрос — агент показал поле ввода: вставка сама, один раз.
  term().screen = h.PROMPT;
  await output(h.PROMPT);
  await skip(QUIET_MS + 200);
  expect(term().pasted).toEqual([LINE]);
  expect(strip()).toBeNull();
});

test("однажды готовый сеанс: следующая просьба вставляется сразу, даже если внизу похожие слова", async () => {
  const { view, started } = await coldStart();
  await started();
  await waitFor(() => expect(term().pasted).toEqual([LINE]), SLOW);
  term().screen = TRUST;
  term().modes.bracketedPasteMode = false;
  await output("ещё пишет");
  view.rerender(<AgentTab id="r1" assistant={assistant()} insert={{ text: "Про реплику:\n[00:01] Олег: «да»\n" }} />);
  await waitFor(() => expect(term().pasted).toHaveLength(2));
});

test("«Вставить сейчас» в диалоге — вставка один раз", async () => {
  fakeTime();
  const { started } = await coldStart();
  term().screen = TRUST;
  await started();
  await skip(200);
  await userEvent.click(within(strip()!).getByRole("button", { name: "Вставить сейчас" }));
  expect(term().pasted).toEqual([LINE]);
  await skip(PASTE_WAIT_MS);
  expect(term().pasted).toEqual([LINE]);
});

test("агент не включил режим вставки — вслепую не вставляем: после срока «Вставить ссылку», один раз", async () => {
  fakeTime();
  const { started } = await coldStart();
  term().modes.bracketedPasteMode = false;
  await started();
  await skip(PASTE_WAIT_MS - 1000);
  expect(term().pasted).toEqual([]);
  expect(within(strip()!).queryByRole("button", { name: "Вставить ссылку" })).toBeNull();
  await skip(1500);
  expect(term().pasted).toEqual([]);
  await userEvent.click(within(strip()!).getByRole("button", { name: "Вставить ссылку" }));
  expect(term().pasted).toEqual([LINE]);
  expect(strip()).toBeNull();
  await skip(PASTE_WAIT_MS);
  expect(term().pasted).toEqual([LINE]);
});

test("срок от первого щелчка: вторая просьба его не продлевает", async () => {
  fakeTime();
  const { view, started } = await coldStart();
  term().modes.bracketedPasteMode = false;
  await started();
  await skip(PASTE_WAIT_MS - 2000);
  view.rerender(<AgentTab id="r1" assistant={assistant()} insert={{ text: "Про реплику:\n[00:01] Олег: «да»\n" }} />);
  await skip(2500);
  expect(within(strip()!).getByRole("button", { name: "Вставить ссылку" })).toBeInTheDocument();
});

test("несколько просьб, пока агент запускается: один запуск, обе ссылки одной вставкой", async () => {
  const { view, started } = await coldStart();
  view.rerender(<AgentTab id="r1" assistant={assistant()} insert={{ text: "Про реплику:\n[00:01] Олег: «да»\n" }} />);
  await started();
  await waitFor(() => expect(term().pasted).toHaveLength(1), SLOW);
  expect(term().pasted[0]).toBe(`${LINE.trimEnd()} Про реплику: [00:01] Олег: «да» `);
  expect(h.shell.agentSpawn).toHaveBeenCalledTimes(1);
});

test("«Отменить» — ссылка не вставляется, но и не пропадает: уведомление с «Копировать»", async () => {
  const { started } = await coldStart();
  await userEvent.click(within(strip()!).getByRole("button", { name: "Отменить" }));
  const note = await screen.findByRole("status", { name: "Ссылка не вставлена" });
  expect(note).toHaveTextContent("Вставка отменена");
  expect(note.querySelector("pre")!.textContent).toBe(REF.trimEnd());
  expect(within(note).getByRole("button", { name: "Копировать" })).toBeInTheDocument();
  await started();
  await new Promise((r) => setTimeout(r, QUIET_MS + 200));
  expect(term().pasted).toEqual([]);
});

test("«Копировать» в полосе ожидания", async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  await coldStart();
  await userEvent.click(within(strip()!).getByRole("button", { name: "Копировать" }));
  expect(writeText).toHaveBeenCalledWith(REF.trimEnd());
});

test("агент закрылся до вставки — ссылка не теряется: уведомление с «Копировать»", async () => {
  const { started } = await coldStart();
  await started();
  await exit("agent-1", 1);
  const note = await screen.findByRole("status", { name: "Ссылка не вставлена" });
  expect(note).toHaveTextContent("Агент завершил работу раньше, чем ссылка была вставлена");
  expect(note.querySelector("pre")!.textContent).toBe(REF.trimEnd());
  // Запуск вручную позже ничего старого не вставит.
  h.shell.agentSpawn.mockResolvedValue("agent-2");
  await userEvent.click(startButton());
  await screen.findByText("Работает");
  await new Promise((r) => setTimeout(r, QUIET_MS + 200));
  expect(term().pasted).toEqual([]);
});

test("агент не запустился — ссылка в уведомлении, фокус на «Копировать»", async () => {
  h.shell.agentSpawn.mockRejectedValue("Служба записи не отвечает");
  await show(assistant(), { insert: { text: REF } });
  const note = await screen.findByRole("status", { name: "Ссылка не вставлена" });
  expect(note).toHaveTextContent("Агент не запустился");
  await waitFor(() => expect(within(note).getByRole("button", { name: "Копировать" })).toHaveFocus());
  expect(strip()).toBeNull();
});

test("диалог первого запуска: точные фразы Claude Code и Codex", () => {
  for (const text of [
    TRUST,
    "❯ 1. Yes, I trust this folder   2. No, exit",
    "Do you trust the files in this folder?",
    "Trust this folder? Codex can read, edit, and run files here, subject to your permission settings.",
    "Continue only if you trust these files. Your trust decision will be saved.",
    "Allow Codex to work in this folder without asking for approval?", // прежние версии Codex
  ]) expect(CONFIRM_SCREEN.test(text)).toBe(true);
});

test("экран входа Codex — тоже диалог первого запуска: вставлять нельзя", () => {
  for (const text of [
    "> 1. Sign in with ChatGPT",
    "  2. Provide your own API key",
    "SIGN IN WITH CHATGPT", // регистр не важен
  ]) expect(CONFIRM_SCREEN.test(text)).toBe(true);
  for (const text of [
    "sign in with your ChatGPT account later",
    "provide an API key in the settings",
  ]) expect(CONFIRM_SCREEN.test(text)).toBe(false);
});

test("обычный разговор — не диалог первого запуска", () => {
  for (const text of [
    "✻ Welcome to Claude Code!   cwd: …/recordings/2026-09-14_11-00",
    "login to the portal fails",
    "нужен sign in with Google",
    "Approval for the budget is pending",
    "approval policy: on-request",
    "Run npm install (y/n)?",
    "Press Enter to continue",
    "Про реплику: [01:05] Анна: «Сдаём отчёт в пятницу.»",
  ]) expect(CONFIRM_SCREEN.test(text)).toBe(false);
});

test("без установленного агента — ссылку можно скопировать, есть «Открыть настройки»", async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const onOpenSettings = vi.fn();
  await show(assistant({ available: { "claude-code": { found: false }, codex: { found: false } } }),
    { onOpenSettings, insert: { text: REF } });
  const note = await screen.findByRole("status", { name: "Ссылка не вставлена" });
  expect(note).toHaveTextContent("Агент недоступен");
  expect(note).toHaveTextContent("[01:05] Анна: «Сдаём отчёт в пятницу.»");
  await userEvent.click(within(note).getByRole("button", { name: "Копировать" }));
  expect(writeText).toHaveBeenCalledWith(REF.trimEnd());
  await userEvent.click(within(note).getByRole("button", { name: "Открыть настройки" }));
  expect(onOpenSettings).toHaveBeenCalledWith("models");
  expect(h.shell.agentSpawn).not.toHaveBeenCalled();
});

test("вне приложения — то же уведомление вместо вставки", async () => {
  h.shell.inTauri.mockReturnValue(false);
  await show(assistant(), { insert: { text: REF } });
  expect(await screen.findByRole("status", { name: "Ссылка не вставлена" })).toHaveTextContent("Сдаём отчёт");
});

// --- контекст и прошлые вопросы ----------------------------------------------------

test("строка «Контекст»: какие файлы получит агент", async () => {
  await show(assistant(), { endpoint: ep });
  expect(await screen.findByText("Контекст: transcript.md · summary.md")).toBeInTheDocument();
  expect(api.getAgentContext).toHaveBeenCalledWith(ep, "r1");
});

test("идёт запись — агент получит ленту живого режима", async () => {
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: ["transcript.md"], live: true });
  await show(assistant(), { endpoint: ep });
  expect(await screen.findByText(/Контекст: transcript\.md — черновая лента живого режима/)).toBeInTheDocument();
});

test("агенту нечего дать (расшифровки нет) — запуск недоступен, сказано почему", async () => {
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: [], live: false });
  await show(assistant(), { endpoint: ep, insert: { text: REF } });
  expect(await screen.findByText(/Агент станет доступен, когда появится расшифровка/)).toBeInTheDocument();
  expect(startButton()).toBeDisabled();
  expect(h.shell.agentSpawn).not.toHaveBeenCalled();
});

test("контекст перечитывается при запуске (итоги могли появиться)", async () => {
  await show(assistant(), { endpoint: ep });
  await screen.findByText("Контекст: transcript.md · summary.md");
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: ["transcript.md", "summary.md", "analysis.json"], live: false });
  await userEvent.click(startButton());
  expect(await screen.findByText("Контекст: transcript.md · summary.md · analysis.json")).toBeInTheDocument();
});

test("«Прошлые вопросы»: свёрнутый список только для чтения, пока агент не запущен", async () => {
  vi.mocked(api.getQa).mockResolvedValue({ items: [
    { q: "Кто готовит отчёт?", a: "**Анна**, к пятнице.", at: 1_790_000_000, provider: "claude-code" },
  ] });
  await show(assistant(), { endpoint: ep });
  const past = await screen.findByRole("group", { name: "Прошлые вопросы" });
  expect(within(past).getByText("Прошлые вопросы (1)")).toBeInTheDocument();
  expect(within(past).getByText("Кто готовит отчёт?")).toBeInTheDocument();
  expect(within(past).getByText("Анна").tagName).toBe("STRONG");
  expect(screen.queryByRole("textbox")).toBeNull();
  await userEvent.click(startButton());
  await screen.findByText("Работает");
  expect(screen.queryByRole("group", { name: "Прошлые вопросы" })).toBeNull();
});

test("прошлых вопросов нет — и блока нет", async () => {
  await show(assistant(), { endpoint: ep });
  await screen.findByText("Контекст: transcript.md · summary.md");
  expect(screen.queryByRole("group", { name: "Прошлые вопросы" })).toBeNull();
});

test("прошлые вопросы видны и без агента", async () => {
  vi.mocked(api.getQa).mockResolvedValue({ items: [{ q: "Что решили?", a: "Перенести релиз.", at: 1, provider: null }] });
  await show(assistant({ available: { "claude-code": { found: false }, codex: { found: false } } }), { endpoint: ep });
  expect(await screen.findByText("Что решили?")).toBeInTheDocument();
});

// --- этапы карточки: агент переживает «живой режим → расшифровка → готово» -----------

test("этапы: «Живой режим · Агент» → «Расшифровка · Агент» → «Расшифровка · Итоги · Ассистент · Агент», сеанс не гаснет", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(assistant());
  const props = { endpoint: ep, id: "r1", folder: "C:/r1", jobs: [] };
  const view = render(<CardTabs {...props} stage="live" transcript={<p>лента</p>} />);
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Живой режим", "Агент"]);
  await userEvent.click(screen.getByRole("tab", { name: "Агент" }));
  await waitFor(() => expect(h.FakeTerminal.all).toHaveLength(1));
  await userEvent.click(await screen.findByRole("button", { name: "Запустить" }));
  await screen.findByText("Работает");
  view.rerender(<CardTabs {...props} stage="pending" transcript={<p>расшифровка идёт</p>} />);
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Расшифровка", "Агент"]);
  view.rerender(<CardTabs {...props} stage="ready" transcript={<p>текст</p>} />);
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Расшифровка", "Итоги", "Ассистент", "Агент"]);
  expect(screen.getByRole("tab", { name: "Агент" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Работает")).toBeInTheDocument();
  expect(h.FakeTerminal.all).toHaveLength(1);
  expect(h.shell.agentKill).not.toHaveBeenCalled();
});

test("вкладки этапа нет (итоги во время перерасшифровки) — показана первая", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(assistant());
  h.shell.inTauri.mockReturnValue(false);
  const props = { endpoint: ep, id: "r1", folder: "C:/r1", jobs: [] };
  const view = render(<CardTabs {...props} stage="ready" transcript={<p>текст</p>} />);
  await userEvent.click(screen.getByRole("tab", { name: "Итоги" }));
  view.rerender(<CardTabs {...props} stage="pending" transcript={<p>идёт расшифровка</p>} />);
  expect(screen.getByRole("tab", { name: "Расшифровка" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("идёт расшифровка")).toBeVisible();
});

test("просьба «Спросить агента» открывает вкладку «Агент» и передаёт ссылку", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(assistant());
  const onAgentTaken = vi.fn();
  const props = { endpoint: ep, id: "r1", folder: "C:/r1", jobs: [], transcript: <p>текст</p>, onAgentTaken };
  const view = render(<CardTabs {...props} />);
  expect(screen.getByRole("tab", { name: "Расшифровка" })).toHaveAttribute("aria-selected", "true");
  view.rerender(<CardTabs {...props} agentRequest={{ text: REF }} />);
  expect(screen.getByRole("tab", { name: "Агент" })).toHaveAttribute("aria-selected", "true");
  // Вкладка приняла просьбу — владелец её сбросит, и заново открытая вкладка не вставит её второй раз.
  await waitFor(() => expect(onAgentTaken).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(term().pasted).toEqual([LINE]), SLOW);
});

test("StrictMode (двойной запуск эффектов в окне разработки): ссылка всё равно вставляется", async () => {
  render(<StrictMode><AgentTab id="r1" assistant={assistant()} insert={{ text: REF }} /></StrictMode>);
  await waitFor(() => expect(h.shell.agentSpawn).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(term().pasted).toEqual([LINE]), SLOW);
});

test("StrictMode без агента: уведомление со ссылкой не пропадает", async () => {
  h.shell.inTauri.mockReturnValue(false);
  render(<StrictMode><AgentTab id="r1" assistant={assistant()} insert={{ text: REF }} /></StrictMode>);
  expect(await screen.findByRole("status", { name: "Ссылка не вставлена" })).toHaveTextContent("Сдаём отчёт");
});

test("другая запись — недоставленная ссылка не уходит в её агента", async () => {
  let resolve: (id: string) => void = () => {};
  h.shell.agentSpawn.mockReturnValue(new Promise<string>((r) => { resolve = r; }));
  const request = { text: REF };
  const view = await show(assistant(), { insert: request });
  await waitFor(() => expect(h.shell.agentSpawn).toHaveBeenCalled());
  h.shell.agentSpawn.mockResolvedValue("agent-2");
  view.rerender(<AgentTab id="r2" assistant={assistant()} insert={request} />);
  await act(async () => resolve("agent-1"));
  await new Promise((r) => setTimeout(r, 400));
  expect(term().pasted).toEqual([]);
});

test("«Продолжить прошлую» — только если этот агент уже работал в папке встречи", async () => {
  vi.mocked(api.getAgentContext).mockResolvedValue({
    files: ["transcript.md"], live: false, sessions: ["codex"],
  });
  await show(assistant(), { endpoint: ep });
  await screen.findByText(/Контекст: transcript\.md/);
  // Claude Code в этой папке не запускался — как раньше, только «Запустить».
  expect(screen.queryByRole("button", { name: "Продолжить прошлую" })).toBeNull();
  expect(startButton()).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Агент" }), "codex");
  const resume = screen.getByRole("button", { name: "Продолжить прошлую" });
  expect(screen.getByRole("button", { name: "Новая сессия" })).toHaveClass("btn--primary");
  await userEvent.click(resume);
  expect(h.shell.agentSpawn).toHaveBeenCalledWith("r1", "codex", 80, 24, true);
  expect(await screen.findByText("Работает")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Продолжить прошлую" })).toBeNull();
});

test("«Новая сессия» при прошлом сеансе запускает агента заново, без продолжения", async () => {
  vi.mocked(api.getAgentContext).mockResolvedValue({
    files: ["transcript.md"], live: false, sessions: ["claude-code"],
  });
  await show(assistant(), { endpoint: ep });
  await userEvent.click(await screen.findByRole("button", { name: "Новая сессия" }));
  expect(h.shell.agentSpawn).toHaveBeenCalledWith("r1", "claude-code", 80, 24, false);
});

// --- сеанс живёт вне карточки: вкладки, другие записи, разделы -----------------------

/** Запустить агента записи `id` (сеанс `sid`) кнопкой и вернуть вид. */
async function running(id: string, sid: string) {
  h.shell.agentSpawn.mockResolvedValueOnce(sid);
  const view = render(<AgentTab id={id} assistant={assistant()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Запустить" }));
  await screen.findByText("Работает");
  return view;
}

test("вкладки карточки: «Расшифровка» и обратно — сеанс не гаснет, вывод на месте", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(assistant());
  render(<CardTabs endpoint={ep} id="r1" folder="C:/r1" jobs={[]} transcript={<p>текст</p>} />);
  await userEvent.click(screen.getByRole("tab", { name: "Агент" }));
  await userEvent.click(await screen.findByRole("button", { name: "Запустить" }));
  await screen.findByText("Работает");
  await data("agent-1", "ответ\r\n");
  await userEvent.click(screen.getByRole("tab", { name: "Расшифровка" }));
  // Точка «агент работает» на вкладке «Агент», пока она закрыта.
  expect(screen.getByRole("tab", { name: "Агент" }).querySelector(".agent-live")).not.toBeNull();
  await data("agent-1", "ещё\r\n");
  await userEvent.click(screen.getByRole("tab", { name: "Агент" }));
  expect(h.shell.agentKill).not.toHaveBeenCalled();
  expect(h.FakeTerminal.all).toHaveLength(1);
  expect(term().text).toBe("ответ\r\nещё\r\n");
  expect(screen.getByText("Работает")).toBeInTheDocument();
});

test("другая запись: у каждой свой сеанс; вернулись — свой терминал с прокруткой", async () => {
  const one = await running("r1", "agent-1");
  await data("agent-1", "про первую\r\n");
  one.unmount();
  const two = await running("r2", "agent-2");
  await data("agent-2", "про вторую\r\n");
  two.unmount();
  render(<AgentTab id="r1" assistant={assistant()} />);
  expect(await screen.findByText("Работает")).toBeInTheDocument();
  const [t1, t2] = h.FakeTerminal.all;
  expect(t1!.text).toBe("про первую\r\n");
  expect(t2!.text).toBe("про вторую\r\n");
  expect(document.querySelector("[data-agent-terminal] .agent__term")).not.toBeNull();
  expect(h.shell.agentKill).not.toHaveBeenCalled();
});

test("«Спросить агента» — в сеанс своей записи, даже если на экране была другая", async () => {
  const one = await running("r1", "agent-1");
  one.unmount();
  const two = await running("r2", "agent-2");
  two.unmount();
  render(<AgentTab id="r1" assistant={assistant()} insert={{ text: REF }} />);
  const [t1, t2] = h.FakeTerminal.all;
  await waitFor(() => expect(t1!.pasted).toEqual([LINE]));
  expect(t2!.pasted).toEqual([]);
  expect(h.shell.agentSpawn).toHaveBeenCalledTimes(2);
});

test("ссылка ждёт холодного запуска, а карточку закрыли — агент готов, ссылка вставлена в его поле ввода", async () => {
  fakeTime();
  const { view, started } = await coldStart();
  term().screen = "";
  await started();
  view.unmount();
  // Агент дорисовал поле ввода, пока карточки не было.
  term().screen = h.PROMPT;
  await output("❯\u00a0");
  await skip(QUIET_MS + 200);
  expect(term().pasted).toEqual([LINE]);
  expect(h.shell.agentKill).not.toHaveBeenCalled();
});

test("ссылка ждёт холодного запуска — переход на другую вкладку карточки её не теряет", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(assistant());
  let resolve: (id: string) => void = () => {};
  h.shell.agentSpawn.mockReturnValue(new Promise<string>((r) => { resolve = r; }));
  const props = { endpoint: ep, id: "r1", folder: "C:/r1", jobs: [], transcript: <p>текст</p> };
  const view = render(<CardTabs {...props} />);
  view.rerender(<CardTabs {...props} agentRequest={{ text: REF }} />);
  await waitFor(() => expect(h.shell.agentSpawn).toHaveBeenCalledTimes(1));
  term().screen = "";
  await userEvent.click(screen.getByRole("tab", { name: "Расшифровка" }));
  await act(async () => resolve("agent-1"));
  term().screen = h.PROMPT;
  await output("❯\u00a0");
  await waitFor(() => expect(term().pasted).toEqual([LINE]), SLOW);
});

test("больше трёх агентов: закрывается давно не открывавшийся бездельник, занятый — никогда; вернулись — «Продолжить прошлую»", async () => {
  fakeTime();
  (await running("r1", "agent-1")).unmount();
  await skip(1000);
  (await running("r2", "agent-2")).unmount();
  await skip(1000);
  (await running("r3", "agent-3")).unmount();
  await skip(BUSY_MS + 1000);
  // Первая запись отвечает прямо сейчас — она занята, хоть и самая давняя.
  await data("agent-1", "думаю…");
  (await running("r4", "agent-4")).unmount();
  expect(h.shell.agentKill).toHaveBeenCalledTimes(1);
  expect(h.shell.agentKill).toHaveBeenCalledWith("agent-2");
  await exit("agent-2", null);
  render(<AgentTab id="r2" assistant={assistant()} />);
  const note = await screen.findByRole("status", { name: "Сессия закрыта" });
  expect(note).toHaveTextContent("Сессия была закрыта, чтобы освободить ресурсы");
  h.shell.agentSpawn.mockResolvedValueOnce("agent-5");
  await userEvent.click(within(note).getByRole("button", { name: "Продолжить прошлую" }));
  expect(h.shell.agentSpawn).toHaveBeenLastCalledWith("r2", "claude-code", 80, 24, true);
  expect(await screen.findByText("Работает")).toBeInTheDocument();
  expect(screen.queryByRole("status", { name: "Сессия закрыта" })).toBeNull();
});

test("все три агента заняты — четвёртый запуск никого не закрывает", async () => {
  for (const [id, sid] of [["r1", "agent-1"], ["r2", "agent-2"], ["r3", "agent-3"]] as const) {
    (await running(id, sid)).unmount();
    await data(sid, "отвечаю…");
  }
  (await running("r4", "agent-4")).unmount();
  expect(h.shell.agentKill).not.toHaveBeenCalled();
});

test("«Остановить», пока агент что-то выводит, — сначала вопрос; без вывода — сразу", async () => {
  await running("r1", "agent-1");
  await data("agent-1", "пишу ответ…");
  await userEvent.click(screen.getByRole("button", { name: "Остановить" }));
  const ask = screen.getByRole("alertdialog", { name: "Остановить агента?" });
  await userEvent.click(within(ask).getByRole("button", { name: "Отмена" }));
  expect(h.shell.agentKill).not.toHaveBeenCalled();
  expect(screen.getByText("Работает")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Остановить" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Остановить" }));
  expect(h.shell.agentKill).toHaveBeenCalledWith("agent-1");
});

test("агент ждёт ответа на вопрос о разрешении (поля ввода нет) — занят: его не закрывают ради четвёртого", async () => {
  fakeTime();
  (await running("r1", "agent-1")).unmount();
  // Claude Code спрашивает разрешения: вместо поля ввода — пункты выбора, вывода нет.
  term().lines = ["", " Bash command", "   rm -rf build", " Do you want to proceed?"];
  term().screen = " ❯ 1. Yes   2. No";
  await skip(1000);
  (await running("r2", "agent-2")).unmount();
  await skip(1000);
  (await running("r3", "agent-3")).unmount();
  await skip(BUSY_MS + 1000);
  (await running("r4", "agent-4")).unmount();
  expect(h.shell.agentKill).toHaveBeenCalledTimes(1);
  expect(h.shell.agentKill).toHaveBeenCalledWith("agent-2");
});

test("«Перезапустить», пока агент что-то выводит, — тот же вопрос, что у «Остановить»", async () => {
  await running("r1", "agent-1");
  await data("agent-1", "пишу ответ…");
  await userEvent.click(screen.getByRole("button", { name: "Перезапустить" }));
  const ask = screen.getByRole("alertdialog", { name: "Перезапустить агента?" });
  await userEvent.click(within(ask).getByRole("button", { name: "Отмена" }));
  expect(h.shell.agentKill).not.toHaveBeenCalled();
  expect(h.shell.agentSpawn).toHaveBeenCalledTimes(1);
  h.shell.agentSpawn.mockResolvedValueOnce("agent-2");
  await userEvent.click(screen.getByRole("button", { name: "Перезапустить" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Перезапустить" }));
  expect(h.shell.agentKill).toHaveBeenCalledWith("agent-1");
  await waitFor(() => expect(h.shell.agentSpawn).toHaveBeenCalledTimes(2));
});

test("сеанс закончился, пока его вкладка закрыта, — терминал освобождается; вернулись — «Завершён» и новый терминал", async () => {
  (await running("r1", "agent-1")).unmount();
  const t = term();
  await exit("agent-1", 0);
  expect(t.disposed).toBe(true);
  render(<AgentTab id="r1" assistant={assistant()} />);
  expect(await screen.findByText("Завершён")).toBeInTheDocument();
  await waitFor(() => expect(h.FakeTerminal.all).toHaveLength(2));
  expect(await screen.findByRole("button", { name: "Запустить" })).toBeEnabled();
});

test("OpenCode — третий агент; только как сценарий npm (opencode.cmd) — в список не попадает, вместо него подсказка", async () => {
  const all = assistant({ available: {
    "claude-code": { found: true, path: "C:/bin/claude.exe" },
    codex: { found: false },
    opencode: { found: true, path: "C:/oc/opencode.exe" },
  } });
  expect(agentProviders(all).map((p) => p.label)).toEqual(["Claude Code", "OpenCode"]);
  expect(defaultProvider({ ...all, provider: "opencode" }, agentProviders(all))).toBe("opencode");
  const npm = assistant({ available: {
    "claude-code": { found: false },
    opencode: { found: true, path: "C:\\npm\\opencode.cmd" },
  } });
  expect(opencodeScriptOnly(npm)).toBe(true);
  expect(agentProviders(npm)).toEqual([]);
  await show(npm);
  expect(screen.getByText(OPENCODE_SCRIPT_NOTE)).toBeInTheDocument();
  expect(OPENCODE_SCRIPT_NOTE).toContain("opencode.exe");
});

test("OpenCode: поле ввода по его TUI, без режима вставки; диалог входа — не вставляем; без признаков — только после долгой тишины", () => {
  const prompt = ["", "  ┃  Ask anything… \"Fix a TODO in the codebase\"", "  ┃  Build  Claude Sonnet 4.5", "",
    "                                   tab agents  ctrl+p commands"];
  expect(opencodePromptVisible(prompt)).toBe(true);
  expect(opencodePromptVisible(["", "  ┃  Build", "        ctrl+p commands  "])).toBe(true);
  expect(opencodePromptVisible(["", "  opencode", "  loading…"])).toBe(false);
  const opts = { bracketed: false, provider: "opencode", titled: false };
  expect(coldReadiness(prompt, opts)).toBe("ready");
  expect(coldReadiness(["  Connect a provider", "  Search", ...prompt], opts)).toBe("confirm");
  expect(coldReadiness(["  Select auth method", ...prompt], opts)).toBe("confirm");
  expect(coldReadiness(["", "  opencode"], opts)).toBe("quiet");
  expect(quietNeeded("ready", "opencode")).toBe(OPENCODE_QUIET_MS);
  expect(quietNeeded("quiet", "opencode")).toBe(OPENCODE_FALLBACK_QUIET_MS);
  expect(OPENCODE_FALLBACK_QUIET_MS).toBeGreaterThan(OPENCODE_QUIET_MS);
  expect(OPENCODE_QUIET_MS).toBeGreaterThan(QUIET_MS);
  expect(quietNeeded("ready", "claude-code")).toBe(QUIET_MS);
  expect(quietNeeded("waiting", "opencode")).toBeNull();
  expect(quietNeeded("confirm", "opencode")).toBeNull();
  // У Claude Code и Codex правило прежнее: «quiet» у них не бывает.
  expect(coldReadiness(["", "  opencode"], { ...opts, provider: "codex" })).toBe("waiting");
});

test("текст до спикеров (Р4): агент пока недоступен — сказано, что ждём спикеров", async () => {
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: [], live: false });
  await show(assistant(), { endpoint: ep, contextVersion: "text:1", textPhase: true });
  expect(await screen.findByText("Текст уже виден — агент станет доступен, когда определятся спикеры.")).toBeInTheDocument();
  expect(startButton()).toBeDisabled();
});

test("пришли спикеры — агент перечитывает, что получит, и запуск становится доступен", async () => {
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: [], live: false });
  const view = await show(assistant(), { endpoint: ep, contextVersion: "text:1", textPhase: true });
  await screen.findByText(/когда определятся спикеры/);
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: ["transcript.md"], live: false });
  view.rerender(<AgentTab id="r1" assistant={assistant()} endpoint={ep} contextVersion="final:2" />);
  await waitFor(() => expect(startButton()).toBeEnabled());
  expect(api.getAgentContext).toHaveBeenCalledTimes(2);
  expect(screen.queryByText(/когда определятся спикеры/)).toBeNull();
});
