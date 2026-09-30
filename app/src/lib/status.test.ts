import { statusOf } from "./status";

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
  expect(statusOf(rec({ source: "import", tracks: {} }), [], null)).toEqual({ kind: "importing" });
  expect(statusOf(rec(), [], null)).toEqual({ kind: "untranscribed" });
});
test("путь папки сравнивается без учёта слэшей", () => {
  const win = ["C:", "r", "2026-09-30_16-04"].join(String.fromCharCode(92));
  expect(statusOf(rec(), [job({ folder: win })], null).kind).toBe("running");
});
