import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import type { GroupsInfo, Recording } from "../../lib/types";
import { RecordingsList } from "../recordings/RecordingsList";
import { GroupsLayer } from "./GroupsLayer";
import { GroupsNav } from "./GroupsNav";
import { useGroupsUi } from "./useGroupsUi";
import { useMemo, useState } from "react";
import { useAgentLive } from "../card/agentSessions";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getGroups: vi.fn(),
  createGroup: vi.fn(),
  patchGroup: vi.fn(),
  deleteGroup: vi.fn(),
  orderGroups: vi.fn(),
  setGroupMembers: vi.fn(),
  getRecordings: vi.fn(),
  getAssistant: vi.fn(),
}));
// Строка записи зовёт useAgentLive на каждую отрисовку: по числу вызовов видно, сколько строк перерисовано.
vi.mock("../card/agentSessions", async (orig) => {
  const real = await orig<typeof import("../card/agentSessions")>();
  return { ...real, useAgentLive: vi.fn(real.useAgentLive) };
});
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  agentKillRecording: vi.fn(async () => {}),
  pickFolder: vi.fn(async () => null),
}));
import * as shell from "../../lib/shell";

const ep = { base: "/api", token: null };
const rec = (id: string, extra: Partial<Recording> = {}): Recording => ({
  id, path: `C:/rec/${id}`, started_at: "2026-09-30T10:00:00", duration_s: 1800, tracks: { sys: "s.wav" },
  has_transcript: true, has_voices: false, title: null, source: "record", ...extra,
});
const ITEMS = [
  rec("a", { title: "Планёрка", group: "g-a" }),
  rec("b", { title: "Ретро", group: "g-a" }),
  rec("c", { title: "Созвон", group: null }),
  rec("d", { title: "Демо", group: "g-b" }),
];
const INFO: GroupsInfo = {
  groups: [
    { id: "g-a", name: "Проект Альфа", color: "#4c8bf5", count: 2 },
    { id: "g-b", name: "Бета", color: "#e5484d", count: 1 },
  ],
  unknown: [{ id: "g-old", count: 1 }],
  none: 3,
};

const onOpen = vi.fn();
const NO_JOBS: never[] = [];
const NO_FILTER = {};
const RESIDENT = { endpoint: ep, snapshot: null };
const onSelect = vi.fn();
const refresh = vi.fn(async () => {});

/** `nav: false` — как в окне 0.4: дерева рядом нет, группы — в кнопке-списке над поиском. */
function Harness({ items = ITEMS, q: initial = "", nav = true }: { items?: Recording[]; q?: string; nav?: boolean }) {
  const [q, setQ] = useState(initial);
  const ui = useGroupsUi(ep, 0, q, NO_FILTER);
  // Как useLibrary в App: те же данные — тот же список задач (строки сравнивают статус по ссылке).
  const library = useMemo(() => ({ items, jobs: NO_JOBS, loading: false, error: null, refresh }), [items]);
  return (
    <div className="app">
      {nav && <nav className="nav">{ui.shown && <GroupsNav ui={ui} active onOpen={(apply) => { onOpen(); apply(); }} />}</nav>}
      <div className="pane-list" data-testid="scope" data-scope={JSON.stringify(ui.libraryScope ? [ui.libraryScope] : null)}>
        <RecordingsList selected={null} onSelect={onSelect} q={q} onQ={setQ} groupsUi={ui}
          searchPlaceholder={ui.scopeName ? `Поиск в «${ui.scopeName}»` : undefined}
          library={library} resident={RESIDENT} />
      </div>
      <GroupsLayer ui={ui} />
    </div>
  );
}

const panel = () => within(screen.getByRole("list", { name: "Группы встреч" }));
/** Строки панели сверху вниз: подпись и число. */
const rows = () => [...document.querySelectorAll<HTMLButtonElement>("[data-scope-key]")]
  .map((b) => `${b.querySelector(".nav-group__name")?.textContent} ${b.querySelector(".nav-group__count")?.textContent}`);
const rowOf = (name: string | RegExp) => panel().getByRole("button", { name: typeof name === "string" ? new RegExp(`^${name},`) : name });
/** Сообщение для диктора — в постоянной области `role=status` (появляется с небольшой задержкой). */
const said = (text: string) => waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(text));
/** Ошибка — в постоянной области `role=alert`. */
const alerted = (text: string) => waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(text));
/** Видимое уведомление с кнопками (вне областей для диктора). */
const toastBox = () => document.querySelector<HTMLElement>(".toast")!;
const recordMenu = async (title: string) => userEvent.click(screen.getByRole("button", { name: `Действия с записью «${title}»` }));
const recordMain = (title: string) => [...document.querySelectorAll<HTMLButtonElement>(".rec-item__main")]
  .find((b) => b.querySelector(".rec-item__title")?.textContent === title)!;

async function setup(info: GroupsInfo | Error = INFO, props: { items?: Recording[]; q?: string; nav?: boolean } = {}) {
  if (info instanceof Error) vi.mocked(api.getGroups).mockRejectedValue(info);
  else vi.mocked(api.getGroups).mockResolvedValue(info);
  const view = render(<Harness {...props} />);
  await waitFor(() => expect(api.getGroups).toHaveBeenCalled());
  await act(async () => {});
  return view;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-06T12:00:00"));
  window.localStorage.clear();
  vi.clearAllMocks();
  vi.mocked(api.setGroupMembers).mockImplementation(async (_ep, _id, change) => (
    { changed: [...(change.add ?? []), ...(change.remove ?? [])], failed: [] }));
  vi.mocked(api.orderGroups).mockResolvedValue({ groups: [] });
  vi.mocked(api.patchGroup).mockImplementation(async (_ep, id, patch) =>
    ({ id, name: patch.name ?? "", color: patch.color ?? "", created_at: "2026-10-01T10:00:00" }));
  vi.mocked(api.createGroup).mockImplementation(async (_ep, g) =>
    ({ id: g.id ?? "g-new", name: g.name, color: g.color ?? "#4c8bf5", created_at: "2026-10-06T12:00:00" }));
});
afterEach(() => vi.useRealTimers());

// --- левая панель ------------------------------------------------------------------------

