/**
 * Формы, которые отдаёт control API резидента (`meet.control`).
 * Виды событий совпадают со строками из `meet/events.py` — они уходят в SSE как есть.
 */

export type Signal = boolean | null;
export type WatcherState = "idle" | "recording" | "grace" | "suppressed" | null;

export type AutoRecord = {
  enabled: boolean;
  processes: string[];
  grace_seconds: number;
  /** Ожидание повторного подключения (`auto_record.grace_minutes`). Нет у старых резидентов. */
  grace_minutes?: number;
  state: WatcherState;
  mic: Signal;
  render: Signal;
  /** Браузеры, где звонок — занятый микрофон. Нет у старых резидентов. */
  browsers?: string[];
  /** Звонок в браузере по последнему опросу детектора. */
  browser?: { exe: string; site: string | null } | null;
};

export type Snapshot = {
  status: "idle" | "recording";
  source: "auto" | "manual" | null;
  folder: string | null;
  elapsed_s: number;
  levels: Record<string, number>;
  auto_record: AutoRecord;
  recordings_dir: string;
  gpu_busy: boolean;
  disk_free_gb: number | null;
  last_stop: { folder: string; reason: "saved" | "discarded" | "short"; at: number } | null;
  /** Запись с ассистентом; `status` выше при ней остаётся "idle". Нет у старых резидентов. */
  live?: LiveStatus;
  /** Папка для встреч в базе знаний (`export.meetings_dir`); не задана — null. */
  meetings_dir?: string | null;
  /** Выбранный в настройках микрофон или вывод не найден — запись идёт с системного. */
  devices_fallback?: { kind: "mic" | "output"; name: string; device: string | null }[];
  /** Последний сбой автоматической выгрузки в базу знаний. */
  kb_export_failed?: { folder: string; error: string; at: number } | null;
  /** Папки записей, которые сейчас обрабатываются в фоне (обрезка ожидания после звонка). */
  processing?: string[];
};

export type CommandResult = Snapshot & { ok: boolean; action: string };

export type Job = {
  id: string;
  kind: string;
  folder: string;
  state: "queued" | "running" | "done" | "failed" | "cancelled";
  stage: string | null;
  label: string | null;
  done: number | null;
  total: number | null;
  note: string | null;
  result: string | null;
  error: string | null;
  /** Секунды эпохи; старые резиденты их не присылали. */
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
};

export type Recording = {
  id: string;
  path: string;
  started_at: string | null;
  duration_s: number | null;
  tracks: Record<string, string>;
  has_transcript: boolean;
  has_voices: boolean;
  title: string | null;
  source: string;
  /** Когда записан транскрипт (секунды эпохи), нет — null. */
  transcript_at?: number | null;
  /** Спикеры не разделены: "skipped_no_token" — нет токена HF, "skipped_no_access" — HF отказал. */
  diarization?: string | null;
  /** Выгрузка в базу знаний: куда и когда; `error` — последняя не удалась. Не выгружалась — null. */
  kb_export?: KbExportRecord | null;
  /** Объединённая встреча (`source: "merge"`); у остальных — null. */
  merge?: MergeInfo | null;
  /** «Переразделить на спикеров» посчитано и ждёт решения. */
  rediarize_ready?: boolean;
};

/**
 * Объединённая встреча: из скольких записей, на каком шаге («pending» — собирается
 * звук, «merged» — звук готов, «done» — расшифрована и исходные обработаны),
 * удалены ли исходные и какие их папки в базе знаний остались нетронутыми.
 */
export type MergeInfo = { parts: number; state: "pending" | "merged" | "done"; deleted: boolean; kb_left: string[] };

/** Фрагмент реплики из поиска по библиотеке: `t` — начало реплики, `ranges` — что подсветить в `snippet`. */
export type SearchHit = { t: number; speaker: string; snippet: string; ranges: [number, number][] };

/** `GET /search`: карточка записи и что в ней нашлось (`total` — подходящих реплик). */
export type SearchItem = Recording & {
  date: string | null;
  hits: SearchHit[];
  total: number;
  /** Запрос нашёлся в названии. */
  title_match: boolean;
};

