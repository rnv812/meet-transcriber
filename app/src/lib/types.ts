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
  /**
   * macOS: звук собеседников идущей записи не пишется (нет разрешения «Запись экрана»
   * или помощника) — запись идёт только с микрофона. `permission` — дело в разрешении.
   */
  system_audio_missing?: { notice: string; permission: boolean } | null;
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
  /**
   * Откуда название: "auto" — по дате или из транскрипта, "user" — задал человек,
   * "ai" — предложила модель (бейдж «ИИ»), "site" — заголовок окна звонка. Нет у старых резидентов.
   */
  title_source?: TitleSource;
  source: string;
  /** Когда записан транскрипт (секунды эпохи), нет — null. */
  transcript_at?: number | null;
  /** Спикеры не разделены: "skipped_no_token" — нет токена HF, "skipped_no_access" — HF отказал. */
  diarization?: string | null;
  /** Распознано не выбранным движком: "not_russian" — запись не на русском, вместо GigaAM работал Whisper. */
  asr_note?: string | null;
  /** macOS: звук собеседников не записан ("missing") или записан не с начала ("partial"). */
  system_audio?: "missing" | "partial" | null;
  /** Почему: нет разрешения «Запись экрана», нет помощника, старая macOS, помощник не запустился. */
  system_audio_reason?: "permission" | "helper" | "unsupported" | "failed" | null;
  /** Выгрузка в базу знаний: куда и когда; `error` — последняя не удалась. Не выгружалась — null. */
  kb_export?: KbExportRecord | null;
  /** Объединённая встреча (`source: "merge"`); у остальных — null. */
  merge?: MergeInfo | null;
  /** «Переразделить на спикеров» посчитано и ждёт решения. */
  rediarize_ready?: boolean;
  /** Последний применённый шаг истории правок встречи (нет правок — null): «Отменить» у итога правки — только пока он последний. */
  edit_head?: string | null;
  /**
   * Категория встречи: `source` "ai" — из анализа, "user" — выбрал человек (модель её не меняет);
   * `id: null` — человек выбрал «Без категории». Не задана — null. Id, которого нет в настройках
   * (категорию удалили), показывается как «Без категории».
   */
  category?: RecordingCategory | null;
};

export type RecordingCategory = { id: string | null; source: "ai" | "user" };

/** Категория встреч из настроек (`categories`): id стабилен, имя и цвет — для окна, описание — для модели. */
export type Category = { id: string; name: string; color: string; description: string };

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

/**
 * Ссылка утверждения профиля на реплику: встреча (id записи), номер первого
 * сегмента реплики, начало, отпечаток текста и цитата. `stale` — реплику с тех
 * пор изменили (разделили, перерасшифровали, отдали другому спикеру): ссылка
 * никуда не ведёт.
 */
