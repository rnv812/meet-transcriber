import { activeJobOf, failedRetranscribe, statusOf } from "./status";

const rec = (o = {}) => ({ id: "2026-09-30_16-04", path: "C:/r/2026-09-30_16-04",
  started_at: null, duration_s: 60, tracks: { sys: "x" }, has_transcript: false,
  has_voices: false, title: null, source: "record", ...o }) as any;
const job = (o = {}) => ({ id: "j", kind: "transcribe", folder: "C:/r/2026-09-30_16-04",
  state: "running", stage: "asr", label: null, done: 1, total: 2, note: null,
  result: null, error: null, ...o }) as any;

test("идущая расшифровка показывает ступень", () => {
  expect(statusOf(rec(), [job()], null)).toEqual(
    { kind: "running", stage: "asr", label: "Распознавание", done: 1, total: 2 });
});
test("упавший импорт без дорожки предлагает повторить импорт", () => {
  const r = rec({ id: "x_import", path: "C:/r/x_import", source: "import", tracks: {} });
  const s = statusOf(r, [job({ kind: "import", folder: "C:/r/x_import", state: "failed",
                               error: "исходный файл пропал" })], null);
  expect(s).toEqual({ kind: "failed", error: "исходный файл пропал", retry: "import" });
});
test("готовая запись", () => {
  expect(statusOf(rec({ has_transcript: true }), [], null)).toEqual({ kind: "ready" });
});
test("идёт запись этой папки", () => {
  expect(statusOf(rec(), [], { status: "recording", folder: "C:/r/2026-09-30_16-04" } as any))
    .toEqual({ kind: "recording" });
});
test("в очереди, импорт без дорожек, нерасшифрованная", () => {
  expect(statusOf(rec(), [job({ state: "queued" })], null)).toEqual({ kind: "queued" });
  expect(statusOf(rec(), [], null)).toEqual({ kind: "untranscribed" });
});
test("путь папки сравнивается без учёта слэшей", () => {
  const win = ["C:", "r", "2026-09-30_16-04"].join(String.fromCharCode(92));
  expect(statusOf(rec(), [job({ folder: win })], null).kind).toBe("running");
});

test("импорт без дорожки и без задачи (резидент перезапущен) — прерван", () => {
  const r = rec({ source: "import", tracks: {} });
  expect(statusOf(r, [], null)).toEqual({ kind: "failed", error: "Импорт прерван", retry: "import" });
});
test("импорт без дорожки с отменённой задачей — прерван", () => {
  const r = rec({ source: "import", tracks: {} });
  expect(statusOf(r, [job({ kind: "import", state: "cancelled" })], null))
    .toEqual({ kind: "failed", error: "Импорт прерван", retry: "import" });
});
test("импорт в работе — по-прежнему ступень, не ошибка", () => {
  const r = rec({ source: "import", tracks: {} });
  expect(statusOf(r, [job({ kind: "import", stage: "copy" })], null).kind).toBe("running");
});
test("activeJobOf: идущая важнее ждущей; чужие и законченные не считаются", () => {
  const q = job({ id: "q", state: "queued" });
  const r = job({ id: "r", state: "running" });
  expect(activeJobOf(rec(), [q, r])?.id).toBe("r");
  expect(activeJobOf(rec(), [q])?.id).toBe("q");
  expect(activeJobOf(rec(), [job({ state: "done" }), job({ folder: "C:/r/other" })])).toBeNull();
});
test("failedRetranscribe: последняя расшифровка упала позже транскрипта", () => {
  const ready = rec({ has_transcript: true, transcript_at: 100 });
  expect(failedRetranscribe(ready, [job({ state: "failed", finished_at: 200 })])?.state).toBe("failed");
  expect(failedRetranscribe(ready, [job({ state: "failed", finished_at: 50 })])).toBeNull();
  expect(failedRetranscribe(ready, [job({ state: "failed", finished_at: 200 }), job({ state: "done" })])).toBeNull();
  expect(failedRetranscribe(rec({ transcript_at: 100 }), [job({ state: "failed", finished_at: 200 })])).toBeNull();
});
