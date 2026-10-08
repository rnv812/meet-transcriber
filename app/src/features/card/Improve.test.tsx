import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import {
  ImproveDialog, ImproveStatus, chosenExtra, chosenGroups, cleanTarget, improveJobOf, targetProblem,
} from "./improve";
import * as api from "../../lib/api";
import type { ImproveGroup, ImproveState, Job, Recording, Transcript } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(async () => ({})),
  getAssistant: vi.fn(),
  getSummary: vi.fn(),
  getQa: vi.fn(async () => ({ items: [] })),
  getAnalysis: vi.fn(async () => ({ state: "none" })),
  getImprove: vi.fn(),
  runImprove: vi.fn(),
  applyImprove: vi.fn(),
  dismissImproveHint: vi.fn(async () => ({ ok: true })),
  undoSpeakers: vi.fn(),
  getRediarized: vi.fn(async () => { throw new Error("нового разделения нет"); }),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => true,
  saveText: vi.fn(async () => "x"),
  openFolder: vi.fn(async () => {}),
  agentKillRecording: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const transcript: Transcript = {
  version: 1, title: null,
  segments: [
    { start: 0, end: 4, speaker: "Ольга", text: "Апи сервиса отвечает медленно", uncertain: false },
    { start: 4, end: 9, speaker: "Пётр", text: "Смотрим кафка и обзор бити", uncertain: false },
  ],
};
const base: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-10-02T10:00:00", duration_s: 1800,
  tracks: { sys: "s.wav" }, has_transcript: true, has_voices: false, title: "Разбор сервиса", source: "record",
  title_source: "user",
};
const sample = (segment: number, start: number, before: string, match: string, after: string) => ({
  segment, offset: before.length, speaker: "Ольга", start, end: start + 0.5, before, match, after,
});
const group = (id: string, find: string, replace: string, kind: "term" | "fix", count: number): ImproveGroup => ({
  id, find, replace, kind, count, confidence: 0.9,
  samples: Array.from({ length: Math.min(count, 7) }, (_, k) => sample(k, 10 * k + 1, "до ", find, " после")),
});
const GROUPS = [
  group("g1", "апи", "API", "term", 12),
  group("g2", "обзор бити", "observability", "term", 3),
  group("g3", "кафка", "Kafka", "term", 5),
  group("g4", "в торник", "во вторник", "fix", 1),
  group("g5", "приду", "пришлю", "fix", 2),
];
const ready: ImproveState = {
  state: "ready",
  proposal: { version: 1, model: "fake", created_at: 1, fingerprint: "x", segments: 2, groups: GROUPS },
};
const job = (state: Job["state"]): Job => ({
  id: "i1", kind: "improve", folder: "C:\\rec\\r1", state, stage: null, label: null, done: null, total: null,
  note: null, result: null, error: null,
});

function dialog(state: ImproveState = ready, extra: Partial<Parameters<typeof ImproveDialog>[0]> = {}) {
  const props = {
    state, busy: false, error: null, playable: true, onPlay: vi.fn(), onApply: vi.fn(), onRerun: vi.fn(),
    onClose: vi.fn(), ...extra,
  };
  render(<ImproveDialog {...props} />);
  return props;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getImprove).mockResolvedValue({ state: "none", hint: false });
  vi.mocked(api.getRecording).mockResolvedValue({ ...base, transcript });
  HTMLMediaElement.prototype.load = vi.fn();
});

// --- окно ---------------------------------------------------------------------------

test("окно: группы терминов «апи → API · 12», отмечены; исправления — только в своём режиме", async () => {
  const props = dialog();
  const list = screen.getByRole("list", { name: "Предложенные замены" });
  const boxes = within(list).getAllByRole("checkbox");
  expect(boxes).toHaveLength(3);
  expect(boxes.every((b) => (b as HTMLInputElement).checked)).toBe(true);
  expect(within(list).getByText("апи").closest(".improve__row")).toHaveTextContent("апи → API· 12");
  expect(screen.queryByText("Прочие исправления")).toBeNull();
  expect(screen.getByText("Будет заменено: 20 мест")).toBeInTheDocument();
  // Правила и термины — по умолчанию выключены.
  expect(screen.getByRole("checkbox", { name: /Запомнить как правила/ })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: /Добавить в термины/ })).not.toBeChecked();

  await userEvent.click(screen.getByRole("radio", { name: "Термины и явные ошибки распознавания" }));
  const fixes = screen.getByText("Прочие исправления").closest("label")!;
  // Счётчик — сколько будет заменено: пока ничего не отмечено, ноль.
  expect(fixes).toHaveTextContent("Прочие исправления· 0");
  expect(within(fixes).getByRole("checkbox")).not.toBeChecked();
  await userEvent.click(within(fixes).getByRole("checkbox"));
  expect(fixes).toHaveTextContent("Прочие исправления· 3");
  expect(screen.getByText("Будет заменено: 23 места")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g3", "g4", "g5"], {}, false, false);
});

