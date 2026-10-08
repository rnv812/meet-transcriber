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
  RecordingCard: ({ id, onOpenSettings, find }: {
    id: string; onOpenSettings?: (s: string) => void; find?: { q: string } | null;
  }) => (
    <div data-testid="card" data-find={find?.q ?? ""}>{id}<button onClick={() => onOpenSettings?.("assistant")}>в настройки</button></div>
  ),
}));
vi.mock("../features/voices/VoicesPane", () => ({ VoicesPane: () => <div data-testid="voices" /> }));
const settingsSave = vi.hoisted(() => vi.fn(async () => true));
vi.mock("../features/settings/SettingsPane", () => ({
  SettingsPane: ({ initial, initialTick, onRunWizard, guardRef }: {
    initial?: string; initialTick?: number; onRunWizard?: () => void;
    guardRef?: { current: unknown };
  }) => (
    <div data-testid="settings" data-initial={initial ?? ""} data-tick={initialTick ?? ""}>
      <button onClick={() => onRunWizard?.()}>Запустить мастер</button>
      <button onClick={() => { if (guardRef) guardRef.current = { dirty: ["Запись"], canSave: true, save: settingsSave }; }}>
        правка
      </button>
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
  // По умолчанию — резидент без групп (как до 0.3.5): интерфейс групп скрыт.
  getGroups: vi.fn(async () => { throw new (await import("../lib/api")).ApiError(404, "нет"); }),
  getParticipants: vi.fn(async () => []),
  getFacets: vi.fn(async () => ({
    total: 3, scope: "library", categories: { items: [], none: 3 },
    groups: { items: [{ id: "g-a", count: 2 }, { id: "g-b", count: 1 }], unknown: [], none: 0 },
    people: [], has: { summary: 0, analysis: 0, assistant: 0, transcript: 3 }, duration: { lt15: 0, m15_60: 3, gt60: 0 },
  })),
  patchSettings: vi.fn(async () => ({ settings: {}, restart_required: [] })),
}));
const engineState = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }));
const openCb = vi.hoisted(() => ({ current: null as ((id: string) => void) | null }));
const sectionCb = vi.hoisted(() => ({ current: null as ((s: string) => void) | null }));
const closeCb = vi.hoisted(() => ({ current: null as (() => void) | null }));
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
  onSettingsCloseGuard: vi.fn(async (cb: () => void) => {
    closeCb.current = cb;
    return () => {};
  }),
  settingsCloseAck: vi.fn(async () => {}),
  settingsCloseGo: vi.fn(async () => {}),
  settingsCloseStay: vi.fn(async () => {}),
  setSettingsDirty: vi.fn(async () => {}),
  engineStatus: vi.fn(async () => engineState.current),
  residentStatus: vi.fn(async () => "engine-missing"),
  markWizardDone: vi.fn(async () => {}),
}));

const OFFLINE = /Служба записи не запущена/;
const online = () => ({ status: "online", endpoint: ep, snapshot: null, lastEvent: null, libraryTick: 0, contentTick: 0 });

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

