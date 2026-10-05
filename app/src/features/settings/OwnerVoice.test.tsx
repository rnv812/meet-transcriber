import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import type {
  OwnerVoiceDerive, OwnerVoiceSample, OwnerVoiceStatus, OwnerVoiceSuggestion, OwnerVoiceTake,
} from "../../lib/types";
import { StepVoice } from "../wizard/StepVoice";
import { OwnerVoiceRow, sampleText, shortDate } from "./OwnerVoice";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getOwnerVoice: vi.fn(),
  recordOwnerVoice: vi.fn(),
  deleteOwnerVoice: vi.fn(),
  deriveOwnerVoice: vi.fn(),
  answerOwnerSuggestion: vi.fn(),
  cancelJob: vi.fn(),
}));

const ep = { base: "/api", token: null };
const status = (extra: Partial<OwnerVoiceStatus> = {}): OwnerVoiceStatus => ({
  samples: [], take: null, ready: true, reason: null, recording: false, seconds: 25, ...extra,
});
const take = (state: OwnerVoiceTake["state"], extra: Partial<OwnerVoiceTake> = {}): OwnerVoiceTake => ({
  state, device: "USB-микрофон", seconds: 25, started_at: 1, error: null, sample_id: null, job: "j1", ...extra,
});
const sample = (extra: Partial<OwnerVoiceSample> = {}): OwnerVoiceSample => ({
  id: "s1", source: "enroll", date: "2026-10-05", seconds: 21.4, device: "Onboard MIC", recording: null,
  quality: 0.82, ...extra,
});

beforeEach(() => vi.clearAllMocks());

test("строка образца словами", () => {
  expect(shortDate("2026-10-05")).toBe("05.10");
  expect(sampleText(sample())).toBe("записан 05.10 · Onboard MIC");
  expect(sampleText(sample({ device: null }))).toBe("записан 05.10");
  expect(sampleText(sample({ source: "meeting", recording: "2026-10-03_11-00", date: "2026-10-03" })))
    .toBe("из встречи 03.10");
});

test("настройки: не записан → «Записать» открывает текст; запись с микрофона из черновика", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status())
    .mockResolvedValueOnce(status({ take: take("analyzing") }))
    .mockResolvedValue(status({ take: take("done", { sample_id: "s1" }), samples: [sample({ device: "USB-микрофон" })] }));
  vi.mocked(api.recordOwnerVoice).mockResolvedValue(status({ take: take("recording") }));
  render(<OwnerVoiceRow endpoint={ep} device="USB-микрофон" pollMs={5} />);
  const group = screen.getByRole("group", { name: "Мой голос" });
  expect(await within(group).findByText("не записан")).toBeInTheDocument();
  await userEvent.click(within(group).getByRole("button", { name: "Записать" }));
  expect(screen.getByText(/Утро выдалось тихим/)).toBeInTheDocument();
  expect(screen.getByText(/Хранится только отпечаток голоса/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(api.recordOwnerVoice).toHaveBeenCalledWith(ep, "USB-микрофон");
  // Готово — строка свёрнута, в ней новый образец.
  expect(await within(group).findByText("записан 05.10 · USB-микрофон")).toBeInTheDocument();
  // Сворачивается эффектом после того, как образец появился в строке.
  await waitFor(() => expect(screen.queryByText(/Утро выдалось тихим/)).toBeNull());
  expect(within(group).getByRole("button", { name: "Перезаписать" })).toBeInTheDocument();
});

test("настройки: «Перезаписать» — только для образца выбранного микрофона", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ samples: [sample()] }));
  const { unmount } = render(<OwnerVoiceRow endpoint={ep} device="USB-микрофон" />);
  expect(await screen.findByRole("button", { name: "Записать" })).toBeInTheDocument();
  unmount();
  render(<OwnerVoiceRow endpoint={ep} device="Onboard MIC" />);
  expect(await screen.findByRole("button", { name: "Перезаписать" })).toBeInTheDocument();
});

test("настройки: записанный образец удаляется после подтверждения", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ samples: [sample()] }));
  vi.mocked(api.deleteOwnerVoice).mockResolvedValue(status());
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Удалить образец: записан 05.10 · Onboard MIC" }));
  const dialog = screen.getByRole("alertdialog", { name: "Удалить записанный образец голоса?" });
  await userEvent.click(within(dialog).getByRole("button", { name: "Оставить" }));
  expect(api.deleteOwnerVoice).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Удалить образец: записан 05.10 · Onboard MIC" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Удалить" }));
  expect(api.deleteOwnerVoice).toHaveBeenCalledWith(ep, "s1");
  expect(await screen.findByText("не записан")).toBeInTheDocument();
});

