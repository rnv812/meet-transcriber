import { createRef } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../../lib/api";
import type { SpeakerRow, SpeakersView, SpeakerStep } from "../../../lib/types";
import { SpeakersPanel } from "./SpeakersPanel";

vi.mock("../../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../../lib/api")>()),
  getSpeakers: vi.fn(),
  applySpeakers: vi.fn(),
  undoSpeakers: vi.fn(),
  redoSpeakers: vi.fn(),
  revertSpeakers: vi.fn(),
}));

const ep = { base: "/api", token: null };
const row = (label: string, extra: Partial<SpeakerRow> = {}): SpeakerRow => ({
  label, name: /^Спикер/.test(label) ? null : label, seconds: 60, share: 0.25, turns: 4,
  samples: [], has_voice: true, suggestions: [], ...extra,
});
const step = (id: string, extra: Partial<SpeakerStep> = {}): SpeakerStep => ({
  id, at: "2026-09-30T17:05:00", created_people: [], enrolled: [],
  ops: [{ type: "rename", label: "Спикер 2", from: "Спикер 2", to: "Анна Смирнова" }], ...extra,
});
const view = (extra: Partial<SpeakersView> = {}): SpeakersView => ({
  owner: "Вы", history: [], pos: 0,
  speakers: [
    row("Спикер 1", { share: 0.52, turns: 12, samples: [
      { start: 95, end: 120, text: "Сначала про сроки поставки оборудования." },
      { start: 3, end: 7, text: "Добрый день, начинаем." }] }),
    row("Спикер 2", { share: 0.3, suggestions: [{ name: "Анна Смирнова", score: 0.87 }, { name: "Борис Козлов", score: 0.46 }] }),
    row("Спикер 3", { share: 0.08, turns: 1 }),
    row("Вы", { share: 0.1, has_voice: false }),
  ],
  ...extra,
});
const people = [
  { name: "Анна Смирнова", color: "#4b6bd6", has_avatar: false },
  { name: "Борис Козлов", color: "#c0793a", has_avatar: false },
];

function setup(props: Partial<Parameters<typeof SpeakersPanel>[0]> = {}) {
  const cardRef = createRef<HTMLElement>();
  const handlers = { onClose: vi.fn(), onPlay: vi.fn(), onShowTurns: vi.fn(), onChanged: vi.fn() };
  const ui = (p: Partial<Parameters<typeof SpeakersPanel>[0]>) => (
    <section ref={cardRef} data-testid="card">
      <input aria-label="поле в карточке" />
      <button type="button">кнопка в карточке</button>
      <SpeakersPanel endpoint={ep} recordingId="r1" people={people} open focus={null} version={1}
        playable cardRef={cardRef} {...handlers} {...props} {...p} />
    </section>
  );
  const utils = render(ui({}));
  return { ...handlers, rerender: (p: Partial<Parameters<typeof SpeakersPanel>[0]>) => utils.rerender(ui(p)) };
}
const rowOf = (label: string) => screen.getByRole("region", { name: new RegExp(`^${label}`) });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSpeakers).mockResolvedValue(view());
});

test("строки: доля времени, фразы с ▶ (около 6 секунд), «Показать все реплики»", async () => {
  const { onPlay, onShowTurns } = setup();
  const first = await screen.findByRole("region", { name: /^Спикер 1/ });
  expect(within(first).getByText(/52% времени · 12 реплик/)).toBeInTheDocument();
  await userEvent.click(within(first).getByRole("button", { name: "Прослушать фразу с 01:35" }));
  expect(onPlay).toHaveBeenCalledWith(95, 101);
  await userEvent.click(within(first).getByRole("button", { name: "Прослушать фразу с 00:03" }));
  expect(onPlay).toHaveBeenLastCalledWith(3, 7);
  await userEvent.click(within(first).getByRole("button", { name: "Показать все реплики" }));
  expect(onShowTurns).toHaveBeenCalledWith("Спикер 1");
});

test("без звука ▶ у фраз неактивна", async () => {
  setup({ playable: false });
  const first = await screen.findByRole("region", { name: /^Спикер 1/ });
  expect(within(first).getByRole("button", { name: "Прослушать фразу с 01:35" })).toBeDisabled();
});

