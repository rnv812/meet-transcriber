/**
 * Просьбы к карточке из профиля человека: показать реплику (номер сегмента —
 * «Расшифровка», прокрутка и подсветка) и вставить текст во вкладку «Агент».
 * Агента в тестах нет — текст виден в уведомлении «Ссылка не вставлена».
 */

import { render, screen, waitFor } from "@testing-library/react";
import { RecordingCard } from "./RecordingCard";
import * as api from "../../lib/api";
import type { Recording, Transcript } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(),
  getAssistant: vi.fn(),
  getSummary: vi.fn(),
  getLiveDraft: vi.fn(),
  getQa: vi.fn(),
  getAgentContext: vi.fn(),
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
const transcript: Transcript = {
  version: 1, title: null,
  segments: [
    seg(0, 4, "Тимур", "Начинаем."),
    seg(65, 70, "Вера", "Итог такой: релиз в пятницу."),
    seg(130, 134, "Тимур", "Договорились."),
  ],
};
const rec: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 200,
  tracks: { sys: "s.opus" }, has_transcript: true, has_voices: true, title: "Планирование", source: "record",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, transcript });
  vi.mocked(api.getSettings).mockResolvedValue({});
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockResolvedValue({ markdown: "## Итоги", created_at: 1 });
  vi.mocked(api.getLiveDraft).mockResolvedValue(null);
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: ["transcript.md"], live: false });
  Element.prototype.scrollIntoView = vi.fn();
});

test("реплика из профиля: «Расшифровка», прокрутка и подсветка; просьба отдана один раз", async () => {
  const taken = vi.fn();
  const { container } = render(<RecordingCard id="r1" endpoint={ep}
    request={{ n: 1, id: "r1", segment: 1 }} onRequestTaken={taken} />);
  await screen.findByText("Начинаем.");
  const row = container.querySelector<HTMLElement>('[data-turn="1"]')!;
  await waitFor(() => expect(row).toHaveClass("turn--flash"));
  expect(row.textContent).toContain("Итог такой");
  expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  expect(screen.getByRole("tab", { name: "Расшифровка" })).toHaveAttribute("aria-selected", "true");
  expect(taken).toHaveBeenCalledTimes(1);
});

test("текст из профиля — во вкладку «Агент», одной строкой", async () => {
  const taken = vi.fn();
  render(<RecordingCard id="r1" endpoint={ep}
    request={{ n: 1, id: "r1", agent: "Помоги подготовиться к разговору с человеком «Вера». Тема разговора:" }}
    onRequestTaken={taken} />);
  await screen.findByText("Начинаем.");
  expect(screen.getByRole("tab", { name: "Агент" })).toHaveAttribute("aria-selected", "true");
  const note = await screen.findByRole("status", { name: "Ссылка не вставлена" });
  expect(note.querySelector("pre")!.textContent).toBe(
    "Помоги подготовиться к разговору с человеком «Вера». Тема разговора:");
  expect(taken).toHaveBeenCalledTimes(1);
});

test("реплика из профиля уже не та (другой спикер или время) — никуда не переходим, тихая строка", async () => {
  const taken = vi.fn();
  const { container } = render(<RecordingCard id="r1" endpoint={ep}
    request={{ n: 1, id: "r1", segment: 2, t: 130, speaker: "Вера" }} onRequestTaken={taken} />);
  await screen.findByText("Начинаем.");
  expect(await screen.findByText(/Реплика из профиля изменилась/)).toBeInTheDocument();
  expect(container.querySelector(".turn--flash")).toBeNull();
  expect(taken).toHaveBeenCalledTimes(1);
});

test("номер за концом расшифровки — не последняя реплика", async () => {
  const { container } = render(<RecordingCard id="r1" endpoint={ep}
    request={{ n: 1, id: "r1", segment: 40, t: 3000 }} onRequestTaken={() => {}} />);
  await screen.findByText("Начинаем.");
  expect(await screen.findByText(/Реплика из профиля изменилась/)).toBeInTheDocument();
  expect(container.querySelector(".turn--flash")).toBeNull();
});

test("расшифровка не готова — просьба отбрасывается, а не срабатывает потом", async () => {
  const taken = vi.fn();
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, has_transcript: false, transcript: null });
  render(<RecordingCard id="r1" endpoint={ep} request={{ n: 1, id: "r1", segment: 1, t: 65, speaker: "Вера" }}
    onRequestTaken={taken} />);
  await waitFor(() => expect(taken).toHaveBeenCalledTimes(1));
});
