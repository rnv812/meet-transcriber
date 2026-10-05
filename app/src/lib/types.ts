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
  /** Откуда идущая запись; `live` — «Запись с ассистентом» (обычная запись и подключённый ассистент). */
  source: "auto" | "manual" | "live" | null;
  folder: string | null;
  elapsed_s: number;
  /** Название звонка из окна браузера, пока идёт автозапись; неизвестно — null. Нет у старых резидентов. */
  title?: string | null;
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
  /**
   * Ход одной шкалой (meet.progress): номер этапа с 1, их число, общая доля
   * 0…1 и ожидаемая длительность всей работы, секунд. Старые резиденты и
   * задачи без плана этапов их не присылают.
   */
  step?: number | null;
  steps?: number | null;
  fraction?: number | null;
  estimate_s?: number | null;
  /**
   * С 0.3.1: доля в конце текущего шага (дальше неё полоска между событиями не
   * продлевается) и в чём меряется ход («audio_s», «time», «chars», «bytes»).
   */
  cap?: number | null;
  unit?: string | null;
  /**
   * Задачи модели: часть n из N («окно 2 из 4»), подшаг (request / generating
   * / validating / repair), «дольше обычного» и сколько осталось, секунд
   * (только уверенная оценка резидента).
   */
  part?: number | null;
  parts?: number | null;
  phase?: string | null;
  slow?: boolean | null;
  eta_s?: number | null;
  /** Предупреждение на всю задачу, например «Распознаётся на процессоре: видеокарта NVIDIA не найдена». */
  warning?: string | null;
  /** Расшифровка: текст уже записан, идут спикеры (`transcript.text`, с 0.3.3) — карточку пора перечитать. */
  text_ready?: boolean | null;
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
  /** Распознано не выбранным движком: "not_russian" — запись не на русском, вместо GigaAM работал Whisper; "cuda_failed" — видеокарта без библиотек CUDA, распознал процессор; "no_gpu" / "no_cuda_libs" — движок с видеокартой, а «Авто» взял процессор: карты нет / нет библиотек CUDA. */
  asr_note?: string | null;
  /** macOS: звук собеседников не записан ("missing") или записан не с начала ("partial"). */
  system_audio?: "missing" | "partial" | null;
  /** Почему: нет разрешения «Запись экрана», нет помощника, старая macOS, помощник не запустился. */
  system_audio_reason?: "permission" | "helper" | "unsupported" | "failed" | null;
  /**
   * "text" — транскрипт пока только текст, спикеры не определены (идёт диаризация или расшифровку
   * прервали между фазами): по нему нет анализа, итогов, правки спикеров. null — окончательный.
   */
  transcript_phase?: "text" | null;
  /** Микрофон звонка по голосам (с 0.3.3): статус — для подсказки, убрано копий — по причинам. */
  mic_split?: MicSplitInfo | null;
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
  /**
   * Задачи Jira, названные во встрече (резидент, meet.jira_refs; только в карточке записи): ссылки по
   * сегментам расшифровки и фразы итогов и наблюдений. Нет — ссылки выключены или адреса Jira нет.
   */
  jira?: JiraRefs;
};

/** Источник ссылки: ключ текстом, сказано с проектом, по слову «баг»/«тикет» (проект по умолчанию), анализ встречи. */
export type JiraSource = "literal" | "spoken" | "context" | "agent";
/**
 * Ссылка в сегменте: `start`/`end` — в символах (UTF-16) текста сегмента в NFC; может закончиться в
 * следующем сегменте той же реплики. `spoken` — сами слова (по ним ссылка находится, если текст поправили).
 */
export type JiraRef = { segment: number; start: number; end: number; key: string; source: JiraSource; spoken: string };
/** Ссылка в итогах и наблюдениях: каждое вхождение `text` — ссылка на `key`. */
export type JiraPhrase = { text: string; key: string; source: JiraSource };
export type JiraRefs = { refs: JiraRef[]; phrases: JiraPhrase[] };

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
  /**
   * "mic" — реплика с микрофона владельца в записи звонка; нет — собеседники. У микрофона
   * `uncertain` — голос под вопросом (между «точно вы» и «точно не вы»), а не нахлёст.
   */
  track?: "mic" | "sys";
  /** Человек рядом с владельцем в комнате: голос с микрофона, но не владелец (с 0.3.3). */
  room?: boolean;
};