test("подсказка из базы: щелчок намечает имя, «Применить» шлёт набор и запоминает голос", async () => {
  vi.mocked(api.applySpeakers).mockResolvedValue(view({ pos: 1, history: [step("s1")] }));
  const { onChanged } = setup();
  await screen.findByRole("region", { name: /^Спикер 2/ });
  await userEvent.click(within(rowOf("Спикер 2")).getByRole("button", { name: "Это Анна Смирнова, сходство 87%" }));
  expect(screen.getByText("Будет изменено: Спикер 2 → Анна Смирнова")).toBeInTheDocument();
  expect(screen.getByText("Итоги не пересчитываются автоматически")).toBeInTheDocument();
  expect(within(rowOf("Спикер 2")).getByRole("checkbox", { name: /Запомнить голос/ })).toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Применить" }));
  expect(api.applySpeakers).toHaveBeenCalledWith(ep, "r1",
    [{ type: "rename", label: "Спикер 2", to: "Анна Смирнова" }], { "Спикер 2": true });
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(screen.queryByText(/Будет изменено/)).toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent("Изменения применены");
});

test("«Назначить…»: поиск по базе, новый человек, «Это я» и «Неизвестный»", async () => {
  setup();
  await screen.findByRole("region", { name: /^Спикер 1/ });
  await userEvent.click(within(rowOf("Спикер 1")).getByRole("button", { name: "Назначить…" }));
  const box = screen.getByRole("combobox", { name: "Кто это: Спикер 1" });
  expect(box).toHaveFocus();
  const list = screen.getByRole("listbox", { name: "Варианты" });
  expect(within(list).getByRole("option", { name: /Это я — Вы/ })).toBeInTheDocument();
  await userEvent.type(box, "бор");
  expect(within(list).getByRole("option", { name: /Борис Козлов/ })).toBeInTheDocument();
  expect(within(list).getByRole("option", { name: /Новый человек «бор»/ })).toBeInTheDocument();
  await userEvent.clear(box);
  await userEvent.type(box, "Глеб Демьянов{Enter}");
  expect(screen.getByText("Будет изменено: Спикер 1 → Глеб Демьянов")).toBeInTheDocument();

  await userEvent.click(within(rowOf("Вы")).getByRole("button", { name: "Назначить…" }));
  await userEvent.click(screen.getByRole("option", { name: /Неизвестный/ }));
  expect(screen.getByText("Будет изменено: Спикер 1 → Глеб Демьянов, Вы → без имени")).toBeInTheDocument();
  // У микрофона голосового отпечатка нет — запоминать нечего.
  expect(within(rowOf("Вы")).queryByRole("checkbox")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Сбросить" }));
  expect(screen.queryByText(/Будет изменено/)).toBeNull();
});

test("«Объединить с…» другого спикера встречи", async () => {
  vi.mocked(api.applySpeakers).mockResolvedValue(view());
  setup();
  await screen.findByRole("region", { name: /^Спикер 3/ });
  await userEvent.click(within(rowOf("Спикер 1")).getByRole("button", { name: "Назначить…" }));
  await userEvent.type(screen.getByRole("combobox"), "Борис Козлов{Enter}");
  await userEvent.click(within(rowOf("Спикер 3")).getByRole("button", { name: "Объединить с…" }));
  const group = screen.getByRole("group", { name: "Объединить «Спикер 3» с другим спикером" });
  expect(within(group).getByRole("button", { name: "Что значит «Объединить»" })).toBeInTheDocument();
  await userEvent.click(within(group).getByRole("button", { name: /Спикер 1/ }));
  expect(screen.getByText(
    "Будет изменено: Спикер 1 → Борис Козлов, Спикер 3 → объединён со спикером «Борис Козлов»")).toBeInTheDocument();
  // Безымянный, ставший Борисом через объединение, — голос по умолчанию запоминается; снимем флажок.
  const remember = within(rowOf("Спикер 3")).getByRole("checkbox", { name: /Запомнить голос/ });
  expect(remember).toBeChecked();
  await userEvent.click(remember);
  await userEvent.click(screen.getByRole("button", { name: "Применить" }));
  expect(api.applySpeakers).toHaveBeenCalledWith(ep, "r1", [
    { type: "rename", label: "Спикер 1", to: "Борис Козлов" },
    { type: "merge", label: "Спикер 3", to: "Спикер 1" },
  ], { "Спикер 1": true, "Спикер 3": false });
});

test("ошибка резидента видна, наметки остаются", async () => {
  vi.mocked(api.applySpeakers).mockRejectedValue(new api.ApiError(409, "Идёт расшифровка — отмените её или дождитесь"));
  setup();
  await screen.findByRole("region", { name: /^Спикер 2/ });
  await userEvent.click(within(rowOf("Спикер 2")).getByRole("button", { name: /Это Анна Смирнова/ }));
  await userEvent.click(screen.getByRole("button", { name: "Применить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Идёт расшифровка");
  expect(screen.getByText(/Будет изменено/)).toBeInTheDocument();
});

test("голос не сохранён — предупреждение, имя применено", async () => {
  vi.mocked(api.applySpeakers).mockResolvedValue(view({ voices_error: "Голос «Анна Смирнова» не сохранён: у записи нет голосовых отпечатков" }));
  setup();
  await screen.findByRole("region", { name: /^Спикер 2/ });
  await userEvent.click(within(rowOf("Спикер 2")).getByRole("button", { name: /Это Анна Смирнова/ }));
  await userEvent.click(screen.getByRole("button", { name: "Применить" }));
  expect(await screen.findByText("Голос «Анна Смирнова» не сохранён: у записи нет голосовых отпечатков"))
    .toBeInTheDocument();
});

test("фокус на строке участника, Esc закрывает панель", async () => {
  const { onClose, rerender } = setup();
  await screen.findByRole("region", { name: /^Спикер 3/ });
  rerender({ focus: { label: "Спикер 3", n: 1 } });
  await waitFor(() => expect(rowOf("Спикер 3")).toHaveFocus());
  fireEvent.keyDown(rowOf("Спикер 3"), { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});

test("«Отменить» и «Повторить»: кнопки и Ctrl+Z / Ctrl+Shift+Z в карточке, но не в поле ввода", async () => {
  vi.mocked(api.getSpeakers).mockResolvedValue(view({ pos: 1, history: [step("s1")] }));
  vi.mocked(api.undoSpeakers).mockResolvedValue(view({ pos: 0, history: [step("s1")] }));
  vi.mocked(api.redoSpeakers).mockResolvedValue(view({ pos: 1, history: [step("s1")] }));
  const { onChanged } = setup();
  const undo = await screen.findByRole("button", { name: /Отменить/ });
  await waitFor(() => expect(undo).toBeEnabled());
  expect(screen.getByRole("button", { name: /Повторить/ })).toBeDisabled();

  fireEvent.keyDown(screen.getByRole("textbox", { name: "поле в карточке" }), { key: "z", code: "KeyZ", ctrlKey: true });
  expect(api.undoSpeakers).not.toHaveBeenCalled();

  fireEvent.keyDown(screen.getByRole("button", { name: "кнопка в карточке" }), { key: "z", code: "KeyZ", ctrlKey: true });
  await waitFor(() => expect(api.undoSpeakers).toHaveBeenCalledWith(ep, "r1"));
  await waitFor(() => expect(screen.getByRole("button", { name: /Повторить/ })).toBeEnabled());
  expect(onChanged).toHaveBeenCalled();
  expect(screen.getByRole("status")).toHaveTextContent("Шаг отменён");

  fireEvent.keyDown(screen.getByRole("button", { name: "кнопка в карточке" }),
    { key: "Z", code: "KeyZ", ctrlKey: true, shiftKey: true });
  await waitFor(() => expect(api.redoSpeakers).toHaveBeenCalledWith(ep, "r1"));
  await waitFor(() => expect(screen.getByRole("button", { name: /Отменить/ })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: /Отменить/ }));
  expect(api.undoSpeakers).toHaveBeenCalledTimes(2);
});

test("закрытая панель Ctrl+Z не перехватывает", async () => {
  vi.mocked(api.getSpeakers).mockResolvedValue(view({ pos: 1, history: [step("s1")] }));
  setup({ open: false });
  await waitFor(() => expect(api.getSpeakers).toHaveBeenCalled());
  fireEvent.keyDown(screen.getByTestId("card"), { key: "z", code: "KeyZ", ctrlKey: true });
  expect(api.undoSpeakers).not.toHaveBeenCalled();
});

test("история изменений: шаги словами, текущий отмечен, возврат к любому состоянию", async () => {
  const history = [
    step("s1", { enrolled: [{ person: "Анна Смирнова", sample_id: "x", label: "SPEAKER_01", created: true }] }),
    step("s2", { at: "2026-09-30T17:20:00", ops: [
      { type: "merge", label: "Спикер 3", from: "Спикер 3", to: "Анна Смирнова", into: "Спикер 2" }] }),
  ];
  vi.mocked(api.getSpeakers).mockResolvedValue(view({ pos: 1, history }));
  vi.mocked(api.revertSpeakers).mockResolvedValueOnce(view({ pos: 2, history }))
    .mockResolvedValueOnce(view({ pos: 0, history }));
  setup();
  await userEvent.click(await screen.findByRole("button", { name: /История изменений \(2\)/ }));
  const list = within(screen.getByRole("region", { name: "История изменений" })).getByRole("list");
  const items = within(list).getAllByRole("listitem");
  expect(items.map((li) => li.textContent)).toEqual([
    "Исходное состояниеВернуть к этому состоянию",
    expect.stringContaining("Спикер 2 → Анна Смирнова · голос запомнен: Анна Смирновасейчас"),
    expect.stringContaining("Спикер 3 объединён со спикером «Анна Смирнова» (отменено)Вернуть к этому состоянию"),
  ]);
  expect(items[1]).toHaveAttribute("aria-current", "step");
  await userEvent.click(within(items[2]!).getByRole("button", { name: "Вернуть к этому состоянию" }));
  expect(api.revertSpeakers).toHaveBeenCalledWith(ep, "r1", "s2");
  await waitFor(() => expect(within(list).getAllByRole("listitem")[2]).toHaveAttribute("aria-current", "step"));
  await userEvent.click(within(within(list).getAllByRole("listitem")[0]!).getByRole("button",
    { name: "Вернуть к этому состоянию" }));
  expect(api.revertSpeakers).toHaveBeenLastCalledWith(ep, "r1", null);
});

test("после отмены наметки исчезнувших строк отбрасываются", async () => {
  vi.mocked(api.getSpeakers).mockResolvedValue(view({ pos: 1, history: [step("s1")] }));
  vi.mocked(api.undoSpeakers).mockResolvedValue(view({
    pos: 0, history: [step("s1")], speakers: [row("Спикер 1"), row("Спикер 4")] }));
  setup();
  await screen.findByRole("region", { name: /^Спикер 2/ });
  await userEvent.click(within(rowOf("Спикер 1")).getByRole("button", { name: "Назначить…" }));
  await userEvent.type(screen.getByRole("combobox"), "Глеб{Enter}");
  await userEvent.click(within(rowOf("Спикер 2")).getByRole("button", { name: /Это Анна Смирнова/ }));
  expect(screen.getByText("Будет изменено: Спикер 1 → Глеб, Спикер 2 → Анна Смирнова")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Отменить/ }));
  await waitFor(() => expect(screen.getByText("Будет изменено: Спикер 1 → Глеб")).toBeInTheDocument());
});

test("история обрезана: сказано, что более ранние изменения отменить нельзя", async () => {
  vi.mocked(api.getSpeakers).mockResolvedValue(view({ pos: 1, history: [step("s1")], trimmed: true }));
  setup();
  await userEvent.click(await screen.findByRole("button", { name: /История изменений \(1\)/ }));
  expect(screen.getByText("Хранятся последние 50 изменений — более ранние отменить нельзя")).toBeInTheDocument();
  expect(screen.getByText("Состояние до этих изменений")).toBeInTheDocument();
});

test("открытие ставит фокус на заголовок панели: Esc сразу закрывает", async () => {
  const { onClose } = setup();
  expect(screen.getByRole("heading", { name: "Спикеры встречи" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(onClose).toHaveBeenCalled();
});
