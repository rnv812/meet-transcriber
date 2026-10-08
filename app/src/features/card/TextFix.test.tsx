import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import * as api from "../../lib/api";
import type { Recording, TextFixResult, TextPreview, Transcript } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(),
  getAssistant: vi.fn(),
  getSummary: vi.fn(),
  getQa: vi.fn(),
  previewTextFix: vi.fn(),
  applyTextFix: vi.fn(),
  undoSpeakers: vi.fn(),
  removeHotword: vi.fn(),
  patchSettings: vi.fn(),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => false,
  saveText: vi.fn(async () => "x"),
  openFolder: vi.fn(async () => {}),
  agentKillRecording: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const seg = (start: number, end: number, speaker: string, text: string) =>
  ({ start, end, speaker, text, uncertain: false });
// Реплики: [0] Спикер 1 (сегменты 0–1), [1] Спикер 2 (сегмент 2).
const transcript: Transcript = {
  version: 1, title: null,
  segments: [
    seg(0, 4, "Спикер 1", "Поднимем кубер нетис на стенде."),
    seg(4.5, 6, "Спикер 1", "Проверим."),
    seg(10, 12, "Спикер 2", "Кубер нетис готов."),
  ],
};
const rec: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 60,
  tracks: {}, has_transcript: true, has_voices: true, title: "Встреча", source: "record",
};
const preview: TextPreview = {
  count: 2, here: { start: 0.6, end: 1.6 },
  samples: [
    { segment: 0, offset: 9, speaker: "Спикер 1", start: 0.6, end: 1.6, before: "Поднимем ", match: "кубер нетис", after: " на стенде." },
    { segment: 2, offset: 0, speaker: "Спикер 2", start: 10, end: 10.8, before: "", match: "Кубер нетис", after: " готов." },
  ],
};
const result: TextFixResult = {
  owner: "Вы", history: [], pos: 1, speakers: [], changed: 1,
  step: { id: "t1", at: "2026-09-30T10:00:00", enrolled: [], created_people: [],
    ops: [{ type: "text", from: "кубер нетис", to: "Kubernetes", count: 1, scope: "one" }] },
  hotword: { term: "Kubernetes", added: true, over_budget: false },
};

beforeEach(() => {
  vi.clearAllMocks();
  // Последний шаг истории — исправление «t1» (после применения резидент так и ответит).
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, transcript, edit_head: "t1" });
  vi.mocked(api.getSettings).mockResolvedValue({ recording: { speaker_name: "Вы" } });
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: null, setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.previewTextFix).mockResolvedValue(preview);
  vi.mocked(api.applyTextFix).mockResolvedValue(result);
  vi.mocked(api.undoSpeakers).mockResolvedValue({ owner: "Вы", history: [], pos: 0, speakers: [] });
  vi.mocked(api.removeHotword).mockResolvedValue({ text: "SIEM\n", budget: 400, used: 4 });
});

/** Выделить символы [start, end) текста реплики, как мышью. */
function select(p: HTMLElement, start: number, end: number) {
  const text = p.firstChild!;
  const range = document.createRange();
  range.setStart(text, start);
  range.setEnd(text, end);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
}

/**
 * Выделить текст мышью и дождаться кнопки «Исправить…». Кнопка появляется после
 * `setTimeout(0)` после mouseup, а выделение может сбросить поздняя перерисовка
 * карточки (ответы getAssistant/getSummary приходят асинхронно). Под нагрузкой
 * это случается: одной попытки с `findBy` (1 с) мало. Поэтому выделяем заново и
 * повторяем, пока кнопка не появится.
 */
async function selectAndOpenFix(text: RegExp, start: number, end: number) {
  await vi.waitFor(async () => {
    const p = screen.getByText(text).closest("p")!;
    select(p, start, end);
    fireEvent.mouseUp(p);
    await screen.findByRole("button", { name: "Исправить…" }, { timeout: 2_000 });
  }, { timeout: 15_000, interval: 50 });
  await userEvent.click(screen.getByRole("button", { name: "Исправить…" }));
}

async function turnText(text: RegExp) {
  return (await screen.findByText(text)).closest("p")!;
}

