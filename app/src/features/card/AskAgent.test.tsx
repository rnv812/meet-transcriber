/**
 * «Спросить агента» из карточки: ✦ у реплики (наведение, фокус, клавиша A,
 * меню правого щелчка), выбранные реплики, переход на «Агент». В браузере
 * (тестах) агента нет — ссылка видна в уведомлении «Ссылка не вставлена»:
 * по ней проверяется её текст.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import * as api from "../../lib/api";
import type { Recording, Transcript } from "../../lib/types";
import { FakeEventSource } from "../../test/setup";

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
    seg(0, 4, "Анна", "Начинаем планёрку."),
    seg(65, 70, "Олег", "Отчёт сдаём\nв пятницу.\x1b[2J"),
    seg(130, 134, "Анна", "Договорились."),
  ],
};
const rec: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 200,
  tracks: { sys: "s.opus" }, has_transcript: true, has_voices: true, title: "Планёрка", source: "record",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, transcript });
  vi.mocked(api.getSettings).mockResolvedValue({});
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockResolvedValue({ markdown: "## Решения\n\n- сдать отчёт в пятницу", created_at: 1 });
  vi.mocked(api.getLiveDraft).mockResolvedValue(null);
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: ["transcript.md"], live: false });
});

async function card() {
  const view = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Начинаем планёрку.");
  return view;
}
const rows = (c: HTMLElement) => [...c.querySelectorAll<HTMLElement>(".turn")];
const unsent = () => screen.findByRole("status", { name: "Ссылка не вставлена" });
const agentTab = () => screen.getByRole("tab", { name: "Агент" });

test("вкладки готовой записи: «Расшифровка · Итоги · Агент» — «Вопросов» больше нет", async () => {
  await card();
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Расшифровка", "Итоги", "Агент"]);
  vi.mocked(api.getQa).mockResolvedValue({ items: [{ q: "Кто готовит отчёт?", a: "Олег.", at: 1, provider: null }] });
  await userEvent.click(agentTab());
  expect(await screen.findByRole("group", { name: "Прошлые вопросы" })).toHaveTextContent("Кто готовит отчёт?");
  expect(api.getQa).toHaveBeenCalledWith(ep, "r1");
});

test("✦ у реплики: переход на «Агент», ссылка с временем, спикером и очищенным текстом", async () => {
  const { container } = await card();
  const ask = within(rows(container)[1]!).getByRole("button", { name: "Спросить агента об этой реплике" });
  expect(ask).toHaveAttribute("aria-keyshortcuts", "A");
  await userEvent.click(ask);
  expect(agentTab()).toHaveAttribute("aria-selected", "true");
  const note = await unsent();
  expect(note.querySelector("pre")!.textContent).toBe("Про реплику:\n[01:05] Олег: «Отчёт сдаём в пятницу.»");
});

test("клавиша A на реплике в фокусе — то же", async () => {
  const { container } = await card();
  const row = rows(container)[2]!;
  row.focus();
  // По коду клавиши: и в русской раскладке (Ф).
  fireEvent.keyDown(row, { key: "ф", code: "KeyA" });
  expect(agentTab()).toHaveAttribute("aria-selected", "true");
  expect((await unsent()).querySelector("pre")!.textContent).toContain("[02:10] Анна: «Договорились.»");
});

test("Ctrl+A на реплике — не просьба к агенту", async () => {
  const { container } = await card();
  const row = rows(container)[0]!;
  row.focus();
  fireEvent.keyDown(row, { key: "a", code: "KeyA", ctrlKey: true });
  expect(agentTab()).toHaveAttribute("aria-selected", "false");
});

test("выбранные реплики: «Спросить агента о выбранных» — все ссылки по порядку", async () => {
  const { container } = await card();
  fireEvent.click(rows(container)[2]!, { ctrlKey: true });
  fireEvent.click(rows(container)[0]!, { ctrlKey: true });
  await userEvent.click(screen.getByRole("button", { name: "Спросить агента о выбранных" }));
  const text = (await unsent()).querySelector("pre")!.textContent;
  expect(text).toBe("Про реплики:\n[00:00] Анна: «Начинаем планёрку.»\n[02:10] Анна: «Договорились.»");
});

test("A на выбранной реплике — о всех выбранных", async () => {
  const { container } = await card();
  fireEvent.click(rows(container)[0]!, { ctrlKey: true });
  fireEvent.click(rows(container)[1]!, { ctrlKey: true });
  rows(container)[1]!.focus();
  fireEvent.keyDown(rows(container)[1]!, { key: "a", code: "KeyA" });
  expect((await unsent()).querySelector("pre")!.textContent).toMatch(/^Про реплики:\n\[00:00\].*\n\[01:05\]/);
});

test("меню правого щелчка: «Спросить агента» и готовые вопросы", async () => {
  const { container } = await card();
  const p = rows(container)[1]!.querySelector<HTMLElement>(".turn__text")!;
  const text = p.firstChild!;
  (document as unknown as { caretRangeFromPoint: unknown }).caretRangeFromPoint = () => {
    const r = document.createRange();
    r.setStart(text, 3);
    return r;
  };
  try {
    fireEvent.contextMenu(p, { clientX: 10, clientY: 10 });
    const menu = await screen.findByRole("dialog", { name: "Разделить реплику здесь" });
    const group = within(menu).getByRole("group", { name: "Спросить агента об этой реплике" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Спросить агента…", "Объясни", "Что из этого следует?", "Сформулируй задачу", "Проверь по базе знаний",
    ]);
    await userEvent.click(within(group).getByRole("button", { name: "Сформулируй задачу" }));
    expect(screen.queryByRole("dialog", { name: "Разделить реплику здесь" })).toBeNull();
    expect((await unsent()).querySelector("pre")!.textContent)
      .toBe("Сформулируй задачу.\nПро реплику:\n[01:05] Олег: «Отчёт сдаём в пятницу.»");
  } finally {
    delete (document as unknown as { caretRangeFromPoint?: unknown }).caretRangeFromPoint;
  }
});

test("меню спикера у реплики: «Спросить агента об этой реплике»", async () => {
  const { container } = await card();
  await userEvent.click(within(rows(container)[0]!).getByRole("button", { name: "Анна" }));
  const menu = await screen.findByRole("dialog", { name: "Кому отдать реплики" });
  await userEvent.click(within(menu).getByRole("button", { name: "Спросить агента об этой реплике" }));
  expect((await unsent()).querySelector("pre")!.textContent).toContain("[00:00] Анна: «Начинаем планёрку.»");
});

test("✦ у пункта итогов — тоже на «Агент»", async () => {
  await card();
  await userEvent.click(screen.getByRole("tab", { name: "Итоги" }));
  await userEvent.click(await screen.findByRole("button", { name: "Спросить агента об этом пункте: сдать отчёт в пятницу" }));
  expect(agentTab()).toHaveAttribute("aria-selected", "true");
  expect((await unsent()).querySelector("pre")!.textContent)
    .toBe("Про пункт итогов:\n«сдать отчёт в пятницу» (раздел «Решения»)");
});

test("повторная просьба после «Скрыть» снова показывает ссылку", async () => {
  const { container } = await card();
  await userEvent.click(within(rows(container)[0]!).getByRole("button", { name: "Спросить агента об этой реплике" }));
  await userEvent.click(within(await unsent()).getByRole("button", { name: "Скрыть" }));
  await userEvent.click(screen.getByRole("tab", { name: "Расшифровка" }));
  await userEvent.click(within(rows(container)[0]!).getByRole("button", { name: "Спросить агента об этой реплике" }));
  await waitFor(() => expect(agentTab()).toHaveAttribute("aria-selected", "true"));
  expect(await unsent()).toBeInTheDocument();
});

test("запись с ассистентом: «Живой режим · Агент»; «Спросить об этом» у подсказки — агенту", async () => {
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, has_transcript: false, source: "live", transcript: null });
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: ["transcript.md"], live: true });
  const live = { active: true, starting: false, stopping: false, folder: "C:/rec/r1", error: null, started_at: 1 };
  render(<RecordingCard id="r1" endpoint={ep} snapshot={{ status: "idle", folder: null, live } as never} />);
  expect(await screen.findByText("Идёт запись с ассистентом")).toBeInTheDocument();
  expect(screen.getAllByRole("tab").slice(0, 2).map((t) => t.textContent)).toEqual(["Живой режим", "Агент"]);
  const stream = await vi.waitFor(() => {
    const found = FakeEventSource.instances.find((s) => s.url.startsWith("/api/live/events"));
    if (!found) throw new Error("поток ассистента ещё не открыт");
    return found;
  });
  act(() => stream.emit("state", { digest: "", transcript: [], status: null, hints: [{
    id: "h1", kind: "risk", text: "У миграции нет ответственного", why: "", source_t: 125, ref: null,
    pinned: false, dismissed: false, created_at: 1, updated_at: 1,
  }] }));
  await userEvent.click(screen.getByRole("tab", { name: "Подсказки" }));
  await userEvent.click(screen.getByRole("button", { name: "Спросить об этом" }));
  expect(agentTab()).toHaveAttribute("aria-selected", "true");
  expect((await unsent()).querySelector("pre")!.textContent)
    .toBe("Про подсказку ассистента:\n[02:05] Риск или неясность: «У миграции нет ответственного»");
});
