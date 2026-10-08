/**
 * Разовое предложение включить авто-анализ — обновившимся с 0.2.x
 * (`analysis.consent` = "pending"): видно в готовой карточке, пока не ответили;
 * ответ уходит резиденту и больше не спрашивается. Новой установке — не видно.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import { ANALYSIS_OFFER, ANALYSIS_OFFER_SHORT } from "./analysis";
import * as api from "../../lib/api";
import type { Recording, Transcript } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(),
  getAssistant: vi.fn(),
  getSummary: vi.fn(),
  getQa: vi.fn(),
  getAnalysis: vi.fn(),
  answerAnalysisOffer: vi.fn(),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => false,
  saveText: vi.fn(async () => "x"),
  openFolder: vi.fn(async () => {}),
  agentKillRecording: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const transcript: Transcript = {
  version: 1, title: null,
  segments: [{ start: 0, end: 4, speaker: "Спикер 1", text: "Начинаем планёрку.", uncertain: false }],
};
const rec: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 60,
  tracks: {}, has_transcript: true, has_voices: true, title: "Встреча", source: "record",
};
const withModel = { provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null, checking: false };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, transcript });
  vi.mocked(api.getSettings).mockResolvedValue({ analysis: { auto: false, consent: "pending" } });
  vi.mocked(api.getAssistant).mockResolvedValue(withModel);
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "none" });
  vi.mocked(api.answerAnalysisOffer).mockImplementation(async (_ep, _id, answer) =>
    ({ analysis: { auto: answer === "granted", consent: answer } }));
});

const offer = () => screen.queryByRole("region", { name: "Предложение: анализ встречи" });

test("текст предложения — тот, что согласован; в строке — коротко", () => {
  expect(ANALYSIS_OFFER_SHORT).toBe("Разметить встречу моделью: главы, важное, выводы?");
  expect(ANALYSIS_OFFER).toBe("Анализ встречи: типы фраз, главы, важное и выводы. Текст встречи отправляется "
    + "выбранной модели (Claude Code, Codex или OpenCode). Включить автоматически после расшифровки?");
});

test.each([["Включить", "granted"], ["Не сейчас", "declined"]] as const)(
  "«%s» — ответ уходит резиденту, предложение больше не видно", async (label, answer) => {
    const onOpenSettings = vi.fn();
    const { rerender } = render(<RecordingCard id="r1" endpoint={ep} onOpenSettings={onOpenSettings} />);
    await screen.findByText(/Начинаем планёрку/);
    await waitFor(() => expect(offer()).not.toBeNull());
    // Одна строка над лентой «Расшифровки» (на месте «Наблюдений» макета), плоская (0.5);
    // подробности — что текст уходит модели — в «?».
    expect(offer()).toHaveTextContent(ANALYSIS_OFFER_SHORT);
    expect(offer()).toHaveClass("card");
    expect(offer()).not.toHaveClass("aurora-wash");
    expect(offer()!.closest('[role="tabpanel"]')).toHaveAccessibleName("Расшифровка");
    expect(document.querySelector(".card__notices [aria-label='Предложение: анализ встречи']")).toBeNull();
    await userEvent.click(within(offer()!).getByRole("button", { name: "Что такое анализ встречи" }));
    expect(await screen.findByText(ANALYSIS_OFFER)).toBeInTheDocument();
    expect(screen.getByText(/Можно изменить в/)).toBeInTheDocument();
    for (const name of ["Включить", "Не сейчас"]) {
      expect(within(offer()!).getByRole("button", { name })).toHaveClass("btn--sm");
    }
    await userEvent.click(within(offer()!).getByRole("button", { name: label }));
    expect(api.answerAnalysisOffer).toHaveBeenCalledWith(ep, "r1", answer);
    await waitFor(() => expect(offer()).toBeNull());
    // Перечитанная карточка не спрашивает снова.
    rerender(<RecordingCard id="r1" endpoint={ep} refreshKey={1} onOpenSettings={onOpenSettings} />);
    await waitFor(() => expect(api.getRecording).toHaveBeenCalledTimes(2));
    expect(offer()).toBeNull();
    expect(api.answerAnalysisOffer).toHaveBeenCalledTimes(1);
  });

test("«в настройках» ведёт в раздел «Анализ встречи»", async () => {
  const onOpenSettings = vi.fn();
  render(<RecordingCard id="r1" endpoint={ep} onOpenSettings={onOpenSettings} />);
  await waitFor(() => expect(offer()).not.toBeNull());
  await userEvent.click(within(offer()!).getByRole("button", { name: "Что такое анализ встречи" }));
  await userEvent.click(await screen.findByRole("button", { name: "настройках" }));
  expect(onOpenSettings).toHaveBeenCalledWith("analysis");
});

test.each([
  ["новая установка (вопроса нет)", { analysis: { auto: true } }, withModel],
  ["уже ответили", { analysis: { auto: false, consent: "declined" } }, withModel],
  ["модель не подключена", { analysis: { auto: false, consent: "pending" } }, { ...withModel, provider: null }],
])("не предлагается: %s", async (_name, cfg, assistant) => {
  vi.mocked(api.getSettings).mockResolvedValue(cfg);
  vi.mocked(api.getAssistant).mockResolvedValue(assistant);
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText(/Начинаем планёрку/);
  await waitFor(() => expect(api.getAssistant).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 50));
  expect(offer()).toBeNull();
});