export type Transcript = {
  version: number;
  title: string | null;
  /** "text" — текст до спикеров (см. `Recording.transcript_phase`); у окончательного поля нет. */
  phase?: "text";
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

/** Профили людей убраны (0.3.2): строка об уборке в «Голосах». `notes` — файл с заметками людей, `folder` — где он. */
export type ProfilesRemovedNotice = { notes: string | null; folder: string };

/**
 * Итог разделения микрофона звонка (`mic_split`): "ok" — разделён, "off" — выключено в настройках;
 * "no_profile" — нет образца голоса владельца, "owner_not_found" — образец не похож ни на один голос
 * микрофона, "no_voice" — мало речи, "skipped_error" — сбой, "skipped_no_token" — нет доступа к HF.
 * В каждом из них, кроме "ok", микрофон подписан владельцем целиком.
 */
export type MicSplitStatus =
  | "ok" | "off" | "no_profile" | "owner_not_found" | "no_voice" | "skipped_error" | "skipped_no_token";
/** Причина, по которой фраза убрана: дубль соседа, эхо колонок, ваш голос через чужой ноутбук. */
export type MicDropReason = "neighbour" | "echo" | "owner_leak";
export type MicSplitInfo = {
  status: MicSplitStatus | string;
  room_speakers?: number;
  /** Сколько фраз убрано, по причинам. */
  dropped?: Partial<Record<MicDropReason | string, number>>;
};
/** Фраза, убранная из расшифровки как повтор (`track` — с какой дорожки она убрана). */
export type MicRemovedItem = { start: number; end: number; text: string; reason: MicDropReason; track: "mic" | "sys" };

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
  /** Запись звонка: где звучит спикер — микрофон (вы или человек в комнате), звонок или оба; иначе null. */
  track?: "mic" | "sys" | "mixed" | null;
  /** Человек рядом с вами в комнате (говорит в микрофон и не владелец) — то же правило, что у реплик. */
  room?: boolean;
  /** Голос строки — только ваш (с микрофона): в базу людей он не записывается. */
  owner_voice_only?: boolean;
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
  /** Шаг запомнил ваш голос («Это я» + «Запомнить мой голос»); нет у старых резидентов. */
  owner_voice?: boolean;
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
  /** У встречи есть отпечаток вашего голоса с микрофона: «Это я» может его запомнить. */
  owner_voice?: boolean;
  /**
   * Этот отпечаток — только кандидат: образец его не узнал (другой микрофон, шум), это крупнейший голос
   * микрофона. «Запомнить мой голос» — по явному «это точно я», флажок выключен по умолчанию.
   */
  owner_voice_candidate?: boolean;
  /** Итог разделения микрофона (`mic_split` транскрипта); не звонок или старая расшифровка — null. */
  mic_split?: (MicSplitInfo & { rule?: number; owner_profile?: string | null }) | null;
  /** Что убрано из расшифровки как дубль соседа, эхо или ваш голос через звонок — по времени. */
  mic_removed?: MicRemovedItem[];
};