test("выделение → «Исправить…»: слово целиком, совпадения, исправить одно место и добавить в термины", async () => {
  render(<RecordingCard id="r1" endpoint={ep} />);
  await turnText(/Поднимем кубер нетис/);
  await selectAndOpenFix(/Поднимем кубер нетис/, 11, 18); // «бер нет» — дополняется до «кубер нетис»
  const box = await screen.findByRole("dialog", { name: "Исправить распознанное" });
  expect(within(box).getByText("кубер нетис")).toBeInTheDocument();
  expect(api.previewTextFix).toHaveBeenCalledWith(ep, "r1", { find: "кубер нетис", segment: 0, offset: 9 });
  expect(await within(box).findByText(/Заменить во всей встрече \(2 совпадения\)/)).toBeInTheDocument();
  // Исправление ещё не вписано (в поле — распознанное): добавлять в термины нечего.
  const term = within(box).getByRole("checkbox", { name: /Добавить в термины распознавания/ });
  expect(term).not.toBeChecked();
  const input = within(box).getByRole("textbox", { name: "Как правильно" });
  await userEvent.clear(input);
  await userEvent.type(input, "Kubernetes");
  expect(term).toBeChecked();
  expect(within(box).getByText("Будет добавлено: Kubernetes")).toBeInTheDocument();
  await userEvent.click(within(box).getByRole("button", { name: "Применить" }));
  expect(api.applyTextFix).toHaveBeenCalledWith(ep, "r1", {
    find: "кубер нетис", replace: "Kubernetes", scope: "one", segment: 0, offset: 9, count: 3, add_hotword: true,
    add_rule: false });
  expect(await screen.findByText(/Исправлено: кубер нетис → Kubernetes \(1\)/)).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Исправить распознанное" })).toBeNull();

  // «Отменить» у термина убирает его из списка, остальное — как было.
  const termRow = screen.getByText("Добавлено в термины: Kubernetes.").closest(".tsel") as HTMLElement;
  await userEvent.click(within(termRow).getByRole("button", { name: "Отменить" }));
  expect(api.removeHotword).toHaveBeenCalledWith(ep, "Kubernetes");
  expect(await screen.findByText("Убрано из терминов: Kubernetes")).toBeInTheDocument();
  // «Отменить» у исправления — шаг истории встречи.
  const fix = screen.getByText(/Исправлено: кубер нетис/).closest(".tsel") as HTMLElement;
  await userEvent.click(within(fix).getByRole("button", { name: "Отменить" }));
  expect(api.undoSpeakers).toHaveBeenCalledWith(ep, "r1", "t1");
  expect(await screen.findByText("Исправление отменено")).toBeInTheDocument();
});

