import { act, renderHook } from "@testing-library/react";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  liveAsk: vi.fn(),
  liveTask: vi.fn(),
}));
import { ApiError, liveAsk, liveTask } from "../lib/api";
import { FakeEventSource } from "../test/setup";
import { MAX_LINES, useLive } from "./useLive";

const ep = { base: "http://h", token: "t" };
const liveSources = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events"));
const line = (i: number) => ({ t: i, speaker: "Демьян", text: `реплика ${i}` });

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

test("state даёт дайджест и статус; строки state не дублируют ленту", () => {
  const { result } = renderHook(() => useLive(ep));
  const es = liveSources().at(-1)!;
  act(() => es.emit("state", { digest: "## Решения", transcript: ["[00:01] Демьян: привет"], status: "дайджест обновлён" }));
  expect(result.current.digest).toBe("## Решения");
  expect(result.current.status).toBe("дайджест обновлён");
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

test("ask: ожидание, затем ответ; ошибка видна", async () => {
  let resolve!: (v: { answer: string }) => void;
  vi.mocked(liveAsk).mockReturnValueOnce(new Promise((r) => { resolve = r; }));
  const { result } = renderHook(() => useLive(ep));
  let done!: Promise<void>;
  act(() => { done = result.current.ask("кто за что?"); });
  expect(liveAsk).toHaveBeenCalledWith(ep, "кто за что?");
  expect(result.current.reply).toEqual({ pending: true, question: "кто за что?", answer: null, error: null });
  await act(async () => { resolve({ answer: "Демьян — за релиз" }); await done; });
  expect(result.current.reply).toEqual({ pending: false, question: "кто за что?", answer: "Демьян — за релиз", error: null });

  vi.mocked(liveAsk).mockRejectedValueOnce(new ApiError(409, "живой режим не идёт"));
  await act(async () => { await result.current.ask("ещё"); });
  expect(result.current.reply).toEqual({ pending: false, question: "ещё", answer: null, error: "живой режим не идёт" });
});

test("setTask уходит в liveTask", async () => {
  vi.mocked(liveTask).mockResolvedValue({ ok: true });
  const { result } = renderHook(() => useLive(ep));
  await act(async () => { await result.current.setTask("релиз 2.0"); });
  expect(liveTask).toHaveBeenCalledWith(ep, "релиз 2.0");
});
