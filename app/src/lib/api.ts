/**
 * Клиент control API резидента.
 *
 * * в приложении — Rust отдаёт порт с токеном командой `endpoint`;
 * * в браузере (dev) — Vite проксирует `/api` и сам подставляет токен.
 *
 * IMPORTANT: токен для SSE/аудио/аватаров уходит в query — EventSource и <audio>
 * не умеют ставить заголовки. Сервер это поддерживает намеренно (см. control.py).
 */

import { inTauri, invoke } from "./shell";
import type {
  AnalysisState, AssistantInfo, BusEvent, ImproveApplyRequest, ImproveApplyResult, ImproveState, CommandResult, ExportPreview, Job, KbExport, LiveDraft, LiveLine, LiveQa, LiveQaPartial, LiveQuick, LiveState, LiveStatus, LiveVoices, Person, PersonCard, ProfilesRemovedNotice,
  Category, OwnerVoiceStatus, ProviderCheck, QaItem, Recording, Sample, SearchItem, Snapshot, SpeakerOpInput, SpeakersView, RelabelRequest, SplitApply, SplitPreview,
  SplitRequest, SplitStatus, ThresholdPlan, SplitTurnRequest, RediarizeParams, RediarizePreview, Summary,
  TextFixRequest, TextFixResult, TextPreview, TitleSource, TitleSuggestion, Transcript,
} from "./types";

export type {
  Analysis, AnalysisChapter, AnalysisFeature, AnalysisInsight, AnalysisState, AnalysisStateName, InsightKind,
  ImproveApplied, ImproveGroup, ImproveKind, ImproveProposal, ImproveState, ImproveStateName, PhraseType, TitleSource,
  TitleSuggestion, Category, RecordingCategory,
} from "./types";

export type Endpoint = {
  /** Без слэша на конце: "http://127.0.0.1:53127" или "/api". */
  base: string;
  /** null в dev: токен подставляет прокси. */
  token: string | null;
};

export class NoResidentError extends Error {}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Не найден — NoResidentError: штатное состояние, а не сбой. */
export async function resolveEndpoint(): Promise<Endpoint> {
  if (!inTauri()) return { base: "/api", token: null };
  const data = await invoke<{ port: number; token: string } | null>("endpoint");
  if (!data?.port) throw new NoResidentError("служба записи не запущена");
  return { base: `http://127.0.0.1:${data.port}`, token: data.token };
}

function auth(ep: Endpoint): Record<string, string> {
  return ep.token ? { Authorization: `Bearer ${ep.token}` } : {};
}

