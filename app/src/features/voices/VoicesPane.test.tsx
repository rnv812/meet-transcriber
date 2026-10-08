import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GRID_MIN, VoicesPane } from "./VoicesPane";
import * as api from "../../lib/api";
import { usePeople } from "../../state/usePeople";
import { CardHeader } from "../card/CardHeader";
import type { Person } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getPeople: vi.fn(),
  getOwnerVoice: vi.fn(async () => ({
    samples: [], take: null, ready: true, reason: null, recording: false, seconds: 25,
  })),
  getPerson: vi.fn(),
  getSample: vi.fn(),
  putAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  renamePerson: vi.fn(),
  mergePerson: vi.fn(),
  deletePerson: vi.fn(),
  setPersonRole: vi.fn(),
  deriveOwnerVoice: vi.fn(),
  getProfilesRemoved: vi.fn(async () => ({ notice: null })),
  dismissProfilesRemoved: vi.fn(),
}));

const ep = { base: "/api", token: null };
const people: Person[] = [
  { name: "Демьян", samples: 3, meetings: 2, seconds: 1200, has_avatar: false, color: "#4b6bd6", role: "CTO Acme" },
  { name: "Аркаша", samples: 1, meetings: 1, seconds: 240, has_avatar: false, color: "#3a9a6a" },
  { name: "Аркадий", samples: 1, meetings: 1, seconds: 600, has_avatar: false, color: "#c0793a", role: "заказчик" },
];
const OWNER_EMPTY = { samples: [], take: null, ready: true, reason: null, recording: false, seconds: 25 };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getOwnerVoice).mockResolvedValue(OWNER_EMPTY);
  vi.mocked(api.getPerson).mockImplementation(async (_e, name) => ({
    name, color: "#000", has_avatar: false, samples: 1,
    meetings: [{ recording: "r1", title: "Планёрка", started_at: "2026-09-30T10:00:00", seconds: 600 }],
  }));
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.load = vi.fn();
});

const noop = () => {};
const setup = (p = people, extra: Partial<Parameters<typeof VoicesPane>[0]> = {}) =>
  render(
    <VoicesPane endpoint={ep} people={p} avatarVersion={{}} onAvatar={noop} onChanged={noop}
      onOpenRecording={noop} {...extra} />,
  );

const rec = {
  id: "r1", path: "p", started_at: null, duration_s: 60, tracks: {}, has_transcript: true,
  has_voices: false, title: "Встреча", source: "record",
};

/** Как App: одна usePeople на окно, аватар-версия уходит и в «Голоса», и в карточку записи. */
function Harness() {
  const { people: list, refresh, avatarVersion, bumpAvatar } = usePeople(ep);
  return (
    <>
      <VoicesPane endpoint={ep} people={list} avatarVersion={avatarVersion} onChanged={noop}
        onAvatar={(n) => { bumpAvatar(n); void refresh(); }} onOpenRecording={noop} />
      <div data-testid="rec">
        <CardHeader rec={rec} speakers={["Демьян"]} people={list} endpoint={ep} avatarVersion={avatarVersion}
          onRename={noop} />
      </div>
    </>
  );
}

test("таблица людей: заголовок, колонки «Человек · Кто это · Встреч · Речи», по строке на человека", () => {
  setup(people.slice(0, 2));
  expect(screen.getByRole("heading", { name: "Голоса" })).toBeInTheDocument();
  expect(screen.getByText("2 человека · узнаются автоматически")).toBeInTheDocument();
  const table = screen.getByRole("table", { name: "Люди с голосом в базе" });
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["Человек", "Кто это", "Встреч", "Речи", "Действие", "Ещё"]);
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  expect(within(rows[0]!).getByRole("button", { name: "Демьян" })).toBeInTheDocument();
  expect(within(rows[0]!).getByRole("cell", { name: "CTO Acme" })).toBeInTheDocument();
  expect(within(rows[0]!).getByRole("cell", { name: "2" })).toBeInTheDocument();
  expect(within(rows[0]!).getByRole("cell", { name: "20 мин" })).toBeInTheDocument();
  expect(within(rows[1]!).getByRole("cell", { name: "4 мин" })).toBeInTheDocument();
  // Роли нет — тихий прочерк, а не пустая ячейка; диктору — «не задано».
  expect(within(rows[1]!).getByRole("cell", { name: "не задано" })).toHaveTextContent("—");
  // «Последний раз» нужен бэкенд — колонки нет.
  expect(screen.queryByText("Последний раз")).toBeNull();
});

