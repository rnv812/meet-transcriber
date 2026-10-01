import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingsList } from "./RecordingsList";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  patchRecording: vi.fn(async () => ({})),
  deleteRecording: vi.fn(async () => ({ ok: true })),
  kbExport: vi.fn(async () => ({ path: "D:/База/2026-09-30 - Планёрка", files: [], kept: [] })),
}));
import type { Job, Recording } from "../../lib/types";

const ep = { base: "/api", token: null };
const rec = (id: string, extra: Partial<Recording> = {}): Recording => ({
  id, path: `C:/rec/${id}`, started_at: "2026-09-30T10:00:00", duration_s: 1800, tracks: { sys: "s.wav" },
  has_transcript: false, has_voices: false, title: null, source: "record", ...extra,
});
const job = (folder: string, extra: Partial<Job>): Job => ({
  id: "j1", kind: "transcribe", folder, state: "running", stage: "asr", label: null,
  done: 1, total: 2, note: null, result: null, error: null, ...extra,
});

const items = [
  rec("a", { has_transcript: true, title: "Планёрка" }),
  rec("b"),
  rec("c"),
];
const jobs = [
  job("C:/rec/b", {}),
  job("C:/rec/c", { state: "failed", error: "нет памяти" }),
];
const refresh = vi.fn(async () => {});
const library = { items, jobs, loading: false, error: null, refresh };
const resident = { status: "online" as const, endpoint: ep, snapshot: null, lastEvent: null, libraryTick: 0 };

function setup(props: Partial<Parameters<typeof RecordingsList>[0]> = {}) {
  const onSelect = vi.fn();
  const onQ = vi.fn();
  render(
    <RecordingsList selected="a" onSelect={onSelect} library={library} resident={resident} q="" onQ={onQ} {...props} />,
  );
  return { onSelect, onQ };
}

const mains = () => within(screen.getByRole("list", { name: "Записи" }))
  .getAllByRole("listitem").map((li) => li.querySelector<HTMLButtonElement>(".rec-item__main")!);

test("бейджи статусов: у готовой нет, у идущей процент, у упавшей ошибка", () => {
  setup();
  const opts = mains();
  expect(opts).toHaveLength(3);
  expect(opts[0]!).toHaveTextContent("Планёрка");
  expect(opts[0]!).not.toHaveTextContent(/Ошибка|Распознавание|очереди/);
  expect(opts[0]!).toHaveAttribute("aria-current", "true");
  expect(opts[1]!).toHaveTextContent("Распознавание 50%");
  expect(opts[1]!).not.toHaveAttribute("aria-current");
  expect(opts[2]!).toHaveTextContent("Ошибка");
});

test("клик по элементу вызывает onSelect(id)", async () => {
  const { onSelect } = setup();
  await userEvent.click(mains()[1]!);
  expect(onSelect).toHaveBeenCalledWith("b");
});

test("поиск по тексту: под записью фрагменты с подсветкой, клик — открыть на реплике", async () => {
  const found = [{
    ...rec("a", { has_transcript: true, title: "Планёрка" }),
    hits: [
      { t: 30, speaker: "Борис", snippet: "Бюджет утвердим завтра.", ranges: [[0, 6]] as [number, number][] },
      { t: 75, speaker: "Анна", snippet: "…по бюджету вопросов нет.", ranges: [[4, 11]] as [number, number][] },
    ],
    total: 5,
  }];
  const onOpenHit = vi.fn();
  setup({ library: { ...library, items: found }, q: "бюджет", onOpenHit });
  const hits = within(screen.getByRole("list", { name: "Найдено в записи «Планёрка»" })).getAllByRole("button");
  expect(hits).toHaveLength(2);
  expect(hits[0]!).toHaveTextContent("00:30Борис: Бюджет утвердим завтра.");
  expect([...document.querySelectorAll("mark.hit")].map((m) => m.textContent)).toEqual(["Бюджет", "бюджету"]);
  expect(screen.getByText("Ещё совпадений: 3")).toBeInTheDocument();
  await userEvent.click(hits[1]!);
  expect(onOpenHit).toHaveBeenCalledWith("a", 75);
});

test("ввод в поиск передаётся наверх", async () => {
  const { onQ } = setup();
  await userEvent.type(screen.getByRole("searchbox"), "а");
  expect(onQ).toHaveBeenLastCalledWith("а");
});