test("панель: «Все записи», группы по порядку с числом, неизвестная, «Без группы», «Новая группа»", async () => {
  await setup();
  expect(rows()).toEqual(["Все записи 7", "Проект Альфа 2", "Бета 1", "Группа без названия 1", "Без группы 3"]);
  expect(rowOf("Проект Альфа")).toHaveAccessibleName("Проект Альфа, 2 встречи");
  expect(screen.getByRole("button", { name: "Новая группа" })).toBeInTheDocument();
  // Точка цвета группы; у «Без группы» — пустой кружок.
  expect(rowOf("Бета").querySelector(".cat-dot")).toHaveStyle({ background: "#e5484d" });
  // «Без группы» — пунктирный кружок, неизвестная — пустой: различимы и в полосе значков.
  expect(rowOf("Без группы").querySelector("svg")).not.toBeNull();
  expect(rowOf("Без группы").querySelector(".cat-dot")).toBeNull();
  expect(rowOf("Группа без названия").querySelector(".cat-dot--none")).not.toBeNull();
  // Все записи — выбранная область, у неё нет меню; у групп и неизвестной — есть.
  expect(rowOf("Все записи")).toHaveAttribute("aria-current", "true");
  expect(panel().getByRole("button", { name: "Действия с группой «Бета»" })).toBeInTheDocument();
  expect(panel().getByRole("button", { name: "Действия с группой «Группа без названия»" })).toBeInTheDocument();
  expect(panel().queryByRole("button", { name: /Действия с группой «Без группы»/ })).toBeNull();
});

test("щелчок по группе — область списка: фильтр, запоминание, заголовок, подсказка поиска", async () => {
  await setup();
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null");
  expect(screen.getByRole("combobox", { name: "Поиск по записям" })).toHaveAttribute("placeholder", "Поиск");
  await userEvent.click(rowOf("Проект Альфа"));
  expect(onOpen).toHaveBeenCalled();
  expect(rowOf("Проект Альфа")).toHaveAttribute("aria-current", "true");
  expect(rowOf("Все записи")).not.toHaveAttribute("aria-current");
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", '["g-a"]');
  expect(window.localStorage.getItem("meet.groupScope")).toBe('"g-a"');
  expect(screen.getByRole("combobox", { name: "Поиск по записям" })).toHaveAttribute("placeholder", "Поиск в «Проект Альфа»");
  expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Проект Альфа · 2 встречи");
  await userEvent.click(rowOf("Без группы"));
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", '["_none"]');
  expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Без группы · 3 встречи");
  // «×» в заголовке — снова все записи.
  await userEvent.click(screen.getByRole("button", { name: "Показать все записи" }));
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null");
  expect(window.localStorage.getItem("meet.groupScope")).toBeNull();
  expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
});

test("запомненная область возвращается; группы больше нет — все записи", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-b"');
  const first = await setup();
  expect(rowOf("Бета")).toHaveAttribute("aria-current", "true");
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", '["g-b"]');
  first.unmount();
  window.localStorage.setItem("meet.groupScope", '"g-gone"');
  await setup();
  expect(rowOf("Все записи")).toHaveAttribute("aria-current", "true");
  expect(window.localStorage.getItem("meet.groupScope")).toBeNull();
});

test("пустая группа — EmptyState; в группе ничего не нашлось — поиск по всем записям", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-b"');
  const first = await setup(INFO, { items: [] });
  expect(screen.getByText("В группе пока нет встреч")).toBeInTheDocument();
  first.unmount();
  await setup(INFO, { items: [], q: "бюджет" });
  expect(screen.getByText("Ничего не найдено в «Бета»")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Искать во всех записях" }));
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null");
});

test("клавиатура панели: ↑/↓ — по строкам, Alt+↓/↑ — переставить группу", async () => {
  await setup();
  rowOf("Проект Альфа").focus();
  await userEvent.keyboard("{ArrowDown}");
  expect(rowOf("Бета")).toHaveFocus();
  await userEvent.keyboard("{End}");
  expect(rowOf("Без группы")).toHaveFocus();
  await userEvent.keyboard("{Home}");
  expect(rowOf("Все записи")).toHaveFocus();
  rowOf("Проект Альфа").focus();
  await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
  expect(api.orderGroups).toHaveBeenCalledWith(ep, ["g-b", "g-a"]);
  vi.mocked(api.orderGroups).mockClear();
  // Первая вверх не идёт; «Все записи» не переставляются.
  await userEvent.keyboard("{Alt>}{ArrowUp}{/Alt}");
  expect(api.orderGroups).not.toHaveBeenCalled();
});

test("меню группы: Переименовать, Цвет, Выше/Ниже (у краёв недоступно), Удалить", async () => {
  await setup();
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Бета»" }));
  const menu = screen.getByRole("menu", { name: "Действия с группой «Бета»" });
  expect(within(menu).getAllByRole("menuitem").map((b) => b.textContent))
    .toEqual(["Переименовать…", "Цвет", "Папка базы знаний…", "Выше", "Ниже", "Удалить"]);
  expect(within(menu).getByRole("menuitem", { name: "Ниже" })).toBeDisabled();
  await userEvent.click(within(menu).getByRole("menuitem", { name: "Выше" }));
  expect(api.orderGroups).toHaveBeenCalledWith(ep, ["g-b", "g-a"]);
  expect(screen.queryByRole("menu")).toBeNull();
});

test("цвет группы — подменю с menuitemradio, отмечен нынешний", async () => {
  await setup();
  fireEvent.contextMenu(rowOf("Проект Альфа"), { clientX: 40, clientY: 120 });
  await userEvent.click(screen.getByRole("menuitem", { name: "Цвет" }));
  const radios = screen.getAllByRole("menuitemradio");
  expect(radios.length).toBeGreaterThan(5);
  expect(screen.getByRole("menuitemradio", { name: "Синий" })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByRole("menuitemradio", { name: "Синий" })).toHaveFocus();
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Красный" }));
  expect(api.patchGroup).toHaveBeenCalledWith(ep, "g-a", { color: "#e5484d" });
});