/** Элемент списка записей: при поиске — с фрагментами. */
export type LibraryItem = Recording & Partial<Pick<SearchItem, "hits" | "total" | "title_match">>;

export type KbExportRecord = {
  path?: string | null;
  at?: number | null;
  /** Наши файлы: имя → SHA-256 записанного (null — без хеша, например аудио). */
  files?: Record<string, string | null> | string[];
  /** Не перезаписаны: изменены вручную или лежали в папке до выгрузки. */
  kept?: string[];
  error?: string | null;
};

/**
 * `POST /recordings/{id}/kb-export`: папка встречи, записанные файлы и не перезаписанные (правленые вручную);
 * `notes` — строки для человека (например, «Старая заметка оставлена: …»), нет у старых резидентов.
 */
export type KbExport = { path: string; files: string[]; kept: string[]; notes?: string[] };

/** `GET /export/preview`: как назовётся папка (относительно папки для встреч) и что в ней будет. */
export type ExportPreview = { folder: string | null; files: string[]; error: string | null };

export type Segment = {
  start: number;
  end: number;
  speaker: string | null;
  text: string;
  uncertain: boolean;
  /** "break" — отметка перерыва между частями объединённой встречи: разделитель, не реплика. */
  kind?: "break";
  /** У сегмента есть слова с таймкодами: реплику можно разделить по слову. */
  has_words?: boolean;
};

export type Transcript = {
  version: number;
  title: string | null;
  segments: Segment[];
  names?: Record<string, string>;
};

export type Person = {
  name: string;
  samples: number;
  meetings: number;
  seconds: number;
  has_avatar: boolean;
  color: string;
};

export type PersonMeeting = {
  recording: string;
  title: string | null;
  started_at: string | null;
  seconds: number;
};

export type PersonCard = {
  name: string;
  color: string;
  has_avatar: boolean;
  samples: number;
  meetings: PersonMeeting[];
};

export type Sample = { recording: string; start: number; end: number; track: string };

/** Фраза спикера для прослушивания в панели «Спикеры». */
export type SpeakerPhrase = { start: number; end: number; text: string };
/** Похожий голос из базы: `score` — сходство 0..1. */
export type SpeakerSuggestion = { name: string; score: number };
/** Строка панели «Спикеры»: `label` — подпись в расшифровке, `name` — null у «Спикер N». */
export type SpeakerRow = {
  label: string;
  name: string | null;
  seconds: number;
  /** Доля времени речи, 0..1. */
  share: number;
  turns: number;
  samples: SpeakerPhrase[];
  /** Есть голосовой отпечаток (его можно запомнить и по нему есть подсказки). */
  has_voice: boolean;
  suggestions: SpeakerSuggestion[];
};
/** Правка из набора: rename — имя, reset — «Неизвестный», merge — объединить с `into`. */
export type SpeakerOp =
  | { type: "rename" | "merge" | "reset"; label: string; from: string; to: string; into?: string }
  /** Реплики другому спикеру: `from` — чьи были, `segments` — сколько сегментов, `turns` — реплик. */
  | { type: "relabel"; from: string[]; to: string; segments: number; turns: number }
  /** Спикер разделён по голосу на группы `into`. */
  | { type: "split"; label: string; mode: "auto" | "people"; into: string[] }
  /** Имена пересчитаны с порогом узнавания `value`. */
  | { type: "threshold"; value: number }
  /** Реплика разделена в `at` секунд, вторая часть — спикеру `to`. */
  | { type: "split_turn"; label: string; to: string; at: number; cut: "word" | "segment" }
  /** Заново разделено на спикеров: `speakers` — сколько их стало. */
  | { type: "rediarize"; speakers: number; params: Record<string, number> };
