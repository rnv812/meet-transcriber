import {
  ApiError, applySpeakers, avatarUrl, deleteRecording, getSpeakers, putHotwords, redoSpeakers, revertSpeakers, undoSpeakers,
} from "./api";
import * as api from "./api";
import { FakeEventSource } from "../test/setup";

test("ошибка резидента приходит текстом", async () => {
  globalThis.fetch = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ error: "идёт расшифровка — отмените её или дождитесь" }), { status: 400 }));
  await expect(deleteRecording({ base: "http://127.0.0.1:1", token: "t" }, "x"))
    .rejects.toEqual(new ApiError(400, "идёт расшифровка — отмените её или дождитесь"));
});
test("аватар с токеном и версией", () => {
  expect(avatarUrl({ base: "http://127.0.0.1:9", token: "t k" }, "Демьян", 3))
    .toBe("http://127.0.0.1:9/voices/%D0%94%D0%B5%D0%BC%D1%8C%D1%8F%D0%BD/avatar?token=t%20k&v=3");
});
const ep = { base: "http://h", token: "t" };
const okFetch = () => {
  const f = vi.fn().mockImplementation(async () => new Response("{}", { status: 200 }));
  globalThis.fetch = f;
  return f;
};

test("панель «Спикеры»: обзор, набор правок, отмена, повтор и возврат к шагу", async () => {
  const f = okFetch();
  await getSpeakers(ep, "a b");
  await applySpeakers(ep, "a b", [{ type: "rename", label: "Спикер 2", to: "Анна" }], { "Спикер 2": true });
  await undoSpeakers(ep, "a b");
  await redoSpeakers(ep, "a b");
  await revertSpeakers(ep, "a b", null);
  const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method ?? "GET", (init as RequestInit).body]);
  expect(calls).toEqual([
    ["http://h/recordings/a%20b/speakers", "GET", undefined],
    ["http://h/recordings/a%20b/speakers/apply", "POST",
      JSON.stringify({ ops: [{ type: "rename", label: "Спикер 2", to: "Анна" }], remember: { "Спикер 2": true } })],
    ["http://h/recordings/a%20b/speakers/undo", "POST", "{}"],
    ["http://h/recordings/a%20b/speakers/redo", "POST", "{}"],
    ["http://h/recordings/a%20b/speakers/revert", "POST", JSON.stringify({ to_step_id: null })],
  ]);
});

test("putHotwords: PUT {text}", async () => {
  const f = okFetch();
  await putHotwords(ep, "Вася, Петя");
  expect(f.mock.calls[0]![0]).toBe("http://h/hotwords");
  expect(f.mock.calls[0]![1]).toMatchObject({ method: "PUT", body: JSON.stringify({ text: "Вася, Петя" }) });
});

test("предпросмотр выгрузки: значения черновика уходят в query", async () => {
  const f = okFetch();
  await api.getExportPreview(ep, {
    folder_template: "{year}/{date} - {title}", transcript_name: "Транскрипт.md", include_srt: true,
  });
  const url = new URL(String(f.mock.calls[0]![0]));
  expect(url.pathname).toBe("/export/preview");
  expect(url.searchParams.get("folder_template")).toBe("{year}/{date} - {title}");
  expect(url.searchParams.get("transcript_name")).toBe("Транскрипт.md");
  expect(url.searchParams.get("include_srt")).toBe("true");
});

test("ask: POST {question} на запись", async () => {
  const f = okFetch();
  await api.ask(ep, "a b", "что решили?");
  expect(f.mock.calls[0]![0]).toBe("http://h/recordings/a%20b/ask");
  expect(f.mock.calls[0]![1]).toMatchObject({ method: "POST", body: JSON.stringify({ question: "что решили?" }) });
});

test("итоги, вопросы, база знаний, ассистент: адреса и методы", async () => {
  const f = okFetch();
  await api.makeSummary(ep, "r1");
  await api.getSummary(ep, "r1");
  await api.getQa(ep, "r1");
  await api.kbExport(ep, "r1");
  await api.getAssistant(ep);
  await api.checkProvider(ep, "codex");
  const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method ?? "GET"]);
  expect(calls).toEqual([
    ["http://h/recordings/r1/summary", "POST"],
    ["http://h/recordings/r1/summary", "GET"],
    ["http://h/recordings/r1/qa", "GET"],
    ["http://h/recordings/r1/kb-export", "POST"],
    ["http://h/assistant", "GET"],
    ["http://h/assistant/check", "POST"],
  ]);
  expect(f.mock.calls[5]![1]).toMatchObject({ body: JSON.stringify({ provider: "codex" }) });
});

test("409 без провайдера приходит текстом", async () => {
  globalThis.fetch = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ error: "Подключите Claude Code или Codex в настройках" }), { status: 409 }));
  await expect(api.makeSummary(ep, "r1"))
    .rejects.toEqual(new ApiError(409, "Подключите Claude Code или Codex в настройках"));
});