test("уход из настроек с несохранённым: «Остаться», «Не сохранять», «Сохранить»", async () => {
  residentState.current = online();
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  // Правок нет — уходим молча.
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.getByTestId("voices")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  await userEvent.click(screen.getByRole("button", { name: "правка" }));
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  const ask = screen.getByRole("alertdialog", { name: "Сохранить изменения в настройках?" });
  expect(ask).toHaveTextContent("«Запись»");
  // По умолчанию — безопасное «Остаться»; Esc — тоже остаться.
  expect(within(ask).getByRole("button", { name: "Остаться" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.getByTestId("settings")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(screen.getByTestId("voices")).toBeInTheDocument());
  expect(settingsSave).toHaveBeenCalledTimes(1);

  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  await userEvent.click(screen.getByRole("button", { name: "правка" }));
  await userEvent.click(screen.getByRole("button", { name: "Записи" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Не сохранять" }));
  expect(screen.queryByTestId("settings")).toBeNull();
  expect(settingsSave).toHaveBeenCalledTimes(1);
});

test("«Запустить мастер» из настроек с правками — сначала вопрос, мастер — после «Не сохранять»", async () => {
  residentState.current = online();
  vi.mocked(shell.residentStatus).mockResolvedValue("running");
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  await userEvent.click(screen.getByRole("button", { name: "правка" }));
  await userEvent.click(screen.getByRole("button", { name: "Запустить мастер" }));
  expect(screen.getByRole("alertdialog", { name: "Сохранить изменения в настройках?" })).toBeInTheDocument();
  expect(screen.queryByTestId("wizard")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Не сохранять" }));
  expect(await screen.findByTestId("wizard")).toBeInTheDocument();
});

test("крестик окна: оболочка спросила — вопрос показан (ack), ответ уходит в оболочку", async () => {
  residentState.current = online();
  render(<App />);
  await waitFor(() => expect(closeCb.current).not.toBeNull());
  // Оболочка спросила, а несохранённого уже нет (успели сохранить) — сразу «закрыть».
  act(() => closeCb.current!());
  expect(shell.settingsCloseGo).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alertdialog")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  await userEvent.click(screen.getByRole("button", { name: "правка" }));
  act(() => closeCb.current!());
  expect(shell.settingsCloseAck).toHaveBeenCalledTimes(1);
  let ask = screen.getByRole("alertdialog", { name: "Сохранить изменения в настройках?" });
  expect(within(ask).getByRole("button", { name: "Остаться" })).toHaveFocus();
  await userEvent.click(within(ask).getByRole("button", { name: "Остаться" }));
  expect(shell.settingsCloseStay).toHaveBeenCalledTimes(1);
  expect(shell.settingsCloseGo).toHaveBeenCalledTimes(1);

  act(() => closeCb.current!());
  ask = screen.getByRole("alertdialog", { name: "Сохранить изменения в настройках?" });
  await userEvent.click(within(ask).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(shell.settingsCloseGo).toHaveBeenCalledTimes(2));
  expect(settingsSave).toHaveBeenCalled();
});

test("«Остаться», пока идёт сохранение, — после сохранения никуда не уходим", async () => {
  residentState.current = online();
  let finish: (ok: boolean) => void = () => {};
  settingsSave.mockImplementationOnce(() => new Promise<boolean>((r) => { finish = r; }));
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  await userEvent.click(screen.getByRole("button", { name: "правка" }));
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Сохранить" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Остаться" }));
  await act(async () => finish(true));
  expect(screen.getByTestId("settings")).toBeInTheDocument();
  expect(screen.queryByTestId("voices")).toBeNull();
});

test("сохранить при уходе не вышло — остаёмся в настройках", async () => {
  residentState.current = online();
  settingsSave.mockResolvedValueOnce(false);
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  await userEvent.click(screen.getByRole("button", { name: "правка" }));
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(screen.getByTestId("settings")).toBeInTheDocument();
});

test("клик по уведомлению из настроек с правками — тоже через вопрос", async () => {
  residentState.current = online();
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  await userEvent.click(screen.getByRole("button", { name: "правка" }));
  await waitFor(() => expect(openCb.current).not.toBeNull());
  act(() => openCb.current!("rec-1"));
  expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Не сохранять" }));
  expect(await screen.findByTestId("card")).toHaveTextContent("rec-1");
});

/** Имена кнопок рейки по порядку. */
const railNames = () => within(screen.getByRole("navigation", { name: "Разделы" })).getAllByRole("button")
  .map((b) => b.getAttribute("aria-label") ?? b.textContent);

test("три раздела; у записей есть список, у голосов — нет", async () => {
  const { container } = render(<App />);
  expect(railNames()).toEqual(["Записи", "Голоса", "Настройки"]);
  expect(container.querySelector('[data-pane="list"]')).not.toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  expect(container.querySelector('[data-pane="list"]')).toBeNull();
});

test("строка поиска из списка уходит в useLibrary", async () => {
  residentState.current = online();
  render(<App />);
  await userEvent.type(screen.getByRole("combobox", { name: "Поиск по записям" }), "план");
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "план", 0, 0, {}, "план");
});

test("префиксы строки — фильтром резиденту, в q — только текст; в карточку — только текст", async () => {
  residentState.current = online();
  useLibrarySpy.mockReturnValue({
    items: [{ id: "a", path: "C:/rec/a", started_at: "2026-09-30T10:00:00", duration_s: 60, tracks: {},
      has_transcript: true, has_voices: false, title: "Планёрка", source: "record" }],
    jobs: [], loading: false, error: null, refresh: async () => {},
  });
  render(<App />);
  await userEvent.type(screen.getByRole("combobox", { name: "Поиск по записям" }), "участник:Анна есть:итоги бюджет");
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "бюджет", 0, 0, { people: ["Анна"], has: ["summary"] },
    "участник:Анна есть:итоги бюджет");
  // Enter — префиксы становятся метками, в поле остаётся текст.
  await userEvent.keyboard("{Enter}");
  expect(screen.getByRole("combobox", { name: "Поиск по записям" })).toHaveValue("бюджет ");
  expect(within(screen.getByRole("group", { name: "Условия поиска" })).getByText("Анна")).toBeInTheDocument();
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "бюджет", 0, 0, { people: ["Анна"], has: ["summary"] }, "бюджет ");
  await userEvent.click(screen.getByText("Планёрка"));
  expect(screen.getByTestId("card")).toHaveAttribute("data-find", "бюджет");
});