test("мастер: модель докачалась — шаг сам предлагает запись", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status({
    ready: false, reason: "Модель разделения на спикеров ещё скачивается — подождите немного" }))
    .mockResolvedValue(status());
  render(<StepVoice endpoint={ep} onNext={vi.fn()} pollMs={5} readyPollMs={10} />);
  expect(await screen.findByText("Модель разделения на спикеров ещё скачивается — подождите немного.")).toBeInTheDocument();
  expect(screen.getByText("Сейчас его не записать.", { exact: false })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Начать запись" })).toBeEnabled();
});

test("настройки: «Удалить» убирает образец", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ samples: [sample(), sample({
    id: "s2", source: "meeting", date: "2026-10-03", device: null, recording: "2026-10-03_11-00" })] }));
  vi.mocked(api.deleteOwnerVoice).mockResolvedValue(status({ samples: [sample()] }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Удалить образец: из встречи 03.10" }));
  expect(api.deleteOwnerVoice).toHaveBeenCalledWith(ep, "s2");
  await waitFor(() => expect(screen.queryByText("из встречи 03.10")).toBeNull());
  expect(screen.getByText("записан 05.10 · Onboard MIC")).toBeInTheDocument();
});

test("настройки: ошибка качества — словами, можно записать ещё раз", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status())
    .mockResolvedValue(status({ take: take("failed", {
      error: "Голос всего на 9 дБ громче фона, нужно хотя бы 15. Найдите место потише и повторите." }) }));
  vi.mocked(api.recordOwnerVoice).mockResolvedValue(status({ take: take("analyzing") }));
  render(<OwnerVoiceRow endpoint={ep} device={null} pollMs={5} />);
  await userEvent.click(await screen.findByRole("button", { name: "Записать" }));
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Голос всего на 9 дБ громче фона");
  expect(screen.getByRole("button", { name: "Записать ещё раз" })).toBeEnabled();
});

test("настройки: во время записи встречи записать нельзя", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ recording: true }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Записать" }));
  expect(screen.getByRole("button", { name: "Начать запись" })).toBeDisabled();
  expect(screen.getByText(/Идёт запись встречи/)).toBeInTheDocument();
});

test("мастер: запись идёт с отсчётом, затем «Голос записан» и «Далее»", async () => {
  const onNext = vi.fn();
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status())
    .mockResolvedValueOnce(status({ take: take("recording") }))
    .mockResolvedValue(status({ take: take("done", { sample_id: "s1" }), samples: [sample()] }));
  vi.mocked(api.recordOwnerVoice).mockResolvedValue(status({ take: take("recording") }));
  render(<StepVoice endpoint={ep} onNext={onNext} pollMs={20} />);
  await userEvent.click(await screen.findByRole("button", { name: "Начать запись" }));
  expect(api.recordOwnerVoice).toHaveBeenCalledWith(ep, null);
  expect(screen.getByText(/Читайте вслух… осталось \d+ с/)).toBeInTheDocument();
  expect(screen.getByRole("meter", { name: "Запись образца" })).toBeInTheDocument();
  expect(await screen.findByText("Голос записан.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Далее" }));
  expect(onNext).toHaveBeenCalled();
});

test("мастер: резидент отказал — текст отказа, «Позже, в настройках» доступно", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status());
  vi.mocked(api.recordOwnerVoice).mockRejectedValue(new api.ApiError(409, "Образец голоса уже записывается"));
  render(<StepVoice endpoint={ep} onNext={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Начать запись" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Образец голоса уже записывается");
  expect(screen.getByRole("button", { name: "Позже, в настройках" })).toBeEnabled();
});

test("мастер: один неудачный опрос не останавливает перепроверку готовности", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status({ ready: false, reason: "Модель ещё скачивается" }))
    .mockRejectedValueOnce(new api.NoResidentError("служба записи не отвечает"))
    .mockResolvedValue(status());
  render(<StepVoice endpoint={ep} onNext={vi.fn()} pollMs={5} readyPollMs={10} />);
  expect(await screen.findByRole("button", { name: "Начать запись" })).toBeEnabled();
  expect(vi.mocked(api.getOwnerVoice).mock.calls.length).toBeGreaterThanOrEqual(3);
});