export type SpeakerStep = {
  id: string;
  at: string;
  ops: SpeakerOp[];
  enrolled: { person: string; sample_id: string; label: string; created: boolean }[];
  created_people: string[];
};
/** «Разделить спикера», шаг 1: голоса реплик готовы или считаются задачей. */
export type SplitStatus = {
  label: string; segments: number; voiced: number; missing: number; ready: boolean; fingerprint: string;
  job?: Job;
};
/** Группа реплик одного голоса в предпросмотре разделения. */
export type SplitGroup = {
  key: string;
  idx: number[];
  seconds: number;
  share: number;
  turns: number;
  samples: SpeakerPhrase[];
  /** Реплик с посчитанным голосом (остальные взяли группу соседей). */
  voiced: number;
  suggestions: SpeakerSuggestion[];
  /** Предложенное имя (похожий человек базы выше порога или выбранный человек). */
  name: string | null;
  /** Режим «по образцам»: человек, к которому отнесены реплики. */
  person?: string;
};
export type SplitPreview = {
  label: string;
  mode: "auto" | "people";
  fingerprint: string;
  segments: number;
  voiced: number;
  groups: SplitGroup[];
  /** Режим «по образцам»: реплики без уверенного сходства — для ручной проверки. */
  unsure: SplitGroup | null;
  /** Наибольшее сходство голосов двух групп, 0..1: высокое — скорее всего, один человек. */
  similar?: number | null;
};
export type SplitRequest =
  | { label: string; mode: "auto"; k: number }
  | { label: string; mode: "people"; people: string[] };
export type SplitApply = {
  label: string; mode: "auto" | "people"; fingerprint: string;
  groups: { idx: number[]; to: string | null; remember: boolean }[];
};
/** Параметры «Переразделить на спикеров»: точное число или диапазон и чувствительность 0..1. */
export type RediarizeParams = {
  num_speakers?: number; min_speakers?: number; max_speakers?: number; sensitivity?: number;
};
export type RediarizePreview = {
  created_at: string | null;
  params: RediarizeParams;
  /** Расшифровку меняли после расчёта — применить нельзя. */
  stale: boolean;
  speakers: { label: string; seconds: number; share: number; turns: number; samples: SpeakerPhrase[] }[];
  /** Сколько спикеров было. */
  before: number;
  /** Сколько сегментов сменят спикера и сколько разрежутся по слову. */
  changed: number;
  cut: number;
  segments: number;
  /** Имена, которые новое разделение сохранило по сходству голоса. */
  kept?: string[];
};
/** Порог узнавания: что станет с именами спикеров встречи. */
export type ThresholdRow = { label: string; auto: boolean; best: string | null; score: number | null; to: string | null };
export type ThresholdPlan = { value: number; rows: ThresholdRow[]; changes: ThresholdRow[] };

export type SpeakersView = {
  speakers: SpeakerRow[];
  /** Владелец микрофона (настройка «Как подписывать микрофон»). */
  owner: string;
  history: SpeakerStep[];
  /** Сколько шагов истории применено: дальше — отменённые (их можно повторить). */
  pos: number;
  /** Самые старые шаги отброшены: история хранит последние 50. */
  trimmed?: boolean;
  voices_error?: string | null;
  step?: SpeakerStep;
  /** Порог узнавания голоса этой встречи; null — общий из настроек. */
  voice_threshold?: number | null;
  /** Общий порог (Настройки → Распознавание). */
  voice_threshold_default?: number;
};
/** «Разделить реплику здесь» (`POST …/speakers/split-turn`): место — символ `char` сегмента `at`. */
export type SplitTurnRequest = {
  turn: number[]; at: number; char: number; to: string | null; labels: string[]; count: number;
};
/** Реплики — другому спикеру (`POST …/speakers/relabel`). */
export type RelabelRequest = { idx: number[]; labels: string[]; count: number; to: string | null };
/** Правка, как её шлёт окно: `to` — имя (rename) или подпись другого спикера (merge). */
export type SpeakerOpInput = { type: "rename" | "merge" | "reset"; label: string; to?: string };

export type BusEvent = { kind: string; at: number; [key: string]: unknown };
export type ProgressEvent = BusEvent & {
  stage: string;
  label: string;
  done: number | null;
  total: number | null;
  note: string | null;
};
export type LevelEvent = BusEvent & { levels: Record<string, number> };
export type JobEvent = BusEvent & { job: Job };

