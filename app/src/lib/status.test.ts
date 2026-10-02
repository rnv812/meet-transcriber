import { activeJobOf, failedRetranscribe, failureAdvice, isLiveRecording, stageLabel, statusOf } from "./status";

test("совет к известной ошибке расшифровки; неизвестной — нет", () => {
  expect(failureAdvice("RuntimeError: CUDA failed with error out of memory")).toMatch(/устройство «Процессор»/);
  expect(failureAdvice("OSError: [Errno 28] No space left on device")).toMatch(/не хватает места/);
  expect(failureAdvice("что-то совсем другое")).toBeNull();
});

const rec = (o = {}) => ({ id: "2026-09-30_16-04", path: "C:/r/2026-09-30_16-04",
  started_at: null, duration_s: 60, tracks: { sys: "x" }, has_transcript: false,
  has_voices: false, title: null, source: "record", ...o }) as any;
const job = (o = {}) => ({ id: "j", kind: "transcribe", folder: "C:/r/2026-09-30_16-04",
  state: "running", stage: "asr", label: null, done: 1, total: 2, note: null,
  result: null, error: null, ...o }) as any;

test("идущая расшифровка показывает ступень", () => {
  expect(statusOf(rec(), [job()], null)).toEqual(
    { kind: "running", stage: "asr", label: "Распознавание", done: 1, total: 2, job: job() });
});
test("название этапа: дорожки распознавания, известные этапы, иначе ярлык резидента", () => {
  expect(stageLabel({ stage: "asr", label: "распознавание собеседников", note: "sys" })).toBe("Распознавание собеседников");
  expect(stageLabel({ stage: "asr", label: null, note: "mic" })).toBe("Распознавание микрофона");
  expect(stageLabel({ stage: "diarize", label: "диаризация", note: "sys" })).toBe("Разделение на спикеров");
  expect(stageLabel({ stage: "llm", label: "модель думает", note: null })).toBe("Модель думает");
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
test("задачи модели (итоги, вопросы) не выдают себя за расшифровку", () => {
  const ready = rec({ has_transcript: true, transcript_at: 100 });
  const summary = job({ kind: "summary", state: "running", stage: "llm" });
  expect(statusOf(ready, [summary], null)).toEqual({ kind: "ready" });
  expect(activeJobOf(ready, [summary])).toBeNull();
  expect(statusOf(ready, [job({ kind: "ask", state: "queued" })], null)).toEqual({ kind: "ready" });
  expect(failedRetranscribe(ready, [job({ kind: "summary", state: "failed", finished_at: 200 })])).toBeNull();
  // Упавшие итоги нерасшифрованной записи — это не «расшифровка упала».
  expect(statusOf(rec(), [job({ kind: "summary", state: "failed", error: "x" })], null))
    .toEqual({ kind: "untranscribed" });
});

const liveSnap = (live: object) => ({ status: "idle", folder: null,
  live: { active: false, starting: false, stopping: false, folder: null, error: null, started_at: null, ...live } }) as any;

test("запись с ассистентом: папка живого режима — «идёт запись», пока он идёт или дописывается", () => {
  const win = ["C:", "r", "2026-09-30_16-04", ""].join(String.fromCharCode(92));
  expect(statusOf(rec(), [], liveSnap({ active: true, folder: win }))).toEqual({ kind: "recording" });
  expect(statusOf(rec(), [], liveSnap({ stopping: true, folder: "C:/r/2026-09-30_16-04" })))
    .toEqual({ kind: "recording" });
  // Чужая папка, ещё не начался (starting) или уже кончился — обычный статус.
  expect(statusOf(rec(), [], liveSnap({ active: true, folder: "C:/r/other" })).kind).toBe("untranscribed");
  expect(statusOf(rec(), [], liveSnap({ starting: true, folder: "C:/r/2026-09-30_16-04" })).kind).toBe("untranscribed");
  expect(statusOf(rec(), [], liveSnap({ folder: "C:/r/2026-09-30_16-04" })).kind).toBe("untranscribed");
  expect(isLiveRecording(rec(), liveSnap({ active: true, folder: "C:/r/2026-09-30_16-04" }))).toBe(true);
  expect(isLiveRecording(rec(), null)).toBe(false);
});

test("объединение: сборка звука — ступень «Объединение», неудача — повторить", () => {
  const r = rec({ id: "m", path: "C:/r/m", source: "merge", tracks: {} });
  expect(statusOf(r, [job({ kind: "merge", folder: "C:/r/m", stage: "merge", done: 0, total: 2 })], null))
    .toMatchObject({ kind: "running", stage: "merge", label: "Объединение", done: 0, total: 2 });
  expect(statusOf(r, [job({ kind: "merge", folder: "C:/r/m", state: "failed", error: "Исходная запись пропала" })], null))
    .toEqual({ kind: "failed", error: "Исходная запись пропала", retry: "transcribe" });
  expect(statusOf(r, [], null)).toEqual({ kind: "failed", error: "Объединение прервано", retry: "transcribe" });
});

test("объединение, сорванное посередине: дорожки лежат, но шаг «pending» — повторить сборку", () => {
  const r = rec({ id: "m", path: "C:/r/m", source: "merge", tracks: { sys: "s" },
                  merge: { parts: 2, state: "pending", deleted: false, kb_left: [] } });
  expect(statusOf(r, [], null)).toEqual({ kind: "failed", error: "Объединение прервано", retry: "transcribe" });
});

test("обрезка ожидания после звонка — «Обработка», пока резидент не закончит", () => {
  const snap = { status: "idle", folder: null, processing: ["C:\\r\\2026-09-30_16-04"] } as any;
  expect(statusOf(rec(), [job({ state: "queued" })], snap))
    .toEqual({ kind: "running", stage: "trim", label: "Обработка" });
  expect(statusOf(rec(), [], { ...snap, processing: [] })).toEqual({ kind: "untranscribed" });
});