test("запись: сбой опроса посреди разбора — опрос продолжается до итога", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status())
    .mockRejectedValueOnce(new api.NoResidentError("служба записи не отвечает"))
    .mockResolvedValue(status({ take: take("done", { sample_id: "s1" }), samples: [sample()] }));
  vi.mocked(api.recordOwnerVoice).mockResolvedValue(status({ take: take("analyzing") }));
  render(<StepVoice endpoint={ep} onNext={vi.fn()} pollMs={5} />);
  await userEvent.click(await screen.findByRole("button", { name: "Начать запись" }));
  expect(await screen.findByText("Голос записан.")).toBeInTheDocument();
});

// --- «Найти по прошлым встречам» ------------------------------------------------

const found = (extra: Partial<OwnerVoiceSuggestion> = {}): OwnerVoiceSuggestion => ({
  meetings: ["2026-10-01_10-00", "2026-10-02_10-00", "2026-10-03_10-00"],
  samples: [1, 2, 3].map((d) => ({ recording: `2026-10-0${d}_10-00`, start: 12, end: 16.5, track: "mic" as const })),
  seconds: 412, quality: 0.83, date: "2026-10-06", ...extra,
});
const derive = (extra: Partial<OwnerVoiceDerive> = {}): OwnerVoiceDerive => ({
  running: false, error: null, last: null, ...extra,
});

test("поиск по встречам: идёт — словами, затем карточка с тремя примерами", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status({ derive: derive() }))
    .mockResolvedValueOnce(status({ derive: derive({ running: true }) }))
    .mockResolvedValue(status({ suggestion: found(), derive: derive({ last: { status: "suggested", reason: null } }) }));
  vi.mocked(api.deriveOwnerVoice).mockResolvedValue(status({ derive: derive({ running: true }) }));
  render(<OwnerVoiceRow endpoint={ep} device={null} pollMs={5} />);
  await userEvent.click(await screen.findByRole("button", { name: "Найти по прошлым встречам" }));
  expect(api.deriveOwnerVoice).toHaveBeenCalledWith(ep);
  expect(screen.getByText(/Ищу ваш голос в последних встречах/)).toBeInTheDocument();
  const card = await screen.findByRole("group", { name: "Найденный голос" });
  expect(card).toHaveTextContent("Похоже, это ваш голос — послушайте:");
  expect(within(card).getAllByRole("button", { name: /^Послушать пример/ })).toHaveLength(3);
  expect(within(card).getByRole("button", { name: "Послушать пример 2 — встреча 02.10" })).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: "Да, это я" })).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: "Нет" })).toBeInTheDocument();
  // Ничего не применено: образцов по-прежнему нет.
  expect(screen.getByText("не записан")).toBeInTheDocument();
});

test("поиск по встречам: ▶ играет кусок микрофона нужной встречи", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ suggestion: found(), derive: derive() }));
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  const load = vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Послушать пример 3 — встреча 03.10" }));
  const audio = document.querySelector("audio")!;
  expect(audio.getAttribute("src")).toBe("/api/recordings/2026-10-03_10-00/audio?track=mic#t=12,16.5");
  expect(play).toHaveBeenCalled();
  play.mockRestore();
  load.mockRestore();
});

test("поиск по встречам: «Да, это я» — образец, карточка исчезает", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ suggestion: found(), derive: derive() }));
  vi.mocked(api.answerOwnerSuggestion).mockResolvedValue(status({
    samples: [sample({ id: "a1", source: "auto", date: "2026-10-06", device: null })], derive: derive() }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Да, это я" }));
  expect(api.answerOwnerSuggestion).toHaveBeenCalledWith(ep, true);
  expect(await screen.findByText("найден по прошлым встречам 06.10")).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Найденный голос" })).toBeNull();
});

test("поиск по встречам: «Нет» снимает предложение без образца", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ suggestion: found(), derive: derive() }));
  vi.mocked(api.answerOwnerSuggestion).mockResolvedValue(status({ derive: derive() }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Нет" }));
  expect(api.answerOwnerSuggestion).toHaveBeenCalledWith(ep, false);
  await waitFor(() => expect(screen.queryByRole("group", { name: "Найденный голос" })).toBeNull());
  expect(screen.getByText("не записан")).toBeInTheDocument();
});

