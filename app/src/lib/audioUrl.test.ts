/**
 * Адрес звука для <audio> — общий у плеера карточки, образцов, «Голосов» и «Моего голоса».
 * На macOS — всегда WebM (`format=webm`), на Windows — без формата (Ogg как есть).
 */
import { AUDIO_FORMAT, audioFormat, audioProblem, audioUrl } from "./audioUrl";

const ep = { base: "http://127.0.0.1:8766", token: "секрет" };

afterEach(() => { vi.unstubAllGlobals(); });

test("формат решается по платформе: macOS — WebM, Windows — без формата", () => {
  expect(audioFormat("macos")).toBe("webm");
  expect(audioFormat("windows")).toBeNull();
  // jsdom — это Windows.
  expect(AUDIO_FORMAT).toBeNull();
});

test("на macOS адрес просит WebM, на Windows — нет", () => {
  const mac = new URL(audioUrl(ep, "r 1", "playback", { format: "webm" }));
  expect(mac.pathname).toBe("/recordings/r%201/audio");
  expect(mac.searchParams.get("track")).toBe("playback");
  expect(mac.searchParams.get("format")).toBe("webm");
  expect(mac.searchParams.get("token")).toBe("секрет");
  const win = new URL(audioUrl(ep, "r1", "mic", { format: null }));
  expect(win.searchParams.has("format")).toBe(false);
  // По умолчанию — формат этого окна.
  expect(audioUrl(ep, "r1", "sys")).toBe(audioUrl(ep, "r1", "sys", { format: AUDIO_FORMAT }));
});

test("кусок дорожки — фрагментом #t=начало,конец (образцы голосов)", () => {
  expect(audioUrl({ base: "/api", token: null }, "r1", "sys", { span: [12, 16.2], format: null }))
    .toBe("/api/recordings/r1/audio?track=sys#t=12,16.2");
  expect(audioUrl({ base: "/api", token: null }, "r1", "mic", { span: [3, 8], format: "webm" }))
    .toBe("/api/recordings/r1/audio?track=mic&format=webm#t=3,8");
});

test("причина сбоя: текст ошибки резидента; запрос — один байт, без фрагмента", async () => {
  const fetch = vi.fn(async () => new Response(
    JSON.stringify({ error: "ffmpeg не найден — дорожки записи не перепакованы" }),
    { status: 503, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  await expect(audioProblem("/api/recordings/r1/audio?track=sys&format=webm#t=3,8"))
    .resolves.toBe("ffmpeg не найден — дорожки записи не перепакованы");
  expect(fetch).toHaveBeenCalledWith("/api/recordings/r1/audio?track=sys&format=webm",
    expect.objectContaining({ headers: { Range: "bytes=0-0" } }));
});

test("причина сбоя: звук отдаётся или резидент не ответил — причины нет", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("x", { status: 206 })));
  await expect(audioProblem("/api/a")).resolves.toBeNull();
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
  await expect(audioProblem("/api/a")).resolves.toBeNull();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>", { status: 500 })));
  await expect(audioProblem("/api/a")).resolves.toBe("ошибка 500");
});
