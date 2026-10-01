import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";

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
  SettingsPane: ({ initial, initialTick }: { initial?: string; initialTick?: number }) => (
    <div data-testid="settings" data-initial={initial ?? ""} data-tick={initialTick ?? ""} />
  ),
}));
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
}));

const OFFLINE = /Сервис записи не запущен/;
const online = () => ({ status: "online", endpoint: ep, snapshot: null, lastEvent: null, libraryTick: 0 });

beforeEach(() => {
  residentState.current = { status: "offline" };
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

test("офлайн: в списке и в карточке — «Сервис записи не запущен», данных нет", () => {
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
