import { ApiError, deleteRecording, avatarUrl, saveTranscript } from "./api";

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
test("saveTranscript: PUT с телом-транскриптом", async () => {
  const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
  globalThis.fetch = f;
  const t = { version: 1, title: null, segments: [] };
  await saveTranscript({ base: "http://h", token: "t" }, "a b", t);
  expect(f.mock.calls[0]![0]).toBe("http://h/recordings/a%20b/transcript");
  expect(f.mock.calls[0]![1]).toMatchObject({ method: "PUT", body: JSON.stringify(t) });
});
