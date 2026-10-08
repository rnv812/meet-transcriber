import type { Job, Recording, Snapshot } from "./types";

export type RecStatus =
  | { kind: "recording" }
  | { kind: "queued" }
  | { kind: "running"; stage: string; label: string; done?: number; total?: number; job?: Job }
  | { kind: "failed"; error: string; retry: "transcribe" | "import" }
  | { kind: "ready" }
  | { kind: "untranscribed" }
  /**
   * Текст расшифровки готов, спикеров ещё нет (`transcript_phase: "text"`, Р4): `job` — задача, которая их
   * определяет: идёт, ждёт или только что закончилась (`done`: окончательная расшифровка записана, карточка
   * её перечитывает); null — задачи нет (отменили, упала, резидент перезапустился), `error` — почему упала.
   */
  | { kind: "text"; job: Job | null; error?: string };

const STAGES: Record<string, string> = {
  convert: "Подготовка звука",
  asr: "Распознавание",
  align: "Выравнивание",
  diarize: "Разделение на спикеров",
  voices: "Узнавание голосов",
  render: "Сохранение",
  copy: "Копирование",
  merge: "Объединение",
  model: "Загрузка модели",
};

/** Название этапа задачи для людей; у встречи из двух дорожек распознавание — у каждой своё. */
export function stageLabel(job: Pick<Job, "stage" | "label" | "note">): string {
  const stage = job.stage ?? "";
  if (stage === "asr" && job.note === "sys") return "Распознавание собеседников";
  if (stage === "asr" && job.note === "mic") return "Распознавание микрофона";
  if (stage === "voices" && job.note === "mic") return "Голоса микрофона";
  const known = STAGES[stage];
  if (known) return known;
  const raw = job.label ?? stage;
  return raw ? raw[0]!.toUpperCase() + raw.slice(1) : "";
}

const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

const INTERRUPTED = "Импорт прерван";
/** Обрезка ожидания после звонка: дорожки подменяются, запись пока не трогать. */
const PROCESSING: RecStatus = { kind: "running", stage: "trim", label: "Обработка" };
const RETRANSCRIBE_KINDS = ["transcribe", "import", "merge"];
const MERGE_INTERRUPTED = "Объединение прервано";

/**
 * Задачи расшифровки этой записи. `/jobs` отдаёт и задачи модели (итоги,
 * вопросы) над той же папкой — статус записи они не меняют.
 */
const jobsOf = (rec: Recording, jobs: Job[]) =>
  jobs.filter((j) => RETRANSCRIBE_KINDS.includes(j.kind) && norm(j.folder) === norm(rec.path));

/** Ждущая или идущая задача записи — её можно отменить. */
export function activeJobOf(rec: Recording, jobs: Job[]): Job | null {
  const mine = jobsOf(rec, jobs);
  return mine.find((j) => j.state === "running") ?? mine.find((j) => j.state === "queued") ?? null;
}

/**
 * Перерасшифровка готовой записи упала: транскрипт остался старый, а статус
 * «готово» об ошибке молчит. Возвращает упавшую задачу, если она последняя из
 * расшифровок этой записи и закончилась позже, чем записан транскрипт.
 */
export function failedRetranscribe(rec: Recording, jobs: Job[]): Job | null {
  if (!rec.has_transcript) return null;
  const last = jobsOf(rec, jobs).at(-1);
  if (last?.state !== "failed") return null;
  const failedAt = last.finished_at ?? last.created_at ?? null;
  const writtenAt = rec.transcript_at ?? null;
  if (failedAt !== null && writtenAt !== null && failedAt <= writtenAt) return null;
  return last;
}

/**
 * Эту запись пишет ассистент: живой режим идёт или дописывает дорожки
 * (`stopping`). Запуск (`starting`) ещё без папки, а после конца запись встаёт
 * в расшифровку обычным порядком.
 */
export function isLiveRecording(rec: Recording, snapshot: Snapshot | null): boolean {
  const live = snapshot?.live;
  return !!live?.folder && (live.active || live.stopping) && norm(live.folder) === norm(rec.path);
}