test("«Кто это» в таблице — в одну строку с многоточием; полный текст — подсказкой обрезанного", () => {
  const long = "Руководитель направления интеграции платформы у заказчика, отвечает за сроки пилота";
  setup([{ ...people[0]!, role: long }]);
  const cell = screen.getByRole("cell", { name: long });
  expect(cell.querySelector(".truncate")).toHaveTextContent(long);
});

test("сверху — карточка «Мой голос» по макету: «Вы», что записано, бейдж и кнопки справа", async () => {
  setup();
  const mine = screen.getByRole("group", { name: "Мой голос" });
  expect(await within(mine).findByText("Не записан")).toBeInTheDocument();
  expect(within(mine).getByText("Вы")).toBeInTheDocument();
  expect(within(mine).getByRole("button", { name: "Записать" })).toBeEnabled();
  expect(within(mine).getByRole("button", { name: "Найти по прошлым встречам" })).toBeEnabled();
  expect(api.getOwnerVoice).toHaveBeenCalledWith(ep);
  // Выше таблицы.
  const table = screen.getByRole("table");
  expect(mine.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("«Мой голос»: записанный образец — «Записан», «Перезаписать»; «Записать» открывает окно записи", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue({
    ...OWNER_EMPTY, samples: [{ id: "s1", source: "enroll", date: "2026-10-05", device: "USB-микрофон" }],
  } as never);
  setup();
  const mine = screen.getByRole("group", { name: "Мой голос" });
  expect(await within(mine).findByText("Записан")).toBeInTheDocument();
  expect(within(mine).getByText(/записан 05\.10 · USB-микрофон/)).toBeInTheDocument();
  await userEvent.click(within(mine).getByRole("button", { name: "Перезаписать" }));
  expect(await screen.findByRole("dialog", { name: "Мой голос" })).toBeInTheDocument();
});

test("«Мой голос»: недоступная кнопка объясняет причину, рядом — ссылка в настройки", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue({
    ...OWNER_EMPTY, ready: false, reason: "Сначала установите движок расшифровки — без него голос не разобрать",
  });
  const onOpenSettings = vi.fn();
  setup(people, { onOpenSettings });
  const mine = screen.getByRole("group", { name: "Мой голос" });
  const derive = await within(mine).findByRole("button", { name: "Найти по прошлым встречам" });
  await waitFor(() => expect(derive).toBeDisabled());
  expect(derive).toHaveAccessibleDescription("Сначала установите движок расшифровки — без него голос не разобрать.");
  await userEvent.click(within(mine).getByRole("button", { name: "Открыть настройки" }));
  expect(onOpenSettings).toHaveBeenCalledWith("engine");
});

test("поиск «Найти человека» ищет и по «Кто это»", async () => {
  setup();
  const search = screen.getByRole("searchbox", { name: "Найти человека" });
  await userEvent.type(search, "заказ");
  const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(1);
  expect(within(rows[0]!).getByRole("button", { name: "Аркадий" })).toBeInTheDocument();
  await userEvent.clear(search);
  await userEvent.type(search, "acme");
  expect(within(screen.getByRole("table")).getByRole("button", { name: "Демьян" })).toBeInTheDocument();
});

test("карточка человека: «Кто это» под именем — Enter сохраняет, видно «Сохранено», список перечитывается", async () => {
  vi.mocked(api.setPersonRole).mockResolvedValue("заказчик, отдел закупок");
  const onChanged = vi.fn();
  setup(people, { onChanged });
  await userEvent.click(screen.getByRole("button", { name: "Аркаша" }));
  const field = await screen.findByRole("textbox", { name: "Кто это" });
  expect(field).toHaveValue("");
  expect(field).toHaveAttribute("placeholder", "Например: CTO Acme, заказчик");
  expect(field).toHaveAttribute("maxLength", "160");
  await userEvent.type(field, "заказчик, отдел закупок{Enter}");
  await waitFor(() => expect(api.setPersonRole).toHaveBeenCalledWith(ep, "Аркаша", "заказчик, отдел закупок"));
  expect(await screen.findByText("Сохранено")).toBeInTheDocument();
  expect(onChanged).toHaveBeenCalled();
});

test("карточка человека: «Кто это» — уход из поля сохраняет, Esc возвращает прежнее, без изменений — без запроса", async () => {
  vi.mocked(api.setPersonRole).mockImplementation(async (_e, _n, role) => role.trim());
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  const field = await screen.findByRole("textbox", { name: "Кто это" });
  expect(field).toHaveValue("CTO Acme");
  // Без изменений — ни запроса, ни «Сохранено».
  await userEvent.click(field);
  await userEvent.tab();
  expect(api.setPersonRole).not.toHaveBeenCalled();
  // Esc — прежний текст.
  await userEvent.clear(field);
  await userEvent.type(field, "кто-то другой{Escape}");
  expect(field).toHaveValue("CTO Acme");
  expect(api.setPersonRole).not.toHaveBeenCalled();
  // Очистить и уйти из поля — пустая строка (роль снята).
  await userEvent.clear(field);
  await userEvent.tab();
  await waitFor(() => expect(api.setPersonRole).toHaveBeenCalledWith(ep, "Демьян", ""));
});

test("карточка человека: ошибка сохранения «Кто это» — текстом, поле не сбрасывается", async () => {
  vi.mocked(api.setPersonRole).mockRejectedValue(new api.ApiError(404, "человека нет"));
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Аркаша" }));
  const field = await screen.findByRole("textbox", { name: "Кто это" });
  await userEvent.type(field, "коллега{Enter}");
  expect(await screen.findByText("человека нет")).toBeInTheDocument();
  expect(field).toHaveValue("коллега");
  expect(screen.queryByText("Сохранено")).toBeNull();
});

test("поиск «Найти человека» фильтрует по имени на клиенте", async () => {
  setup();
  const search = screen.getByRole("searchbox", { name: "Найти человека" });
  const names = () => within(screen.getByRole("table")).getAllByRole("row").slice(1).map((r) => r.textContent);
  expect(names()).toHaveLength(3);
  await userEvent.type(search, "аркад");
  expect(names()).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Аркадий" })).toBeInTheDocument();
  await userEvent.clear(search);
  await userEvent.type(search, "АРКА");
  expect(names()).toHaveLength(2);
  await userEvent.clear(search);
  await userEvent.type(search, "никого");
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByText("Никого не нашлось — ни по имени, ни по «Кто это»")).toBeInTheDocument();
  expect(api.getPeople).not.toHaveBeenCalled();
});

