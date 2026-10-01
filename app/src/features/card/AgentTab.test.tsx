import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentTab, agentProviders, defaultProvider } from "./AgentTab";
import { CardTabs } from "./CardTabs";
import * as api from "../../lib/api";
import type { AgentData, AgentExit } from "../../lib/shell";
import type { AssistantInfo } from "../../lib/types";

const h = vi.hoisted(() => {
  class FakeTerminal {
    static all: FakeTerminal[] = [];
    cols = 80;
    rows = 24;
    written: string[] = [];
    selection = "";
    pasted: string[] = [];
    disposed = false;
    resets = 0;
    onDataCb: ((d: string) => void) | null = null;
    onResizeCb: ((s: { cols: number; rows: number }) => void) | null = null;
    keys: ((e: KeyboardEvent) => boolean) | null = null;
    constructor(public options: unknown) { FakeTerminal.all.push(this); }
    loadAddon() {}
    open() {}
    attachCustomKeyEventHandler(fn: (e: KeyboardEvent) => boolean) { this.keys = fn; }
    onData(cb: (d: string) => void) { this.onDataCb = cb; return { dispose() {} }; }
    onResize(cb: (s: { cols: number; rows: number }) => void) { this.onResizeCb = cb; return { dispose() {} }; }
    write(data: string) { this.written.push(data); }
    reset() { this.written = []; this.resets++; }
    focus() {}
    dispose() { this.disposed = true; }
    hasSelection() { return this.selection !== ""; }
    getSelection() { return this.selection; }
    clearSelection() { this.selection = ""; }
    paste(text: string) { this.pasted.push(text); }
    /** Как будто подгонка под окно поменяла размер. */
    resize(cols: number, rows: number) { this.cols = cols; this.rows = rows; this.onResizeCb?.({ cols, rows }); }
    get text() { return this.written.join(""); }
  }
  return {
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
const data = (id: string, text: string) => act(() => h.listeners.data.forEach((cb) => cb({ id, data: text })));
const exit = (id: string, code: number | null) => act(() => h.listeners.exit.forEach((cb) => cb({ id, code })));

async function show(info: AssistantInfo | null = assistant(), more: { onOpenSettings?: (s: string) => void } = {}) {
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

test("вкладка «Агент» в карточке готовой записи", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(assistant());
  h.shell.inTauri.mockReturnValue(false);
  render(<CardTabs endpoint={{ base: "/api", token: null }} id="r1" folder="C:/r1" jobs={[]} transcript={<p>текст</p>} />);
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
  expect(screen.getByText("Подключите Claude Code или Codex в настройках")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Открыть настройки" }));
  expect(onOpenSettings).toHaveBeenCalledWith("assistant");
});

test("выбор агента: только установленные, запуск выбранного с размером терминала", async () => {
  await show(assistant({ provider: "codex" }));
  const select = screen.getByRole("combobox", { name: "Агент" });
  expect(select).toHaveValue("codex");
  expect([...(select as HTMLSelectElement).options].map((o) => o.text)).toEqual(["Claude Code", "Codex"]);
  await userEvent.selectOptions(select, "claude-code");
  await userEvent.click(startButton());
  expect(h.shell.agentSpawn).toHaveBeenCalledWith("r1", "claude-code", 80, 24);
  expect(await screen.findByText("Работает")).toBeInTheDocument();
  expect(screen.getByText(/Агент запущен в папке встречи/)).toBeInTheDocument();
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
  h.shell.agentSpawn.mockRejectedValue("Сервис записи не отвечает — агент не может запуститься");
  await show();
  await userEvent.click(startButton());
  expect(await screen.findByRole("alert")).toHaveTextContent("Сервис записи не отвечает");
  expect(startButton()).toBeEnabled();
});

test("закрытие карточки гасит агента и терминал", async () => {
  const { unmount } = await show();
  await userEvent.click(startButton());
  await screen.findByText("Работает");
  const t = term();
  unmount();
  expect(h.shell.agentKill).toHaveBeenCalledWith("agent-1");
  expect(t.disposed).toBe(true);
  expect(h.listeners.data).toHaveLength(0);
});

test("ответ на запуск после закрытия карточки — сеанс сразу гасится", async () => {
  let resolve: (id: string) => void = () => {};
  h.shell.agentSpawn.mockReturnValue(new Promise<string>((r) => { resolve = r; }));
  const { unmount } = await show();
  await userEvent.click(startButton());
  unmount();
  await act(async () => resolve("agent-7"));
  expect(h.shell.agentKill).toHaveBeenCalledWith("agent-7");
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