async function request(ep: Endpoint, path: string, init: RequestInit, contentType: string | null): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${ep.base}${path}`, {
      ...init,
      headers: { ...auth(ep), ...(contentType ? { "Content-Type": contentType } : {}) },
    });
  } catch (cause) {
    throw new NoResidentError(`служба записи не отвечает: ${String(cause)}`);
  }
  if (response.status === 401) throw new ApiError(401, "неверный токен службы записи");
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    let message = text;
    try {
      const parsed = JSON.parse(text) as { error?: unknown };
      if (typeof parsed.error === "string") message = parsed.error;
    } catch {
      /* не JSON — оставляем текст как есть */
    }
    throw new ApiError(response.status, message || `служба записи ответила ${response.status}`);
  }
  return response;
}

async function json<T>(ep: Endpoint, path: string, init: RequestInit = {}): Promise<T> {
  return (await (await request(ep, path, init, "application/json")).json()) as T;
}

const body = (method: string, data: unknown): RequestInit => ({ method, body: JSON.stringify(data) });
const enc = encodeURIComponent;

export const getState = (ep: Endpoint) => json<Snapshot>(ep, "/state");

// --- записи ----------------------------------------------------------------

/** `categories` — фильтр по категориям (id и "_none" — «Без категории»); резидент применяет его до лимита списка. */
export function getRecordings(ep: Endpoint, q?: string, categories?: string[]) {
  const params = [q ? `q=${enc(q)}` : "", categories?.length ? `categories=${enc(categories.join(","))}` : ""]
    .filter(Boolean).join("&");
  return json<{ root: string; items: Recording[] }>(ep, `/recordings${params ? `?${params}` : ""}`);
}
/** Несколько самых свежих записей (папки сортируются как даты): панель записи в строке меню. */
export const getRecentRecordings = (ep: Endpoint, limit: number) =>
  json<{ root: string; items: Recording[] }>(ep, `/recordings?limit=${limit}`);
/** Поиск по тексту встреч и названиям (правила — lib/search.ts): записи с фрагментами. */
export const searchLibrary = (ep: Endpoint, q: string, signal?: AbortSignal, categories?: string[]) =>
  json<{ items: SearchItem[] }>(ep, `/search?q=${enc(q)}${categories?.length
    ? `&categories=${enc(categories.join(","))}` : ""}`, { signal });
export const getRecording = (ep: Endpoint, id: string) =>
  json<Recording & { transcript: Transcript | null }>(ep, `/recordings/${enc(id)}`);
/** Название записи не длиннее (резидент обрезает так же). */
export const TITLE_MAX = 200;
/**
 * `title: null` (или пустое) — вернуть автоматическое название. `title_source: "ai"` —
 * человек принял предложенное моделью (бейдж «ИИ» остаётся); без него название — «своё».
 */
export const patchRecording = (ep: Endpoint, id: string,
  patch: { title: string | null; title_source?: Extract<TitleSource, "ai"> }) =>
  json<Recording>(ep, `/recordings/${enc(id)}`, body("PATCH", patch));
/** Категория, выбранная человеком: id из настроек или null — «Без категории» (модель её больше не ставит). */
export const setRecordingCategory = (ep: Endpoint, id: string, category: string | null) =>
  json<Recording>(ep, `/recordings/${enc(id)}/category`, body("PUT", { id: category }));
/**
 * Категории: нынешний и стандартный списки, сколько встреч в каждой категории и без категории —
 * по всей библиотеке или, с запросом поиска `q`, среди найденных (`scope`).
 */
export type CategoriesInfo = {
  categories: Category[]; defaults: Category[]; counts: Record<string, number>; none: number;
  scope?: "library" | "search";
};
export const getCategoriesInfo = (ep: Endpoint, q?: string) =>
  json<CategoriesInfo>(ep, `/categories${q ? `?q=${enc(q)}` : ""}`);
/** Категории из ответа `GET /settings`: битые записи отбрасываются. */
export function categoriesOf(settings: Record<string, unknown> | null | undefined): Category[] {
  const raw = settings?.categories;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((c) => {
    const item = c as Partial<Category> | null;
    return item && typeof item.id === "string" && typeof item.name === "string"
      ? [{ id: item.id, name: item.name, color: typeof item.color === "string" ? item.color : "#9aa0a6",
        description: typeof item.description === "string" ? item.description : "" }]
      : [];
  });
}
export const deleteRecording = (ep: Endpoint, id: string) =>
  json<{ ok: boolean }>(ep, `/recordings/${enc(id)}`, { method: "DELETE" });
export const importFile = (ep: Endpoint, path: string) =>
  json<{ recording: string; job: Job }>(ep, "/recordings/import", body("POST", { path }));
/**
 * Объединить записи одной встречи: резидент создаёт новую запись, собирает звук,
 * расшифровывает её заново и затем удаляет исходные (если не `keepOriginals`).
 */
export const mergeRecordings = (ep: Endpoint, ids: string[], keepOriginals = false) =>
  json<{ recording: string; job: Job }>(ep, "/recordings/merge",
    body("POST", { ids, keep_originals: keepOriginals }));
export const transcribe = (ep: Endpoint, id: string) =>
  json<Job>(ep, `/recordings/${enc(id)}/transcribe`, body("POST", {}));

// --- спикеры встречи (панель «Спикеры») --------------------------------------

export const getSpeakers = (ep: Endpoint, id: string) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers`);
/**
 * Набор правок одним шагом истории; `remember` — запомнить голос строки в базе,
 * `rememberOwner` — «Запомнить мой голос»: ваш голос из этой встречи — образцом владельца;
 * `ownerCandidate` — вы подтвердили голос-кандидат («это точно я»), не узнанный образцом.
 */