test("«Заменить во всей встрече» — показывает совпадения и заменяет все", async () => {
  vi.mocked(api.applyTextFix).mockResolvedValue({ ...result, changed: 2, hotword: undefined });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await turnText(/Поднимем кубер нетис/);
  await selectAndOpenFix(/Поднимем кубер нетис/, 9, 20);
  const box = await screen.findByRole("dialog", { name: "Исправить распознанное" });
  const all = await within(box).findByRole("checkbox", { name: /Заменить во всей встрече/ });
  expect(all).not.toBeChecked();
  await vi.waitFor(() => expect(all).toBeEnabled());
  await userEvent.click(all);
  const list = within(box).getByRole("list", { name: "Совпадения во встрече" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  const input = within(box).getByRole("textbox", { name: "Как правильно" });
  await userEvent.clear(input);
  await userEvent.type(input, "Kubernetes");
  await userEvent.click(within(box).getByRole("checkbox", { name: /Добавить в термины/ }));
  await userEvent.type(input, "{Enter}");
  expect(api.applyTextFix).toHaveBeenCalledWith(ep, "r1", expect.objectContaining({
    scope: "all", replace: "Kubernetes", add_hotword: false }));
  expect(await screen.findByText(/\(2\)\. Итоги не пересчитываются автоматически/)).toBeInTheDocument();
});

test("Ctrl+E при выделении открывает окно сразу; без выделения — ничего", async () => {
  render(<RecordingCard id="r1" endpoint={ep} />);
  const p = await turnText(/Кубер нетис готов/);
  fireEvent.keyDown(document.body, { key: "e", code: "KeyE", ctrlKey: true });
  expect(screen.queryByRole("dialog", { name: "Исправить распознанное" })).toBeNull();
  select(p, 0, 5);
  fireEvent.keyDown(document.body, { key: "e", code: "KeyE", ctrlKey: true });
  await screen.findByRole("dialog", { name: "Исправить распознанное" });
  expect(api.previewTextFix).toHaveBeenCalledWith(ep, "r1", { find: "Кубер", segment: 2, offset: 0 });
});

test("«Исправить…» над лентой: с выделением — то же окно, что Ctrl+E; без выделения — подсказка", async () => {
  render(<RecordingCard id="r1" endpoint={ep} />);
  const p = await turnText(/Поднимем кубер/);
  const bar = screen.getByRole("search", { name: "Поиск по расшифровке" });
  const fix = within(bar).getByRole("button", { name: "Исправить распознанное" });
  window.getSelection()!.removeAllRanges();
  await userEvent.click(fix);
  const hint = screen.getByRole("dialog", { name: "Как исправить распознанное" });
  expect(hint).toHaveTextContent("Выделите в реплике");
  expect(screen.queryByRole("dialog", { name: "Исправить распознанное" })).toBeNull();
  await userEvent.click(fix); // повторное нажатие закрывает подсказку
  expect(screen.queryByRole("dialog", { name: "Как исправить распознанное" })).toBeNull();
  select(p, 9, 14); // «кубер»
  await userEvent.click(fix);
  const box = await screen.findByRole("dialog", { name: "Исправить распознанное" });
  expect(within(box).getByText("кубер")).toHaveClass("tfix__find");
});

test("подсказка «Исправить…» без выделения: раскрытие, фокус в подсказку, Esc и уход фокуса закрывают", async () => {
  render(<RecordingCard id="r1" endpoint={ep} />);
  await turnText(/Поднимем кубер/);
  const bar = screen.getByRole("search", { name: "Поиск по расшифровке" });
  const fix = within(bar).getByRole("button", { name: "Исправить распознанное" });
  window.getSelection()!.removeAllRanges();
  expect(fix).toHaveAttribute("aria-expanded", "false");
  expect(fix).not.toHaveAttribute("aria-controls");

  // Открыли — кнопка раскрыта и указывает на подсказку, фокус — в подсказке.
  fix.focus();
  await userEvent.keyboard("{Enter}");
  const hint = screen.getByRole("dialog", { name: "Как исправить распознанное" });
  expect(fix).toHaveAttribute("aria-expanded", "true");
  expect(fix).toHaveAttribute("aria-controls", hint.id);
  expect(hint.id).not.toBe("");
  expect(hint).toHaveFocus();

  // Esc — закрыть и вернуть фокус на кнопку.
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Как исправить распознанное" })).toBeNull();
  expect(fix).toHaveAttribute("aria-expanded", "false");
  expect(fix).toHaveFocus();

  // Уход фокуса из подсказки (Tab дальше) — тоже закрывает.
  await userEvent.click(fix);
  const again = screen.getByRole("dialog", { name: "Как исправить распознанное" });
  expect(again).toHaveFocus();
  const search = within(bar).getByRole("searchbox", { name: "Найти в расшифровке" });
  fireEvent.focusOut(again, { relatedTarget: search });
  expect(screen.queryByRole("dialog", { name: "Как исправить распознанное" })).toBeNull();

  // Повторное нажатие на кнопку закрывает подсказку и оставляет фокус на кнопке.
  await userEvent.click(fix);
  expect(screen.getByRole("dialog", { name: "Как исправить распознанное" })).toHaveFocus();
  await userEvent.click(fix);
  expect(screen.queryByRole("dialog", { name: "Как исправить распознанное" })).toBeNull();
  expect(fix).toHaveFocus();
});

test("правый щелчок: выделение — «Исправить…», без него — «Исправить слово» в меню реплики", async () => {
  render(<RecordingCard id="r1" endpoint={ep} />);
  const p = await turnText(/Кубер нетис готов/);
  select(p, 6, 11);
  fireEvent.contextMenu(p, { clientX: 10, clientY: 10 });
  await screen.findByRole("dialog", { name: "Исправить распознанное" });
  expect(api.previewTextFix).toHaveBeenLastCalledWith(ep, "r1", { find: "нетис", segment: 2, offset: 6 });
  await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
  window.getSelection()!.removeAllRanges();

  const text = p.firstChild!;
  (document as unknown as { caretRangeFromPoint: unknown }).caretRangeFromPoint = () => {
    const r = document.createRange();
    r.setStart(text, 2);
    return r;
  };
  fireEvent.contextMenu(p, { clientX: 10, clientY: 10 });
  const menu = await screen.findByRole("dialog", { name: "Разделить реплику здесь" });
  const fixWord = within(menu).getByRole("button", { name: "Исправить слово «Кубер»…" });
  // Подсказка — облачко Aurora (описание кнопки), не системный title.
  expect(fixWord).not.toHaveAttribute("title");
  expect(fixWord).toHaveAccessibleDescription("Исправить распознанное (выделите слова и нажмите Ctrl+E)");
  await userEvent.click(fixWord);
  await screen.findByRole("dialog", { name: "Исправить распознанное" });
  expect(api.previewTextFix).toHaveBeenLastCalledWith(ep, "r1", { find: "Кубер", segment: 2, offset: 0 });
  delete (document as unknown as { caretRangeFromPoint?: unknown }).caretRangeFromPoint;
});

test("выделение через границу двух фраз — подсказка вместо исправления", async () => {
  render(<RecordingCard id="r1" endpoint={ep} />);
  const p = await turnText(/Поднимем кубер нетис/);
  select(p, 24, 38); // «стенде. Проверим»
  fireEvent.keyDown(document.body, { key: "e", code: "KeyE", ctrlKey: true });
  const box = await screen.findByRole("dialog", { name: "Исправить распознанное" });
  expect(within(box).getByText(/захватывает две фразы/)).toBeInTheDocument();
  expect(within(box).getByRole("button", { name: "Применить" })).toBeDisabled();
});

test("«Исправлять так же в будущих встречах» — правило; «Отменить» убирает его из настроек", async () => {
  vi.mocked(api.applyTextFix).mockResolvedValue({ ...result, hotword: undefined, rule: {
    from: "кубер нетис", to: "Kubernetes", replaced: { from: "Кубер нетис", to: "K8s" } } });
  vi.mocked(api.getSettings).mockResolvedValue({ recording: { speaker_name: "Вы" }, asr: { replacements: [
    { from: "дев опс", to: "DevOps" }, { from: "кубер нетис", to: "Kubernetes" }] } });
  vi.mocked(api.patchSettings).mockResolvedValue({ settings: {}, restart_required: [] });
  render(<RecordingCard id="r1" endpoint={ep} />);
  const p = await turnText(/Поднимем кубер нетис/);
  select(p, 9, 20);
  fireEvent.keyDown(document.body, { key: "e", code: "KeyE", ctrlKey: true });
  const box = await screen.findByRole("dialog", { name: "Исправить распознанное" });
  const future = within(box).getByRole("checkbox", { name: /Исправлять так же в будущих встречах/ });
  expect(future).not.toBeChecked();
  expect(future).toBeDisabled(); // исправление ещё не вписано
  const input = within(box).getByRole("textbox", { name: "Как правильно" });
  await userEvent.clear(input);
  await userEvent.type(input, "Kubernetes");
  await userEvent.click(future);
  await userEvent.click(within(box).getByRole("button", { name: "Применить" }));
  expect(api.applyTextFix).toHaveBeenCalledWith(ep, "r1", expect.objectContaining({ add_rule: true }));
  const row = (await screen.findByText("Исправлять в будущих встречах: кубер нетис → Kubernetes")).closest(".tsel") as HTMLElement;
  await userEvent.click(within(row).getByRole("button", { name: "Отменить" }));
  // Правило убрано, а вытесненное им (то же «как распознаётся») — возвращено.
  expect(api.patchSettings).toHaveBeenCalledWith(ep, { asr: { replacements: [
    { from: "дев опс", to: "DevOps" }, { from: "Кубер нетис", to: "K8s" }] } });
  expect(await screen.findByText("Правило возвращено: Кубер нетис → K8s")).toBeInTheDocument();
});

test("«Отменить» у исправления пропадает, когда последним шагом стала другая правка", async () => {
  vi.mocked(api.applyTextFix).mockResolvedValue({ ...result, hotword: undefined });
  const { rerender } = render(<RecordingCard id="r1" endpoint={ep} />);
  const p = await turnText(/Поднимем кубер нетис/);
  select(p, 9, 20);
  fireEvent.keyDown(document.body, { key: "e", code: "KeyE", ctrlKey: true });
  const box = await screen.findByRole("dialog", { name: "Исправить распознанное" });
  const input = within(box).getByRole("textbox", { name: "Как правильно" });
  await userEvent.clear(input);
  await userEvent.type(input, "Kubernetes{Enter}");
  const row = (await screen.findByText(/Исправлено: кубер нетис/)).closest(".tsel") as HTMLElement;
  await within(row).findByRole("button", { name: "Отменить" });
  // Между делом — другая правка (панель «Спикеры», CLI): запись перечитана, последний шаг — не наш.
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, transcript, edit_head: "other" });
  rerender(<RecordingCard id="r1" endpoint={ep} refreshKey={1} />);
  await vi.waitFor(() => expect(within(row).queryByRole("button", { name: "Отменить" })).toBeNull());
  expect(within(row).getByText(/Исправлено: кубер нетис/)).toBeInTheDocument();
});

test("термин — только новые слова; без значимых новых слов флажок выключен", async () => {
  render(<RecordingCard id="r1" endpoint={ep} />);
  const p = await turnText(/Кубер нетис готов/);
  select(p, 12, 17);
  fireEvent.keyDown(document.body, { key: "e", code: "KeyE", ctrlKey: true });
  const box = await screen.findByRole("dialog", { name: "Исправить распознанное" });
  const term = within(box).getByRole("checkbox", { name: /Добавить в термины распознавания/ });
  const input = within(box).getByRole("textbox", { name: "Как правильно" });
  await userEvent.clear(input);
  await userEvent.type(input, "не готов");
  expect(term).not.toBeChecked(); // «не» — служебное слово
  await userEvent.clear(input);
  await userEvent.type(input, "готов подпись");
  expect(term).toBeChecked();
  expect(within(box).getByText("Будет добавлено: подпись")).toBeInTheDocument();
});