/**
 * `reloading` — карточка перечитывается после смены задач записи (её `rec` старее `jobs`): текст до спикеров
 * при только что законченной задаче — «заканчиваю», а не «прервалась». Без часов: перечитали, а текст всё ещё
 * без спикеров — окончательная не записалась. Список записей флага не знает — для него это прерывание.
 */
export function statusOf(rec: Recording, jobs: Job[], snapshot: Snapshot | null,
  { reloading = false }: { reloading?: boolean } = {}): RecStatus {
  const mine = jobsOf(rec, jobs);
  const noTracks = Object.keys(rec.tracks ?? {}).length === 0;
  const isImport = rec.source === "import" && noTracks;
  // Объединённая встреча, звук которой ещё не собран (или сборка не удалась —
  // тогда дорожки могут и лежать, но неполные: шаг остаётся «pending»).
  const isMerging = rec.source === "merge" && (noTracks || rec.merge?.state === "pending");

  if (snapshot?.status === "recording" && snapshot.folder && norm(snapshot.folder) === norm(rec.path))
    return { kind: "recording" };
  if (isLiveRecording(rec, snapshot)) return { kind: "recording" };
  if (snapshot?.processing?.some((p) => norm(p) === norm(rec.path))) return PROCESSING;
  if (rec.transcript_phase === "text") {
    const active = activeJobOf(rec, jobs);
    if (active) return { kind: "text", job: active };
    const last = mine.at(-1);
    // Задача кончилась, а карточка ещё не перечитана: спикеры уже есть — не «прервалась».
    if (last?.state === "done" && reloading) return { kind: "text", job: last };
    return last?.state === "failed" && last.error ? { kind: "text", job: null, error: last.error }
      : { kind: "text", job: null };
  }
  if (mine.some((j) => j.state === "queued")) return { kind: "queued" };
  const running = mine.find((j) => j.state === "running");
  if (running) {
    const stage = running.stage ?? "";
    const out: RecStatus = {
      kind: "running",
      stage,
      label: STAGES[stage] ?? running.label ?? stage,
      job: running,
    };
    if (running.done !== null) out.done = running.done;
    if (running.total !== null) out.total = running.total;
    return out;
  }
  const last = mine[mine.length - 1];
  if (last?.state === "failed" && !rec.has_transcript)
    return { kind: "failed", error: last.error ?? "", retry: isImport ? "import" : "transcribe" };
  if (rec.has_transcript) return { kind: "ready" };
  // Импорт без дорожки и без живой задачи: задачу отменили или резидент
  // перезапустился (задачи живут только в его памяти) — копия не доедет.
  if (isImport) return { kind: "failed", error: last?.error || INTERRUPTED, retry: "import" };
  // «Расшифровать» на ней резидент превращает в повтор сборки звука.
  if (isMerging) return { kind: "failed", error: last?.error || MERGE_INTERRUPTED, retry: "transcribe" };
  return { kind: "untranscribed" };
}

/**
 * Что делать, если расшифровка упала с известной ошибкой: короткий совет к
 * тексту ошибки. Неизвестная ошибка — null (остаются «Повторить» и журнал).
 */
export function failureAdvice(error: string): string | null {
  const e = error.toLowerCase();
  if (/out of memory|cuda.*memory|cublas|cudnn/.test(e))
    return "Не хватило памяти видеокарты. Закройте программы, которые её используют, или выберите в "
      + "«Настройки → Распознавание» устройство «Процессор» и повторите.";
  if (/движок расшифровки не установлен|no module named/.test(e))
    return "Движок расшифровки не установлен или повреждён: установите его заново в «Настройки → Движок и модели».";
  if (/no space left|недостаточно места|errno 28/.test(e))
    return "На диске не хватает места: освободите несколько гигабайт и повторите.";
  if (/huggingface|hf_token|401|403/.test(e))
    return "Нет доступа к модели Hugging Face: проверьте токен в «Настройки → Спикеры».";
  return null;
}

/** Задачи модели (итоги, вопросы) над папкой записи — в порядке постановки. */
export function modelJobsOf(folder: string, jobs: Job[], kind: "summary" | "ask"): Job[] {
  return jobs.filter((j) => j.kind === kind && norm(j.folder) === norm(folder));
}

export const isActiveJob = (j: Job): boolean => j.state === "queued" || j.state === "running";
