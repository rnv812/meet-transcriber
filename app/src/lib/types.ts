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
  /**
   * Чем кончилась последняя запись: `discarded` — «Остановить без сохранения»,
   * `temporary` — временная встреча закончилась и удалена (обе — не сохранение).
   */
  last_stop: { folder: string; reason: "saved" | "discarded" | "short" | "temporary"; at: number } | null;
  /**
   * Идёт временная встреча с ассистентом: вне библиотеки, на «Стоп» удаляется.
   * После «Сохранить как обычную встречу» — false. Нет у старых резидентов.
   */
  temporary?: boolean;
  /**
   * Чью историю ассистента «Остановить без сохранения» не удалит (агент вкладки
   * «Агент» без известного id): «Codex», «OpenCode». Нет у старых резидентов.
   */
  forget_gaps?: string[];
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
  /** Задача модели: модель, выбранная человеком для этого действия (0.3.4); нет — модель по умолчанию. */
  provider?: string;
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
  /** Какая модель придумала название «ИИ» (0.3.4); не от модели или неизвестно — null. */
  title_llm?: LlmOrigin | null;
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
   * Группа встречи — одна или никакой (null); id из `GET /groups`, неизвестный id — «Группа без
   * названия». Категория — отдельно. Нет у резидентов до 0.3.5.
   */
  group?: string | null;
  /** Названные спикеры окончательной расшифровки по порядку (без «Спикер N», «Неизвестный», «Собеседник»). */
  people?: string[];
  /** Есть итоги встречи. */
  has_summary?: boolean;
  /** Есть анализ встречи. */
  has_analysis?: boolean;
  /**
   * Во встрече работал ассистент (его сводка или живая лента в папке): и у «Записи с ассистентом»,
   * и у обычной записи, где его включили по ходу. `source: "live"` этого не говорит.
   */
  has_assistant?: boolean;
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

/** Группа встреч (`.meet-groups.json` резидента): id стабилен и не переиспользуется. */
export type Group = { id: string; name: string; color: string; created_at: string; kb_folder?: string };
/**
 * Группа в `GET /groups`: со счётчиком встреч (у встречи одна группа; с запросом — среди найденного).
 * `kb_folder` — папка базы знаний группы (путь внутри базы через «/»), если задана.
 */
export type GroupInfo = Pick<Group, "id" | "name" | "color"> & { count: number; kb_folder?: string };
/** Id группы, который есть в meta.json встреч, но не в списке: «Группа без названия». */
export type UnknownGroup = { id: string; count: number };
export type GroupsInfo = {
  groups: GroupInfo[];
  unknown: UnknownGroup[];
  /** Сколько встреч без группы (с запросом — среди найденного). */
  none?: number;
  scope?: "library" | "search";
  /** Файл групп не прочитать как список (повреждён): первая запись отложит его рядом. */
  broken?: boolean;
  /** Последний отложенный повреждённый файл групп (путь). */
  broken_copy?: string;
  /** Файл групп от более новой версии Meet: группы только для чтения. */
  newer?: boolean;
};
/** Ответ записи групп: если повреждённый файл пришлось отложить — куда (`moved_broken`). */
export type GroupWrite = { moved_broken?: string };
/** Итог «В группу»/«Убрать из группы»: что поменялось и что не удалось (по записи). */
export type GroupMembersResult = { changed: string[]; failed: { id: string; error: string }[] };
/** Участник встреч для подсказок: сколько встреч, когда последняя; `owner` — владелец микрофона. */
export type Participant = { name: string; meetings: number; last_at: string | null; owner: boolean };

/**
 * Счётчики панели «Фильтры» (`GET /facets`) при нынешнем запросе и фильтре: каждое измерение —
 * среди встреч, прошедших все прочие условия (своё не учитывается), `total` — прошедших все.
 * `duration`: до 15 мин, 15–60 мин, больше часа (без длительности — нигде).
 */
export type Facets = {
  total: number;
  scope: "library" | "search";
  categories: { items: { id: string; count: number }[]; none: number };
  groups: { items: { id: string; count: number }[]; unknown: UnknownGroup[]; none: number };
  /** До 20 самых частых участников. */
  people: { name: string; count: number }[];
  has: Record<LibraryHas, number>;
  duration: { lt15: number; m15_60: number; gt60: number };
};

