import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { SHELL_POLL_MS, SHELL_STARTING_POLLS } from "../features/wizard/useWizardGate";
import * as api from "../lib/api";
import * as shell from "../lib/shell";

const ep = { base: "/api", token: null };
const residentState = vi.hoisted(() => ({ current: { status: "offline" } as Record<string, unknown> }));
vi.mock("../state/useResident", () => ({ useResident: () => residentState.current }));
const useLibrarySpy = vi.hoisted(() => vi.fn());
vi.mock("../state/useLibrary", () => ({ useLibrary: useLibrarySpy }));
const refreshPeople = vi.hoisted(() => vi.fn(async () => {}));
vi.mock("../state/usePeople", () => ({
  usePeople: () => ({ people: [], refresh: refreshPeople, avatarVersion: {}, bumpAvatar: () => {} }),
}));
vi.mock("../features/card/RecordingCard", () => ({
  RecordingCard: ({ id, onOpenSettings }: { id: string; onOpenSettings?: (s: string) => void }) => (
    <div data-testid="card">{id}<button onClick={() => onOpenSettings?.("assistant")}>в настройки</button></div>
  ),
}));
vi.mock("../features/voices/VoicesPane", () => ({ VoicesPane: () => <div data-testid="voices" /> }));
vi.mock("../features/settings/SettingsPane", () => ({
  SettingsPane: ({ initial, initialTick, onRunWizard }: {
    initial?: string; initialTick?: number; onRunWizard?: () => void;
  }) => (
    <div data-testid="settings" data-initial={initial ?? ""} data-tick={initialTick ?? ""}>
      <button onClick={() => onRunWizard?.()}>Запустить мастер</button>
    </div>
  ),
}));
vi.mock("../features/wizard/Wizard", () => ({
  Wizard: ({ start, onClose, onInstallStarted }: {
    start?: string; onClose: () => void; onInstallStarted?: () => void;
  }) => (
    <div data-testid="wizard" data-start={start ?? ""}>
      <button onClick={onClose}>закрыть мастер</button>
      <button onClick={() => onInstallStarted?.()}>начать установку</button>
    </div>
  ),
}));
vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  getSettings: vi.fn(async () => ({ ui: { wizard_done: false } })),
  patchSettings: vi.fn(async () => ({ settings: {}, restart_required: [] })),
}));
const engineState = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }));
const openCb = vi.hoisted(() => ({ current: null as ((id: string) => void) | null }));
const sectionCb = vi.hoisted(() => ({ current: null as ((s: string) => void) | null }));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  onOpenRecording: vi.fn(async (cb: (id: string) => void) => {
    openCb.current = cb;
    return () => {};
  }),
  onOpenSection: vi.fn(async (cb: (s: string) => void) => {
    sectionCb.current = cb;
    return () => {};
  }),
  engineStatus: vi.fn(async () => engineState.current),
  residentStatus: vi.fn(async () => "engine-missing"),
  markWizardDone: vi.fn(async () => {}),
}));

const OFFLINE = /Служба записи не запущена/;
const online = () => ({ status: "online", endpoint: ep, snapshot: null, lastEvent: null, libraryTick: 0 });

beforeEach(() => {
  vi.clearAllMocks();
  residentState.current = { status: "offline" };
  engineState.current = null;
  vi.mocked(shell.residentStatus).mockResolvedValue("engine-missing");
  localStorage.removeItem("meet.wizard_done");
  openCb.current = null;
  sectionCb.current = null;
  window.history.replaceState({}, "", "/");
  useLibrarySpy.mockReset();
  useLibrarySpy.mockReturnValue({ items: [], jobs: [], loading: false, error: null, refresh: async () => {} });
});

test("три раздела; у записей есть список, у голосов — нет", async () => {
  const { container } = render(<App />);
  expect(screen.getByRole("navigation")).toHaveTextContent(/Записи.*Голоса.*Настройки/);
  expect(container.querySelector('[data-pane="list"]')).not.toBeNull();
  await userEvent.click(screen.getByText("Голоса"));
  expect(container.querySelector('[data-pane="list"]')).toBeNull();
});

test("строка поиска из списка уходит в useLibrary", async () => {
  residentState.current = online();
  render(<App />);
  await userEvent.type(screen.getByRole("searchbox"), "план");
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "план", 0);
});

test("?recording=abc в адресе — выбрана запись abc", () => {
  residentState.current = online();
  window.history.replaceState({}, "", "/?recording=abc");
  render(<App />);
  expect(screen.getByTestId("card")).toHaveTextContent("abc");
});

test("событие open-recording переключает на «Записи» и выбирает запись", async () => {
  residentState.current = online();
  render(<App />);
  await userEvent.click(screen.getByText("Голоса"));
  expect(screen.getByTestId("voices")).toBeInTheDocument();
  await vi.waitFor(() => expect(openCb.current).not.toBeNull());
  act(() => openCb.current!("rec-42"));
  expect(screen.getByTestId("card")).toHaveTextContent("rec-42");
});

