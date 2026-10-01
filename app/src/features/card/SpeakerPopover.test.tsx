import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SpeakerPopover } from "./SpeakerPopover";
import * as api from "../../lib/api";
import type { Person } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  nameSpeakers: vi.fn(),
  saveTranscript: vi.fn(),
  getRecording: vi.fn(),
}));

const ep = { base: "/api", token: null };
const people: Person[] = [{ name: "Демьян", samples: 1, meetings: 1, seconds: 10, has_avatar: false, color: "#c0793a" }];
const setup = (onDone = vi.fn(), onApplied = vi.fn()) => {
  render(<SpeakerPopover endpoint={ep} recordingId="r1" label="Спикер 2" people={people}
    onApplied={onApplied} onDone={onDone} />);
  return { onDone, onApplied };
};

beforeEach(() => vi.clearAllMocks());

test("ввод с пробелами и регистром: подсказка из базы, Enter называет", async () => {
  vi.mocked(api.nameSpeakers).mockResolvedValue({ ok: true, renamed: 1, enrolled: ["Демьян"], voices_error: null });
  const { onDone, onApplied } = setup();
  const input = screen.getByRole("textbox", { name: "Кто это?" });
  expect(input).toHaveFocus();
  await userEvent.type(input, "  демьян {Enter}");
  expect(api.nameSpeakers).toHaveBeenCalledWith(ep, "r1", { "Спикер 2": "Демьян" });
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(onApplied).toHaveBeenCalled();
});

test("подсказка показывает «уже в базе»", async () => {
  setup();
  await userEvent.type(screen.getByRole("textbox"), "дем");
  expect(screen.getByRole("button", { name: /Демьян — уже в базе/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Новый человек «дем»/ })).toBeInTheDocument();
});

test("пустое поле: «Готово» неактивна", async () => {
  setup();
  expect(screen.getByRole("button", { name: "Готово" })).toBeDisabled();
  await userEvent.type(screen.getByRole("textbox"), "   ");
  expect(screen.getByRole("button", { name: "Готово" })).toBeDisabled();
});

test("voices_error показывается предупреждением", async () => {
  vi.mocked(api.nameSpeakers).mockResolvedValue({ ok: true, renamed: 1, enrolled: [], voices_error: "нет сайдкара" });
  const { onApplied, onDone } = setup();
  await userEvent.type(screen.getByRole("textbox"), "Пётр{Enter}");
  expect(await screen.findByText(/нет сайдкара/)).toBeInTheDocument();
  expect(onApplied).toHaveBeenCalled();
  expect(onDone).not.toHaveBeenCalled();
});

test("ошибка резидента (400) видна", async () => {
  vi.mocked(api.nameSpeakers).mockRejectedValue(new Error("Недопустимое имя"));
  setup();
  await userEvent.type(screen.getByRole("textbox"), "a/b{Enter}");
  expect(await screen.findByRole("alert")).toHaveTextContent("Недопустимое имя");
});

test("флажок снят: saveTranscript с заменой спикера, без nameSpeakers", async () => {
  vi.mocked(api.getRecording).mockResolvedValue({
    id: "r1", transcript: {
      version: 1, title: null,
      segments: [
        { start: 0, end: 1, speaker: "Спикер 2", text: "a", uncertain: true },
        { start: 2, end: 3, speaker: "Демьян", text: "b", uncertain: false },
      ],
    },
  } as never);
  vi.mocked(api.saveTranscript).mockResolvedValue({ ok: true });
  const { onDone } = setup();
  await userEvent.click(screen.getByRole("checkbox", { name: /Запомнить голос/ }));
  await userEvent.type(screen.getByRole("textbox"), "Матвей");
  await userEvent.click(screen.getByRole("button", { name: "Готово" }));
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(api.nameSpeakers).not.toHaveBeenCalled();
  const t = vi.mocked(api.saveTranscript).mock.calls[0]![2];
  expect(t.segments.map((s) => s.speaker)).toEqual(["Матвей", "Демьян"]);
  expect(t.segments[0]).toMatchObject({ text: "a", uncertain: true, start: 0 });
});

test("Enter выбирает точное совпадение, а не первое по префиксу", async () => {
  vi.mocked(api.nameSpeakers).mockResolvedValue({ ok: true, renamed: 1, enrolled: [], voices_error: null });
  const two = [{ ...people[0]!, name: "Демьян Петров" }, people[0]!];
  render(<SpeakerPopover endpoint={ep} recordingId="r1" label="Спикер 2" people={two} onDone={vi.fn()} />);
  await userEvent.type(screen.getByRole("textbox"), "демьян{Enter}");
  expect(api.nameSpeakers).toHaveBeenCalledWith(ep, "r1", { "Спикер 2": "Демьян" });
});

test("двойной Enter отправляет один раз", async () => {
  vi.mocked(api.nameSpeakers).mockImplementation(() => new Promise(() => {}));
  setup();
  await userEvent.type(screen.getByRole("textbox"), "Пётр{Enter}{Enter}");
  expect(api.nameSpeakers).toHaveBeenCalledTimes(1);
});