export const applySpeakers = (ep: Endpoint, id: string, ops: SpeakerOpInput[], remember: Record<string, boolean>,
  rememberOwner = false, ownerCandidate = false) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers/apply`,
    body("POST", rememberOwner
      ? { ops, remember, remember_owner: true, ...(ownerCandidate ? { owner_candidate: true } : {}) }
      : { ops, remember }));
/**
 * Реплики (номера сегментов) — другому спикеру одним шагом истории. `labels` и
 * `count` — как их видит окно: расшифровка изменилась — 409, а не правка не тех реплик.
 * `to`: спикер встречи или имя человека; null — новый «Спикер N».
 */
export const relabelTurns = (ep: Endpoint, id: string, req: RelabelRequest) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers/relabel`, body("POST", req));
/** «Разделить реплику здесь»: вторая часть (и остаток реплики) — другому спикеру. */
export const splitTurn = (ep: Endpoint, id: string, req: SplitTurnRequest) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers/split-turn`, body("POST", req));
/** «Исправить…»: сколько раз слово или фраза встречается во встрече и когда звучит выбранное место. */
export const previewTextFix = (ep: Endpoint, id: string, req: { find: string; segment?: number; offset?: number }) =>
  json<TextPreview>(ep, `/recordings/${enc(id)}/text/preview`, body("POST", { ...req, whole_word: true }));
/** Заменить одним шагом истории встречи (его отменяет «Отменить» спикеров) и, если просили, — в термины. */
export const applyTextFix = (ep: Endpoint, id: string, req: TextFixRequest) =>
  json<TextFixResult>(ep, `/recordings/${enc(id)}/text/apply`, body("POST", { ...req, whole_word: true }));
/** «Переразделить на спикеров»: задача только диаризации (без распознавания). */
export const rediarize = (ep: Endpoint, id: string, params: RediarizeParams) =>
  json<{ job: Job }>(ep, `/recordings/${enc(id)}/speakers/rediarize`, body("POST", params));
/** Посчитанное разделение для предпросмотра; нет — 404. */
export const getRediarized = (ep: Endpoint, id: string) =>
  json<RediarizePreview>(ep, `/recordings/${enc(id)}/speakers/rediarize`);
export const applyRediarized = (ep: Endpoint, id: string) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers/rediarize/apply`, body("POST", {}));
export const discardRediarized = (ep: Endpoint, id: string) =>
  json<{ ok: boolean }>(ep, `/recordings/${enc(id)}/speakers/rediarize`, { method: "DELETE" });
/** «Разделить спикера»: голоса его реплик готовы? Нет — резидент ставит задачу (`job`). */
export const prepareSplit = (ep: Endpoint, id: string, label: string) =>
  json<SplitStatus>(ep, `/recordings/${enc(id)}/speakers/split/prepare`, body("POST", { label }));
export const previewSplit = (ep: Endpoint, id: string, req: SplitRequest) =>
  json<SplitPreview>(ep, `/recordings/${enc(id)}/speakers/split/preview`, body("POST", req));
export const applySplit = (ep: Endpoint, id: string, req: SplitApply) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers/split/apply`, body("POST", req));
/** Что сделает порог узнавания голоса с именами спикеров встречи (без записи). */
export const thresholdPlan = (ep: Endpoint, id: string, value: number) =>
  json<ThresholdPlan>(ep, `/recordings/${enc(id)}/speakers/threshold`, body("POST", { value }));
export const applyThreshold = (ep: Endpoint, id: string, value: number) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers/threshold/apply`, body("POST", { value }));
/** Отменить последний шаг; `expectStep` — только если последний именно он (иначе 409, «уже не последнее»). */
export const undoSpeakers = (ep: Endpoint, id: string, expectStep?: string) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers/undo`,
    body("POST", expectStep ? { expect_step: expectStep } : {}));
export const redoSpeakers = (ep: Endpoint, id: string) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers/redo`, body("POST", {}));
/** К состоянию сразу после шага; null — до всех правок. */
export const revertSpeakers = (ep: Endpoint, id: string, toStepId: string | null) =>
  json<SpeakersView>(ep, `/recordings/${enc(id)}/speakers/revert`, body("POST", { to_step_id: toStepId }));
export const exportRecording = (ep: Endpoint, id: string, format: string) =>
  json<{ filename: string; content: string }>(ep, `/recordings/${enc(id)}/export?format=${enc(format)}`);

export const getJobs = (ep: Endpoint) => json<{ items: Job[] }>(ep, "/jobs");
export const cancelJob = (ep: Endpoint, id: string) =>
  json<{ ok: boolean }>(ep, `/jobs/${enc(id)}`, { method: "DELETE" });

export const recordingCommand = (ep: Endpoint, command: "start" | "stop" | "cancel") =>
  json<CommandResult>(ep, `/recording/${command}`, { method: "POST" });
export const setAutoRecord = (ep: Endpoint, enabled: boolean) =>
  json<Snapshot>(ep, "/auto-record", body("POST", { enabled }));

// --- люди ------------------------------------------------------------------

export const getPeople = (ep: Endpoint) => json<{ items: Person[] }>(ep, "/voices");
export const getPerson = (ep: Endpoint, name: string) => json<PersonCard>(ep, `/voices/${enc(name)}`);
export const getSample = (ep: Endpoint, name: string) => json<Sample>(ep, `/voices/${enc(name)}/sample`);
export async function putAvatar(ep: Endpoint, name: string, blob: Blob): Promise<void> {
  await request(ep, `/voices/${enc(name)}/avatar`, { method: "PUT", body: blob },
    blob.type || "application/octet-stream");
}
export const deleteAvatar = (ep: Endpoint, name: string) =>
  json<{ ok: boolean }>(ep, `/voices/${enc(name)}/avatar`, { method: "DELETE" });