test("переименование: проверка как у резидента, ответ резидента — в окне", async () => {
  await setup();
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Проект Альфа»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Переименовать…" }));
  const dialog = screen.getByRole("dialog", { name: "Переименовать группу" });
  const name = within(dialog).getByRole("textbox", { name: "Название" });
  expect(name).toHaveValue("Проект Альфа");
  expect(name).toHaveFocus();
  expect(name).toHaveAttribute("maxlength", "60");
  await userEvent.clear(name);
  await userEvent.type(name, "бета{Enter}");
  expect(within(dialog).getByRole("alert")).toHaveTextContent("Группа «бета» уже есть");
  expect(name).toHaveAttribute("aria-invalid", "true");
  await userEvent.clear(name);
  await userEvent.type(name, "Все записи{Enter}");
  expect(within(dialog).getByRole("alert")).toHaveTextContent("«Все записи» — не название группы");
  await userEvent.clear(name);
  await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  expect(within(dialog).getByRole("alert")).toHaveTextContent("Нужно название группы");
  expect(api.patchGroup).not.toHaveBeenCalled();
  // Резидент отказал (гонка с другим окном) — его слова, окно остаётся.
  vi.mocked(api.patchGroup).mockRejectedValueOnce(new api.ApiError(400, "группа «Гамма» уже есть"));
  await userEvent.type(name, "Гамма");
  await userEvent.click(within(dialog).getByRole("radio", { name: "Зелёный" }));
  await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  expect(within(dialog).getByRole("alert")).toHaveTextContent("Группа «Гамма» уже есть");
  await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  expect(api.patchGroup).toHaveBeenLastCalledWith(ep, "g-a", { name: "Гамма", color: "#2fa36b" });
  expect(screen.queryByRole("dialog")).toBeNull();
  // Фокус — обратно на «⋯» группы.
  expect(panel().getByRole("button", { name: "Действия с группой «Проект Альфа»" })).toHaveFocus();
});