export const isJob = (e: BusEvent): e is JobEvent => e.kind.startsWith("job.");
export const isLevel = (e: BusEvent): e is LevelEvent => e.kind === "record.level";

// --- ассистент ---------------------------------------------------------------

/** `GET /recordings/{id}/summary`; итогов нет — 404. */
export type Summary = { markdown: string; created_at: number | null };

/** Пара из `qa.jsonl`; `at` — секунды эпохи. */
export type QaItem = { q: string; a: string; at: number; provider: string | null };

/** Что видит резидент: CLI найдены, локальная модель отвечает. */
export type ProviderAvailability = { found: boolean; path?: string | null; base_url?: string };

/**
 * `GET /assistant`. `provider` — кто ответит сейчас; null при `checking` значит
 * «ещё считается», а не «никого нет».
 */
export type AssistantInfo = {
  provider: string | null;
  setting: string;
  available: Record<string, ProviderAvailability>;
  knowledge_dir: string | null;
  checking: boolean;
  /** Нет у резидента прежней версии. */
  proxy?: ProxyInfo;
};

/**
 * Прокси, который получат Claude Code и Codex (`llm.proxy`): режим сохранённой
 * настройки, действующий адрес (логин и пароль скрыты) и откуда он; `system` —
 * что дал бы вариант «как в системе» (переменные среды или прокси Windows).
 */
export type ProxyInfo = {
  mode: "system" | "none" | "custom";
  effective: string | null;
  source: "env" | "system" | "setting" | null;
  system: string | null;
};

/** `POST /assistant/check`. */
export type ProviderCheck = { ok: boolean; error?: string | null; provider?: string | null };

/** Состояние живого режима, общая часть ответов `/live/start` и `/live/stop`. */
export type LiveStatus = {
  active: boolean;
  starting: boolean;
  stopping: boolean;
  folder: string | null;
  error: string | null;
  started_at: number | null;
};

/** Быстрые действия вопросов ассистенту (`meet.assist.qa.QUICK`). */
export type LiveQuick = "missed" | "decisions" | "reply" | "brief";

/** Вопрос в истории ассистента: ждёт ответа (`pending`), ответ или ошибка. */
export type LiveQa = {
  id: number;
  q: string;
  a: string | null;
  error: string | null;
  pending: boolean;
  at: number;
  quick: LiveQuick | null;
};

/** Пункт живой сводки со стабильным id (`p3`, `d1`, `q2`). */
export type LiveItem = { id: string; text: string };
export type LiveTask = { id: string; who: string | null; what: string; due: string | null };

/** Живая сводка встречи (`meet.assist.live_state`). */
export type LiveSummary = {
  topic: string;
  points: LiveItem[];
  decisions: LiveItem[];
  tasks: LiveTask[];
  open_questions: LiveItem[];
};

/** Вид подсказки: стоит спросить, риск, без ответа, термин, следующий шаг. */
export type LiveHintKind = "question" | "risk" | "unanswered" | "term" | "followup";

export type LiveHint = {
  id: string;
  kind: LiveHintKind;
  text: string;
  /** Почему важно сейчас — одна строка. */
  why: string;
  /** Секунды записи реплики, к которой относится подсказка. */
  source_t: number;
  /** Файл базы знаний (у «Термина»). */
  ref: string | null;
  pinned: boolean;
  dismissed: boolean;
  created_at: number;
  updated_at: number;
};

/**
 * `event: state` потока `/live/events`: сводка (структурой и Markdown'ом —
 * `digest`), подсказки, история вопросов, хвост ленты, тихий статус
 * («Подсказки временно недоступны»). `hints_enabled: false` — режим
 * «Только сводка».
 */
export type LiveState = {
  digest: string;
  transcript: string[];
  status: string | null;
  qa?: LiveQa[];
  version?: number;
  summary?: LiveSummary;
  hints?: LiveHint[];
  hints_enabled?: boolean;
  /** Настройки `assist`, с которыми запущен ассистент. */
  prefs?: { quiet_default?: boolean; activity?: string };
};

/** `event: line`: новая строка ленты; `t` — секунды от начала записи. */
export type LiveLine = { t: number; speaker: string | null; text: string };