test("запомненный фильтр по категориям уходит в useLibrary (фильтрует резидент)", () => {
  window.localStorage.setItem("meet.categoryFilter", JSON.stringify(["retro", "_none"]));
  try {
    residentState.current = online();
    render(<App />);
    expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "", 0, 0, { categories: ["retro", "_none"] }, "");
  } finally {
    window.localStorage.removeItem("meet.categoryFilter");
  }
});

/** Кнопка-список групп над поиском (0.4: группы ушли из рейки в список записей). */
const groupPicker = () => screen.findByRole("button", { name: /^Группа встреч: / });
/** Открыть кнопку-список и выбрать область в дереве. */
async function pickGroup(name: string) {
  await userEvent.click(await groupPicker());
  const list = screen.getByRole("list", { name: "Группы встреч" });
  await userEvent.click(within(list).getByRole("button", { name: new RegExp(`^${name},`) }));
}

test("группы — кнопкой-списком над поиском, в рейке их нет; выбранная группа уходит в фильтр useLibrary", async () => {
  vi.mocked(api.getGroups).mockResolvedValue({
    groups: [{ id: "g-a", name: "Проект Альфа", color: "#4c8bf5", count: 2 }], unknown: [], none: 1,
  });
  residentState.current = online();
  const { container } = render(<App />);
  const button = await groupPicker();
  expect(button).toHaveAccessibleName("Группа встреч: Все записи");
  expect(container.querySelector('[data-pane="list"]')).toContainElement(button);
  // Рейка — только разделы: ни дерева групп, ни режима «полосы значков».
  expect(within(screen.getByRole("navigation", { name: "Разделы" })).queryByRole("list")).toBeNull();
  expect(railNames()).toEqual(["Записи", "Голоса", "Настройки"]);
  expect(container.querySelector(".app")).not.toHaveAttribute("data-nav-rail");
  await userEvent.click(button);
  expect(screen.getByRole("dialog", { name: "Группы" })).toHaveTextContent(/Все записи.*Проект Альфа.*Без группы.*Новая группа/);
  await userEvent.click(within(screen.getByRole("list", { name: "Группы встреч" })).getByRole("button", { name: /^Проект Альфа,/ }));
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "", 0, 0, { groups: ["g-a"] }, "");
  expect(screen.getByRole("combobox", { name: "Поиск по записям" })).toHaveAttribute("placeholder", "Поиск в «Проект Альфа»");
  // В «Голосах» списка записей (и кнопки-списка) нет; вернулись — та же группа.
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  expect(screen.queryByRole("button", { name: /^Группа встреч: / })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Записи" }));
  expect(await groupPicker()).toHaveAccessibleName("Группа встреч: Проект Альфа");
  await pickGroup("Все записи");
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "", 0, 0, {}, "");
  window.localStorage.removeItem("meet.groupScope");
});