test("после ошибки фокус возвращается в поле", async () => {
  vi.mocked(api.nameSpeakers).mockRejectedValue(new Error("плохо"));
  setup();
  await userEvent.type(screen.getByRole("textbox"), "x{Enter}");
  await screen.findByRole("alert");
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveFocus());
});

const serg = [{ ...people[0]!, name: "Матвей" }];
const renderWith = (list: Person[]) =>
  render(<SpeakerPopover endpoint={ep} recordingId="r1" label="Спикер 2" people={list} onDone={vi.fn()} />);
const ok = () => vi.mocked(api.nameSpeakers).mockResolvedValue({ ok: true, renamed: 1, enrolled: [], voices_error: null });

test("«Готово» без точного совпадения создаёт введённое имя", async () => {
  ok();
  renderWith(serg);
  await userEvent.type(screen.getByRole("textbox"), "Мат");
  await userEvent.click(screen.getByRole("button", { name: "Готово" }));
  expect(api.nameSpeakers).toHaveBeenCalledWith(ep, "r1", { "Спикер 2": "Мат" });
});

test("Enter без точного совпадения берёт первую подсказку", async () => {
  ok();
  renderWith(serg);
  await userEvent.type(screen.getByRole("textbox"), "Мат{Enter}");
  expect(api.nameSpeakers).toHaveBeenCalledWith(ep, "r1", { "Спикер 2": "Матвей" });
});

test("«Готово» с точным совпадением берёт каноничное имя", async () => {
  ok();
  renderWith([{ ...people[0]!, name: "Демьян Петров" }, people[0]!]);
  await userEvent.type(screen.getByRole("textbox"), "демьян");
  await userEvent.click(screen.getByRole("button", { name: "Готово" }));
  expect(api.nameSpeakers).toHaveBeenCalledWith(ep, "r1", { "Спикер 2": "Демьян" });
});

test("безымянный спикер: поле пустое, голос запоминается по умолчанию", () => {
  setup();
  expect(screen.getByRole("textbox", { name: "Кто это?" })).toHaveValue("");
  expect(screen.getByRole("checkbox", { name: /Запомнить голос/ })).toBeChecked();
  expect(screen.queryByText("Имя будет исправлено только в этой записи")).toBeNull();
});

test("названный спикер: имя в поле, флажок снят, исправление только в этой записи", async () => {
  vi.mocked(api.getRecording).mockResolvedValue({
    id: "r1", transcript: {
      version: 1, title: null, names: { "Спикер 1": "Демьян Петров" },
      segments: [
        { start: 0, end: 1, speaker: "Демьян Петров", text: "a", uncertain: false },
        { start: 2, end: 3, speaker: "Спикер 2", text: "b", uncertain: false },
      ],
    },
  } as never);
  vi.mocked(api.saveTranscript).mockResolvedValue({ ok: true });
  const onDone = vi.fn();
  render(<SpeakerPopover endpoint={ep} recordingId="r1" label="Демьян Петров" people={people} onDone={onDone} />);
  const input = screen.getByRole("textbox", { name: "Кто это?" });
  expect(input).toHaveValue("Демьян Петров");
  expect(screen.getByRole("checkbox", { name: /Запомнить голос/ })).not.toBeChecked();
  expect(screen.getByText("Имя будет исправлено только в этой записи")).toBeInTheDocument();
  await userEvent.clear(input);
  await userEvent.type(input, "Пётр Демьянов");
  await userEvent.click(screen.getByRole("button", { name: "Готово" }));
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(api.nameSpeakers).not.toHaveBeenCalled();
  const t = vi.mocked(api.saveTranscript).mock.calls[0]![2];
  expect(t.segments.map((x) => x.speaker)).toEqual(["Пётр Демьянов", "Спикер 2"]);
  expect(t.names).toEqual({ "Спикер 1": "Пётр Демьянов" });
});

test("названный спикер: флажок включён вручную — nameSpeakers, voices_error предупреждением", async () => {
  vi.mocked(api.nameSpeakers).mockResolvedValue(
    { ok: true, renamed: 1, enrolled: [], voices_error: "в сайдкаре нет «Демьян Петров»" });
  render(<SpeakerPopover endpoint={ep} recordingId="r1" label="Демьян Петров" people={people} onDone={vi.fn()} />);
  await userEvent.click(screen.getByRole("checkbox", { name: /Запомнить голос/ }));
  expect(screen.queryByText("Имя будет исправлено только в этой записи")).toBeNull();
  const input = screen.getByRole("textbox", { name: "Кто это?" });
  await userEvent.clear(input);
  await userEvent.type(input, "Пётр{Enter}");
  expect(api.nameSpeakers).toHaveBeenCalledWith(ep, "r1", { "Демьян Петров": "Пётр" });
  expect(await screen.findByText(/в сайдкаре нет/)).toBeInTheDocument();
});

test("имя не изменилось — просто закрыть", async () => {
  const onDone = vi.fn();
  render(<SpeakerPopover endpoint={ep} recordingId="r1" label="Демьян Петров" people={people} onDone={onDone} />);
  await userEvent.click(screen.getByRole("button", { name: "Готово" }));
  expect(onDone).toHaveBeenCalled();
  expect(api.saveTranscript).not.toHaveBeenCalled();
  expect(api.nameSpeakers).not.toHaveBeenCalled();
});