export type ProfileRef = { m: string; i: number; t?: number; h?: string; q?: string; stale?: boolean };
export type ProfileStatement = { text: string; refs: ProfileRef[] };
export type ProfileSectionKey = "style" | "values" | "how_to_talk" | "avoid" | "topics";
/** Профиль человека (meet.profiles): стиль общения по репликам во встречах. */
export type Profile = {
  version: number;
  person_id: string;
  name: string;
  updated_at: number;
  model?: string;
  meetings: number;
  turns: number;
  sampled?: number;
  /** Реплик пока немного: профиль сокращённый. */
  reduced?: boolean;
  summary: string;
  /** Опора «Коротко» на реплики. */
  summary_refs?: ProfileRef[];
  sections: Partial<Record<ProfileSectionKey, ProfileStatement[]>>;
  /** Проверка утверждений агентом (второй слой): не завершена — `checked: false`. */
  review?: { checked: boolean; blocked: number; error?: string };
  /** Сколько утверждений скрыто человеком («Скрыть»). */
  hidden_count?: number;
  /** Встречи, на которые ссылаются утверждения: название и дата. */
  sources: Record<string, { title: string; date: string }>;
  filtered?: number;
  warnings?: string[];
  /** Гипотеза по модели PCM (только при достаточных данных и если включена). */
  pcm?: Pcm;
};
/** Типы PCM (ключи — как у модели), их подписи — lib/pcm.ts. */
export type PcmType = "thinker" | "persister" | "harmonizer" | "imaginer" | "rebel" | "promoter";
export type PcmClaim = { type: PcmType; confidence: number; refs: ProfileRef[] };
/** Раздел «Модель PCM»: гипотеза по репликам во встречах, не сертифицированная оценка. */
export type Pcm = {
  base: PcmClaim;
  phase?: PcmClaim;
  /** Выраженность каждого типа 0–5 («этажи»). */
  floors: Record<PcmType, number>;
  perception?: { value: string; refs: ProfileRef[] };
  channel?: { value: string; examples: string[] };
  needs?: { value: string; how_to_recognize: string };
  stress_signs?: ProfileStatement[];
  back_to_constructive?: string[];
  conversation?: string[];
};
export type ProfileStateName = "none" | "queued" | "running" | "ready" | "failed";
/** GET /voices/{name}/profile. Профили выключены — только `{enabled: false}`. */
export type ProfileView = {
  enabled: boolean;
  /** Постоянный id человека (по нему — задача профиля); нет — профиля и заметок ещё не было. */
  id?: string | null;
  name?: string;
  /** Это владелец микрофона («Вы»): профиль обновляется только вручную. */
  self?: boolean;
  stats?: { turns: number; meetings: number } | null;
  /** Сколько данных: none — профиля не будет, reduced — сокращённый, full — полный. */
  level?: "none" | "reduced" | "full" | null;
  /** «Недостаточно данных: …» при level none. */
  note?: string;
  state?: ProfileStateName;
  profile?: Profile | null;
  notes?: string;
  error?: string;
  job?: Job;
  /** Последняя встреча, где человек говорил: туда — «Подготовиться к разговору». */
  latest_meeting?: string | null;
  /** Последняя встреча библиотеки (общей встречи нет). */
  latest_any?: string | null;
  /** Сколько утверждений человек скрыл. */
  hidden?: number;
  /** Резидент впервые считает реплики библиотеки: счётчиков ещё нет. */
  indexing?: boolean;
  /** Новый профиль не прошёл проверку агентом — показан прежний, проверенный (текст — причина). */
  kept_previous?: string;
  /** После профиля появились новые реплики. */
  has_new?: boolean;
  /** Показывать ли раздел «Модель PCM» (настройка `profiles.pcm`). */
  pcm_enabled?: boolean;
  /** Данных для гипотезы PCM мало: «Недостаточно данных: … — нужно от 15 реплик в 3 встречах». */
  pcm_note?: string;
};

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
  | { type: "rediarize"; speakers: number; params: Record<string, number> }
  /** «Исправить…»: распознанное `from` заменено на `to` — `count` раз (одно или во всей встрече). */
  | { type: "text"; from: string; to: string; count: number; scope: "one" | "all" }
  /** «Улучшить расшифровку»: замены ИИ одним шагом — `count` мест, `terms` групп терминов. */
  | { type: "text"; scope: "ai"; from: string; to: string; count: number; terms: number;
    groups?: ImproveApplied[] };
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
/** Совпадение для «Исправить…»: сегмент, начало в его тексте, когда звучит и окружение. */
export type TextSample = {
  segment: number; offset: number; speaker: string | null; start: number; end: number;
  before: string; match: string; after: string;
};
/** `POST …/text/preview`: сколько раз слово во встрече, первые совпадения, когда звучит выбранное. */
export type TextPreview = { count: number; samples: TextSample[]; here: { start: number; end: number } | null };
/** `POST …/text/apply`: заменить выбранное совпадение (`one`) или все (`all`); `count` — сегментов в окне. */
export type TextFixRequest = {
  find: string; replace: string; scope: "one" | "all"; segment: number; offset: number; count: number;
  add_hotword: boolean;
  /** Исправлять так же в будущих расшифровках: правило в `asr.replacements`. */
  add_rule: boolean;
};
/** Термин распознавания после «Исправить…»: добавлен ли (или уже был), не длиннее ли список лимита. */
export type HotwordAdded = { term: string; added: boolean; over_budget?: boolean; error?: string };
/** Правило замены для будущих расшифровок (`asr.replacements`). */
export type ReplacementRule = {
  from: string; to: string; error?: string;
  /** Правило с тем же «как распознаётся», которое это заменило: отмена его вернёт. */
  replaced?: { from: string; to: string } | null;
};
export type TextFixResult = SpeakersView & { changed: number; hotword?: HotwordAdded; rule?: ReplacementRule | null };
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

/** Сводка живого режима записи (`live_state.json`) — черновик итогов. */
export type LiveDraft = { summary: LiveSummary; hints: LiveHint[]; markdown: string; saved_at?: number };

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
 * `digest`), подсказки, тихий статус («Подсказки временно недоступны»).
 * `hints_enabled: false` — режим «Только сводка». История вопросов приходит
 * своим событием `qa`; хвост ленты строками (`transcript`) — только странице
 * `meet assist` в браузере.
 */