/** Образец вашего голоса: только отпечаток (набор чисел), без звука. */
export type OwnerVoiceSample = {
  id: string;
  /** enroll — записан в мастере или настройках, meeting — «Это я» во встрече, auto — найден по встречам. */
  source: "enroll" | "meeting" | "auto";
  /** ГГГГ-ММ-ДД. */
  date: string;
  seconds: number;
  device: string | null;
  recording: string | null;
  quality: number | null;
};
/** Запись образца: ~25 с с микрофона, затем разбор задачей. */
export type OwnerVoiceTake = {
  state: "recording" | "analyzing" | "done" | "failed";
  device: string | null;
  seconds: number;
  started_at: number;
  /** Почему не получилось — словами для человека. */
  error: string | null;
  sample_id: string | null;
  job: string | null;
};
export type OwnerVoiceStatus = {
  samples: OwnerVoiceSample[];
  take: OwnerVoiceTake | null;
  /** Можно ли записать: движок, токен Hugging Face и модель разделения на спикеров на месте. */
  ready: boolean;
  /** Чего не хватает (если !ready). */
  reason: string | null;
  /** Идёт запись встречи — микрофон занят. */
  recording: boolean;
  /** Сколько секунд пишется образец. */
  seconds: number;
  /** Голос, найденный по прошлым встречам и ещё не подтверждённый (нет — null). */
  suggestion?: OwnerVoiceSuggestion | null;
  /** «Найти по прошлым встречам»: идёт ли поиск и чем кончился последний. */
  derive?: OwnerVoiceDerive;
};
/** Кусок микрофона прошлой встречи — послушать найденный голос. */
export type OwnerVoiceSampleRef = { recording: string; start: number; end: number; track: "mic" };
/** Найденный по прошлым встречам голос: образцом станет только после «Да, это я». */
export type OwnerVoiceSuggestion = {
  meetings: string[];
  /** Три куска из разных встреч. */
  samples: OwnerVoiceSampleRef[];
  /** Сколько секунд вашей речи нашлось. */
  seconds: number;
  quality: number | null;
  /** ГГГГ-ММ-ДД. */
  date: string | null;
  /** Есть записанный образец, а найденный голос на него не похож. */
  conflict?: boolean;
};
export type OwnerVoiceDerive = {
  running: boolean;
  /** Идущая задача поиска — её можно остановить. */
  job?: string | null;
  /** Задача упала — текст ошибки. */
  error: string | null;
  /** Итог последнего поиска; `reason` — почему ничего не предложено. */
  last: {
    status: "suggested" | "too_few" | "inconsistent" | "already" | "in_base";
    reason: string | null;
    date?: string;
    /** already — совпавший образец; in_base — человек из базы; newest — самая новая запись тогда. */
    sample_id?: string;
    person?: string;
    newest?: string | null;
    checked?: number;
    used?: number;
    found?: number;
  } | null;
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
 * Прокси, который получат Claude Code, Codex и OpenCode (`llm.proxy`): режим сохранённой
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
  /** Звук уже пишется (своя запись или отвод обычной); модель может ещё грузиться — см. `ready`. */
  active: boolean;
  starting: boolean;
  stopping: boolean;
  /**
   * Модель распознавания загружена, ассистент слушает. Старый резидент поля
   * не присылает — тогда `active` и значит «слушает».
   */
  ready?: boolean;
  /** Этап старта, пока ассистент не готов: «загружаю модель распознавания…»; null — ещё неизвестен. */
  stage?: string | null;
  /**
   * Запись остановлена и сохранена, а её ассистент дописывает хвост ленты и
   * сводку в фоне. Это не «идёт»: новая запись не заблокирована.
   */
  finishing?: boolean;
  /** Ассистент подключён с самого начала «Записи с ассистентом». */
  with_recording?: boolean;
  folder: string | null;
  error: string | null;
  /** Когда появилась `error` (стенное время резидента): та же ошибка снова — новое время. */
  error_at?: number | null;
  /**
   * К какой записи относится `error` (папка): хвост ассистента прошлой записи
   * может упасть, когда идёт уже другая. Null — ни к какой; старый резидент поля не присылает.
   */
  error_folder?: string | null;
  started_at: number | null;
  /**
   * Ассистент включён посреди обычной записи («Включить ассистента»): запись
   * ведёт резидент (`status: "recording"`), ассистент слушает её отвод.
   * Старый резидент поля не присылает.
   */
  attached?: boolean;
  /**
   * Чем кончился последний запуск: вместе с записью, к которой был подключён
   * (`recording`), выключили (`detach`), упал (`crash`), остановили (`stop`);
   * пока ассистент жив — null.
   */
  ended_by?: "recording" | "detach" | "crash" | "stop" | null;
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
  /** Ответ, который ещё пишется (событие `qa_partial`), — только в окне. */
  partial?: string;
};

/** `event: qa_partial`: текст ответа на сейчас, пока модель его пишет. */
export type LiveQaPartial = { id: number; a: string };

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

/**
 * Вид подсказки: «Вам вопрос» (к владельцу обратились — с черновиком ответа),
 * стоит спросить, риск, без ответа, термин, следующий шаг.
 */
export type LiveHintKind = "ask_you" | "question" | "risk" | "unanswered" | "term" | "followup";

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
  /** Черновик ответа владельца (у «Вам вопрос»); у старого ассистента поля нет. */
  reply?: string | null;
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
  /** Ассистент включён посреди записи и догоняет уже записанное. */
  catchup?: LiveCatchup;
};

/**
 * Догонялка ассистента, включённого посреди записи: распознаёт начало
 * встречи с дорожек на диске (не больше последних 30 минут), пока слушает
 * дальше. `from_t`/`to_t` — секунды записи; `capped` — самое начало не войдёт.
 */
export type LiveCatchup = {
  active: boolean;
  percent: number;
  from_t: number | null;
  to_t: number | null;
  capped: boolean;
  complete: boolean;
};

/**
 * `event: line`: новая строка ленты; `t` — секунды от начала записи.
 * `catchup` — строка догнанного начала встречи (приходит позже живых, а
 * стоит раньше них).
 */
export type LiveLine = { t: number; speaker: string | null; text: string; catchup?: boolean };

// --- анализ встречи (analysis.json, M2) ------------------------------------------

export type TitleSource = "auto" | "user" | "ai" | "site";

/** Части разметки; выключенные в настройках не запрашиваются и в файле отсутствуют. */
export type AnalysisFeature = "types" | "importance" | "chapters" | "insights" | "category" | "title" | "issues";
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
  /** Задачи Jira, названные неполно (0.3.1): окно получает их уже слитыми в `Recording.jira`. */
  issues?: { key: string; segments: number[]; spoken: string; confidence: number }[];
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