/** Что есть (или нет) у встречи: итоги, анализ, запись с ассистентом, расшифровка. */
export type LibraryHas = "summary" | "analysis" | "assistant" | "transcript";
/**
 * Фильтр библиотеки по карточке (резидент — meet.library_filter, до лимита): категории и группы —
 * любая из (`_none` — «Без категории» / «Без группы»); участники — по началу слов имени, нужны все;
 * даты `from`/`to` — ГГГГ-ММ-ДД включительно (встреча без даты не проходит); длительность в
 * секундах (без длительности не проходит); `title` — слова в названии; `in: "title"` — весь поиск
 * только в названиях (прежний вид).
 */
export type LibraryFilter = {
  categories?: string[];
  groups?: string[];
  people?: string[];
  from?: string;
  to?: string;
  has?: LibraryHas[];
  lacks?: LibraryHas[];
  min_s?: number;
  max_s?: number;
  /** Слова, которые должны быть в названии (`название:`), — все, по правилам поиска; прочий `q` — как обычно. */
  title?: string;
  /** Прежний вид: весь `q` — только в названиях (окно его больше не шлёт). */
  in?: "title";
};

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
  /** Что подсветить в названии (UTF-16, по названию в NFC); нет у резидентов до 0.3.5. */
  title_ranges?: [number, number][];
};

/** Элемент списка записей: при поиске — с фрагментами. */
export type LibraryItem = Recording & Partial<Pick<SearchItem, "hits" | "total" | "title_match" | "title_ranges">>;

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
  /** «Кто это» (0.4): короткая строка до 160 символов; нет или "" — не задано. Старый резидент не присылает. */
  role?: string;
};

/** «Кто это» (0.4): больше резидент не хранит. */
export const ROLE_MAX = 160;

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
  role?: string;
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

/** Какая модель сделала результат (анализ, итоги, название, улучшение): `model` — имя из настроек, у Codex — null. */
export type LlmOrigin = { provider: string; model: string | null };

/** `GET /recordings/{id}/summary`; итогов нет — 404. `llm` — какая модель их сделала (с 0.3.4). */
export type Summary = { markdown: string; created_at: number | null; llm?: LlmOrigin | null };

/** Пара из `qa.jsonl`; `at` — секунды эпохи. */
export type QaItem = { q: string; a: string; at: number; provider: string | null; model?: string | null };

/** Что видит резидент: CLI найдены, локальная модель отвечает. */
export type ProviderAvailability = { found: boolean; path?: string | null; base_url?: string };

/**
 * `GET /assistant`. `provider` — кто ответит сейчас; null при `checking` значит
 * «ещё считается», а не «никого нет».
 */
/**
 * Включённая модель для выбора у действий карточки (`GET /assistant` → `models`):
 * `local` — данные не покидают компьютер, `available` — найдена на машине (вход в CLI
 * не проверяется), иначе `reason`.
 */
export type ModelChoice = {
  provider: string;
  model: string | null;
  label: string;
  default: boolean;
  local: boolean;
  available: boolean;
  reason: string | null;
};

export type AssistantInfo = {
  provider: string | null;
  /** Модель по умолчанию: "auto" или имя провайдера. */
  setting: string;
  /** Включённые модели (0.3.4); нет у старых резидентов. */
  enabled?: string[];
  models?: ModelChoice[];
  available: Record<string, ProviderAvailability>;
  knowledge_dir: string | null;
  checking: boolean;
  /** Нет у резидента прежней версии. */
  proxy?: ProxyInfo;
  /** Профиль сессии по умолчанию (`assist.profile`, 0.3.7): меню старта ставит его первым. */
  profile?: AgentProfile;
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
export type ProviderCheck = {
  ok: boolean; error?: string | null; provider?: string | null;
  /** Что ещё узнала проверка: у локальной модели — окно контекста. */
  detail?: string | null;
};

/** Модель на локальном сервере: размер (байты) и число параметров — у Ollama, контекст — у vLLM. */
export type LocalModel = { id: string; size?: number | null; params?: string | null; context?: number | null };

/** `POST /assistant/local-models`: модели локального сервера или почему их нет. */
export type LocalModels = {
  ok: boolean;
  models: LocalModel[];
  /** Текст для человека, когда `ok` — false. */
  error?: string | null;
  reason?: "bad_url" | "unreachable" | "auth" | "not_openai" | "empty" | null;
  /** Откуда список: `/v1/models` или родной список Ollama. */
  source?: "openai" | "ollama" | null;
  url?: string | null;
  missing?: boolean;
  warning?: string | null;
};

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
  /** Агент-участник (`assist.participant`, 0.3.6): что с ним и что он видит. */
  agent?: AgentInfo;
  /** Микрофон по голосам в этом сеансе; нет — неизвестно. */
  mic?: LiveMic;
};