test("/groups не ответил — «Группы недоступны» с повтором над поиском, список не сужается", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-a"');
  vi.mocked(api.getGroups).mockRejectedValueOnce(new api.ApiError(500, "сбой"));
  residentState.current = online();
  const { container } = render(<App />);
  const pane = container.querySelector<HTMLElement>('[data-pane="list"]')!;
  expect(await within(pane).findByRole("note")).toHaveTextContent("Группы недоступны");
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "", 0, 0, {}, "");
  vi.mocked(api.getGroups).mockResolvedValue({
    groups: [{ id: "g-a", name: "Проект Альфа", color: "#4c8bf5", count: 2 }], unknown: [], none: 1,
  });
  await userEvent.click(within(pane).getByRole("button", { name: "Повторить" }));
  expect(await groupPicker()).toHaveAccessibleName("Группа встреч: Проект Альфа");
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "", 0, 0, { groups: ["g-a"] }, "");
  window.localStorage.removeItem("meet.groupScope");
});

test("старый резидент без /groups — ни групп, ни кнопки-списка", async () => {
  vi.mocked(api.getGroups).mockRejectedValue(new api.ApiError(404, "нет"));
  residentState.current = online();
  render(<App />);
  await waitFor(() => expect(api.getGroups).toHaveBeenCalled());
  await act(async () => {});
  expect(screen.queryByRole("list", { name: "Группы встреч" })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Группа встреч/ })).toBeNull();
  expect(railNames()).toEqual(["Записи", "Голоса", "Настройки"]);
});

const idle = (extra: Record<string, unknown> = {}) => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
});

test("шапки нет: кнопка записи — в рейке под знаком, «Мало места» — внизу над «Настройками»", () => {
  residentState.current = { ...online(), snapshot: idle({ disk_free_gb: 3.2 }), snapshotAt: Date.now(),
    applySnapshot: () => {} };
  render(<App />);
  expect(screen.queryByRole("banner")).toBeNull();
  expect(railNames()).toEqual(["Начать запись", "Другие варианты записи", "Записи", "Голоса",
    "Мало места: 3.2 ГБ", "Настройки"]);
});

test("во время записи в рейке — «Остановить и сохранить» с подсказкой «Идёт запись · мм:сс»", () => {
  residentState.current = { ...online(), snapshot: idle({ status: "recording", source: "manual", elapsed_s: 767 }),
    snapshotAt: Date.now(), applySnapshot: () => {} };
  render(<App />);
  const nav = screen.getByRole("navigation", { name: "Разделы" });
  expect(within(nav).getByRole("button", { name: "Остановить и сохранить" }))
    .toHaveAccessibleDescription("Идёт запись · 12:47");
  expect(within(nav).queryByRole("button", { name: "Начать запись" })).toBeNull();
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
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
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
  expect(detail).toHaveTextContent("Приложение перезапускает её — подождите несколько секунд.");
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
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  expect(refreshPeople).toHaveBeenCalledTimes(1);
});

test("карточка просит настройки «Ассистент» — открыт раздел настроек с нужной секцией", async () => {
  residentState.current = online();
  window.history.replaceState({}, "", "/?recording=abc");
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "в настройки" }));
  expect(screen.getByTestId("settings")).toHaveAttribute("data-initial", "assistant");
  // Обычный переход в настройки из меню — с начала, без запомненной секции.
  await userEvent.click(screen.getByRole("button", { name: "Записи" }));
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  expect(screen.getByTestId("settings")).toHaveAttribute("data-initial", "");
});