export const renamePerson = (ep: Endpoint, name: string, to: string) =>
  json<unknown>(ep, `/voices/${enc(name)}/rename`, body("POST", { to }));
export const mergePerson = (ep: Endpoint, name: string, into: string) =>
  json<unknown>(ep, `/voices/${enc(name)}/merge`, body("POST", { into }));
export const deletePerson = (ep: Endpoint, name: string) =>
  json<unknown>(ep, `/voices/${enc(name)}`, { method: "DELETE" });

// Профили людей убраны в 0.3.2: строка об уборке в «Голосах» и «Понятно».
export const getProfilesRemoved = (ep: Endpoint) =>
  json<{ notice: ProfilesRemovedNotice | null }>(ep, "/notices/profiles-removed");
export const dismissProfilesRemoved = (ep: Endpoint) =>
  json<{ ok: boolean }>(ep, "/notices/profiles-removed", { method: "DELETE" });

// --- настройки и сервис -----------------------------------------------------

export type Processes = {
  available: boolean;
  running?: string[];
  selected?: string[];
  known?: string[];
  error?: string;
};
export type DeviceItem = { name: string; default: boolean };
export type Devices = {
  available: boolean;
  system?: { name: string; rate: number };
  mic?: { name: string; rate: number };
  /** Микрофоны и устройства вывода WASAPI; нет у старых резидентов. */
  inputs?: DeviceItem[];
  outputs?: DeviceItem[];
  /** Можно ли выбрать устройство (recording.mic_device / output_device). */
  pinning: boolean;
  error?: string;
};
export type DeviceKind = "mic" | "output";
export type DeviceCheck = { ok: boolean; peak: number; device: string; fallback?: boolean };
export type EngineState = {
  installed: boolean;
  missing: string[];
  /** `optional` — без него движок работает (GigaAM: тогда распознаёт Whisper); `note` — почему его нет. */
  components: { module: string; title: string; installed: boolean; optional?: boolean; note?: string }[];
  gpu: { available: boolean; name: string | null };
  flavor: "cuda" | "cpu" | "mac";
  download_gb: number;
  python: string;
  target: string;
  ffmpeg: boolean;
  /** Устройство распознавания по сохранённой настройке (с учётом «Авто»). */
  device?: "cuda" | "cpu";
  /** Годится ли видеокарта для распознавания (карта видна, движок не для процессора, есть библиотеки CUDA);
   *  нет у старых резидентов. */
  cuda_ok?: boolean;
  /** Почему видеокарта не годится: «видеокарта NVIDIA не найдена», «установлен движок для процессора», … */
  cuda_reason?: string | null;
};
export type Model = {
  id: string;
  kind: "asr" | "diarization" | "align";
  /** Движок модели распознавания: "faster-whisper" или "gigaam" (id с префиксом «gigaam/»). */
  backend?: string;
  title: string;
  note: string;
  size_gb: number;
  gated?: boolean;
  recommended?: boolean;
  downloaded: boolean;
  size_on_disk: number;
  selected: boolean;
  blocked: boolean;
  /** Скачанную модель можно удалить (GigaAM: в папке приложения, не в общем кэше HF). */
  removable?: boolean;
};
export type ModelsState = {
  items: Model[];
  cache: string;
  token: boolean;
  selected: string | null;
  can_download: boolean;
  /** GigaAM качает свой пакет — он приходит с движком. Нет у старых резидентов. */
  can_download_gigaam?: boolean;
  /** Папка моделей GigaAM (внутри папки данных приложения). */
  gigaam_cache?: string;
  /** Необязательная установка GigaAM не прошла: «GigaAM не установилась: … — используется Whisper». */
  gigaam_install_error?: string | null;
};

/** Префикс id моделей GigaAM в каталоге: «gigaam/v3_e2e_rnnt». */
export const GIGAAM_PREFIX = "gigaam/";
export const isGigaam = (m: Pick<Model, "id">) => m.id.startsWith(GIGAAM_PREFIX);
/** Можно ли скачать модель: у GigaAM свой загрузчик. */
export const canDownloadModel = (state: ModelsState, m: Model) =>
  isGigaam(m) ? Boolean(state.can_download_gigaam) : state.can_download;

export const getSettings = (ep: Endpoint) => json<Record<string, unknown>>(ep, "/settings");
export const patchSettings = (ep: Endpoint, updates: Record<string, unknown>) =>
  json<{ settings: Record<string, unknown>; restart_required: string[] }>(ep, "/settings", body("PATCH", updates));
export type Hotwords = { text: string; budget: number; used: number };
export const getHotwords = (ep: Endpoint) => json<Hotwords>(ep, "/hotwords");
export const putHotwords = (ep: Endpoint, text: string) =>
  json<Hotwords>(ep, "/hotwords", body("PUT", { text }));
