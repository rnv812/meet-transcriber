import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import type { OwnerVoiceSample, OwnerVoiceStatus, OwnerVoiceTake } from "../../lib/types";
import { StepVoice } from "../wizard/StepVoice";
import { OwnerVoiceRow, sampleText, shortDate } from "./OwnerVoice";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getOwnerVoice: vi.fn(),
  recordOwnerVoice: vi.fn(),
  deleteOwnerVoice: vi.fn(),
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
  expect(screen.queryByText(/Утро выдалось тихим/)).toBeNull();
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