test("офлайн: в списке и в карточке — «Служба записи не запущена», данных нет", () => {
  useLibrarySpy.mockReturnValue({
    items: [{ id: "a", path: "C:/rec/a", started_at: "2026-09-30T10:00:00", duration_s: 60, tracks: {},
      has_transcript: true, has_voices: false, title: "Старая", source: "record" }],
    jobs: [], loading: false, error: null, refresh: async () => {},
  });
  const { container } = render(<App />);
  const list = container.querySelector('[data-pane="list"]')!;
  const detail = container.querySelector('[data-pane="detail"]')!;
  expect(list).toHaveTextContent(OFFLINE);
  expect(list).not.toHaveTextContent("Старая");
  expect(detail).toHaveTextContent(OFFLINE);
  expect(detail).toHaveTextContent("Приложение перезапускает его — подождите несколько секунд.");
});

test.each(["Голоса", "Настройки"])("офлайн: в разделе «%s» — то же сообщение", async (name) => {
  const { container } = render(<App />);
  await userEvent.click(screen.getByText(name));
  expect(container.querySelector('[data-pane="detail"]')).toHaveTextContent(OFFLINE);
});

test("пустой список при живом резиденте — подсказка про запись и перетаскивание", () => {
  residentState.current = online();
  render(<App />);
  expect(screen.getByText("Записей пока нет")).toBeInTheDocument();
  expect(screen.getByText("Нажмите «Начать запись» или перетащите файл")).toBeInTheDocument();
});

test("вход в «Голоса» перечитывает базу людей", async () => {
  residentState.current = online();
  render(<App />);
  refreshPeople.mockClear();
  await userEvent.click(screen.getByText("Голоса"));
  expect(refreshPeople).toHaveBeenCalledTimes(1);
});

test("карточка просит настройки «Ассистент» — открыт раздел настроек с нужной секцией", async () => {
  residentState.current = online();
  window.history.replaceState({}, "", "/?recording=abc");
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "в настройки" }));
  expect(screen.getByTestId("settings")).toHaveAttribute("data-initial", "assistant");
  // Обычный переход в настройки из меню — с начала, без запомненной секции.
  await userEvent.click(screen.getByText("Записи"));
  await userEvent.click(screen.getByText("Настройки"));
  expect(screen.getByTestId("settings")).toHaveAttribute("data-initial", "");
});

test("?section=assistant в адресе — открыты настройки «Ассистент», запись из адреса не теряется", async () => {
  residentState.current = online();
  window.history.replaceState({}, "", "/?recording=abc&section=assistant");
  render(<App />);
  expect(screen.getByTestId("settings")).toHaveAttribute("data-initial", "assistant");
  await userEvent.click(screen.getByText("Записи"));
  expect(screen.getByTestId("card")).toHaveTextContent("abc");
});

test("событие open-section открывает настройки на нужной секции — и повторно тоже", async () => {
  residentState.current = online();
  render(<App />);
  await vi.waitFor(() => expect(sectionCb.current).not.toBeNull());
  act(() => sectionCb.current!("assistant"));
  const pane = screen.getByTestId("settings");
  expect(pane).toHaveAttribute("data-initial", "assistant");
  const tick = pane.getAttribute("data-tick");
  act(() => sectionCb.current!("assistant"));
  expect(screen.getByTestId("settings").getAttribute("data-tick")).not.toBe(tick);
});

const missingEngine = () => ({
  installed: false, version: "0.1.0", env_dir: "C:\meet\engine\0.1.0", profile: null,
  gpu: null, free_gb: 50, needs_gb: 2,
});

test("движка нет, резидента нет — сам открывается мастер", async () => {
  engineState.current = missingEngine();
  render(<App />);
  expect(await screen.findByTestId("wizard")).toHaveAttribute("data-start", "hardware");
  expect(screen.queryByRole("navigation")).toBeNull();
});

test("«Пропустить» мастер: флаг в localStorage и в оболочке; вместо мастера — «Движок не установлен»", async () => {
  engineState.current = missingEngine();
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "закрыть мастер" }));
  expect(localStorage.getItem("meet.wizard_done")).toBe("1");
  expect(shell.markWizardDone).toHaveBeenCalled();
  expect(screen.queryByTestId("wizard")).toBeNull();
  expect(screen.getByRole("navigation")).toBeInTheDocument();
  expect(screen.getAllByText("Движок не установлен").length).toBeGreaterThan(0);
});

test("wizard_done — мастер сам не показывается при installed:false; «Установить» открывает его на движке", async () => {
  localStorage.setItem("meet.wizard_done", "1");
  engineState.current = missingEngine();
  const { container } = render(<App />);
  const detail = container.querySelector<HTMLElement>('[data-pane="detail"]')!;
  await waitFor(() => expect(detail).toHaveTextContent("Движок не установлен"));
  expect(screen.queryByTestId("wizard")).toBeNull();
  expect(detail).not.toHaveTextContent(OFFLINE);
  await userEvent.click(within(detail).getByRole("button", { name: "Установить" }));
  expect(screen.getByTestId("wizard")).toHaveAttribute("data-start", "engine");
});