test("зона импорта: выбрать файл в браузере — сообщение, без пути", async () => {
  setup();
  expect(screen.getByText(/Перетащите аудио или видео сюда/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "выбрать файл" }));
  expect(await screen.findByText("Импорт — из приложения или перетаскиванием в окно приложения")).toBeInTheDocument();
});

// --- переименование и меню ------------------------------------------------------

const item = (name: string) => screen.getByText(name).closest("li")!;
const titleInput = () => screen.getByRole("textbox", { name: "Название записи" });

test("F2 на записи — поле на месте названия; Enter сохраняет, фокус возвращается", async () => {
  const onChanged = vi.fn();
  setup({ onChanged });
  mains()[0]!.focus();
  await userEvent.keyboard("{F2}");
  expect(titleInput()).toHaveValue("Планёрка");
  expect(titleInput()).toHaveAttribute("maxLength", "200");
  await userEvent.clear(titleInput());
  await userEvent.type(titleInput(), "  Планёрка команды  {Enter}");
  expect(api.patchRecording).toHaveBeenCalledWith(ep, "a", { title: "Планёрка команды" });
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalledWith("a"));
  expect(refresh).toHaveBeenCalled();
  expect(mains()[0]!).toHaveFocus();
});

test("Esc отменяет, пустое название — вернуть автоматическое, уход фокуса — сохранить", async () => {
  vi.mocked(api.patchRecording).mockClear();
  setup();
  await userEvent.dblClick(screen.getByText("Планёрка"));
  await userEvent.type(titleInput(), "Другое{Escape}");
  expect(api.patchRecording).not.toHaveBeenCalled();
  expect(screen.getByText("Планёрка")).toBeInTheDocument();
  await userEvent.dblClick(screen.getByText("Планёрка"));
  await userEvent.clear(titleInput());
  await userEvent.click(document.body);
  expect(api.patchRecording).toHaveBeenCalledWith(ep, "a", { title: null });
});

test("меню «⋯»: пункты, переименование и удаление с подтверждением; Esc закрывает", async () => {
  const onDeleting = vi.fn();
  setup({ onDeleting, resident: { ...resident, snapshot: { meetings_dir: "D:/База" } as never } });
  const more = within(item("Планёрка")).getByRole("button", { name: "Действия с записью «Планёрка»" });
  await userEvent.click(more);
  const menu = screen.getByRole("menu", { name: "Действия с записью «Планёрка»" });
  expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(
    ["Переименовать", "Экспорт в базу знаний", "Удалить…"]);
  expect(within(menu).getAllByRole("menuitem")[0]).toHaveFocus();
  await userEvent.keyboard("{ArrowUp}");
  expect(within(menu).getByRole("menuitem", { name: "Удалить…" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(more).toHaveFocus();

  await userEvent.click(more);
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить…" }));
  expect(screen.getByRole("menu")).toHaveTextContent("Удалить запись и расшифровку? Это действие нельзя отменить.");
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить" }));
  expect(onDeleting).toHaveBeenCalledWith("a");
  await vi.waitFor(() => expect(api.deleteRecording).toHaveBeenCalledWith(ep, "a"));

  await userEvent.click(more);
  await userEvent.click(screen.getByRole("menuitem", { name: "Переименовать" }));
  expect(titleInput()).toHaveFocus();
});

test("правая кнопка открывает то же меню; выгрузка в базу знаний сообщает путь", async () => {
  setup({ resident: { ...resident, snapshot: { meetings_dir: "D:/База" } as never } });
  fireEvent.contextMenu(item("Планёрка"), { clientX: 40, clientY: 50 });
  await userEvent.click(screen.getByRole("menuitem", { name: "Экспорт в базу знаний" }));
  expect(api.kbExport).toHaveBeenCalledWith(ep, "a");
  expect(await screen.findByRole("status")).toHaveTextContent("Выгружено в базу знаний: D:/База/2026-09-30 - Планёрка");
});

test("без папки для встреч и у нерасшифрованной записи — без выгрузки; ошибка видна", async () => {
  vi.mocked(api.patchRecording).mockRejectedValueOnce(new Error("записи нет"));
  setup();
  await userEvent.click(within(item("Планёрка")).getByRole("button", { name: /Действия/ }));
  expect(screen.queryByRole("menuitem", { name: "Экспорт в базу знаний" })).toBeNull();
  await userEvent.click(screen.getByRole("menuitem", { name: "Переименовать" }));
  await userEvent.type(titleInput(), "Х{Enter}");
  expect(await screen.findByRole("alert")).toHaveTextContent("записи нет");
});