/**
 * Микрофон в живом режиме (`assist.app.mic_view`): `split` — делить ли его по
 * голосам (настройка), `owner_profile` — есть ли образец голоса владельца.
 * Делить нужно, а образца нет — окно предлагает его записать (без него сосед
 * за тем же ноутбуком подписан «Вы»).
 */
export type LiveMic = { split: boolean; owner_profile: boolean };

// --- чат агента-участника (V4) ------------------------------------------------

/** Вид записи журнала чата (`assistant/chat.jsonl`). */
export type ChatKind = "agent" | "user" | "attachment" | "meeting" | "system" | "tool";
/**
 * Статус сообщения агента: `writing` — пишется (пузырь «Пишет…»), `shown` — показано,
 * `cancelled` — остановлено (текст остаётся), `failed` — ошибка (видна); `held`,
 * `dropped`, `superseded` в ленту не попадают.
 */
export type ChatStatus = "writing" | "shown" | "held" | "dropped" | "superseded" | "dismissed" | "cancelled" | "failed";
/** Реакции на сообщения агента: 👍 «Полезно», 👎 «Не по теме», ❓ «Поясни» (ключи — эмодзи). */
export type ChatReaction = "👍" | "👎" | "❓";

/**
 * Сообщение чата — свёрнутая запись журнала (`msg` + `patch`). Поля зависят от вида:
 * у агента — `text`, `status`, `buttons`, `pin`, `reactions`, `re`, `explains`; у пользователя —
 * `text`, `attachments` (id вложений), `via: "button"` (нажатие), `after_meeting`;
 * у вложения — `type`, `name`, `status`, `path`, `summary`; у события встречи — `event`.
 */
export type ChatMessage = {
  id: string;
  seq: number;
  /** Стенное время записи (секунды Unix). */
  at: number;
  kind: ChatKind;
  /** Секунды записи (во время встречи); после встречи нет. */
  t?: number;
  text?: string;
  /** У строки вызова инструмента (`kind: "tool"`, `event: "call"`) — `running` / `done` / `error` / `denied`. */
  status?: ChatStatus | "parsing" | "ready" | "removed" | ToolStatus;
  mode?: "reply" | "proactive";
  re?: string;
  /** Ответ агента на ❓: id сообщения, которое он поясняет. */
  explains?: string;
  buttons?: string[];
  pin?: boolean;
  reactions?: Partial<Record<ChatReaction, number>>;
  attachments?: string[];
  /** Нажатие кнопки агента; `reaction` — ❓ после встречи (просьба пояснить сообщение `re`). */
  via?: "button" | "reaction" | "command";
  client_id?: string;
  after_meeting?: boolean;
  error?: string;
  note?: string;
  merged_into?: string;
  /** Ход по расшифровке (`writing`), который допишется к этому сообщению (0.5): окно пишет его под ним. */
  merge_into?: string;
  /** Системная строка ворот согласия: «Ассистент хотел … — запрос заблокирован» (0.3.7). */
  gate?: boolean | ToolGate;
  /**
   * Карточка подтверждения Meet (0.3.7): точный вызов агента, ждёт «Разрешить один раз» / «Отклонить»;
   * её `tool_use_id` (0.4) ставит её в строку того же вызова. `command` — ответ слэш-команды Meet (0.4).
   */
  card?: "confirm" | "command";
  /** Ответ слэш-команды: имя команды, список команд (`/help`), MCP-серверы (`/mcp`), неизвестная — как набрана. */
  command?: string;
  items?: AgentCommand[];
  servers?: { name: string; status: string; error?: string }[];
  unknown?: string;
  /** Системная строка с ошибкой или пометкой. */
  level?: "error" | "info";
  /** Однократная строка: `agent_mode` — «Ассистент теперь сам выполняет обычные действия…» (0.4). */
  notice?: string;
  // --- строка вызова инструмента агента (0.4, `kind: "tool"`, `event: "call"`) ---
  tool_use_id?: string;
  /** Вид вызова: shell, read, search, edit, web, mcp, skill, agent, hook, other. */
  view?: string;
  added?: number;
  removed?: number;
  server?: string | null;
  input_preview?: string;
  /** Вывод (до 64 КБ); `truncated` — обрезан. */
  output_preview?: string;
  truncated?: boolean;
  duration_ms?: number;
  /** Реплика агента хода, которому принадлежит вызов. */
  reply?: string;
  parent?: string;
  tool?: string;
  title?: string;
  args?: string;
  /** «3 строки, 812 симв.» — размер вызова. */
  size?: string;
  /** Длинный вызов: начало и конец с пометкой «…⟨скрыто: …⟩…» (полностью — `args`). */
  preview?: string;
  /** Необычные параметры команды («без песочницы») — крупно. */
  warnings?: string[];
  /** Что разрешит «Разрешать такое до конца встречи» (нет — кнопки нет). */
  grant?: { key: string; label: string };
  /** Запись разрешения «до конца встречи». */
  label?: string;
  revoked?: boolean;
  decision?: "allow" | "allow_meeting" | "deny" | "timeout" | "cancelled" | "expired";
  /** Когда карточка перестанет ждать (секунды Unix). */
  expires_at?: number;
  event?: string;
  type?: "image" | "doc" | "kb_note" | "past_meeting";
  name?: string;
  path?: string;
  summary?: string;
  vision?: boolean;
  delivered?: boolean;
  /** У вложения-документа: исходный файл (чип-источник открывает его, если оболочка пустит). */
  source?: string;
  [key: string]: unknown;
};