/** Убрать один термин (отмена «Добавлено в термины»): остальной список резидент не трогает. */
export const removeHotword = (ep: Endpoint, term: string) =>
  json<Hotwords>(ep, "/hotwords/remove", body("POST", { term }));
export const getEngine = (ep: Endpoint) => json<EngineState>(ep, "/engine");
/** Модели Meet, оставшиеся в общем кэше Hugging Face после переезда в свою папку. */
export type StorageLeftovers = { cache: string; repos: { id: string; bytes: number }[]; bytes: number };
/** `GET /storage`: где движок и модели. Переносит их оболочка (`storage_move`). */
export type StorageInfo = {
  /** Выбранная папка; null — по умолчанию (папка данных и общий кэш HF). */
  root: string | null;
  custom: boolean;
  home: string;
  engine_dir: string;
  models_dir: string;
  /** Кэш HF, которым пользуется Meet (общий или свой `models/hf`). */
  hf_cache: string;
  shared_cache: string;
  missing: string | null;
  models_bytes: number;
  moving: boolean;
  /** Почему переносить сейчас нельзя («идёт запись»); null — можно. */
  busy: string | null;
  leftovers: StorageLeftovers | null;
};
export const getStorage = (ep: Endpoint) => json<StorageInfo>(ep, "/storage");
/** Ответ на вопрос после переезда: удалить модели Meet из общего кэша HF или оставить. */
export const answerLeftovers = (ep: Endpoint, remove: boolean) =>
  json<{ ok: boolean; removed: string[]; error?: string }>(ep, "/storage/leftovers", body("POST", { delete: remove }));
export const getModels = (ep: Endpoint) => json<ModelsState>(ep, "/models");
export const downloadModel = (ep: Endpoint, id: string) =>
  json<Job>(ep, "/models/download", body("POST", { id }));
/** Удалить скачанную модель GigaAM. */
export const removeModel = (ep: Endpoint, id: string) =>
  json<{ ok: boolean; error?: string }>(ep, "/models/remove", body("POST", { id }));
export const getDiagnostics = (ep: Endpoint, lines = 200) =>
  json<Record<string, unknown>>(ep, `/diagnostics?lines=${lines}`);
export const getDevices = (ep: Endpoint) => json<Devices>(ep, "/devices");
/** ~2 с записи с устройства (null — системное) → пиковый уровень; 409 во время записи. */
export const testDevice = (ep: Endpoint, kind: DeviceKind, name: string | null) =>
  json<DeviceCheck>(ep, "/devices/test", body("POST", { kind, name }));
export const getProcesses = (ep: Endpoint) => json<Processes>(ep, "/processes");

// --- образец вашего голоса ------------------------------------------------------

/** Что записано, ход записи и можно ли записать. */
export const getOwnerVoice = (ep: Endpoint) => json<OwnerVoiceStatus>(ep, "/owner-voice");
/** Записать ~25 с с микрофона (null — системный) и разобрать; 409 во время записи встречи. */
export const recordOwnerVoice = (ep: Endpoint, device: string | null) =>
  json<OwnerVoiceStatus>(ep, "/owner-voice/record", body("POST", { device }));
export const deleteOwnerVoice = (ep: Endpoint, id: string) =>
  json<OwnerVoiceStatus>(ep, `/owner-voice/${enc(id)}`, { method: "DELETE" });
/** «Найти по прошлым встречам»: задача ищет ваш голос; найденное — только предложение. */
export const deriveOwnerVoice = (ep: Endpoint) =>
  json<OwnerVoiceStatus>(ep, "/owner-voice/derive", body("POST", {}));
/** Ответ на найденный голос: `true` — «Да, это я» (станет образцом), `false` — «Нет». */
export const answerOwnerSuggestion = (ep: Endpoint, accept: boolean) =>
  json<OwnerVoiceStatus>(ep, "/owner-voice/suggestion", body("POST", { accept }));


// --- Hugging Face ---------------------------------------------------------------

export type HfCheck = {
  ok: boolean;
  reason: "ok" | "invalid_token" | "terms_not_accepted" | "network";
  message: string;
};
/** Токен в ответ не уходит никогда — только где он лежит и итог последней проверки. */
export type HfStatus = {
  configured: boolean;
  source: "keyring" | "env" | "config" | null;
  check: HfCheck | null;
};

/** Резидент проверяет токен до 12 с; ждём с запасом. */
export const HF_TIMEOUT_MS = 15_000;

export const getHfStatus = (ep: Endpoint) => json<HfStatus>(ep, "/hf/status");