test("поиск по встречам: ничего не нашлось — честная причина", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ derive: derive({ last: {
    status: "too_few", reason: "Подходящих встреч пока 2, а нужно хотя бы 3", date: "2026-10-06" } }) }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  expect(await screen.findByText("Поиск 06.10: голос не предложен. Подходящих встреч пока 2, а нужно хотя бы 3."))
    .toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Найденный голос" })).toBeNull();
});

test("поиск по встречам: сбой задачи — ошибкой", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ derive: derive({ error: "RuntimeError: сломалось" }) }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Поиск не удался: RuntimeError: сломалось");
});

test("поиск по встречам: во время записи встречи или без модели — недоступен", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ recording: true, derive: derive() }));
  const { unmount } = render(<OwnerVoiceRow endpoint={ep} device={null} />);
  expect(await screen.findByRole("button", { name: "Найти по прошлым встречам" })).toBeDisabled();
  unmount();
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ ready: false, reason: "Нет модели", derive: derive() }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  expect(await screen.findByRole("button", { name: "Найти по прошлым встречам" })).toBeDisabled();
});

test("поиск по встречам: отказ резидента — текстом", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ derive: derive() }));
  vi.mocked(api.deriveOwnerVoice).mockRejectedValue(new api.ApiError(409, "Образец голоса сейчас записывается"));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Найти по прошлым встречам" }));
  expect(await screen.findByText(/Образец голоса сейчас записывается/)).toBeInTheDocument();
});

test("поиск по встречам: сбой задачи — только ошибка, без старой причины", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ derive: derive({ error: "RuntimeError: сломалось",
    last: { status: "too_few", reason: "Мало встреч", date: "2026-10-05" } }) }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Поиск не удался");
  expect(screen.queryByText(/Мало встреч/)).toBeNull();
});

test("поиск по встречам: «Остановить поиск» снимает задачу", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status({ derive: derive({ running: true, job: "j9" }) }))
    .mockResolvedValue(status({ derive: derive() }));
  vi.mocked(api.cancelJob).mockResolvedValue({ ok: true } as never);
  render(<OwnerVoiceRow endpoint={ep} device={null} pollMs={100000} />);
  await userEvent.click(await screen.findByRole("button", { name: "Остановить поиск" }));
  expect(api.cancelJob).toHaveBeenCalledWith(ep, "j9");
  await waitFor(() => expect(screen.queryByText(/Ищу ваш голос/)).toBeNull());
  expect(screen.getByRole("button", { name: "Найти по прошлым встречам" })).toBeEnabled();
});

test("поиск по встречам: голос не похож на записанный образец — предупреждение", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ samples: [sample()], suggestion: found({ conflict: true }),
    derive: derive() }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  const card = await screen.findByRole("group", { name: "Найденный голос" });
  expect(card).toHaveTextContent("Он не похож на ваш записанный образец — послушайте внимательно.");
});

test("поиск по встречам: пока ответ идёт, «Да» и «Нет» недоступны", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ suggestion: found(), derive: derive() }));
  let done: (v: OwnerVoiceStatus) => void = () => {};
  vi.mocked(api.answerOwnerSuggestion).mockReturnValue(new Promise((r) => { done = r; }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Да, это я" }));
  expect(screen.getByRole("button", { name: "Нет" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Да, это я" })).toBeDisabled();
  done(status({ derive: derive() }));
  await waitFor(() => expect(screen.queryByRole("group", { name: "Найденный голос" })).toBeNull());
  expect(api.answerOwnerSuggestion).toHaveBeenCalledTimes(1);
});

test("поиск по встречам: запись удалена — её ▶ недоступен", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ suggestion: found(), derive: derive() }));
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  const load = vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  const button = await screen.findByRole("button", { name: "Послушать пример 1 — встреча 01.10" });
  await userEvent.click(button);
  fireEvent.error(document.querySelector("audio")!);
  expect(await screen.findByRole("button", { name: "Пример 1 недоступен — запись удалена" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Послушать пример 2 — встреча 02.10" })).toBeEnabled();
  play.mockRestore();
  load.mockRestore();
});

test("поиск по встречам: примеров не осталось — так и сказано", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ suggestion: found({ samples: [] }), derive: derive() }));
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  const card = await screen.findByRole("group", { name: "Найденный голос" });
  expect(card).toHaveTextContent("Похоже, это ваш голос, но записи с примерами уже удалены.");
});