test("окно: снятый флажок, правила и термины уходят в «Применить выбранное»", async () => {
  const props = dialog();
  await userEvent.click(within(screen.getByText("кафка").closest("label")!).getByRole("checkbox"));
  expect(screen.getByText("Будет заменено: 15 мест")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("checkbox", { name: /Запомнить как правила/ }));
  await userEvent.click(screen.getByRole("checkbox", { name: /Добавить в термины/ }));
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2"], {}, true, true);
});

test("окно: ничего не выбрано — применить нельзя; «Отмена» закрывает", async () => {
  const props = dialog({ ...ready, proposal: { ...ready.proposal!, groups: [GROUPS[0]!] } });
  await userEvent.click(within(screen.getByText("апи").closest("label")!).getByRole("checkbox"));
  expect(screen.getByRole("button", { name: "Применить выбранное" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(props.onClose).toHaveBeenCalled();
});

test("окно: места группы — до пяти, с окружением и ▶", async () => {
  const props = dialog();
  await userEvent.click(screen.getByRole("button", { name: "Показать места: апи" }));
  const places = screen.getByRole("list", { name: "Места: апи" });
  const items = within(places).getAllByRole("listitem");
  expect(items).toHaveLength(6); // пять мест и «и ещё 7»
  expect(items[0]).toHaveTextContent("до апи после");
  expect(items[5]).toHaveTextContent("и ещё 7");
  await userEvent.click(within(items[1]!).getByRole("button", { name: /^(Слушать|Прослушать) с / }));
  expect(props.onPlay).toHaveBeenCalledWith(10.7, 12);
  await userEvent.click(screen.getByRole("button", { name: "Скрыть места: апи" }));
  expect(screen.queryByRole("list", { name: "Места: апи" })).toBeNull();
});

test("окно: идёт проверка, пустой результат, ошибка", async () => {
  dialog({ state: "running" });
  expect(screen.getByRole("status")).toHaveTextContent("ИИ проверяет расшифровку…");
  expect(screen.queryByRole("button", { name: "Применить выбранное" })).toBeNull();
});

test("окно: ИИ ничего не нашёл — «Проверить заново»", async () => {
  const props = dialog({ ...ready, proposal: { ...ready.proposal!, groups: [] } });
  expect(screen.getByText("ИИ не нашёл неверно распознанных терминов.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Проверить заново" }));
  expect(props.onRerun).toHaveBeenCalled();
});

// --- строка в карточке ------------------------------------------------------------------

test("ImproveStatus: очередь, готово, ошибка, подсказка после GigaAM", async () => {
  const onOpen = vi.fn();
  const onDismiss = vi.fn();
  const { rerender } = render(
    <ImproveStatus state={{ state: "queued" }} busy={false} onOpen={onOpen} onRetry={onOpen} onDismiss={onDismiss} />);
  expect(screen.getByRole("status")).toHaveTextContent("Улучшение в очереди…");
  rerender(<ImproveStatus state={ready} busy={false} onOpen={onOpen} onRetry={onOpen} onDismiss={onDismiss} />);
  expect(screen.getByRole("status")).toHaveTextContent("ИИ предлагает исправить 3 термина");
  // Предложение ИИ — выноска на сиянии, а не тихая строка.
  expect(screen.getByRole("status")).toHaveClass("callout", "aurora-wash");
  await userEvent.click(screen.getByRole("button", { name: "Просмотреть" }));
  rerender(<ImproveStatus state={{ state: "failed", error: "таймаут" }} busy={false} onOpen={onOpen} onRetry={onOpen}
    onDismiss={onDismiss} />);
  expect(screen.getByText("Улучшение расшифровки не удалось")).toHaveAttribute("title", "таймаут");
  rerender(<ImproveStatus state={{ state: "none", hint: true }} busy={false} onOpen={onOpen} onRetry={onOpen}
    onDismiss={onDismiss} />);
  expect(screen.getByRole("status")).toHaveTextContent("Похоже, в тексте есть термины латиницей — улучшить?");
  expect(screen.getByRole("status")).toHaveClass("callout", "aurora-wash");
  await userEvent.click(screen.getByRole("button", { name: "Не нужно" }));
  expect(onDismiss).toHaveBeenCalled();
  rerender(<ImproveStatus state={{ state: "none", hint: false }} busy={false} onOpen={onOpen} onDismiss={onDismiss} />);
  expect(screen.queryByRole("status")).toBeNull();
});

test("выбор групп и задача улучшения записи", () => {
  expect(chosenGroups(GROUPS, "terms", new Set(["g2"]), new Set(["g4"])).map((g) => g.id)).toEqual(["g1", "g3"]);
  expect(chosenGroups(GROUPS, "all", new Set(), new Set()).map((g) => g.id)).toEqual(["g1", "g2", "g3"]);
  expect(chosenGroups(GROUPS, "all", new Set(), new Set(["g5"])).map((g) => g.id)).toEqual(["g1", "g2", "g3", "g5"]);
  const more = [{ ...GROUPS[2]!, more: [GROUPS[2]!.samples[0]!, GROUPS[2]!.samples[1]!] }];
  expect(chosenExtra(more, new Set(["g3:1"]))).toEqual({ g3: [1] });
  expect(chosenExtra(more, new Set())).toEqual({});
  expect(improveJobOf("C:/rec/r1", [job("running")])?.id).toBe("i1");
  expect(improveJobOf("C:/rec/r1", [job("done")])).toBeNull();
});

// --- карточка: запуск, применение, отмена ---------------------------------------------------

test("карточка: подсказка после GigaAM запускает улучшение", async () => {
  vi.mocked(api.getImprove).mockResolvedValue({ state: "none", hint: true });
  vi.mocked(api.runImprove).mockResolvedValue(job("queued"));
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Похоже, в тексте есть термины латиницей — улучшить?");
  vi.mocked(api.getImprove).mockResolvedValue({ state: "queued", job: job("queued"), hint: false });
  await userEvent.click(screen.getByRole("button", { name: "Улучшить" }));
  expect(api.runImprove).toHaveBeenCalledWith(ep, "r1");
  const box = await screen.findByRole("dialog", { name: "Улучшить расшифровку" });
  await waitFor(() => expect(within(box).getByRole("status")).toHaveTextContent("Улучшение в очереди…"));
});

test("карточка: «Ещё действия» и кнопка в строке поиска; без модели — недоступно", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: null, setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Апи сервиса отвечает медленно");
  await waitFor(() => expect(screen.getByRole("button", { name: "Улучшить расшифровку" })).toBeDisabled());
  await userEvent.click(screen.getByRole("button", { name: "Ещё действия" }));
  const menu = screen.getByRole("menu", { name: "Ещё действия с записью" });
  const item = within(menu).getByRole("menuitem", { name: "Улучшить расшифровку" });
  expect(item).toBeDisabled();
  expect(item).toHaveAttribute("title", "Подключите Claude Code, Codex или OpenCode в настройках");
});

test("карточка: готовое предложение — применить выбранное, итог и «Отменить»", async () => {
  vi.mocked(api.getImprove).mockResolvedValue(ready);
  vi.mocked(api.applyImprove).mockResolvedValue({
    rows: [], history: [], pos: 1, trimmed: false, voices_error: null, voice_threshold_default: 0.75,
    step: { id: "s1", at: "2026-10-02T11:00:00", ops: [], enrolled: [], created_people: [] },
    changed: 20, groups: [
      { from: "апи", to: "API", kind: "term", count: 12 }, { from: "обзор бити", to: "observability", kind: "term", count: 3 },
      { from: "кафка", to: "Kafka", kind: "term", count: 5 }],
  } as unknown as Awaited<ReturnType<typeof api.applyImprove>>);
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("ИИ предлагает исправить 3 термина");
  vi.mocked(api.getRecording).mockResolvedValue({ ...base, transcript, edit_head: "s1" });
  vi.mocked(api.getImprove).mockResolvedValue({ state: "none", hint: false });
  await userEvent.click(screen.getByRole("button", { name: "Улучшить расшифровку" }));
  const box = await screen.findByRole("dialog", { name: "Улучшить расшифровку" });
  expect(api.runImprove).not.toHaveBeenCalled(); // предложение уже есть
  await userEvent.click(within(box).getByRole("button", { name: "Применить выбранное" }));
  expect(api.applyImprove).toHaveBeenCalledWith(ep, "r1", {
    groups: ["g1", "g2", "g3"], extra: {}, created_at: 1, add_rules: false, add_terms: false,
  });
  await screen.findByText(/Улучшено ИИ: 20 замен \(3 термина\)/);
  expect(screen.queryByRole("dialog", { name: "Улучшить расшифровку" })).toBeNull();
  vi.mocked(api.undoSpeakers).mockResolvedValue({} as Awaited<ReturnType<typeof api.undoSpeakers>>);
  await userEvent.click(await screen.findByRole("button", { name: "Отменить" }));
  expect(api.undoSpeakers).toHaveBeenCalledWith(ep, "r1", "s1");
  await screen.findByText("Улучшение отменено");
});

// --- исправления раунда 1 -----------------------------------------------------------------

test("окно: другие места термина — на проверку, по одному флажку, не отмечены; счётчик — что заменится", async () => {
  const kafka: ImproveGroup = {
    ...group("g1", "кафка", "Kafka", "term", 1),
    more: [sample(7, 70, "Франц ", "Кафка", " писал романы"), sample(8, 80, "в ", "кафка", " лежат события")],
  };
  const props = dialog({ ...ready, proposal: { ...ready.proposal!, groups: [kafka] } });
  const label = screen.getByText("кафка").closest(".improve__row")!;
  expect(label).toHaveTextContent("· 1");
  // Свёрнутая строка сразу говорит о непроверенных местах.
  expect(label).toHaveTextContent("· ещё 2 места");
  await userEvent.click(screen.getByRole("button", { name: "Показать места: кафка" }));
  expect(screen.getByText("Ещё 2 места — проверьте: ИИ их не отмечал")).toBeInTheDocument();
  const extra = within(screen.getByRole("list", { name: "Ещё места: кафка" })).getAllByRole("checkbox");
  expect(extra.every((b) => !(b as HTMLInputElement).checked)).toBe(true);
  expect(screen.getByText(/Франц/).closest("li")).toHaveTextContent("Франц Кафка писал романы");
  await userEvent.click(extra[1]!);
  expect(label).toHaveTextContent("· 2");
  expect(label).toHaveTextContent("· ещё 1 место");
  expect(screen.getByText("Будет заменено: 2 места")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1"], { g1: [1] }, false, false);
});

test("окно: каждое исправление обычного слова — своим флажком", async () => {
  const props = dialog();
  await userEvent.click(screen.getByRole("radio", { name: "Термины и явные ошибки распознавания" }));
  await userEvent.click(screen.getByRole("button", { name: "Показать места: Прочие исправления" }));
  await userEvent.click(within(screen.getByText("приду", { selector: ".improve__from" }).closest("label")!).getByRole("checkbox"));
  const all = within(screen.getByText("Прочие исправления").closest("label")!).getByRole("checkbox") as HTMLInputElement;
  expect(all.checked).toBe(false);
  expect(all.indeterminate).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g3", "g5"], {}, false, false);
});

test("окно: ничего не идёт и списка нет — не изображает проверку", async () => {
  const props = dialog({ state: "none" });
  expect(screen.queryByText("ИИ проверяет расшифровку…")).toBeNull();
  expect(screen.getByText(/Списка замен нет/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Проверить заново" }));
  expect(props.onRerun).toHaveBeenCalled();
});

test("окно: повтор не удался — прежний список доступен вместе с ошибкой", async () => {
  const props = dialog({ ...ready, state: "failed", error: "таймаут" });
  expect(screen.getByRole("alert")).toHaveTextContent("Улучшение расшифровки не удалось: таймаут");
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g3"], {}, false, false);
});

test("ImproveStatus: только исправления — тоже строка в карточке", () => {
  render(<ImproveStatus state={{ ...ready, proposal: { ...ready.proposal!, groups: [GROUPS[3]!, GROUPS[4]!] } }}
    busy={false} onOpen={vi.fn()} onRetry={vi.fn()} onDismiss={vi.fn()} />);
  expect(screen.getByRole("status")).toHaveTextContent("ИИ предлагает 2 исправления распознавания");
});

// --- «как правильно», вписанное человеком ------------------------------------------------------

const rowOf = (find: string) => screen.getByText(find, { selector: ".improve__from" }).closest(".improve__row")! as HTMLElement;

test("правка: щелчок по замене — поле; Enter принимает, отметка «изменено» и вписанное уходит в «Применить»", async () => {
  const props = dialog();
  await userEvent.click(within(rowOf("апи")).getByRole("button", { name: "API" }));
  const input = screen.getByRole("textbox", { name: "Как правильно: апи" });
  expect(input).toHaveValue("API");
  expect(input).toHaveFocus();
  await userEvent.clear(input);
  await userEvent.type(input, "  MSSP  {Enter}");
  expect(screen.queryByRole("textbox", { name: "Как правильно: апи" })).toBeNull();
  const row = rowOf("апи");
  expect(row).toHaveTextContent("апи → MSSP");
  expect(within(row).getByText("изменено")).toHaveAttribute("title", "Вписано вручную; ИИ предлагал «API»");
  expect(within(row).getByRole("checkbox")).toHaveAccessibleName("апи → MSSP");
  // Количество мест не меняется: вписанное — во всех местах группы.
  expect(row).toHaveTextContent("· 12");
  expect(screen.getByText("Будет заменено: 20 мест")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g3"], {}, false, false, { g1: "MSSP" });
});

test("правка: ✎ открывает поле, Esc отменяет и не закрывает окно", async () => {
  const props = dialog();
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: кафка" }));
  const input = screen.getByRole("textbox", { name: "Как правильно: кафка" });
  await userEvent.type(input, "Z{Escape}");
  expect(props.onClose).not.toHaveBeenCalled();
  expect(screen.queryByRole("textbox")).toBeNull();
  // Фокус — обратно на замену, а не в никуда.
  expect(within(rowOf("кафка")).getByRole("button", { name: "Kafka" })).toHaveFocus();
  expect(rowOf("кафка")).toHaveTextContent("кафка → Kafka");
  expect(within(rowOf("кафка")).queryByText("изменено")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g3"], {}, false, false);
});

test("правка: уход из поля принимает; «вернуть предложенное» снимает правку", async () => {
  const props = dialog();
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: кафка" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Как правильно: кафка" }), " Streams");
  await userEvent.tab();
  expect(rowOf("кафка")).toHaveTextContent("кафка → Kafka Streams");
  await userEvent.click(within(rowOf("кафка")).getByRole("button", { name: "вернуть предложенное" }));
  expect(rowOf("кафка")).toHaveTextContent("кафка → Kafka");
  expect(within(rowOf("кафка")).queryByText("изменено")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g3"], {}, false, false);
});

test("правка: пусто — ошибка, применить нельзя; Esc возвращает предложенное", async () => {
  const props = dialog();
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: апи" }));
  const input = screen.getByRole("textbox", { name: "Как правильно: апи" });
  await userEvent.clear(input);
  await userEvent.type(input, "   {Enter}");
  expect(screen.getByRole("textbox", { name: "Как правильно: апи" })).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("alert")).toHaveTextContent("Впишите, как правильно");
  expect(screen.getByRole("button", { name: "Применить выбранное" })).toBeDisabled();
  // Уход из поля пустое тоже не принимает: поле остаётся, фокус — обратно в него.
  await userEvent.tab();
  const still = screen.getByRole("textbox", { name: "Как правильно: апи" });
  await waitFor(() => expect(still).toHaveFocus());
  const apply = screen.getByRole("button", { name: "Применить выбранное" });
  expect(apply).toBeDisabled();
  // Почему недоступно — видно рядом с кнопкой и связано с ней.
  expect(screen.getByText("Сначала исправьте, как правильно, или отмените правку (Esc)")).toBeVisible();
  expect(apply).toHaveAccessibleDescription("Сначала исправьте, как правильно, или отмените правку (Esc)");
  await userEvent.type(screen.getByRole("textbox", { name: "Как правильно: апи" }), "{Escape}");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(rowOf("апи")).toHaveTextContent("апи → API");
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g3"], {}, false, false);
});

test("правка: вписали исходное — группа не заменяется; предложенное после пробелов — не правка", async () => {
  const props = dialog();
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: апи" }));
  const input = screen.getByRole("textbox", { name: "Как правильно: апи" });
  await userEvent.clear(input);
  await userEvent.type(input, " апи {Enter}");
  expect(rowOf("апи")).toHaveTextContent("как в тексте — не заменяется");
  expect(rowOf("апи")).toHaveTextContent("· 0");
  expect(screen.getByText("Будет заменено: 8 мест")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: кафка" }));
  const kafka = screen.getByRole("textbox", { name: "Как правильно: кафка" });
  await userEvent.clear(kafka);
  await userEvent.type(kafka, "  Kafka {Enter}");
  expect(within(rowOf("кафка")).queryByText("изменено")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g2", "g3"], {}, false, false);
});

test("правка: только регистр, смесь кириллицы и латиницы, знаки — как вписано", async () => {
  const props = dialog();
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: апи" }));
  const input = screen.getByRole("textbox", { name: "Как правильно: апи" });
  await userEvent.clear(input);
  await userEvent.type(input, "Api{Enter}");
  expect(within(rowOf("апи")).getByText("изменено")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: обзор бити" }));
  const obs = screen.getByRole("textbox", { name: "Как правильно: обзор бити" });
  await userEvent.clear(obs);
  await userEvent.type(obs, "Сбер (C++) $1 API-шлюз");
  // «Применить» сразу из поля: уход из поля принимает правку.
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g3"], {}, false, false,
    { g1: "Api", g2: "Сбер (C++) $1 API-шлюз" });
});

test("правка: исправление обычного слова — тоже; правка снятой группы не уходит", async () => {
  const props = dialog();
  await userEvent.click(screen.getByRole("radio", { name: "Термины и явные ошибки распознавания" }));
  await userEvent.click(screen.getByRole("button", { name: "Показать места: Прочие исправления" }));
  await userEvent.click(within(rowOf("в торник")).getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: в торник" }));
  const input = screen.getByRole("textbox", { name: "Как правильно: в торник" });
  await userEvent.clear(input);
  await userEvent.type(input, "в четверг{Enter}");
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: кафка" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Как правильно: кафка" }), "2{Enter}");
  await userEvent.click(within(rowOf("кафка")).getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g4"], {}, false, false, { g4: "в четверг" });
});

test("правка: новое предложение — правки сброшены", async () => {
  const props = { state: ready, busy: false, error: null, playable: true, onPlay: vi.fn(), onApply: vi.fn(), onClose: vi.fn() };
  const { rerender } = render(<ImproveDialog {...props} />);
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: апи" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Как правильно: апи" }), "S{Enter}");
  expect(rowOf("апи")).toHaveTextContent("апи → APIS");
  rerender(<ImproveDialog {...props} state={{ ...ready, proposal: { ...ready.proposal!, created_at: 2 } }} />);
  expect(rowOf("апи")).toHaveTextContent("апи → API");
  expect(within(rowOf("апи")).queryByText("изменено")).toBeNull();
});

test("карточка: вписанное уходит в запрос `targets`", async () => {
  vi.mocked(api.getImprove).mockResolvedValue(ready);
  vi.mocked(api.applyImprove).mockResolvedValue({
    rows: [], history: [], pos: 1, trimmed: false, voices_error: null, voice_threshold_default: 0.75,
    step: { id: "s1", at: "2026-10-02T11:00:00", ops: [], enrolled: [], created_people: [] },
    changed: 12, groups: [{ from: "апи", to: "MSSP", kind: "term", count: 12, edited: true }],
  } as unknown as Awaited<ReturnType<typeof api.applyImprove>>);
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("ИИ предлагает исправить 3 термина");
  await userEvent.click(screen.getByRole("button", { name: "Улучшить расшифровку" }));
  const box = await screen.findByRole("dialog", { name: "Улучшить расшифровку" });
  await userEvent.click(within(box).getByRole("button", { name: "Изменить замену: апи" }));
  const input = within(box).getByRole("textbox", { name: "Как правильно: апи" });
  await userEvent.clear(input);
  await userEvent.type(input, "MSSP{Enter}");
  await userEvent.click(within(box).getByRole("checkbox", { name: "кафка → Kafka" }));
  await userEvent.click(within(box).getByRole("checkbox", { name: /Запомнить как правила/ }));
  await userEvent.click(within(box).getByRole("button", { name: "Применить выбранное" }));
  expect(api.applyImprove).toHaveBeenCalledWith(ep, "r1", {
    groups: ["g1", "g2"], extra: {}, created_at: 1, add_rules: true, add_terms: false, targets: { g1: "MSSP" },
  });
});

test("cleanTarget: пробелы по краям и внутри, NFC", () => {
  expect(cleanTarget("  MS\t SP  ")).toBe("MS SP");
  expect(cleanTarget("Ё")).toBe("Ё");
  expect(cleanTarget("   ")).toBe("");
});

test("правка: длиннее 200 символов — ошибка, применить нельзя; укоротили — принимается", async () => {
  const props = dialog();
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: апи" }));
  const input = screen.getByRole("textbox", { name: "Как правильно: апи" });
  await userEvent.clear(input);
  await userEvent.paste("M".repeat(201));
  await userEvent.keyboard("{Enter}");
  expect(screen.getByRole("alert")).toHaveTextContent("Слишком длинно: не больше 200 символов");
  expect(input).toHaveAccessibleDescription("Слишком длинно: не больше 200 символов");
  expect(screen.getByRole("button", { name: "Применить выбранное" })).toBeDisabled();
  await userEvent.keyboard("{Backspace}{Enter}");
  expect(screen.queryByRole("alert")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Применить выбранное" }));
  expect(props.onApply).toHaveBeenCalledWith(["g1", "g2", "g3"], {}, false, false, { g1: "M".repeat(200) });
});

test("правка: одни невидимые знаки — как пусто; управляющие внутри — ошибка", async () => {
  dialog();
  await userEvent.click(screen.getByRole("button", { name: "Изменить замену: апи" }));
  const input = screen.getByRole("textbox", { name: "Как правильно: апи" });
  await userEvent.clear(input);
  await userEvent.paste("​‍");
  await userEvent.keyboard("{Enter}");
  expect(screen.getByRole("alert")).toHaveTextContent("Впишите, как правильно");
  await userEvent.paste("MS‮SP");
  await userEvent.keyboard("{Enter}");
  expect(screen.getByRole("alert")).toHaveTextContent("Невидимые или управляющие знаки");
  expect(screen.getByRole("button", { name: "Применить выбранное" })).toBeDisabled();
});

test("targetProblem: пусто, невидимое, управляющее, длина", () => {
  for (const v of ["", "​", "﻿", "​‮"]) expect(targetProblem(cleanTarget(v))).toMatch(/^Впишите/);
  for (const v of ["MS​SP", "MS\x00SP", "MS\x1b[31mSP", "‮MSSP", "MS\ud800SP"]) {
    expect(targetProblem(cleanTarget(v))).toMatch(/^Невидимые/);
  }
  // Переводы строк — просто пробелы.
  expect(cleanTarget("MS\r\nSP\n")).toBe("MS SP");
  expect(targetProblem(cleanTarget("MS\nSP"))).toBeNull();
  expect(targetProblem("M".repeat(201))).toMatch(/^Слишком длинно/);
  expect(targetProblem("Сбер API-шлюз")).toBeNull();
});

test("окно — лист Aurora по центру на затемнении: поверх окна (в body), щелчок по затемнению, ✕ и Esc закрывают", async () => {
  const props = dialog();
  const sheet = screen.getByRole("dialog", { name: "Улучшить расшифровку" });
  expect(sheet).toHaveClass("sheet");
  expect(sheet).toHaveAttribute("aria-modal", "true");
  const layer = sheet.parentElement!;
  expect(layer).toHaveClass("backdrop", "backdrop--modal");
  expect(layer.parentElement).toBe(document.body);
  expect(screen.getByRole("heading", { name: "Улучшить расшифровку" })).toHaveFocus();
  fireEvent.mouseDown(sheet);
  expect(props.onClose).not.toHaveBeenCalled();
  fireEvent.mouseDown(layer);
  expect(props.onClose).toHaveBeenCalledTimes(1);
  await userEvent.click(within(sheet).getByRole("button", { name: "Закрыть" }));
  expect(props.onClose).toHaveBeenCalledTimes(2);
  await userEvent.keyboard("{Escape}");
  expect(props.onClose).toHaveBeenCalledTimes(3);
});
