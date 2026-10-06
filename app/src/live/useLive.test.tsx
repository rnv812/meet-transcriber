import { act, renderHook } from "@testing-library/react";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  liveAsk: vi.fn(),
  liveHint: vi.fn(),
  liveTask: vi.fn(),
}));
import { ApiError, liveAsk, liveHint, liveTask } from "../lib/api";
import { FakeEventSource } from "../test/setup";
import { MAX_LINES, useLive } from "./useLive";

const ep = { base: "http://h", token: "t" };
const liveSources = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events"));
const line = (i: number) => ({ t: i, speaker: "Демьян", text: `реплика ${i}` });
const hintOf = (id: string) => ({
  id, kind: "risk" as const, text: `подсказка ${id}`, why: "", source_t: 1, ref: null, pinned: false,
  dismissed: false, created_at: 1, updated_at: 1,
});

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.useRealTimers());

test("строки копятся из потока, хранятся последние 300", () => {
  const { result } = renderHook(() => useLive(ep));
  const es = liveSources().at(-1)!;
  expect(es.url).toBe("http://h/live/events?token=t");
  act(() => {
    for (let i = 0; i < 305; i++) es.emit("line", line(i), i);
  });
  expect(MAX_LINES).toBe(300);
  expect(result.current.lines).toHaveLength(300);
  // Номер строки (id: события) остаётся при ней: по нему лента ключует строки.
  expect(result.current.lines[0]).toEqual({ ...line(5), id: 5 });
  expect(result.current.lines.at(-1)).toEqual({ ...line(304), id: 304 });
});

test("state даёт сводку, статус и историю вопросов; строки state не дублируют ленту", () => {
  const { result } = renderHook(() => useLive(ep));
  const es = liveSources().at(-1)!;
  expect(result.current.loaded).toBe(false);
  const qa = [{ id: 1, q: "срок?", a: "пятница", error: null, pending: false, at: 1, quick: null }];
  act(() => es.emit("state", {
    digest: "### Решения", transcript: ["[00:01] Демьян: привет"], status: "Подсказки временно недоступны",
  }));
  act(() => es.emit("qa", { qa }));
  expect(result.current.digest).toBe("### Решения");
  expect(result.current.status).toBe("Подсказки временно недоступны");
  expect(result.current.qa).toEqual(qa);
  expect(result.current.loaded).toBe(true);
  expect(result.current.lines).toEqual([]);
});