/** Состояние вызова инструмента агента (строка хода работы). */
export type ToolStatus = "running" | "done" | "error" | "denied";
/** Решение ворот Meet по вызову: `auto`/`allowed` — «разрешено автоматически», `approved`/`declined` — «спросил вас», `denied` — «запрещено: …». */
export type ToolGate = { decision: string; label: string };
/** Слэш-команда для подсказки в строке ввода: `meet` — выполняет Meet, `cli` — уходит в CLI, `skill` — навык пользователя (тоже в CLI). */
export type AgentCommand = { name: string; hint: string; description: string; source: "meet" | "cli" | "skill" };
/** MCP-сервер сессии с состоянием (`connected`, `failed`, `needs-auth`, `pending`, `disabled`) — дополнение `/mcp …`. */
export type AgentMcpServer = { name: string; status: string };
/** Модель CLI (`initialize` → `models`) — дополнение `/model …`. */
export type AgentModel = { value: string; label: string };

/** Вложение — запись журнала `kind: "attachment"` (id `a<N>`). */
/** Вложение журнала; `path` — файл на диске (картинка — в `assistant/files/`), `type` — `image` или документ. */
export type ChatAttachment = ChatMessage & {
  kind: "attachment"; name: string; status: "parsing" | "ready" | "failed" | "removed"; path?: string; type?: string;
};

/** «Как часто писать»: ключ настроек (`assist.frequency`). */
export type AgentFrequency = "less" | "normal" | "more";
/** Та же настройка подписью — так её видит агент и `AgentInfo.frequency`. */
export type AgentFrequencyLabel = "реже" | "обычно" | "чаще";
/**
 * Профиль сессии ассистента (`assist.profile`, 0.3.7): «Рабочая встреча» (`work` —
 * база знаний, прошлые встречи, подсказки по делу) или «Личный» (`personal` —
 * созвон, стрим, видео: без базы знаний и рабочей рамки).
 */
export type AgentProfile = "work" | "personal";

