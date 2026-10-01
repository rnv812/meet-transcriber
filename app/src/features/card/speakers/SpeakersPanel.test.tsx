import { createRef } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../../lib/api";
import type { SpeakerRow, SpeakersView } from "../../../lib/types";
import { SpeakersPanel } from "./SpeakersPanel";

vi.mock("../../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../../lib/api")>()),
  getSpeakers: vi.fn(),
  applySpeakers: vi.fn(),
}));

const ep = { base: "/api", token: null };
const row = (label: string, extra: Partial<SpeakerRow> = {}): SpeakerRow => ({
  label, name: /^Спикер/.test(label) ? null : label, seconds: 60, share: 0.25, turns: 4,
  samples: [], has_voice: true, suggestions: [], ...extra,
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
        playable {...handlers} {...props} {...p} />
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
  vi.mocked(api.applySpeakers).mockResolvedValue(view({ pos: 1 }));
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
  vi.mocked(api.applySpeakers).mockResolvedValue(view({ voices_error: "у записи нет голосовых отпечатков" }));
  setup();
  await screen.findByRole("region", { name: /^Спикер 2/ });
  await userEvent.click(within(rowOf("Спикер 2")).getByRole("button", { name: /Это Анна Смирнова/ }));
  await userEvent.click(screen.getByRole("button", { name: "Применить" }));
  expect(await screen.findByText(/Голос не сохранён: у записи нет голосовых отпечатков/)).toBeInTheDocument();
});

test("фокус на строке участника, Esc закрывает панель", async () => {
  const { onClose, rerender } = setup();
  await screen.findByRole("region", { name: /^Спикер 3/ });
  rerender({ focus: { label: "Спикер 3", n: 1 } });
  await waitFor(() => expect(rowOf("Спикер 3")).toHaveFocus());
  fireEvent.keyDown(rowOf("Спикер 3"), { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});