export type LiveState = {
  digest: string;
  transcript?: string[];
  status: string | null;
  /** Прежний ассистент присылал историю вопросов здесь. */
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

// --- анализ встречи (analysis.json, M2) ------------------------------------------

export type TitleSource = "auto" | "user" | "ai" | "site";

/** Части разметки; выключенные в настройках не запрашиваются и в файле отсутствуют. */
export type AnalysisFeature = "types" | "importance" | "chapters" | "insights" | "category" | "title";
export type PhraseType =
  "statement" | "question" | "idea" | "decision" | "task" | "risk" | "agreement" | "objection";
export type InsightKind = "insight" | "contradiction" | "attention" | "followup";

/** Глава: отрезок номеров реплик (сегментов транскрипта) с названием и подписью для полосы плеера. */
export type AnalysisChapter = { start_i: number; end_i: number; title: string; short: string };
/** Наблюдение; `refs` — номера реплик (сегментов транскрипта). */
export type AnalysisInsight = { id: string; kind: InsightKind; text: string; refs: number[]; why: string };

/**
 * `analysis.json` записи. Ключи `phrase_types` и `importance` — номера сегментов
 * транскрипта строкой (`#i` в промпте); реплики без типа — утверждения, без
 * важности — неважные. Номера действительны, пока анализ не устарел (`stale`).
 */
export type Analysis = {
  version: 1;
  model: string;
  created_at: number;
  fingerprint: string;
  /** Сколько сегментов было в расшифровке (M3): устаревший анализ с тем же числом ещё можно показать. */
  segments?: number;
  features: AnalysisFeature[];
  phrase_types?: Record<string, PhraseType>;
  importance?: Record<string, number>;
  chapters?: AnalysisChapter[];
  insights?: AnalysisInsight[];
  /** id — из `categories` настроек; ничего не подошло — null. */
  category?: { id: string; confidence: number } | null;
  title?: string | null;
  /** Что не разобралось (части, отброшенные проверкой). */
  warnings?: string[];
};

export type AnalysisStateName = "none" | "queued" | "running" | "ready" | "stale" | "failed";

/** `GET /recordings/{id}/analysis`: прежний анализ отдаётся и пока идёт новый, и после сбоя. */
export type AnalysisState = {
  state: AnalysisStateName;
  analysis?: Analysis;
  error?: string;
  job?: Job;
};

/** `POST /recordings/{id}/title/suggest`: название и откуда оно (свежий анализ или вызов модели). */
export type TitleSuggestion = { title: string; from: "analysis" | "model" };

// --- «Улучшить расшифровку» ------------------------------------------------------

/** Вид замены: термин (во всей встрече) или явная ошибка распознавания (в названных фразах). */
export type ImproveKind = "term" | "fix";
/** Место замены с окружением (как образцы «Исправить…»). */
export type ImproveSample = TextSample;
/**
 * Группа замен «как распознано → как правильно»: `count` мест в фразах, которые
 * назвала модель (применяются по умолчанию), и `more` — другие места того же
 * термина во встрече, каждое на отдельную проверку (по умолчанию не применяются).
 */
export type ImproveGroup = {
  id: string;
  find: string;
  replace: string;
  kind: ImproveKind;
  confidence: number;
  count: number;
  samples: ImproveSample[];
  more?: ImproveSample[];
};
export type ImproveProposal = {
  version: number;
  model: string;
  created_at: number;
  fingerprint: string;
  segments: number;
  groups: ImproveGroup[];
  warnings?: string[];
};
export type ImproveStateName = "none" | "queued" | "running" | "ready" | "failed";
/** `GET /recordings/{id}/improve`; `hint` — предложить улучшение после GigaAM. */
export type ImproveState = {
  state: ImproveStateName;
  proposal?: ImproveProposal;
  error?: string;
  job?: Job;
  hint?: boolean;
};
/** Применённая группа (в ответе и в шаге истории). */
export type ImproveApplied = { from: string; to: string; kind: ImproveKind; count: number };
/** `extra` — {группа: номера отмеченных мест из `more`}; `created_at` — какой список видел человек. */
export type ImproveApplyRequest = {
  groups: string[];
  extra?: Record<string, number[]>;
  created_at?: number;
  add_rules?: boolean;
  add_terms?: boolean;
};
export type ImproveApplyResult = SpeakersView & {
  changed: number;
  groups: ImproveApplied[];
  rules?: { added: ReplacementRule[]; error?: string };
  terms?: { added: string[]; error?: string };
};