/** `state.agent` и событие `agent`: что с агентом и что он видит. */
export type AgentInfo = {
  state: "listening" | "writing" | "error";
  error: string | null;
  provider: string;
  /** «Claude Code (claude-opus-5-5)»: у Claude Code — модель, которую запустил CLI (`system/init`), до первого хода — заданная. */
  label: string;
  vision: boolean;
  tools: boolean;
  /** Claude Code: модель, которую запустил CLI (`system/init`); null — ещё не известна (и у других провайдеров). */
  model?: string | null;
  /** Claude Code: модель из настроек (`llm.model`). */
  model_configured?: string | null;
  /** CLI запустил не ту модель, что в настройках. */
  model_mismatch?: boolean;
  deny_enforced: boolean;
  frequency: AgentFrequencyLabel;
  /** Профиль сессии; старый резидент — нет поля (как «Рабочая встреча»). */
  profile?: AgentProfile;
  session: "new" | "resumed" | "seeded" | null;
  /** Id ответа, который пишется сейчас. */
  writing: string | null;
  /** `kb` — есть карта (база знаний и/или прошлые встречи группы); `kb_docs` — в ней структура базы знаний. */
  sees: { conversation: boolean; kb: boolean; kb_docs?: boolean; materials: number; images: number };
  /** Расширенные возможности по согласию (`assist.agent_freedom`, 0.3.7). */
  freedom?: boolean;
  /**
   * Что агент может: `consent` — файлы, MCP, веб по согласию (`mcp` — имена серверов, если CLI их назвал);
   * `files` — Codex/OpenCode: только чтение файлов по просьбе; `read` — только чтение встречи и базы
   * знаний; `meet` — просит Meet прочитать (локальная модель).
   */
  can?: {
    /** 0.4: `act` — Codex/OpenCode в автомоде правят рабочие папки и выполняют команды сами. */
    mode: "consent" | "act" | "files" | "read" | "meet";
    mcp: string[] | null;
    /** Как действует по просьбе (`assist.agent_mode`). */
    agent_mode?: "auto" | "confirm";
    /** Claude Code: автомод на деле работает (false — недоступен, спрашивает каждое действие; null — ещё не известно). */
    auto?: boolean | null;
  };
  /** `/model имя` — модель, выбранная командой до конца сессии. */
  model_override?: string | null;
  /** Слэш-команды для подсказки на «/». Старый ребёнок — нет поля. */
  commands?: AgentCommand[];
  /** Для дополнения аргументов: MCP-серверы с состоянием (свежие после `/mcp`) и модели CLI. */
  mcp_servers?: AgentMcpServer[];
  models?: AgentModel[];
  /** Разрешения «до конца встречи» (× — отозвать). */
  grants?: { id: string; label: string }[];
};

/** `event: chat_partial`: текст ответа на сейчас (≤ 10 раз в секунду). */
export type ChatPartial = { id: string; text: string };

/** `GET /live/chat` и `event: chat_snapshot`: лента целиком (последние 200). */
export type ChatSnapshot = {
  messages: ChatMessage[];
  seq: number;
  agent?: AgentInfo;
  partial?: ChatPartial | null;
};

/**
 * `event: chat`: новое сообщение или правка; `seq` не новее известного — отбросить.
 * Правка может перевести сообщение в скрытый статус (`held`, `dropped`, `superseded`;
 * у вложения — `removed`) — окно тогда убирает его из ленты. Каждый ход агента
 * начинается с `add` в статусе `writing`, и ход, кончившийся молчанием, правкой
 * уходит в `dropped`: окно не показывает «Пишет…», пока нет текста (`chat_partial`)
 * или пока ответ человеку не пишется ~1,5 с (`live/chatModel.ts`).
 */
export type ChatEvent =
  | { seq: number; op: "add"; message: ChatMessage }
  | { seq: number; op: "patch"; id: string; set: Partial<ChatMessage> };

/** Ответ `POST /live/chat` (сразу, 202). `duplicate` — тот же `client_id` уже был. */
export type ChatPostResult = { id: string; queued: boolean; attachments: string[]; duplicate?: boolean };

/** Сообщение агенту: текст и/или id вложений; `client_id` — один на сообщение (повтор безопасен). */
export type ChatPost = { text: string; client_id: string; attachments?: string[] };

/** Ответ `/live/chat/paste` и `/live/chat/attach`: запись вложения (может быть `failed`). */
export type ChatAttachResult = {
  id: string;
  status: "parsing" | "ready" | "failed";
  attachment: ChatAttachment;
  error?: string;
};

export type AgentFrequencyResult = { frequency: AgentFrequency; label: AgentFrequencyLabel; live: boolean };
/** Ответ `PUT /live/profile`: профиль идущей сессии сменён (настройка по умолчанию — нет). */
export type AgentProfileResult = { profile: AgentProfile; label: string; live: boolean };

/** Прежний ассистент встречи до 0.3.6: подсказки и вопросы — только для чтения. */
export type LegacyAssistant = { hints: LiveHint[]; qa: QaItem[] };