test("?section=assistant в адресе — открыты настройки «Ассистент», запись из адреса не теряется", async () => {
  residentState.current = online();
  window.history.replaceState({}, "", "/?recording=abc&section=assistant");
  render(<App />);
  expect(screen.getByTestId("settings")).toHaveAttribute("data-initial", "assistant");
  await userEvent.click(screen.getByRole("button", { name: "Записи" }));
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

test("папки движка и моделей нет — не мастер, а «Повторить» и «Вернуть на системный диск»", async () => {
  engineState.current = missingEngine();
  vi.mocked(shell.residentStatus).mockResolvedValue("storage-missing");
  const { container } = render(<App />);
  const detail = container.querySelector<HTMLElement>('[data-pane="detail"]')!;
  await waitFor(() => expect(detail).toHaveTextContent("Папка движка и моделей недоступна"));
  expect(screen.queryByTestId("wizard")).toBeNull();
  expect(detail).not.toHaveTextContent("Движок не установлен");
  expect(within(detail).getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  expect(within(detail).getByRole("button", { name: "Вернуть на системный диск" })).toBeInTheDocument();
});

test("повреждённый файл выбора папки — тот же экран, не мастер", async () => {
  engineState.current = missingEngine();
  vi.mocked(shell.residentStatus).mockResolvedValue("storage-unreadable");
  const { container } = render(<App />);
  const detail = container.querySelector<HTMLElement>('[data-pane="detail"]')!;
  await waitFor(() => expect(detail).toHaveTextContent("Вернуть на системный диск"));
  expect(screen.queryByTestId("wizard")).toBeNull();
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
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
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
    // Опрос заводится только после того, как ответ оболочки применён; под
    // нагрузкой это случается позже первого вызова. Крутим по одному тику, пока
    // опрос не подтвердит себя вторым вызовом, — и только потом считаем потолок.
    await waitFor(async () => {
      await act(async () => { await vi.advanceTimersByTimeAsync(SHELL_POLL_MS); });
      expect(vi.mocked(shell.residentStatus).mock.calls.length).toBeGreaterThan(1);
    });
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

// --- группы вместе с поиском (интеграция groups-ui и search-ui) ------------------------------

const TWO_GROUPS = {
  groups: [
    { id: "g-a", name: "Альфа", color: "#4c8bf5", count: 2 },
    { id: "g-b", name: "Бета", color: "#e5484d", count: 1 },
  ],
  unknown: [], none: 1,
};
const field = () => screen.getByRole("combobox", { name: "Поиск по записям" });

async function withGroups() {
  vi.mocked(api.getGroups).mockResolvedValue(TWO_GROUPS);
  residentState.current = online();
  render(<App />);
  await groupPicker();
}
afterEach(() => window.localStorage.removeItem("meet.groupScope"));

test("область группы и метка «группа:» — пересечение: та же группа — она, другая — ничего", async () => {
  await withGroups();
  await pickGroup("Альфа");
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "", 0, 0, { groups: ["g-a"] }, "");
  expect(field()).toHaveAttribute("placeholder", "Поиск в «Альфа»");
  await userEvent.type(field(), "группа:Бета");
  // У встречи одна группа: «Альфа» ∩ «Бета» пусто — резиденту id, которого нет ни у одной встречи.
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "", 0, 0, { groups: ["0-no-match"] }, "группа:Бета");
  await userEvent.clear(field());
  await userEvent.type(field(), "группа:Альфа бюджет");
  expect(useLibrarySpy).toHaveBeenLastCalledWith(ep, "бюджет", 0, 0, { groups: ["g-a"] }, "группа:Альфа бюджет");
});

test("счётчики групп — по разобранному запросу: текст без префиксов и условия без групп", async () => {
  await withGroups();
  await pickGroup("Альфа");
  await userEvent.type(field(), "участник:Анна группа:Бета бюджет");
  // Префиксы — не слова поиска; своя «группа:» в счётчиках групп не участвует (как в «Фильтрах»).
  await waitFor(() => expect(api.getGroups).toHaveBeenLastCalledWith(ep, "бюджет", { people: ["Анна"] }),
    { timeout: 2000 });
});

test("«Фильтры» в области группы: счётчики — в области, измерения «Группа» нет", async () => {
  await withGroups();
  await userEvent.click(screen.getByRole("button", { name: "Фильтры" }));
  await waitFor(() => expect(api.getFacets).toHaveBeenCalled());
  expect(within(screen.getByRole("dialog", { name: "Фильтры" })).getByText("Группа", { selector: "legend" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Фильтры" }));
  vi.mocked(api.getFacets).mockClear();
  await pickGroup("Альфа");
  await userEvent.click(screen.getByRole("button", { name: "Фильтры" }));
  await waitFor(() => expect(api.getFacets).toHaveBeenCalledWith(ep, undefined, { groups: ["g-a"] }, expect.any(AbortSignal)));
  const panel = screen.getByRole("dialog", { name: "Фильтры" });
  expect(within(panel).queryByText("Группа", { selector: "legend" })).toBeNull();
  expect(within(panel).getByText("Есть", { selector: "legend" })).toBeInTheDocument();
  expect(panel).toHaveTextContent("Число встреч — в «Альфа»");
});