/** Запрос с проверкой на huggingface.co: не дольше HF_TIMEOUT_MS. */
async function hfCheck(ep: Endpoint, path: string, data: unknown): Promise<HfCheck> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HF_TIMEOUT_MS);
  try {
    return await json<HfCheck>(ep, path, { ...body("POST", data), signal: controller.signal });
  } catch (cause) {
    if (controller.signal.aborted) throw new Error("Проверка не ответила за 15 секунд — попробуйте ещё раз");
    throw cause;
  } finally {
    clearTimeout(timer);
  }
}

/** Проверить токен и сохранить, только если доступ есть. */
export const setHfToken = (ep: Endpoint, token: string) => hfCheck(ep, "/hf/token", { token });
export const deleteHfToken = (ep: Endpoint) => json<HfStatus>(ep, "/hf/token", { method: "DELETE" });
/** Перепроверить сохранённый токен (условия модели могли принять с тех пор). */
export const recheckHf = (ep: Endpoint) => hfCheck(ep, "/hf/check", {});

// --- ассистент: итоги и вопросы ----------------------------------------------

/** Итоги задачей (kind "summary"); 409 — нет провайдера или идёт расшифровка. */
export const makeSummary = (ep: Endpoint, id: string) =>
  json<Job>(ep, `/recordings/${enc(id)}/summary`, { method: "POST" });