/** `GET /recordings/{id}/chat`: чат записи после встречи. */
export type RecordingChat = {
  messages: ChatMessage[];
  seq: number;
  /** Встреча без чата (до 0.3.6): прежние подсказки и вопросы; иначе null. */
  legacy: LegacyAssistant | null;
  /** Идёт живой режим этой записи — писать через `/live/chat`. */
  live: boolean;
  /** Задача ответа («Продолжить разговор»), которая ждёт или идёт. */
  job: Job | null;
  /** Агент-участник включён в настройках (`assist.participant`); выключен — писать нельзя (409). Старый резидент — нет поля. */
  enabled?: boolean;
  /** Профиль, с которым шла сессия ассистента этой встречи; null или нет поля — неизвестно. */
  profile?: AgentProfile | null;
};

/** `GET /assistant/kb-docs`: документы базы знаний (пути от неё через «/», без исключённых). */
export type KbDocs = { root: string | null; docs: string[]; more: boolean };

/** Ответ `POST /recordings/{id}/chat`. */
export type ContinueChatResult = { message: ChatMessage; job: Job | null; duplicate?: boolean };

/** Событие резидента `chat.updated`: чат записи изменился — перечитать. */
export type ChatUpdatedEvent = BusEvent & { kind: "chat.updated"; id: string; partial?: ChatPartial };

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
 * стоит раньше них). `voice` — ключ голоса строки (`sys:3`, `mic:1`): имя
 * голоса может прийти позже (`event: voices`).
 */
export type LiveLine = { t: number; speaker: string | null; text: string; catchup?: boolean; voice?: string };

/**
 * `event: voices`: подписи голосов, пришедшие задним числом (`speakers`:
 * ключ голоса → подпись), и номера спрятанных строк-дублей (`hidden`, как
 * `id:` строк). Состояние целиком, а не дельта: приходит при каждом
 * подключении и при каждой смене `rev`. `session` — метка ассистента: номера
 * строк у нового ассистента в той же записи начинаются заново.
 */
export type LiveVoices = { rev: number; speakers: Record<string, string>; hidden: number[]; session?: string };

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
  /** «claude-code:sonnet» — подпись прежних версий; с 0.3.4 есть и `llm`. */
  model: string;
  llm?: LlmOrigin;
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
  /** Части, которых модель так и не дала (0.3.5): окно говорит об этом, а не показывает пустую полосу. */
  missing?: AnalysisFeature[];
  /** Сколько элементов каждой части отброшено проверкой (номер реплики вне встречи и т. п.). */
  dropped?: Partial<Record<AnalysisFeature, number>>;
  /** Куски встречи, которые не разобрались совсем, и причина первого (0.3.5). */
  unparsed?: { parts: number; of: number; reason?: string };
  /** Часть есть не у всех разобравшихся кусков: «1/3» (0.3.5). */
  partial?: Partial<Record<AnalysisFeature, string>>;
  /** Сервер обрезал промпт по своему контексту: сколько токенов видел и сколько было нужно (0.3.5). */
  context_cut?: { seen: number; need: number; ollama?: boolean; capped?: boolean };
};

export type AnalysisStateName = "none" | "queued" | "running" | "ready" | "stale" | "failed";

/** `GET /recordings/{id}/analysis`: прежний анализ отдаётся и пока идёт новый, и после сбоя. */
export type AnalysisState = {
  state: AnalysisStateName;
  analysis?: Analysis;
  error?: string;
  job?: Job;
  /** «failed»: модель, выбранная для упавшей задачи — «Повторить» идёт ею же (0.3.4). */
  provider?: string;
};

/** `POST /recordings/{id}/title/suggest`: название и откуда оно (свежий анализ или вызов модели). */
export type TitleSuggestion = { title: string; from: "analysis" | "model"; llm?: LlmOrigin };

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
  llm?: LlmOrigin;
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
  /** «failed»: модель, выбранная для упавшей задачи — «Повторить» идёт ею же (0.3.4). */
  provider?: string;
};
/** Применённая группа (в ответе и в шаге истории); `edited` — «как правильно» вписал человек. */
export type ImproveApplied = { from: string; to: string; kind: ImproveKind; count: number; edited?: boolean };
/**
 * `extra` — {группа: номера отмеченных мест из `more`}; `targets` — {группа: «как правильно»,
 * вписанное человеком вместо предложенного}; `created_at` — какой список видел человек.
 */
export type ImproveApplyRequest = {
  groups: string[];
  extra?: Record<string, number[]>;
  targets?: Record<string, string>;
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