test("живой режим: start/stop/ask/task", async () => {
  const f = okFetch();
  await api.liveStart(ep);
  await api.liveStop(ep);
  await api.liveAsk(ep, "кто за что?");
  await api.liveTask(ep, "релиз 2.0");
  expect(f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body]))
    .toEqual([
      ["http://h/live/start", "POST", undefined],
      ["http://h/live/stop", "POST", undefined],
      ["http://h/live/ask", "POST", JSON.stringify({ question: "кто за что?" })],
      ["http://h/live/task", "POST", JSON.stringify({ task: "релиз 2.0" })],
    ]);
});

test("openLiveEvents: токен в query, state и line разбираются, close закрывает", () => {
  const onState = vi.fn();
  const onLine = vi.fn();
  const live = api.openLiveEvents({ base: "http://h", token: "t k" }, { onState, onLine });
  const source = FakeEventSource.instances.at(-1)!;
  expect(source.url).toBe("http://h/live/events?token=t%20k");
  source.emit("state", { digest: "# Д", transcript: ["[00:01] Демьян: привет"], status: null });
  source.emit("line", { t: 1.5, speaker: "Демьян", text: "привет" }, 7);
  expect(onState).toHaveBeenCalledWith({ digest: "# Д", transcript: ["[00:01] Демьян: привет"], status: null });
  expect(onLine).toHaveBeenCalledWith({ t: 1.5, speaker: "Демьян", text: "привет" }, 7);
  source.emit("line", { t: 2, speaker: null, text: "без id" });
  expect(onLine).toHaveBeenLastCalledWith({ t: 2, speaker: null, text: "без id" }, null);
  live.close();
  expect(source.closed).toBe(true);
});

test("openLiveEvents: onError говорит, сдался ли браузер (409) или переподключается сам", () => {
  const onError = vi.fn();
  api.openLiveEvents({ base: "http://h", token: null }, { onError });
  const source = FakeEventSource.instances.at(-1)!;
  source.fail(false);
  expect(onError).toHaveBeenLastCalledWith(false);
  source.fail(true);
  expect(onError).toHaveBeenLastCalledWith(true);
});

test("setHfToken: POST /hf/token {token}; ответ — результат проверки", async () => {
  const f = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ ok: false, reason: "invalid_token", message: "Неверный токен" }), { status: 200 }));
  globalThis.fetch = f;
  expect(await api.setHfToken(ep, "hf_x")).toEqual({ ok: false, reason: "invalid_token", message: "Неверный токен" });
  expect(f.mock.calls[0]![0]).toBe("http://h/hf/token");
  expect(f.mock.calls[0]![1]).toMatchObject({ method: "POST", body: JSON.stringify({ token: "hf_x" }) });
  expect(f.mock.calls[0]![1].signal).toBeInstanceOf(AbortSignal);
});

test("setHfToken: не уложился в 15 секунд — понятная ошибка", async () => {
  vi.useFakeTimers();
  try {
    globalThis.fetch = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_, reject) => {
      init.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })) as unknown as typeof fetch;
    const pending = api.setHfToken(ep, "hf_x");
    const check = expect(pending).rejects.toThrow("Проверка не ответила за 15 секунд — попробуйте ещё раз");
    await vi.advanceTimersByTimeAsync(api.HF_TIMEOUT_MS);
    await check;
  } finally {
    vi.useRealTimers();
  }
});

test("hf: статус, удаление и перепроверка — свои адреса", async () => {
  const f = okFetch();
  await api.getHfStatus(ep);
  await api.deleteHfToken(ep);
  await api.recheckHf(ep);
  expect(f.mock.calls.map((c) => [c[0], (c[1] as RequestInit).method ?? "GET"])).toEqual([
    ["http://h/hf/status", "GET"], ["http://h/hf/token", "DELETE"], ["http://h/hf/check", "POST"],
  ]);
});

test("recheckHf: тот же предел 15 секунд, что и у сохранения", async () => {
  vi.useFakeTimers();
  try {
    globalThis.fetch = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_, reject) => {
      init.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })) as unknown as typeof fetch;
    const pending = api.recheckHf(ep);
    const check = expect(pending).rejects.toThrow("Проверка не ответила за 15 секунд — попробуйте ещё раз");
    await vi.advanceTimersByTimeAsync(api.HF_TIMEOUT_MS);
    await check;
  } finally {
    vi.useRealTimers();
  }
});

test("testDevice: POST /devices/test {kind, name}; null — системное", async () => {
  const f = okFetch();
  await api.testDevice(ep, "mic", "USB-микрофон");
  expect(f.mock.calls[0]![0]).toBe("http://h/devices/test");
  expect(f.mock.calls[0]![1]).toMatchObject({ method: "POST", body: JSON.stringify({ kind: "mic", name: "USB-микрофон" }) });
  await api.testDevice(ep, "output", null);
  expect(f.mock.calls[1]![1]).toMatchObject({ body: JSON.stringify({ kind: "output", name: null }) });
});