test("новая группа: окно, Esc закрывает, создание со свободным цветом", async () => {
  await setup();
  await userEvent.click(screen.getByRole("button", { name: "Новая группа" }));
  expect(screen.getByRole("dialog", { name: "Новая группа" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Новая группа" })).toHaveFocus();
  await userEvent.click(screen.getByRole("button", { name: "Новая группа" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Название" }), "  Проект   Гамма {Enter}");
  // Синий и красный заняты — первый свободный из палитры.
  expect(api.createGroup).toHaveBeenCalledWith(ep, { name: "Проект Гамма", color: "#3aa7b8" });
});

test("удаление: уведомление «Отменить» возвращает группу с тем же id, местом и всеми полями", async () => {
  vi.mocked(api.deleteGroup).mockResolvedValue({
    group: { id: "g-a", name: "Проект Альфа", color: "#4c8bf5", created_at: "2026-09-01T10:00:00", parent: "g-x" } as never,
    index: 0,
  });
  window.localStorage.setItem("meet.groupScope", '"g-a"');
  await setup();
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Проект Альфа»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить" }));
  expect(api.deleteGroup).toHaveBeenCalledWith(ep, "g-a");
  await said("Группа «Проект Альфа» удалена");
  const toast = toastBox();
  // Уведомление — в контейнере Aurora .toasts: место у края окна задаёт он.
  expect(document.querySelector(".toasts .toast")).not.toBeNull();
  // Открытую группу удалили — список ко всем записям.
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null");
  await userEvent.click(within(toast).getByRole("button", { name: "Отменить" }));
  expect(api.createGroup).toHaveBeenCalledWith(ep, {
    id: "g-a", name: "Проект Альфа", color: "#4c8bf5", created_at: "2026-09-01T10:00:00", parent: "g-x", index: 0,
  });
  await waitFor(() => expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", '["g-a"]'));
  expect(document.querySelector(".toast")).toBeNull();
});

test("неизвестная группа: «Назвать…» — тем же id; «Убрать из встреч» — после подтверждения", async () => {
  vi.mocked(api.getRecordings).mockResolvedValue({ root: "C:/rec", items: [rec("x", { group: "g-old" })] });
  await setup();
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Группа без названия»" }));
  expect(screen.getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Назвать…", "Убрать из встреч"]);
  await userEvent.click(screen.getByRole("menuitem", { name: "Назвать…" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Название" }), "Архив{Enter}");
  expect(api.createGroup).toHaveBeenCalledWith(ep, { id: "g-old", name: "Архив", color: "#3aa7b8" });

  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Группа без названия»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Убрать из встреч" }));
  const ask = screen.getByRole("alertdialog", { name: "Убрать группу из встреч?" });
  expect(ask).toHaveTextContent("уйдёт из 1 встречи");
  await userEvent.click(within(ask).getByRole("button", { name: "Убрать" }));
  await waitFor(() => expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-old", { remove: ["x"] }));
  expect(api.getRecordings).toHaveBeenCalledWith(ep, undefined, { groups: ["g-old"] });
});

// --- повреждённый файл, новее, старый резидент --------------------------------------------

test("файл групп повреждён — предупреждение с путём копии, интерфейс работает", async () => {
  await setup({ ...INFO, broken: true, broken_copy: "C:/rec/.meet-groups.json.broken-20261006" });
  const warn = screen.getByText(/Файл групп повреждён, копия:/).closest("[role=alert]")!;
  // Видно имя файла, весь путь — в подсказке и для диктора.
  expect(warn.querySelector(".nav-groups__path")).toHaveTextContent(".meet-groups.json.broken-20261006");
  // Сжимается начало имени, время в конце — отдельно и целиком.
  expect(warn.querySelector(".nav-groups__path-tail")).toHaveTextContent(/^20261006$/);
  expect(warn).toHaveAccessibleDescription("Файл групп повреждён, копия: C:/rec/.meet-groups.json.broken-20261006");
  expect(warn.querySelector(".sr-only")).toHaveTextContent("C:/rec/.meet-groups.json.broken-20261006");
  expect(screen.getByRole("button", { name: "Новая группа" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Скрыть предупреждение" }));
  expect(screen.queryByText(/Файл групп повреждён/)).toBeNull();
});

test("файл групп от более новой версии — только смотреть: ни меню групп, ни «Новая группа», ни перестановки", async () => {
  await setup({ ...INFO, newer: true });
  expect(screen.getByRole("note")).toHaveTextContent("более новой версией Meet");
  expect(screen.queryByRole("button", { name: "Новая группа" })).toBeNull();
  // Меню — только у неизвестной группы, и в нём только «Убрать из встреч» (это meta.json встреч).
  expect(panel().getAllByRole("button", { name: /Действия с группой/ }).map((b) => b.getAttribute("aria-label")))
    .toEqual(["Действия с группой «Группа без названия»"]);
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Группа без названия»" }));
  expect(screen.getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Убрать из встреч"]);
  await userEvent.keyboard("{Escape}");
  rowOf("Проект Альфа").focus();
  await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
  expect(api.orderGroups).not.toHaveBeenCalled();
  // Перенести встречу можно (это её meta.json), создать группу из меню — нет.
  await recordMenu("Созвон");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  expect(screen.getAllByRole("menuitemradio").map((b) => b.textContent)).toEqual(["Проект Альфа", "Бета", "Без группы"]);
  expect(screen.queryByRole("menuitem", { name: "Новая группа…" })).toBeNull();
});

test("старый резидент без /groups — интерфейса групп нет, ошибок нет", async () => {
  await setup(new api.ApiError(404, "нет такого адреса"));
  expect(screen.queryByRole("list", { name: "Группы встреч" })).toBeNull();
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null");
  expect(screen.getByRole("combobox", { name: "Поиск по записям" })).toHaveAttribute("placeholder", "Поиск");
  await recordMenu("Планёрка");
  expect(screen.queryByRole("menuitem", { name: "Переместить в группу" })).toBeNull();
  expect(screen.getByRole("alert")).toBeEmptyDOMElement();
});

test("старый резидент: запомненная область в фильтр не идёт", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-a"');
  await setup(new api.ApiError(404, "нет такого адреса"));
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null");
  expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
});

// --- меню записи и панель выбора -----------------------------------------------------------

test("меню записи: «Переместить в группу ▸» — menuitemradio, «Без группы», черта, «Новая группа…»", async () => {
  await setup();
  await recordMenu("Планёрка");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  const menu = screen.getByRole("menu", { name: "Группа записи «Планёрка»" });
  const radios = within(menu).getAllByRole("menuitemradio");
  expect(radios.map((b) => [b.textContent, b.getAttribute("aria-checked")]))
    .toEqual([["Проект Альфа", "true"], ["Бета", "false"], ["Без группы", "false"]]);
  expect(radios[0]).toHaveFocus();
  expect(within(menu).getByRole("separator")).toBeInTheDocument();
  expect(within(menu).getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Новая группа…", "Назад"]);
  await userEvent.click(within(menu).getByRole("menuitemradio", { name: "Бета" }));
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-b", { add: ["a"] });
  await said("Перемещено в «Бета»");
  const toast = toastBox();
  // «Отменить» — обратно в прежнюю группу.
  await userEvent.click(within(toast).getByRole("button", { name: "Отменить" }));
  expect(api.setGroupMembers).toHaveBeenLastCalledWith(ep, "g-a", { add: ["a"], restore: true });
});

test("меню записи: «Назад» возвращает к действиям, фокус — на «Переместить в группу»", async () => {
  await setup();
  await recordMenu("Созвон");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  expect(screen.getByRole("menuitemradio", { name: "Без группы" })).toHaveAttribute("aria-checked", "true");
  await userEvent.click(screen.getByRole("menuitem", { name: "Назад" }));
  expect(screen.getByRole("menuitem", { name: "Переместить в группу" })).toHaveFocus();
});

test("меню записи: «Новая группа…» — создать и перенести в неё", async () => {
  await setup();
  await recordMenu("Созвон");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Новая группа…" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Название" }), "Клиент{Enter}");
  expect(api.createGroup).toHaveBeenCalledWith(ep, { name: "Клиент", color: "#3aa7b8" });
  await waitFor(() => expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-new", { add: ["c"] }));
  // Группы ещё нет в списке (резидент его не перечитал) — имя берётся из ответа на создание.
  await said("Перемещено в «Клиент»");
});

test("меню выбранной записи переносит все выбранные; «Без группы» — из каждой прежней группы", async () => {
  await setup();
  fireEvent.click(recordMain("Планёрка"), { ctrlKey: true });
  fireEvent.click(recordMain("Демо"), { ctrlKey: true });
  fireEvent.click(recordMain("Созвон"), { ctrlKey: true });
  await recordMenu("Демо");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  const menu = screen.getByRole("menu", { name: "Переместить в группу: 3 встречи" });
  // Группы разные — ни один вариант не отмечен.
  expect(within(menu).getAllByRole("menuitemradio").filter((b) => b.getAttribute("aria-checked") === "true")).toEqual([]);
  await userEvent.click(within(menu).getByRole("menuitemradio", { name: "Без группы" }));
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-a", { remove: ["a"] });
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-b", { remove: ["d"] });
  expect(api.setGroupMembers).toHaveBeenCalledTimes(2);
  await said("Перемещено в «Без группы»");
  const toast = toastBox();
  await userEvent.click(within(toast).getByRole("button", { name: "Отменить" }));
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-a", { add: ["a"], restore: true });
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-b", { add: ["d"], restore: true });
});

test("не выбранная строка в режиме выбора переносится одна", async () => {
  await setup();
  fireEvent.click(recordMain("Планёрка"), { ctrlKey: true });
  fireEvent.click(recordMain("Ретро"), { ctrlKey: true });
  await recordMenu("Созвон");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Бета" }));
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-b", { add: ["c"] });
});

test("панель выбора: «В группу ▾» — одиночный выбор; «Убрать из группы» — когда группа у всех одна", async () => {
  await setup();
  fireEvent.click(recordMain("Планёрка"), { ctrlKey: true });
  fireEvent.click(recordMain("Ретро"), { ctrlKey: true });
  const bar = screen.getByRole("toolbar", { name: "Выбранные записи" });
  const remove = within(bar).getByRole("button", { name: "Убрать из группы" });
  expect(remove).toHaveAccessibleDescription("Убрать из «Проект Альфа»");
  await userEvent.click(within(bar).getByRole("button", { name: "В группу" }));
  const menu = screen.getByRole("menu", { name: "Переместить в группу: 2 встречи" });
  expect(within(menu).getByRole("menuitemradio", { name: "Проект Альфа" })).toHaveAttribute("aria-checked", "true");
  await userEvent.click(within(menu).getByRole("menuitemradio", { name: "Бета" }));
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-b", { add: ["a", "b"] });
  vi.mocked(api.setGroupMembers).mockClear();
  await userEvent.click(remove);
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-a", { remove: ["a", "b"] });
  // Группы разные — «Убрать из группы» нет.
  fireEvent.click(recordMain("Созвон"), { ctrlKey: true });
  expect(within(bar).queryByRole("button", { name: "Убрать из группы" })).toBeNull();
});

test("перенос не удался у части встреч — ошибка вместо «Отменить»", async () => {
  vi.mocked(api.setGroupMembers).mockResolvedValueOnce({ changed: [], failed: [{ id: "a", error: "записи нет" }] });
  await setup();
  await recordMenu("Планёрка");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Бета" }));
  await alerted("Не удалось перенести 1 из 1: записи нет");
  expect(within(toastBox()).queryByRole("button", { name: "Отменить" })).toBeNull();
});

// --- перетаскивание (pointer-события) ------------------------------------------------------

function pointer(target: Element | Window, type: string, x: number, y: number, extra: PointerEventInit = {}) {
  fireEvent(target, new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 7, pointerType: "mouse",
    button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: x, clientY: y, ...extra }));
}
/** Что «под указателем»: jsdom не раскладывает страницу. */
function hover(el: () => Element | null) {
  document.elementFromPoint = vi.fn(() => el());
}
afterEach(() => { delete (document as { elementFromPoint?: unknown }).elementFromPoint; });

test("перетаскивание встречи на группу в панели: подсветка, тень, перенос, «Отменить»; щелчка нет", async () => {
  await setup();
  const li = () => rowOf("Бета").closest("li");
  hover(li);
  const row = recordMain("Созвон");
  pointer(row, "pointerdown", 300, 200);
  pointer(window, "pointermove", 302, 202);
  expect(document.querySelector(".drag-ghost")).toBeNull(); // до порога — ничего
  pointer(window, "pointermove", 120, 140);
  expect(document.querySelector(".drag-ghost")).toHaveTextContent("Созвон");
  expect(li()).toHaveClass("nav-group--drop");
  expect(rowOf("Без группы").closest("li")).toHaveClass("nav-group--droppable");
  pointer(window, "pointerup", 120, 140);
  fireEvent.click(row); // браузер шлёт щелчок сразу за отпусканием
  await act(async () => {});
  expect(onSelect).not.toHaveBeenCalled();
  expect(document.querySelector(".drag-ghost")).toBeNull();
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-b", { add: ["c"] });
  await said("Перемещено в «Бета»");
  const toast = toastBox();
  await userEvent.click(within(toast).getByRole("button", { name: "Отменить" }));
  expect(api.setGroupMembers).toHaveBeenLastCalledWith(ep, "g-b", { remove: ["c"] });
});

test("перетаскивание выбранных: тень «2 встречи», на «Без группы» — убрать из групп", async () => {
  await setup();
  fireEvent.click(recordMain("Планёрка"), { ctrlKey: true });
  fireEvent.click(recordMain("Демо"), { ctrlKey: true });
  hover(() => rowOf("Без группы").closest("li"));
  pointer(recordMain("Демо"), "pointerdown", 300, 200);
  pointer(window, "pointermove", 120, 300);
  expect(document.querySelector(".drag-ghost")).toHaveTextContent("2 встречи");
  pointer(window, "pointerup", 120, 300);
  await act(async () => {});
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-a", { remove: ["a"] });
  await waitFor(() => expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-b", { remove: ["d"] }));
});

test("перетаскивание: Esc или отпускание вне цели — ничего не меняется", async () => {
  await setup();
  hover(() => rowOf("Бета").closest("li"));
  pointer(recordMain("Созвон"), "pointerdown", 300, 200);
  pointer(window, "pointermove", 120, 140);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
  expect(document.querySelector(".drag-ghost")).toBeNull();
  pointer(window, "pointerup", 120, 140);
  await act(async () => {});
  hover(() => document.body);
  pointer(recordMain("Созвон"), "pointerdown", 300, 200);
  pointer(window, "pointermove", 600, 500);
  pointer(window, "pointerup", 600, 500);
  await act(async () => {});
  expect(api.setGroupMembers).not.toHaveBeenCalled();
  expect(document.querySelector(".toast")).toBeNull();
});

test("порядок групп перетаскиванием в панели: линия вставки, PUT /groups/order, область не меняется", async () => {
  await setup();
  const beta = () => rowOf("Бета").closest("li")!;
  hover(beta);
  beta().getBoundingClientRect = () => ({ top: 100, bottom: 126, left: 0, right: 180, height: 26, width: 180, x: 0, y: 100,
    toJSON: () => ({}) });
  pointer(rowOf("Проект Альфа"), "pointerdown", 40, 80);
  pointer(window, "pointermove", 40, 120);
  expect(beta()).toHaveClass("nav-group--insert-after");
  expect(rowOf("Проект Альфа").closest("li")).toHaveClass("nav-group--dragging");
  expect(document.querySelector(".drag-ghost")).toHaveTextContent("Проект Альфа");
  pointer(window, "pointerup", 40, 120);
  fireEvent.click(rowOf("Проект Альфа"));
  await act(async () => {});
  expect(api.orderGroups).toHaveBeenCalledWith(ep, ["g-b", "g-a"]);
  expect(rowOf("Все записи")).toHaveAttribute("aria-current", "true");
});

// --- фокус, уведомление, Tab (fix round 1) -------------------------------------------------

test("Alt+↑ у первой группы ничего не ждёт: набор в поиске после этого фокус не теряет", async () => {
  await setup();
  rowOf("Проект Альфа").focus();
  await userEvent.keyboard("{Alt>}{ArrowUp}{/Alt}");
  expect(api.orderGroups).not.toHaveBeenCalled();
  const search = screen.getByRole("combobox", { name: "Поиск по записям" });
  await userEvent.click(search);
  await userEvent.type(search, "бюджет");
  // Поиск перечитывает группы (счётчики среди найденного) — фокус остаётся в поле.
  await waitFor(() => expect(api.getGroups).toHaveBeenCalledWith(ep, "бюджет", undefined), { timeout: 2000 });
  await act(async () => {});
  expect(search).toHaveFocus();
  expect(search).toHaveValue("бюджет");
});

test("перестановка не удалась — фокус потом не прыгает к группе", async () => {
  vi.mocked(api.orderGroups).mockRejectedValueOnce(new api.ApiError(503, "файл групп сейчас занят"));
  await setup();
  rowOf("Проект Альфа").focus();
  await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
  await alerted("Файл групп сейчас занят");
  const search = screen.getByRole("combobox", { name: "Поиск по записям" });
  await userEvent.type(search, "план");
  await waitFor(() => expect(api.getGroups).toHaveBeenCalledWith(ep, "план", undefined), { timeout: 2000 });
  await act(async () => {});
  expect(search).toHaveFocus();
});

test("Tab: в списке групп одна остановка — выбранная область; «⋯» — по Shift+F10", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-b"');
  await setup();
  const stops = [...document.querySelectorAll<HTMLElement>(".nav-groups [data-scope-key]")].filter((b) => b.tabIndex === 0);
  expect(stops).toEqual([rowOf("Бета")]);
  for (const more of panel().getAllByRole("button", { name: /Действия с группой/ })) expect(more).toHaveAttribute("tabindex", "-1");
  expect(rowOf("Бета")).toHaveAttribute("aria-keyshortcuts", "Alt+ArrowUp Alt+ArrowDown Shift+F10");
  rowOf("Бета").focus();
  fireEvent.contextMenu(rowOf("Бета"), { clientX: 0, clientY: 0 });
  expect(screen.getByRole("menu", { name: "Действия с группой «Бета»" })).toBeInTheDocument();
});

test("удаление из меню строки: строки больше нет — фокус на «Все записи»; «Отменить» — на вернувшуюся группу", async () => {
  vi.mocked(api.deleteGroup).mockResolvedValue({
    group: { id: "g-a", name: "Проект Альфа", color: "#4c8bf5", created_at: "2026-09-01T10:00:00" }, index: 0,
  });
  await setup();
  vi.mocked(api.getGroups).mockResolvedValue({ ...INFO, groups: INFO.groups.slice(1), unknown: [...INFO.unknown, { id: "g-a", count: 2 }] });
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Проект Альфа»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить" }));
  await waitFor(() => expect(rowOf("Все записи")).toHaveFocus());
  await said("Группа «Проект Альфа» удалена");
  vi.mocked(api.getGroups).mockResolvedValue(INFO);
  await userEvent.click(within(toastBox()).getByRole("button", { name: "Отменить" }));
  await waitFor(() => expect(rowOf("Проект Альфа")).toHaveFocus());
});

test("удаление из меню заголовка открытой группы — фокус на «Все записи»", async () => {
  vi.mocked(api.deleteGroup).mockResolvedValue({
    group: { id: "g-b", name: "Бета", color: "#e5484d", created_at: "2026-09-01T10:00:00" }, index: 1,
  });
  window.localStorage.setItem("meet.groupScope", '"g-b"');
  await setup();
  vi.mocked(api.getGroups).mockResolvedValue({ ...INFO, groups: INFO.groups.slice(0, 1) });
  const head = screen.getByRole("heading", { level: 2 }).parentElement!;
  await userEvent.click(within(head).getByRole("button", { name: "Действия с группой «Бета»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить" }));
  await waitFor(() => expect(rowOf("Все записи")).toHaveFocus());
  expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
});

test("уведомление: области для диктора есть заранее и без кнопок; «Отменить» переноса — фокус на встречу", async () => {
  await setup();
  // Области в документе ещё до сообщения (вставленную готовой область дикторы пропускают).
  expect(screen.getByRole("status")).toBeEmptyDOMElement();
  expect(screen.getByRole("alert")).toBeEmptyDOMElement();
  await recordMenu("Планёрка");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Бета" }));
  await said("Перемещено в «Бета»");
  // В области — только текст; кнопки — в видимом уведомлении, оно названо тем же текстом.
  expect(screen.getByRole("status")).toHaveTextContent(/^Перемещено в «Бета»$/);
  expect(within(screen.getByRole("status")).queryByRole("button")).toBeNull();
  expect(screen.getByRole("group", { name: "Перемещено в «Бета»" })).toBe(toastBox());
  await userEvent.click(within(toastBox()).getByRole("button", { name: "Отменить" }));
  expect(api.setGroupMembers).toHaveBeenLastCalledWith(ep, "g-a", { add: ["a"], restore: true });
  await waitFor(() => expect(recordMain("Планёрка")).toHaveFocus());
  expect(document.querySelector(".toast")).toBeNull();
});

test("уведомление закрыли «×» — фокус туда, откуда пришёл", async () => {
  await setup();
  await recordMenu("Созвон");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Бета" }));
  await said("Перемещено в «Бета»");
  const search = screen.getByRole("combobox", { name: "Поиск по записям" });
  search.focus();
  await userEvent.click(within(toastBox()).getByRole("button", { name: "Закрыть уведомление" }));
  expect(document.querySelector(".toast")).toBeNull();
  expect(search).toHaveFocus();
});

test("перечитывание групп (поиск, события) не перерисовывает строки списка", async () => {
  await setup();
  vi.mocked(useAgentLive).mockClear();
  vi.mocked(api.getGroups).mockResolvedValue({ ...INFO, groups: INFO.groups.map((g) => ({ ...g, count: g.count + 1 })) });
  await userEvent.type(screen.getByRole("combobox", { name: "Поиск по записям" }), "пл");
  await waitFor(() => expect(api.getGroups).toHaveBeenCalledWith(ep, "пл", undefined), { timeout: 2000 });
  await act(async () => {});
  expect(rows()[1]).toBe("Проект Альфа 3");
  expect(useAgentLive).not.toHaveBeenCalled();
  // Меню переноса всё равно видит свежий список.
  await recordMenu("Созвон");
  await userEvent.click(screen.getByRole("menuitem", { name: "Переместить в группу" }));
  expect(screen.getAllByRole("menuitemradio").map((b) => b.textContent)).toEqual(["Проект Альфа", "Бета", "Без группы"]);
});

test("«Отменить» удаление пустой открытой группы возвращает и область", async () => {
  vi.mocked(api.deleteGroup).mockResolvedValue({
    group: { id: "g-d", name: "Пустая", color: "#8e6cd8", created_at: "2026-09-01T10:00:00" }, index: 2,
  });
  const withEmpty: GroupsInfo = { ...INFO, groups: [...INFO.groups, { id: "g-d", name: "Пустая", color: "#8e6cd8", count: 0 }] };
  window.localStorage.setItem("meet.groupScope", '"g-d"');
  await setup(withEmpty);
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", '["g-d"]');
  // После удаления группы нет нигде: встреч у неё не было, в неизвестные она не попадает.
  vi.mocked(api.getGroups).mockResolvedValue(INFO);
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Пустая»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить" }));
  await waitFor(() => expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null"));
  await said("Группа «Пустая» удалена");
  // Ответ на перечитывание — не сразу (как настоящий запрос): окно успевает отрисоваться со старым списком.
  vi.mocked(api.getGroups).mockImplementation(() => new Promise((ok) => setTimeout(() => ok(withEmpty), 50)));
  await userEvent.click(within(toastBox()).getByRole("button", { name: "Отменить" }));
  await waitFor(() => expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", '["g-d"]'));
  await act(async () => {});
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", '["g-d"]');
  expect(window.localStorage.getItem("meet.groupScope")).toBe('"g-d"');
  expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Пустая · 0 встреч");
});

// --- кнопка-список групп над поиском (0.4) ---------------------------------------------------

/** Кнопка-список над поиском: имя текущей группы. */
const picker = () => screen.getByRole("button", { name: /^Группа встреч: / });
const pickerBox = () => screen.getByRole("dialog", { name: "Группы" });
const pickerTree = () => within(pickerBox());

test("над поиском — кнопка-список с текущей группой; дерево с «Новая группа» — в поповере; выбор — область", async () => {
  await setup(INFO, { nav: false });
  // Дерева рядом нет: группы — только в кнопке-списке.
  expect(screen.queryByRole("list", { name: "Группы встреч" })).toBeNull();
  const button = picker();
  expect(button).toHaveAccessibleName("Группа встреч: Все записи");
  expect(button).toHaveAttribute("aria-haspopup", "dialog");
  expect(button).toHaveAttribute("aria-expanded", "false");
  // Кнопка — над поиском.
  const search = screen.getByRole("combobox", { name: "Поиск по записям" });
  expect(button.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await userEvent.click(button);
  expect(button).toHaveAttribute("aria-expanded", "true");
  expect(pickerTree().getByRole("list", { name: "Группы встреч" })).toBeInTheDocument();
  expect(pickerTree().getByRole("button", { name: "Новая группа" })).toBeInTheDocument();
  // Фокус — на выбранной области в дереве.
  expect(pickerTree().getByRole("button", { name: /^Все записи,/ })).toHaveFocus();
  await userEvent.click(pickerTree().getByRole("button", { name: /^Проект Альфа,/ }));
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", '["g-a"]');
  expect(screen.queryByRole("dialog", { name: "Группы" })).toBeNull();
  expect(picker()).toHaveAccessibleName("Группа встреч: Проект Альфа");
  expect(picker()).toHaveFocus();
  expect(search).toHaveAttribute("placeholder", "Поиск в «Проект Альфа»");
  expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Проект Альфа · 2 встречи");
  // Повторно — выбранная группа отмечена; «Без группы» — тоже область.
  await userEvent.click(picker());
  expect(pickerTree().getByRole("button", { name: /^Проект Альфа,/ })).toHaveAttribute("aria-current", "true");
  await userEvent.click(pickerTree().getByRole("button", { name: /^Без группы,/ }));
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", '["_none"]');
  expect(picker()).toHaveAccessibleName("Группа встреч: Без группы");
});

test("кнопка-список с клавиатуры: ↓, Enter, Пробел открывают; Esc закрывает и возвращает фокус", async () => {
  await setup(INFO, { nav: false });
  picker().focus();
  await userEvent.keyboard("{ArrowDown}");
  expect(pickerTree().getByRole("button", { name: /^Все записи,/ })).toHaveFocus();
  // Внутри — клавиатура дерева.
  await userEvent.keyboard("{ArrowDown}");
  expect(pickerTree().getByRole("button", { name: /^Проект Альфа,/ })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Группы" })).toBeNull();
  expect(picker()).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  expect(pickerBox()).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(picker()).toHaveFocus();
  await userEvent.keyboard(" ");
  expect(pickerBox()).toBeInTheDocument();
  await userEvent.keyboard("{Enter}");
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null");
  expect(picker()).toHaveFocus();
});

test("меню группы в поповере — вне стекла (порталом); Esc закрывает только меню; пункт меню работает", async () => {
  await setup(INFO, { nav: false });
  await userEvent.click(picker());
  const box = pickerBox();
  await userEvent.click(within(box).getByRole("button", { name: "Действия с группой «Бета»" }));
  const menu = screen.getByRole("menu", { name: "Действия с группой «Бета»" });
  // Стекло (backdrop-filter) сдвинуло бы fixed-меню: меню — не внутри поповера.
  expect(box.contains(menu)).toBe(false);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(pickerBox()).toBeInTheDocument();
  expect(within(box).getByRole("button", { name: "Действия с группой «Бета»" })).toHaveFocus();
  // Нажатие на пункт меню — не «снаружи» поповера.
  await userEvent.click(within(box).getByRole("button", { name: "Действия с группой «Бета»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Выше" }));
  expect(api.orderGroups).toHaveBeenCalledWith(ep, ["g-b", "g-a"]);
  expect(pickerBox()).toBeInTheDocument();
});

test("окно названия из раскрытого дерева: поповер не закрывается от щелчков в окне, фокус — обратно в дерево", async () => {
  await setup(INFO, { nav: false });
  await userEvent.click(picker());
  await userEvent.click(pickerTree().getByRole("button", { name: "Действия с группой «Бета»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Переименовать…" }));
  const input = screen.getByRole("textbox", { name: "Название" });
  await userEvent.click(input);
  await userEvent.clear(input);
  await userEvent.type(input, "Гамма");
  expect(pickerBox()).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(pickerBox()).toBeInTheDocument();
  expect(pickerTree().getByRole("button", { name: "Действия с группой «Бета»" })).toHaveFocus();
  // «Новая группа» из дерева — то же окно.
  await userEvent.click(pickerTree().getByRole("button", { name: "Новая группа" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Название" }), "Клиент{Enter}");
  expect(api.createGroup).toHaveBeenCalledWith(ep, { name: "Клиент", color: "#3aa7b8" });
});

test("перетаскивание встречи на группу в раскрытом дереве: нажатие на строку поповер не закрывает", async () => {
  await setup(INFO, { nav: false });
  await userEvent.click(picker());
  hover(() => pickerTree().getByRole("button", { name: /^Бета,/ }).closest("li"));
  const row = recordMain("Созвон");
  pointer(row, "pointerdown", 300, 200);
  fireEvent.mouseDown(row); // браузер шлёт mousedown вслед за pointerdown
  pointer(window, "pointermove", 120, 140);
  expect(pickerBox()).toBeInTheDocument();
  expect(document.querySelector(".drag-ghost")).toHaveTextContent("Созвон");
  expect(pickerTree().getByRole("button", { name: /^Бета,/ }).closest("li")).toHaveClass("nav-group--drop");
  pointer(window, "pointerup", 120, 140);
  fireEvent.click(row); // щелчок за отпусканием гасится: ни открытия встречи, ни закрытия дерева
  await act(async () => {});
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-b", { add: ["c"] });
  expect(onSelect).not.toHaveBeenCalled();
  expect(pickerBox()).toBeInTheDocument();
  // Простой щелчок по встрече — открыть её; дерево закрывается.
  delete (document as { elementFromPoint?: unknown }).elementFromPoint;
  await userEvent.click(recordMain("Демо"));
  expect(onSelect).toHaveBeenCalledWith("d");
  expect(screen.queryByRole("dialog", { name: "Группы" })).toBeNull();
});

test("открытая группа: рядом с кнопкой-списком — меню группы и «Показать все записи»", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-b"');
  await setup(INFO, { nav: false });
  expect(picker()).toHaveAccessibleName("Группа встреч: Бета");
  const head = screen.getByRole("heading", { level: 2 }).parentElement!;
  expect(head).toContainElement(picker());
  await userEvent.click(within(head).getByRole("button", { name: "Действия с группой «Бета»" }));
  expect(screen.getByRole("menu", { name: "Действия с группой «Бета»" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(within(head).getByRole("button", { name: "Показать все записи" }));
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null");
  expect(picker()).toHaveAccessibleName("Группа встреч: Все записи");
});

test("порядок групп перетаскиванием в раскрытом дереве: нажатие на группу не перехватывает список записей", async () => {
  await setup(INFO, { nav: false });
  await userEvent.click(picker());
  const beta = () => pickerTree().getByRole("button", { name: /^Бета,/ }).closest("li")!;
  const alpha = () => pickerTree().getByRole("button", { name: /^Проект Альфа,/ });
  hover(beta);
  beta().getBoundingClientRect = () => ({ top: 100, bottom: 126, left: 0, right: 180, height: 26, width: 180, x: 0, y: 100,
    toJSON: () => ({}) });
  pointer(alpha(), "pointerdown", 40, 80);
  pointer(window, "pointermove", 40, 120);
  expect(beta()).toHaveClass("nav-group--insert-after");
  expect(document.querySelector(".drag-ghost")).toHaveTextContent("Проект Альфа");
  pointer(window, "pointerup", 40, 120);
  fireEvent.click(alpha());
  await act(async () => {});
  expect(api.orderGroups).toHaveBeenCalledWith(ep, ["g-b", "g-a"]);
  expect(screen.getByTestId("scope")).toHaveAttribute("data-scope", "null");
  expect(pickerBox()).toBeInTheDocument();
});

test("Ctrl+K из раскрытого дерева — к поиску, дерево закрывается", async () => {
  await setup(INFO, { nav: false });
  await userEvent.click(picker());
  expect(pickerTree().getByRole("button", { name: /^Все записи,/ })).toHaveFocus();
  await userEvent.keyboard("{Control>}k{/Control}");
  expect(screen.getByRole("combobox", { name: "Поиск по записям" })).toHaveFocus();
  expect(screen.queryByRole("dialog", { name: "Группы" })).toBeNull();
  // Дерево открыто, фокус на кнопке-списке — так же.
  await userEvent.click(picker());
  picker().focus();
  await userEvent.keyboard("{Control>}k{/Control}");
  expect(screen.getByRole("combobox", { name: "Поиск по записям" })).toHaveFocus();
  expect(screen.queryByRole("dialog", { name: "Группы" })).toBeNull();
});

test("старый резидент без /groups — кнопки-списка нет", async () => {
  await setup(new api.ApiError(404, "нет такого адреса"), { nav: false });
  expect(screen.queryByRole("button", { name: /^Группа встреч/ })).toBeNull();
});

// --- папка базы знаний группы ----------------------------------------------------------------

test("«Папка базы знаний…» в меню группы: папка внутри базы — PATCH kb_folder, видна в заголовке группы", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue({ knowledge_dir: "D:\\KB" } as never);
  vi.mocked(shell.pickFolder).mockResolvedValueOnce("D:\\KB\\Проекты\\Альфа");
  const view = await setup();
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Проект Альфа»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Папка базы знаний…" }));
  await waitFor(() => expect(api.patchGroup).toHaveBeenCalledWith(ep, "g-a", { kb_folder: "Проекты/Альфа" }));
  expect(shell.pickFolder).toHaveBeenCalledWith("D:\\KB");
  await said("Папка базы знаний группы «Проект Альфа»: Проекты/Альфа");
  // Резидент отдал группу с папкой: она в заголовке открытой группы и в меню — «Убрать…».
  vi.mocked(api.getGroups).mockResolvedValue({
    ...INFO, groups: [{ ...INFO.groups[0]!, kb_folder: "Проекты/Альфа" }, INFO.groups[1]!],
  });
  view.unmount();
  await setup({ ...INFO, groups: [{ ...INFO.groups[0]!, kb_folder: "Проекты/Альфа" }, INFO.groups[1]!] });
  await userEvent.click(rowOf("Проект Альфа"));
  expect(document.querySelector(".group-head")).toHaveTextContent("Папка базы знаний: Проекты/Альфа");
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Проект Альфа»" }));
  vi.mocked(shell.pickFolder).mockResolvedValueOnce(null);
  await userEvent.click(screen.getByRole("menuitem", { name: "Папка базы знаний…" }));
  expect(shell.pickFolder).toHaveBeenLastCalledWith("D:\\KB\\Проекты\\Альфа");
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Проект Альфа»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Убрать папку базы знаний" }));
  await waitFor(() => expect(api.patchGroup).toHaveBeenLastCalledWith(ep, "g-a", { kb_folder: null }));
});

test("«Папка базы знаний…»: папка вне базы — уведомление, ничего не записано; базы нет — подсказка", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue({ knowledge_dir: "D:\\KB" } as never);
  vi.mocked(shell.pickFolder).mockResolvedValueOnce("C:\\Users\\me\\Desktop");
  await setup();
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Бета»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Папка базы знаний…" }));
  await alerted("Папка вне базы знаний");
  expect(api.patchGroup).not.toHaveBeenCalled();
  vi.mocked(api.getAssistant).mockResolvedValue({ knowledge_dir: null } as never);
  await userEvent.click(panel().getByRole("button", { name: "Действия с группой «Бета»" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Папка базы знаний…" }));
  await alerted("Базы знаний нет");
  expect(shell.pickFolder).toHaveBeenCalledTimes(1);
});