test("строка открывает карточку: кликом по строке и Enter на имени", async () => {
  setup();
  const row = screen.getByRole("button", { name: "Аркаша" }).closest("tr")!;
  await userEvent.click(within(row).getByRole("cell", { name: "1" }));
  expect(await screen.findByLabelText("Имя")).toHaveValue("Аркаша");
  expect(row).toHaveAttribute("aria-selected", "true");
  screen.getByRole("button", { name: "Демьян" }).focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(screen.getByLabelText("Имя")).toHaveValue("Демьян"));
});

test("«Переименовать» в строке открывает карточку с именем под рукой", async () => {
  setup();
  const row = screen.getByRole("button", { name: "Демьян" }).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "Переименовать" }));
  const input = await screen.findByLabelText("Имя");
  await waitFor(() => expect(input).toHaveFocus());
});

test("«Ещё»: объединить — к выбору человека, удалить — к подтверждению; прежние диалоги", async () => {
  vi.mocked(api.deletePerson).mockResolvedValue({});
  setup();
  const row = () => screen.getByRole("button", { name: "Демьян" }).closest("tr")!;
  await userEvent.click(within(row()).getByRole("button", { name: "Ещё: Демьян" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Объединить с…" }));
  await waitFor(() => expect(screen.getByLabelText("Объединить с…")).toHaveFocus());
  await userEvent.click(within(row()).getByRole("button", { name: "Ещё: Демьян" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить голос" }));
  expect(screen.getByRole("alertdialog")).toHaveAccessibleName("Удалить голос «Демьян»?");
  expect(api.deletePerson).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
  await waitFor(() => expect(api.deletePerson).toHaveBeenCalledWith(ep, "Демьян"));
});

test("пустое состояние: та же шапка «Голоса» и «Мой голос», пояснение — под ними, без поиска", () => {
  setup([]);
  expect(screen.getByRole("heading", { name: "Голоса" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Мой голос" })).toBeInTheDocument();
  const empty = screen.getByText("База голосов пуста");
  expect(screen.getByRole("group", { name: "Мой голос" }).compareDocumentPosition(empty)
    & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByRole("searchbox")).toBeNull();
  // Одна колонка с заполненной «Голосов»: шапка и карточки — в .voices__col.
  expect(document.querySelector(".voices__col .voices__head")).not.toBeNull();
});

test("клик по человеку открывает карточку со встречами; встреча открывает запись", async () => {
  const onOpenRecording = vi.fn();
  setup(people, { onOpenRecording });
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  await userEvent.click(await screen.findByText("Планёрка"));
  expect(onOpenRecording).toHaveBeenCalledWith("r1");
});

test("загрузка файла: putAvatar, refresh, аватар везде с картинкой и новой версией", async () => {
  vi.mocked(api.putAvatar).mockResolvedValue();
  vi.mocked(api.getPeople)
    .mockResolvedValueOnce({ items: people })
    .mockResolvedValue({ items: people.map((p) => (p.name === "Демьян" ? { ...p, has_avatar: true } : p)) });
  render(<Harness />);
  await userEvent.click(within(await screen.findByRole("table")).getByRole("button", { name: "Демьян" }));
  const before = screen.getByTestId("rec").querySelector("img");
  expect(before).toBeNull();
  const file = new File(["x"], "a.png", { type: "image/png" });
  await userEvent.upload(await screen.findByTestId("avatar-file"), file);
  await waitFor(() => expect(api.putAvatar).toHaveBeenCalledWith(ep, "Демьян", file));
  await waitFor(() => expect(screen.getByTestId("rec").querySelector("img")).not.toBeNull());
  expect(api.getPeople).toHaveBeenCalledTimes(2);
  const src = screen.getByTestId("rec").querySelector("img")!.getAttribute("src")!;
  expect(src).toMatch(/\/avatar\?v=\d{5,}$/);
  expect(document.querySelector(".pcard img")!.getAttribute("src")).toBe(src);
});

test("слишком большой файл не отправляется", async () => {
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  const big = new File(["x"], "big.png", { type: "image/png" });
  Object.defineProperty(big, "size", { value: 11 * 1024 * 1024 });
  await userEvent.upload(await screen.findByTestId("avatar-file"), big);
  expect(await screen.findByText("Файл больше 10 МБ")).toBeInTheDocument();
  expect(api.putAvatar).not.toHaveBeenCalled();
});

test("сброс к инициалам: deleteAvatar и onAvatar", async () => {
  vi.mocked(api.deleteAvatar).mockResolvedValue({ ok: true });
  const onAvatar = vi.fn();
  setup([{ ...people[0]!, has_avatar: true }, ...people.slice(1)], { onAvatar });
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  await userEvent.click(await screen.findByRole("button", { name: "Аватар" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Сбросить к инициалам" }));
  // Сначала подтверждение: фотография пропадёт.
  expect(api.deleteAvatar).not.toHaveBeenCalled();
  await userEvent.click(within(screen.getByRole("alertdialog", { name: "Убрать фотографию?" }))
    .getByRole("button", { name: "Убрать" }));
  await waitFor(() => expect(api.deleteAvatar).toHaveBeenCalledWith(ep, "Демьян"));
  expect(onAvatar).toHaveBeenCalledWith("Демьян");
});

test("Escape в имени отменяет переименование", async () => {
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  const input = await screen.findByLabelText("Имя");
  await userEvent.clear(input);
  await userEvent.type(input, "Пётр{Escape}");
  expect(api.renamePerson).not.toHaveBeenCalled();
  expect(input).toHaveValue("Демьян");
});

test("карточка получает фокус для Ctrl+V", async () => {
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  await waitFor(() => expect(document.querySelector(".pcard")).toHaveFocus());
});

test("ошибка загрузки: текст виден", async () => {
  vi.mocked(api.putAvatar).mockRejectedValue(new api.ApiError(400, "не изображение"));
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  await userEvent.upload(await screen.findByTestId("avatar-file"), new File(["x"], "a.txt", { type: "image/png" }));
  expect(await screen.findByText("не изображение")).toBeInTheDocument();
});

test("вставка изображения из буфера", async () => {
  vi.mocked(api.putAvatar).mockResolvedValue();
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  const card = document.querySelector(".pcard")!;
  const file = new File(["x"], "s.png", { type: "image/png" });
  const ev = new Event("paste", { bubbles: true, cancelable: true }) as Event & { clipboardData: unknown };
  ev.clipboardData = { files: [file], items: [] };
  card.dispatchEvent(ev);
  await waitFor(() => expect(api.putAvatar).toHaveBeenCalledWith(ep, "Демьян", file));
});

test("объединение: выбор, подтверждение, mergePerson", async () => {
  vi.mocked(api.mergePerson).mockResolvedValue({});
  const onChanged = vi.fn();
  setup(people, { onChanged });
  await userEvent.click(screen.getByRole("button", { name: "Аркаша" }));
  await userEvent.click(await screen.findByRole("combobox", { name: "Объединить с…" }));
  await userEvent.click(screen.getByRole("option", { name: "Аркадий" }));
  expect(screen.getByRole("alertdialog")).toHaveAccessibleName(/^Объединить «.+» с «Аркадий»\?$/);
  expect(api.mergePerson).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: /^(Объединить|Удалить)$/ }));
  await waitFor(() => expect(api.mergePerson).toHaveBeenCalledWith(ep, "Аркаша", "Аркадий"));
  expect(onChanged).toHaveBeenCalled();
});

test("удаление: подтверждение, deletePerson, карточка закрыта", async () => {
  vi.mocked(api.deletePerson).mockResolvedValue({});
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  await userEvent.click(await screen.findByRole("button", { name: "Удалить голос" }));
  await userEvent.click(screen.getByRole("button", { name: /^(Объединить|Удалить)$/ }));
  await waitFor(() => expect(api.deletePerson).toHaveBeenCalledWith(ep, "Демьян"));
  expect(document.querySelector(".pcard")).toBeNull();
});

test("переименование: ошибка дубликата текстом", async () => {
  vi.mocked(api.renamePerson).mockRejectedValue(new api.ApiError(400, "человек с таким именем уже есть"));
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  const input = await screen.findByLabelText("Имя");
  await userEvent.clear(input);
  await userEvent.type(input, "Аркаша{Enter}");
  expect(await screen.findByText("человек с таким именем уже есть")).toBeInTheDocument();
});

test("образец: getSample и src с #t=start,end", async () => {
  vi.mocked(api.getSample).mockResolvedValue({ recording: "r1", start: 5, end: 9, track: "sys" });
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
  await userEvent.click(await screen.findByRole("button", { name: "Прослушать образец" }));
  await waitFor(() => expect(document.querySelector("audio")!.getAttribute("src")).toMatch(/track=sys#t=5,9$/));
});

test("панель человека — колонка рядом с сеткой: не шире области за вычетом сетки, ширину тянут разделителем", async () => {
  // Область «Голосов» шириной 700 px (в jsdom раскладки нет — ширину задаём сами).
  const width = vi.spyOn(HTMLElement.prototype, "clientWidth", "get")
    .mockImplementation(function (this: HTMLElement) { return this.classList.contains("voices") ? 700 : 0; });
  localStorage.setItem("meet.pane.voices-card", "640");
  try {
    const { container } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Демьян" }));
    const area = container.querySelector<HTMLElement>(".voices")!;
    const aside = container.querySelector(".voices__card")!;
    // Панель — не внутри сетки, а следом за ней.
    expect(container.querySelector(".voices__main")!.contains(aside)).toBe(false);
    const split = screen.getByRole("separator", { name: "Ширина карточки человека" });
    expect(split.nextElementSibling).toBe(aside);
    // Запомнено 640, но сетке нужно GRID_MIN: панель 500, не дальше края.
    expect(area.style.getPropertyValue("--person-w")).toBe(`${700 - GRID_MIN}px`);
    expect(split).toHaveAttribute("aria-valuemax", String(700 - GRID_MIN));
    // Тянут влево — шире, но не больше предела.
    fireEvent.pointerDown(split, { button: 0, clientX: 300, pointerId: 1 });
    fireEvent.pointerMove(split, { clientX: 0, pointerId: 1 });
    fireEvent.pointerUp(split, { clientX: 0, pointerId: 1 });
    expect(area.style.getPropertyValue("--person-w")).toBe(`${700 - GRID_MIN}px`);
    // Вправо — уже, до минимума карточки; ширина запоминается.
    fireEvent.pointerDown(split, { button: 0, clientX: 300, pointerId: 1 });
    fireEvent.pointerMove(split, { clientX: 400, pointerId: 1 });
    fireEvent.pointerUp(split, { clientX: 400, pointerId: 1 });
    expect(area.style.getPropertyValue("--person-w")).toBe("400px");
    expect(localStorage.getItem("meet.pane.voices-card")).toBe("400");
  } finally {
    width.mockRestore();
    localStorage.clear();
  }
});