test("dev: резидент отвечает — мастер не показывается, даже если движка нет", async () => {
  engineState.current = missingEngine();
  residentState.current = online();
  render(<App />);
  await waitFor(() => expect(shell.engineStatus).toHaveBeenCalled());
  await act(async () => {});
  expect(screen.queryByTestId("wizard")).toBeNull();
});

test("оболочка говорит, что резидент работает, — мастер не показывается", async () => {
  engineState.current = missingEngine();
  vi.mocked(shell.residentStatus).mockResolvedValueOnce("running");
  render(<App />);
  await waitFor(() => expect(shell.residentStatus).toHaveBeenCalled());
  await act(async () => {});
  expect(screen.queryByTestId("wizard")).toBeNull();
});

test("пропуск без резидента дописывается в его настройки, когда он появится", async () => {
  localStorage.setItem("meet.wizard_done", "1");
  residentState.current = online();
  render(<App />);
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { ui: { wizard_done: true } }));
});

test("настройки: «Запустить мастер» открывает его с начала; закрыли — снова настройки", async () => {
  residentState.current = online();
  render(<App />);
  await userEvent.click(screen.getByText("Настройки"));
  await userEvent.click(screen.getByRole("button", { name: "Запустить мастер" }));
  expect(screen.getByTestId("wizard")).toHaveAttribute("data-start", "hardware");
  await userEvent.click(screen.getByRole("button", { name: "закрыть мастер" }));
  expect(screen.getByTestId("settings")).toBeInTheDocument();
});

test("в приложении мастер — только при «engine-missing» от оболочки: «starting» его не вызывает", async () => {
  engineState.current = missingEngine();
  vi.mocked(shell.residentStatus).mockResolvedValue("starting");
  const { container } = render(<App />);
  await waitFor(() => expect(shell.residentStatus).toHaveBeenCalled());
  await act(async () => {});
  expect(screen.queryByTestId("wizard")).toBeNull();
  expect(container.querySelector('[data-pane="detail"]')).toHaveTextContent(OFFLINE);
});

test("оболочка ещё «starting» — окно спрашивает снова и показывает мастер при «engine-missing»", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    engineState.current = missingEngine();
    vi.mocked(shell.residentStatus).mockResolvedValue("starting");
    render(<App />);
    await waitFor(() => expect(shell.residentStatus).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByTestId("wizard")).toBeNull();
    vi.mocked(shell.residentStatus).mockResolvedValue("engine-missing");
    await act(async () => { await vi.advanceTimersByTimeAsync(SHELL_POLL_MS); });
    expect(screen.getByTestId("wizard")).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

test("опрос «starting» ограничен: оболочка так и не ответила иначе — окно перестаёт спрашивать", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    engineState.current = missingEngine();
    vi.mocked(shell.residentStatus).mockResolvedValue("starting");
    render(<App />);
    await waitFor(() => expect(shell.residentStatus).toHaveBeenCalled());
    await act(async () => { await vi.advanceTimersByTimeAsync(SHELL_POLL_MS * (SHELL_STARTING_POLLS + 5)); });
    const calls = vi.mocked(shell.residentStatus).mock.calls.length;
    expect(calls).toBeLessThanOrEqual(SHELL_STARTING_POLLS + 1);
    await act(async () => { await vi.advanceTimersByTimeAsync(SHELL_POLL_MS * 10); });
    expect(vi.mocked(shell.residentStatus).mock.calls.length).toBe(calls);
  } finally {
    vi.useRealTimers();
  }
});

test("резидент ожил, пока мастер сам открыт и не тронут, — мастер убирается, флаг не пишется", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    engineState.current = missingEngine();
    vi.mocked(shell.residentStatus).mockResolvedValue("engine-missing");
    render(<App />);
    expect(await screen.findByTestId("wizard")).toBeInTheDocument();
    vi.mocked(shell.residentStatus).mockResolvedValue("running");
    await act(async () => { await vi.advanceTimersByTimeAsync(SHELL_POLL_MS); });
    expect(screen.queryByTestId("wizard")).toBeNull();
    expect(localStorage.getItem("meet.wizard_done")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("после начала установки мастер не убирается, когда резидент поднимается", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    engineState.current = missingEngine();
    vi.mocked(shell.residentStatus).mockResolvedValue("engine-missing");
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "начать установку" }));
    vi.mocked(shell.residentStatus).mockResolvedValue("running");
    await act(async () => { await vi.advanceTimersByTimeAsync(SHELL_POLL_MS); });
    expect(screen.getByTestId("wizard")).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});