/** Итогов нет — ApiError 404. */
export const getSummary = (ep: Endpoint, id: string) => json<Summary>(ep, `/recordings/${enc(id)}/summary`);
/** Черновик итогов из живого режима (сводка ассистента во время встречи); нет — null. */
export async function getLiveDraft(ep: Endpoint, id: string): Promise<LiveDraft | null> {
  try {
    return await json<LiveDraft>(ep, `/recordings/${enc(id)}/live-draft`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}
/** Прошлые вопросы (`qa.jsonl`; новые задаёт `meet ask` или агент во вкладке «Агент»). */
export const getQa = (ep: Endpoint, id: string) => json<{ items: QaItem[] }>(ep, `/recordings/${enc(id)}/qa`);
/**
 * Что получит агент во вкладке «Агент» (без записи файлов). `live` — точной
 * расшифровки ещё нет, агент получит ленту живого режима; пустой список —
 * агенту пока нечего дать.
 */
/** `sessions` — агенты, уже работавшие в папке встречи (нет поля — ни одного). */
export const getAgentContext = (ep: Endpoint, id: string) =>
  json<{ files: string[]; live: boolean; sessions?: string[] }>(ep, `/recordings/${enc(id)}/agent-context`);
export const getAssistant = (ep: Endpoint) => json<AssistantInfo>(ep, "/assistant");
/** Короткий вызов модели — до полутора минут. */
export const checkProvider = (ep: Endpoint, provider: string) =>
  json<ProviderCheck>(ep, "/assistant/check", body("POST", { provider }));

// --- анализ встречи и название ---------------------------------------------------

/** Состояние анализа и сама разметка (для M3/M4 — `analysis`). */
export const getAnalysis = (ep: Endpoint, id: string) =>
  json<AnalysisState>(ep, `/recordings/${enc(id)}/analysis`);
/** «Переанализировать»: задача (kind "analyze"); уже ждёт или идёт — та же. 409 — нет модели или идёт расшифровка. */
export const runAnalysis = (ep: Endpoint, id: string) =>
  json<Job>(ep, `/recordings/${enc(id)}/analysis`, { method: "POST" });
/**
 * Ответ на разовое предложение включить авто-анализ (обновившимся с 0.2.x,
 * `analysis.consent`). «Включить» ставит и анализ этой записи — по правилам
 * автоматического. → секция `analysis` настроек.
 */
export const answerAnalysisOffer = (ep: Endpoint, id: string, answer: "granted" | "declined") =>
  json<{ analysis: Record<string, unknown> }>(ep, `/recordings/${enc(id)}/analysis/consent`, body("POST", { answer }));
/** «Предложить название»: только предложение (до пары минут, если нужен вызов модели). */
export const suggestTitle = (ep: Endpoint, id: string) =>
  json<TitleSuggestion>(ep, `/recordings/${enc(id)}/title/suggest`, { method: "POST" });

// --- «Улучшить расшифровку» ------------------------------------------------------

/** Состояние улучшения: задача, готовое предложение (группы замен) и подсказка после GigaAM. */
export const getImprove = (ep: Endpoint, id: string) =>
  json<ImproveState>(ep, `/recordings/${enc(id)}/improve`);
/** Поставить задачу (kind "improve"); уже ждёт или идёт — та же. 409 — нет модели или идёт расшифровка. */
export const runImprove = (ep: Endpoint, id: string) =>
  json<Job>(ep, `/recordings/${enc(id)}/improve`, { method: "POST" });
/** Выбранные группы — одним шагом истории встречи; по желанию — правилами и в термины. */
export const applyImprove = (ep: Endpoint, id: string, req: ImproveApplyRequest) =>
  json<ImproveApplyResult>(ep, `/recordings/${enc(id)}/improve/apply`, body("POST", req));
/** Подсказку «Похоже, в тексте есть термины латиницей» больше не показывать. */
export const dismissImproveHint = (ep: Endpoint, id: string) =>
  json<{ ok: boolean }>(ep, `/recordings/${enc(id)}/improve/dismiss`, { method: "POST" });

// --- база знаний ----------------------------------------------------------------

/** Выгрузить встречу в базу знаний; 400 — папка для встреч не задана или шаблон негоден. */
export const kbExport = (ep: Endpoint, id: string) =>
  json<KbExport>(ep, `/recordings/${enc(id)}/kb-export`, { method: "POST" });

/** Пример папки и файлов по (несохранённым) значениям раздела «Экспорт встреч». */
export function getExportPreview(ep: Endpoint, values: Record<string, string | boolean>) {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) q.set(key, String(value));
  return json<ExportPreview>(ep, `/export/preview?${q}`);
}

// --- живой режим ---------------------------------------------------------------

/** Ответ сразу (`starting`); дальше — события `live.started` / `live.failed`. 409/400 — ApiError. */
export const liveStart = (ep: Endpoint) => json<{ ok: boolean } & LiveStatus>(ep, "/live/start", { method: "POST" });
/** Ответ сразу; конец — событием `live.stopped`. */
export const liveStop = (ep: Endpoint) =>
  json<{ ok: boolean; action: string } & LiveStatus>(ep, "/live/stop", { method: "POST" });
/**
 * «Включить ассистента» посреди обычной записи: запись не прерывается,
 * ассистент догоняет уже записанное и слушает дальше. Ответ сразу (`starting`).
 */
export const liveAttach = (ep: Endpoint) => json<{ ok: boolean } & LiveStatus>(ep, "/live/attach", { method: "POST" });
/** «Выключить ассистента»: запись идёт дальше, его сводка остаётся с пометкой «неполная». */
export const liveDetach = (ep: Endpoint) =>
  json<{ ok: boolean; action: string } & LiveStatus>(ep, "/live/detach", { method: "POST" });
/**
 * Вопрос или быстрое действие (`quick`, тогда вопрос не нужен); `since_t` —
 * с какой секунды записи «Что я пропустил?». Ответ модели может идти минуты.
 */
export const liveAsk = (ep: Endpoint, question: string, opts: { quick?: LiveQuick; since_t?: number } = {}) =>
  json<{ answer: string }>(ep, "/live/ask", body("POST", { question, ...opts }));
/** Закрепить, открепить или скрыть подсказку ассистента. */
export const liveHint = (ep: Endpoint, id: string, action: "pin" | "unpin" | "dismiss" | "restore") =>
  json<{ ok: boolean; changed?: boolean }>(ep, "/live/hint", body("POST", { id, action }));
export const liveTask = (ep: Endpoint, task: string) =>
  json<{ ok: boolean }>(ep, "/live/task", body("POST", { task }));

// --- URL для <audio>/<img> ---------------------------------------------------

/**
 * Адрес дорожки для <audio>. `playback` — то, что играет карточка: обе стороны
 * звонка, сведённые резидентом (импорт — как есть); `sys`/`mic`/`source` —
 * отдельные дорожки (образцы голосов, диагностика).
 */
export function audioUrl(ep: Endpoint, id: string, track: "playback" | "sys" | "mic" | "source"): string {
  const q = new URLSearchParams({ track });
  if (ep.token) q.set("token", ep.token);
  return `${ep.base}/recordings/${enc(id)}/audio?${q}`;
}

/** `version` сбрасывает кэш картинки после смены аватара. */
export function avatarUrl(ep: Endpoint, name: string, version: number): string {
  const parts: string[] = [];
  if (ep.token) parts.push(`token=${enc(ep.token)}`);
  parts.push(`v=${version}`);
  return `${ep.base}/voices/${enc(name)}/avatar?${parts.join("&")}`;
}

// --- события -----------------------------------------------------------------

const EVENT_KINDS = [
  "job.queued", "job.started", "job.progress", "job.done", "job.failed",
  "record.started", "record.stopped", "record.discarded", "record.device", "record.device_fallback", "record.device_pinned",
  "record.waiting", "record.silence", "record.level", "record.system_audio", "progress", "log", "error",
  "live.starting", "live.started", "live.stopping", "live.stopped", "live.failed",
  // Этап старта ассистента сменился («загружаю модель распознавания…», готов).
  "live.stage",
  // Запись изменилась вне задач: обрезка ожидания после звонка, выгрузка в базу знаний.
  "recording.processing", "recording.updated",
  // Анализ встречи готов, не удался или устарел: {"id", "state"}.
  "analysis.updated",
  // «Улучшить расшифровку»: предложение готово, не удалось или применено: {"id", "state"}.
  "improve.updated",
];

function parseEvent(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** Первым сообщением сервер присылает `state` со снимком. Возвращает закрытие. */
export function openEvents(
  ep: Endpoint,
  handlers: {
    onSnapshot?: (s: Snapshot) => void;
    onEvent?: (e: BusEvent) => void;
    onError?: () => void;
  },
): () => void {
  const query = ep.token ? `?token=${enc(ep.token)}` : "";
  const source = new EventSource(`${ep.base}/events${query}`);
  source.addEventListener("state", (m) => {
    const data = parseEvent((m as MessageEvent<string>).data);
    if (data) handlers.onSnapshot?.(data as Snapshot);
  });
  source.onmessage = (m) => {
    const data = parseEvent(m.data as string);
    if (data) handlers.onEvent?.(data as BusEvent);
  };
  // Подписываемся широко: новый вид события в Python не требует правки фронта.
  for (const kind of EVENT_KINDS) {
    source.addEventListener(kind, (m) => {
      const data = parseEvent((m as MessageEvent<string>).data);
      if (data) handlers.onEvent?.(data as BusEvent);
    });
  }
  source.onerror = () => handlers.onError?.();
  return () => source.close();
}

/**
 * Поток живого ассистента: `state` (сводка, подсказки, статус) при каждом их
 * изменении, `qa` (история вопросов) — когда меняется она, `qa_partial` —
 * ответ, который ещё пишется, и `line` на каждую новую строку.
 *
 * `onLine` получает и номер строки (`id:` события, null — без него): поток,
 * открытый заново, начинает с хвоста ленты, и по номеру повторы отбрасываются.
 * `onError(closed)`: closed — браузер сдался (не 200: живого режима нет — 409)
 * и сам больше не переподключится; иначе он переподключается сам, с
 * Last-Event-ID, и получит только пропущенные строки.
 */
export function openLiveEvents(
  ep: Endpoint,
  handlers: {
    onState?: (s: LiveState) => void;
    onQa?: (qa: LiveQa[]) => void;
    /** Кусок ответа, который ещё пишется (`qa_partial`): текст ответа на сейчас. */
    onQaPartial?: (part: LiveQaPartial) => void;
    onLine?: (l: LiveLine, id: number | null) => void;
    /** Подписи голосов задним числом и спрятанные дубли — состояние целиком. */
    onVoices?: (v: LiveVoices) => void;
    onError?: (closed: boolean) => void;
  },
): { close: () => void } {
  const query = ep.token ? `?token=${enc(ep.token)}` : "";
  const source = new EventSource(`${ep.base}/live/events${query}`);
  source.addEventListener("state", (m) => {
    const data = parseEvent((m as MessageEvent<string>).data);
    if (data) handlers.onState?.(data as LiveState);
  });
  source.addEventListener("qa", (m) => {
    const data = parseEvent((m as MessageEvent<string>).data) as { qa?: unknown } | null;
    if (data && Array.isArray(data.qa)) handlers.onQa?.(data.qa as LiveQa[]);
  });
  source.addEventListener("qa_partial", (m) => {
    const data = parseEvent((m as MessageEvent<string>).data) as Partial<LiveQaPartial> | null;
    if (data && typeof data.id === "number" && typeof data.a === "string") {
      handlers.onQaPartial?.({ id: data.id, a: data.a });
    }
  });
  source.addEventListener("line", (m) => {
    const event = m as MessageEvent<string>;
    const data = parseEvent(event.data);
    const id = event.lastEventId === "" ? NaN : Number(event.lastEventId);
    if (data) handlers.onLine?.(data as LiveLine, Number.isFinite(id) ? id : null);
  });
  source.addEventListener("voices", (m) => {
    const data = parseEvent((m as MessageEvent<string>).data) as Partial<LiveVoices> | null;
    if (!data || typeof data.rev !== "number") return;
    const speakers: Record<string, string> = {};
    if (data.speakers && typeof data.speakers === "object") {
      for (const [voice, speaker] of Object.entries(data.speakers)) {
        if (typeof speaker === "string") speakers[voice] = speaker;
      }
    }
    const hidden = Array.isArray(data.hidden) ? data.hidden.filter((i): i is number => typeof i === "number") : [];
    handlers.onVoices?.({ rev: data.rev, speakers, hidden });
  });
  source.onerror = () => handlers.onError?.(source.readyState === EventSource.CLOSED);
  return { close: () => source.close() };
}