test("поток отвергнут (409): переподключение с паузой, уже показанные строки не повторяются", async () => {
  vi.useFakeTimers();
  const { result } = renderHook(() => useLive(ep));
  const first = liveSources().at(-1)!;
  act(() => { first.emit("line", line(0), 0); first.emit("line", line(1), 1); });
  act(() => first.fail(true));
  expect(first.closed).toBe(true);
  expect(result.current.error).toMatch(/Нет связи с ассистентом/);
  expect(liveSources()).toHaveLength(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(liveSources()).toHaveLength(2);
  const second = liveSources().at(-1)!;
  // Новый поток без Last-Event-ID начинает с хвоста ленты — повторы отбрасываем.
  act(() => { second.emit("line", line(0), 0); second.emit("line", line(1), 1); second.emit("line", line(2), 2); });
  expect(result.current.lines.map((l) => l.t)).toEqual([0, 1, 2]);
  expect(result.current.error).toBeNull();
});

test("пауза растёт при повторных отказах", async () => {
  vi.useFakeTimers();
  renderHook(() => useLive(ep));
  act(() => liveSources().at(-1)!.fail(true));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  act(() => liveSources().at(-1)!.fail(true));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(liveSources()).toHaveLength(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(liveSources()).toHaveLength(3);
});

test("обрыв, после которого браузер переподключается сам, — новый поток не открываем", async () => {
  vi.useFakeTimers();
  renderHook(() => useLive(ep));
  act(() => liveSources().at(-1)!.fail(false));
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(liveSources()).toHaveLength(1);
  expect(liveSources()[0]!.closed).toBe(false);
});

test("живой режим не идёт — поток не открывается и не переподключается; строки остаются", async () => {
  vi.useFakeTimers();
  const { result, rerender } = renderHook(({ active }) => useLive(ep, active), { initialProps: { active: true } });
  const es = liveSources().at(-1)!;
  act(() => es.emit("line", line(0), 0));
  rerender({ active: false });
  expect(es.closed).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(liveSources()).toHaveLength(1);
  expect(result.current.lines).toHaveLength(1);
});

test("размонтирование закрывает поток и отменяет переподключение", async () => {
  vi.useFakeTimers();
  const { unmount } = renderHook(() => useLive(ep));
  act(() => liveSources().at(-1)!.fail(true));
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(liveSources()).toHaveLength(1);

  const again = renderHook(() => useLive(ep));
  const es = liveSources().at(-1)!;
  again.unmount();
  expect(es.closed).toBe(true);
});

test("ask: запрос в пути — asking; ответ придёт историей; отказ виден", async () => {
  let resolve!: (v: { answer: string }) => void;
  vi.mocked(liveAsk).mockReturnValueOnce(new Promise((r) => { resolve = r; }));
  const { result } = renderHook(() => useLive(ep));
  let done!: Promise<void>;
  act(() => { done = result.current.ask("кто за что?"); });
  expect(liveAsk).toHaveBeenCalledWith(ep, "кто за что?", {});
  expect(result.current.asking).toBe(true);
  await act(async () => { resolve({ answer: "Демьян — за релиз" }); await done; });
  expect(result.current.asking).toBe(false);
  expect(result.current.askError).toBeNull();

  vi.mocked(liveAsk).mockRejectedValueOnce(new ApiError(409, "живой режим не идёт"));
  await act(async () => { await result.current.ask("", { quick: "missed", since_t: 300 }); });
  expect(liveAsk).toHaveBeenLastCalledWith(ep, "", { quick: "missed", since_t: 300 });
  expect(result.current.askError).toBe("живой режим не идёт");
});

test("setTask уходит в liveTask", async () => {
  vi.mocked(liveTask).mockResolvedValue({ ok: true });
  const { result } = renderHook(() => useLive(ep));
  await act(async () => { await result.current.setTask("релиз 2.0"); });
  expect(liveTask).toHaveBeenCalledWith(ep, "релиз 2.0");
});

test("state даёт структурную сводку и подсказки; «Только сводка» — hintsEnabled false", () => {
  const { result } = renderHook(() => useLive(ep));
  const es = liveSources().at(-1)!;
  const summary = { topic: "Релиз", points: [], decisions: [{ id: "d1", text: "в пятницу" }], tasks: [], open_questions: [] };
  act(() => es.emit("state", { digest: "", transcript: [], status: null, summary, hints: [hintOf("h1")], hints_enabled: false }));
  expect(result.current.summary).toEqual(summary);
  expect(result.current.hints.map((h) => h.id)).toEqual(["h1"]);
  expect(result.current.hintsEnabled).toBe(false);
});

test("действие с подсказкой видно сразу; не дошло — откат и ошибка", async () => {
  vi.mocked(liveHint).mockResolvedValueOnce({ ok: true }).mockRejectedValueOnce(new ApiError(409, "Ассистент не запущен"));
  const { result } = renderHook(() => useLive(ep));
  const es = liveSources().at(-1)!;
  act(() => es.emit("state", { digest: "", transcript: [], status: null, hints: [hintOf("h1"), hintOf("h2")] }));
  await act(async () => { await result.current.hint("h1", "dismiss"); });
  expect(liveHint).toHaveBeenCalledWith(ep, "h1", "dismiss");
  expect(result.current.hints.map((h) => h.id)).toEqual(["h2"]);
  // Ассистент прислал своё состояние — пометка больше не нужна.
  act(() => es.emit("state", { digest: "", transcript: [], status: null, hints: [hintOf("h2")] }));
  expect(result.current.hints.map((h) => h.id)).toEqual(["h2"]);
  await act(async () => { await result.current.hint("h2", "pin"); });
  expect(result.current.hints[0]!.pinned).toBe(false);
  // Ошибка — у подсказки, а не во вкладке «Спросить».
  expect(result.current.hintError).toEqual({ id: "h2", text: "Ассистент не запущен", action: "pin" });
  expect(result.current.askError).toBeNull();
});

test("пометка держится, пока ассистент не подтвердил действие", async () => {
  let done!: (v: { ok: boolean }) => void;
  vi.mocked(liveHint).mockReturnValueOnce(new Promise((r) => { done = r; }));
  const { result } = renderHook(() => useLive(ep));
  const es = liveSources().at(-1)!;
  act(() => es.emit("state", { digest: "", transcript: [], status: null, hints: [hintOf("h1")] }));
  let call!: Promise<boolean | void>;
  act(() => { call = result.current.hint("h1", "pin"); });
  // Старое состояние, пришедшее до ответа, пометку не стирает.
  act(() => es.emit("state", { digest: "", transcript: [], status: null, hints: [hintOf("h1")] }));
  expect(result.current.hints[0]!.pinned).toBe(true);
  await act(async () => { done({ ok: true }); await call; });
  act(() => es.emit("state", { digest: "", transcript: [], status: null, hints: [{ ...hintOf("h1"), pinned: true }] }));
  expect(result.current.hints[0]!.pinned).toBe(true);
});

test("ответ, который пишется: куски qa_partial видны у вопроса, не чаще 10 раз в секунду", async () => {
  vi.useFakeTimers();
  const { result } = renderHook(() => useLive(ep));
  const es = liveSources().at(-1)!;
  const pending = [{ id: 7, q: "что ответить?", a: null, error: null, pending: true, at: 1, quick: null }];
  act(() => es.emit("qa", { qa: pending }));
  act(() => es.emit("qa_partial", { id: 7, a: "Пред" }));
  expect(result.current.qa[0]!.partial).toBe("Пред");            // первый кусок — сразу
  act(() => {
    es.emit("qa_partial", { id: 7, a: "Предлагаю" });
    es.emit("qa_partial", { id: 7, a: "Предлагаю перенести" });
  });
  expect(result.current.qa[0]!.partial).toBe("Пред");            // дальше — не чаще 100 мс
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(result.current.qa[0]!.partial).toBe("Предлагаю перенести");
  const done = [{ ...pending[0]!, a: "Предлагаю перенести релиз.", pending: false }];
  act(() => es.emit("qa", { qa: done }));
  expect(result.current.qa[0]!.partial).toBeUndefined();          // готовый ответ — в истории
  expect(result.current.qa[0]!.a).toBe("Предлагаю перенести релиз.");
});

test("имя голоса задним числом: прежние строки переподписаны, дубли спрятаны", () => {
  const { result } = renderHook(() => useLive(ep));
  const es = liveSources().at(-1)!;
  const said = (i: number, voice?: string) => ({ t: i, speaker: "Собеседник", text: `реплика ${i}`, ...(voice ? { voice } : {}) });
  act(() => {
    es.emit("voices", { rev: 0, speakers: {}, hidden: [] });
    es.emit("line", said(0, "sys:0"), 0);
    es.emit("line", said(1, "sys:1"), 1);
    es.emit("line", said(2), 2);
  });
  expect(result.current.lines.map((l) => l.speaker)).toEqual(["Собеседник", "Собеседник", "Собеседник"]);
  act(() => es.emit("voices", { rev: 1, speakers: { "sys:0": "Демьян" }, hidden: [1] }));
  expect(result.current.lines.map((l) => [l.id, l.speaker])).toEqual([[0, "Демьян"], [2, "Собеседник"]]);
  // Новая строка того же голоса приходит уже с именем — и показывается с ним же.
  act(() => es.emit("line", { ...said(3, "sys:0"), speaker: "Демьян" }, 3));
  expect(result.current.lines.at(-1)!.speaker).toBe("Демьян");
  // Строка хранится как пришла: подпись — только на показе.
  expect(result.current.lines[0]).toEqual({ ...said(0, "sys:0"), id: 0, speaker: "Демьян" });
});

test("новый ассистент: его имена и спрятанные номера не ложатся на строки прежнего", async () => {
  vi.useFakeTimers();
  const { result } = renderHook(() => useLive(ep));
  const first = liveSources().at(-1)!;
  act(() => {
    first.emit("voices", { rev: 0, speakers: {}, hidden: [], session: "a" });
    first.emit("line", { t: 0, speaker: "Собеседник", text: "старая", voice: "a1/sys:0" }, 0);
    first.emit("line", { t: 1, speaker: "Собеседник", text: "ещё старая", voice: "a1/sys:0" }, 1);
  });
  act(() => first.fail(true));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  const second = liveSources().at(-1)!;
  // Новый ассистент нумерует заново: его sys:0 и строка №1 — другие.
  act(() => {
    second.emit("voices", { rev: 2, speakers: { "b2/sys:0": "Демьян" }, hidden: [1], session: "b" });
    second.emit("line", { t: 5, speaker: "Демьян", text: "новая", voice: "b2/sys:0" }, 2);
  });
  expect(result.current.lines.map((l) => [l.text, l.speaker])).toEqual([
    ["старая", "Собеседник"], ["ещё старая", "Собеседник"], ["новая", "Демьян"],
  ]);
  act(() => second.emit("voices", { rev: 3, speakers: { "b2/sys:0": "Демьян" }, hidden: [1, 2], session: "b" }));
  expect(result.current.lines.map((l) => l.text)).toEqual(["старая", "ещё старая"]);
});

test("новый ассистент: строки прежнего сохраняют его имена и спрятанные дубли", async () => {
  vi.useFakeTimers();
  const { result } = renderHook(() => useLive(ep));
  const first = liveSources().at(-1)!;
  act(() => {
    first.emit("voices", { rev: 0, speakers: {}, hidden: [], session: "a" });
    first.emit("line", { t: 0, speaker: "Собеседник", text: "до имени", voice: "a1/sys:0" }, 0);
    first.emit("line", { t: 1, speaker: "Собеседник рядом", text: "дубль", voice: "a1/mic:0" }, 1);
    first.emit("voices", { rev: 2, speakers: { "a1/sys:0": "Демьян" }, hidden: [1], session: "a" });
  });
  expect(result.current.lines.map((l) => [l.text, l.speaker])).toEqual([["до имени", "Демьян"]]);
  act(() => first.fail(true));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  const second = liveSources().at(-1)!;
  act(() => {
    second.emit("voices", { rev: 0, speakers: {}, hidden: [], session: "b" });
    second.emit("line", { t: 5, speaker: "Собеседник", text: "новая", voice: "b2/sys:0" }, 2);
  });
  // Имя, пришедшее задним числом от прежнего ассистента, и его спрятанный дубль — остаются.
  expect(result.current.lines.map((l) => [l.text, l.speaker])).toEqual([
    ["до имени", "Демьян"], ["новая", "Собеседник"],
  ]);
  // И на следующем событии нового сеанса — тоже (ревью, раунд 3).
  act(() => second.emit("voices", { rev: 1, speakers: { "b2/sys:0": "Пётр" }, hidden: [], session: "b" }));
  expect(result.current.lines.map((l) => [l.text, l.speaker])).toEqual([
    ["до имени", "Демьян"], ["новая", "Пётр"],
  ]);
});

test("без метки сеанса карта голосов заменяется целиком с каждого подключения", async () => {
  vi.useFakeTimers();
  const { result } = renderHook(() => useLive(ep));
  const first = liveSources().at(-1)!;
  act(() => {
    first.emit("line", { t: 0, speaker: "Собеседник", text: "привет", voice: "sys:0" }, 0);
    first.emit("voices", { rev: 3, speakers: { "sys:0": "Демьян" }, hidden: [] });
  });
  expect(result.current.lines[0]!.speaker).toBe("Демьян");
  act(() => first.fail(true));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  const second = liveSources().at(-1)!;
  // Карта — состояние, а не дельта: пустая карта снимает прежние имена.
  act(() => second.emit("voices", { rev: 0, speakers: {}, hidden: [] }));
  expect(result.current.lines[0]!.speaker).toBe("Собеседник");
});
