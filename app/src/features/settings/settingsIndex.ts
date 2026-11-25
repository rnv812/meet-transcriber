/**
 * Меню настроек и индекс поиска по ним (0.4).
 *
 * `MENU_GROUPS` — группы и разделы в порядке окна. `SETTINGS_INDEX` —
 * статический список строк всех разделов: подпись, как в окне, пояснение и
 * слова, по которым строку ищут иначе (`keywords`); `advanced` — строка лежит
 * в «Тонкой настройке» раздела (поиск её раскрывает). Тест
 * `settingsIndex.test.tsx` обходит все разделы и сверяет: каждая подпись
 * строки есть здесь.
 */

import { IS_MAC, OS_TEXT } from "../../lib/platform";
import { FREEDOM_LABEL, PARTICIPANT_LABEL } from "./ParticipantRows";
import { PROCESS_TAP_LABEL, SKIP_BLUETOOTH_LABEL } from "./SoundSection";

export type SectionId =
  | "app" | "sound" | "auto" | "asr" | "speakers" | "dictionary"
  | "models" | "assistant" | "categories" | "export"
  | "advanced" | "diagnostics" | "about";

/**
 * Блок внутри раздела, к которому ведёт прежний id (0.6.2: 17 разделов
 * сведены в 13): «Оформление» и «Приложение» — в «Общем», «Движок и модели» —
 * в «Распознавании», «Анализ встречи» — в «Моделях и анализе», «Jira» — в
 * «Экспорте и Jira». Якорь — `data-block` у первой карточки блока.
 */
export type BlockId = "appearance" | "app" | "engine" | "analysis" | "jira";

/** Меню: группы и их разделы (порядок — как в окне). */
export const MENU_GROUPS: { title: string; items: { id: SectionId; title: string }[] }[] = [
  { title: "Общее", items: [{ id: "app", title: "Общее" }] },
  { title: "Запись", items: [{ id: "sound", title: "Звук" }, { id: "auto", title: "Автозапись" }] },
  {
    title: "Расшифровка",
    items: [{ id: "asr", title: "Распознавание" }, { id: "speakers", title: "Спикеры" }, { id: "dictionary", title: "Словарь" }],
  },
  { title: "ИИ", items: [{ id: "models", title: "Модели и анализ" }, { id: "assistant", title: "Ассистент" }] },
  { title: "Встречи", items: [{ id: "categories", title: "Категории" }, { id: "export", title: "Экспорт и Jira" }] },
  {
    title: "Система",
    items: [{ id: "advanced", title: "Дополнительно" }, { id: "diagnostics", title: "Диагностика" }, { id: "about", title: "О программе" }],
  },
];

export const MENU = MENU_GROUPS.flatMap((g) => g.items);

/** Название раздела в меню. */
export const sectionTitle = (id: SectionId): string => MENU.find((m) => m.id === id)?.title ?? id;

/** Строка настроек в индексе поиска. */
export type SettingsEntry = {
  section: SectionId;
  /** Подпись строки — как в окне (по ней поиск находит строку на странице). */
  label: string;
  hint?: string;
  /** Другие слова, которыми эту настройку называют. */
  keywords?: string[];
  /** Строка в «Тонкой настройке» раздела. */
  advanced?: boolean;
  /**
   * Подписи переключателей, под которыми строка скрыта, от внешнего к
   * внутреннему: пока какой-то выключен, строки в окне нет. Поиск называет
   * первый выключенный («Включите «…», чтобы увидеть «…»»).
   */
  requires?: string[];
  /**
   * Ключи настроек строки (`секция.ключ`), которые «Сбросить раздел» возвращает
   * к умолчанию и по которым строка помечена «изменено». Не входят никогда:
   * согласия и история (`analysis.consent`, `analysis.auto`, условия, мастер),
   * ключи с умолчанием по истории (`llm.provider`, `ui.theme`), закрытые папки
   * ассистента, его режим и расширенные возможности, пути, категории, секреты
   * и параметры запуска агента. Не входят и ключи мимо черновика (оформление,
   * переключатель автозаписи) и списки, собранные пользователем (правила
   * замены, проекты Jira, включённые модели — от них зависит провайдер).
   */
  keys?: string[];
};

const UNDER_PARTICIPANT = [PARTICIPANT_LABEL];

export const SETTINGS_INDEX: SettingsEntry[] = [
  // Общее
  { section: "app", label: "Тема", hint: "Системная, светлая или тёмная", keywords: ["тёмная", "светлая", "dark", "light"] },
  { section: "app", label: "Палитра сияния", hint: "Цвет сияния, знака ИИ и главной кнопки", keywords: ["цвет", "акцент"] },
  { section: "app", label: "Вид сияния", hint: "Сияние или волны", keywords: ["волны", "фон"] },
  { section: "app", label: "Живое сияние", hint: "Медленный дрейф сияния", keywords: ["анимация", "движение"] },
  { section: "app", label: OS_TEXT.autostart, hint: "Приложение запускается и отслеживает звонки", keywords: ["автозапуск", "при входе"] },
  { section: "app", label: "Уведомления", keys: ["ui.notifications"], hint: "Все, только важные или никаких", keywords: ["оповещения"] },
  { section: "speakers", label: "Ваше имя в расшифровке", keys: ["recording.speaker_name"], hint: "Как подписаны ваши реплики", keywords: ["имя", "спикер"] },
  { section: "asr", label: "Расшифровывать сразу после записи", keys: ["recording.auto_transcribe"], keywords: ["автоматически", "расшифровка"] },
  { section: "app", label: "Папка записей", hint: "Где лежат записи встреч", keywords: ["путь", "каталог"] },
  { section: "app", label: "Мастер первого запуска", keywords: ["мастер", "первая настройка"] },
  { section: "advanced", label: "Пройти мастер первого запуска заново", keywords: ["мастер", "первая настройка", "заново"] },
  // Запись
  { section: "sound", label: "Микрофон", keys: ["recording.mic_device"], hint: "Устройство и проверка уровня", keywords: ["mic", "устройство"] },
  { section: "sound", label: OS_TEXT.otherSide, keys: ["recording.output_device"], hint: "Откуда записывается звук звонка", keywords: ["динамики", "наушники", "loopback"] },
  // Строка только на macOS.
  ...(IS_MAC ? [{ section: "sound" as const, label: SKIP_BLUETOOTH_LABEL, keys: ["recording.skip_bluetooth_mic"], hint: "Встроенный микрофон вместо наушников", keywords: ["airpods", "bluetooth", "гарнитура", "наушники"] }] : []),
  // macOS 14.4+ (на более старой выключателя в разделе нет).
  ...(IS_MAC ? [{ section: "sound" as const, label: PROCESS_TAP_LABEL, keys: ["recording.mac_capture"], hint: "Core Audio вместо ScreenCaptureKit", keywords: ["process tap", "screencapturekit", "запись экрана", "системный звук"] }] : []),
  { section: "auto", label: "Записывать звонки автоматически", keywords: ["автозапись"] },
  { section: "auto", label: "Программы звонков", keys: ["auto_record.processes"], hint: "Zoom, Teams, Telegram и свои", keywords: ["zoom", "teams", "telegram", "мессенджеры"] },
  { section: "auto", label: "Звонки в браузере", keys: ["auto_record.browsers"], hint: "Браузеры, в которых вы созваниваетесь", keywords: ["chrome", "edge", "firefox", "яндекс"] },
  { section: "auto", label: "Ждать повторного подключения", keys: ["auto_record.grace_minutes"], hint: "Минуты до остановки записи после звонка", keywords: ["ожидание", "пауза"] },
  { section: "auto", label: "Минимальная длительность звонка", keys: ["auto_record.min_call_seconds"], hint: "Короткие записи не расшифровываются сами" },
  {
    section: "auto", label: "Только если в заголовке окна сайт звонка", keys: ["auto_record.browser_require_site"], hint: "Строгий режим для браузеров",
    keywords: ["строгий режим", "диктовка"], advanced: true,
  },
  { section: "auto", label: "Сайты звонков", keys: ["auto_record.call_sites"], hint: "Google Meet, Телемост и другие", keywords: ["meet", "телемост", "заголовок окна"], advanced: true },
  // Расшифровка
  { section: "asr", label: "Устройство", keys: ["asr.device"], hint: "Видеокарта или процессор", keywords: ["gpu", "cpu", "cuda", "видеокарта", "процессор"] },
  { section: "asr", label: "Движок", keys: ["asr.backend", "asr.cpu_backend"], hint: "Whisper или GigaAM", keywords: ["whisper", "gigaam"] },
  { section: "asr", label: "Модель Whisper", keys: ["asr.model", "asr.cpu_model"], keywords: ["whisper", "large", "small"] },
  { section: "asr", label: "Модель Whisper для записей не на русском", keys: ["asr.model", "asr.cpu_model"], keywords: ["whisper", "английский", "язык"] },
  { section: "asr", label: "Модель GigaAM", keys: ["asr.gigaam_model"], keywords: ["gigaam", "русский"] },
  { section: "asr", label: "Язык речи", keys: ["asr.language"], hint: "ru, en или auto", keywords: ["язык", "language"] },
  {
    section: "asr", label: "Уточнять время каждого слова", keys: ["asr.align"], hint: "Точнее границы реплик",
    keywords: ["выравнивание", "align", "таймкоды"], advanced: true,
  },
  { section: "speakers", label: "Токен Hugging Face", hint: "Для разделения на спикеров", keywords: ["hf", "hugging face", "pyannote", "диаризация"] },
  { section: "speakers", label: "Мой голос", hint: "Образец голоса, чтобы узнавать вас", keywords: ["образец", "голос"] },
  { section: "speakers", label: "Порог узнавания голоса", keys: ["asr.voice_threshold"], keywords: ["голоса", "узнавание", "спикеры"] },
  { section: "speakers", label: "Различать голоса в микрофоне", keys: ["asr.mic_speakers"], hint: "Люди рядом с вами — отдельными спикерами",
    keywords: ["один микрофон", "переговорная", "коллега рядом", "несколько человек", "комната"] },
  { section: "speakers", label: "Убирать повторы соседа и эхо", keys: ["asr.mic_dedupe"], hint: "Дубли слов из микрофона и звонка",
    keywords: ["дубли", "эхо", "повторы", "сосед"] },
  { section: "speakers", label: "Отмечать одновременную речь", keys: ["asr.overlap"], hint: "Пометка «нахлёст»", keywords: ["нахлёст", "перебивают"] },
  { section: "speakers", label: "Быстрее разделять на спикеров", keys: ["asr.fast_diarization"], hint: "Вдвое быстрее, редкие участники могут слиться",
    keywords: ["скорость", "диаризация", "ускорить", "спикеры"] },
  { section: "dictionary", label: "Термины распознавания", hint: "Слова, которые модель должна знать", keywords: ["hotwords", "словарь", "термины"] },
  { section: "dictionary", label: "Исправления для будущих расшифровок", hint: "Как распознаётся → как правильно", keywords: ["замены", "правила"] },
  { section: "asr", label: "Состояние", hint: "Установлен ли движок расшифровки", keywords: ["движок", "переустановить"] },
  { section: "asr", label: "Видеокарта", keywords: ["gpu", "cuda"] },
  { section: "asr", label: "ffmpeg", keywords: ["аудио"] },
  { section: "asr", label: "Компоненты", keywords: ["pytorch", "cuda"] },
  { section: "asr", label: "Расположение", hint: "Где установлен движок", keywords: ["путь"] },
  { section: "asr", label: "Где хранить движок и модели", hint: "Перенос на другой диск", keywords: ["диск", "перенос", "место"] },
  { section: "asr", label: "Папка моделей", keywords: ["кэш", "hugging face"] },
  { section: "asr", label: "Папка моделей GigaAM", keywords: ["gigaam"] },
  // ИИ
  { section: "models", label: "Модели", hint: "Какие включены и какая по умолчанию", keywords: ["провайдер", "claude", "codex", "opencode", "antigravity", "agy", "gemini", "локальная"] },
  { section: "models", label: "Модель Claude Code", keys: ["llm.model", "llm.effort"], keywords: ["sonnet", "opus", "haiku", "уровень рассуждений", "effort"] },
  { section: "models", label: "Уровень рассуждений Codex", keys: ["llm.codex_effort"], keywords: ["effort", "reasoning", "думает"] },
  { section: "models", label: "Модель OpenCode", keys: ["llm.opencode_model"] },
  { section: "models", label: "Модель Antigravity CLI", keys: ["llm.agy_model"], keywords: ["agy", "gemini", "antigravity"] },
  { section: "models", label: "Адрес сервера", keys: ["llm.base_url"], hint: "Локальная модель: LM Studio, Ollama, vLLM", keywords: ["ollama", "lm studio", "url"] },
  { section: "models", label: "Имя модели", keys: ["llm.local_model"], hint: "Локальная модель", keywords: ["ollama", "qwen"] },
  { section: "models", label: "Прокси для подключения к моделям", keys: ["llm.proxy"], keywords: ["proxy", "сеть"] },
  { section: "models", label: "Локальную модель — через прокси", keys: ["llm.local_via_proxy"], keywords: ["proxy"], advanced: true },
  { section: "assistant", label: "Ассистент — участник встречи", keys: ["assist.participant"], hint: "Агент, который пишет во время встречи", keywords: ["участник", "агент"] },
  { section: "assistant", label: "Профиль по умолчанию", keys: ["assist.profile"], hint: "Рабочая встреча или личный", keywords: ["личный", "рабочая"], requires: UNDER_PARTICIPANT },
  { section: "assistant", label: "Как часто писать", keys: ["assist.frequency"], keywords: ["частота", "реже", "чаще"], requires: UNDER_PARTICIPANT },
  {
    section: "assistant", label: "Расширенные возможности ассистента (файлы вне встречи, MCP, веб) — по согласию",
    keywords: ["mcp", "веб", "свобода", "согласие"], requires: UNDER_PARTICIPANT,
  },
  {
    section: "assistant", label: "Нажимать кнопки ассистента голосом", keys: ["assist.voice_buttons"], hint: "Сказать надпись кнопки вслух",
    keywords: ["голос", "кнопки", "voice_buttons", "вслух"], requires: UNDER_PARTICIPANT,
  },
  {
    section: "assistant", label: "Действия ассистента", hint: "Действует сам или спрашивает каждое действие",
    keywords: ["автомод", "разрешить", "подтверждение", "agent_mode"], requires: [PARTICIPANT_LABEL, FREEDOM_LABEL],
  },
  { section: "assistant", label: "База знаний для ассистента", hint: "Папка с материалами", keywords: ["obsidian", "папка", "kb"] },
  { section: "assistant", label: "Показывать ассистенту карту: базу знаний и прошлые встречи группы", keys: ["assist.kb_map"], keywords: ["карта", "прошлые встречи"], requires: UNDER_PARTICIPANT },
  { section: "assistant", label: "Не показывать ассистенту", hint: "Исключённые папки базы знаний", keywords: ["исключения", "исключённые"], requires: UNDER_PARTICIPANT },
  {
    section: "assistant", label: "Окно живой расшифровки, с", keys: ["assist.window_seconds"], hint: "Как часто расшифровывается новый звук",
    keywords: ["живая расшифровка", "секунды"], advanced: true,
  },
  { section: "assistant", label: "Не отвлекать по умолчанию", keys: ["assist.quiet_default"], hint: "Без подсветки и счётчиков", keywords: ["тихо", "колокольчик"], advanced: true },
  {
    section: "assistant", label: "Строка сессии в окне ассистента", keys: ["ui.live_session_bar"],
    hint: "Модель, профиль и «Что я знаю» над чатом", keywords: ["скрыть строку", "шапка сессии", "панель", "live_session_bar"],
    advanced: true,
  },
  { section: "assistant", label: "Активность подсказок", keys: ["assist.activity"], hint: "Прежний режим (участник выключен)", keywords: ["подсказки", "сводка"], advanced: true },
  { section: "assistant", label: "Модель для живых подсказок", keys: ["assist.hints_model"], hint: "Прежний режим (участник выключен)", keywords: ["подсказки", "быстрее"], advanced: true },
  { section: "assistant", label: "Сколько подсказок держать", keys: ["assist.max_hints"], hint: "Прежний режим (участник выключен)", keywords: ["подсказки"], advanced: true },
  { section: "models", label: "Анализировать встречу после расшифровки", keywords: ["анализ", "автоматически"] },
  { section: "models", label: "Типы реплик", keys: ["analysis.types", "transcript_view.types"], hint: "Размечать и показывать: вопрос, решение, задача, риск", keywords: ["значки", "разметка", "фильтры"] },
  { section: "models", label: "Важность", keys: ["analysis.importance", "transcript_view.importance"], hint: "Размечать и показывать: полоса у важных реплик", keywords: ["важные реплики", "разметка"] },
  { section: "models", label: "Главы", keys: ["analysis.chapters", "transcript_view.chapters"], hint: "Размечать и показывать: заголовки глав", keywords: ["разметка", "темы"] },
  { section: "models", label: "Наблюдения", keys: ["analysis.insights", "transcript_view.insights"], hint: "Размечать и показывать: блок «Наблюдения»", keywords: ["разметка", "противоречия"] },
  { section: "models", label: "Ссылки на задачи", keys: ["analysis.issues"], hint: "Размечать и показывать: задачи Jira", keywords: ["jira", "разметка"] },
  { section: "models", label: "Кривая важности над плеером", keys: ["transcript_view.curve"], keywords: ["плеер", "кривая"] },
  { section: "models", label: "Подписи глав на полосе плеера", keys: ["transcript_view.bar_labels"], keywords: ["плеер", "главы"] },
  { section: "models", label: "Улучшать расшифровку автоматически после распознавания", keys: ["analysis.improve_auto"], keywords: ["улучшить", "исправления"] },
  { section: "models", label: "Название встречи", keys: ["analysis.title"], hint: "Модель предлагает название", keywords: ["заголовок"] },
  { section: "models", label: "Придумывать название встречи", keys: ["assistant.auto_title"], hint: "Название ставится само", keywords: ["заголовок", "автоматически"] },
  { section: "models", label: "Определять категорию автоматически", keys: ["analysis.category"], keywords: ["категория"] },
  // Встречи
  { section: "categories", label: "Категории встреч", hint: "Названия, цвета и описания для ИИ", keywords: ["категория", "цвет"] },
  { section: "export", label: "Папка для встреч", hint: "Куда выгружаются встречи в базе знаний", keywords: ["obsidian", "база знаний", "выгрузка"] },
  { section: "export", label: "Шаблон имени папки", keys: ["export.folder_template"], keywords: ["подстановки", "выгрузка"] },
  { section: "export", label: "Папки выгруженных встреч", hint: "Переложить по шаблону", keywords: ["выгрузка", "перенести", "шаблон"] },
  { section: "export", label: "Имя файла расшифровки", keys: ["export.transcript_name"], keywords: ["выгрузка"] },
  { section: "export", label: "Имя файла итогов", keys: ["export.summary_name"], keywords: ["выгрузка"] },
  { section: "export", label: "Что выгружать", keys: ["export.include_transcript", "export.include_summary", "export.include_audio", "export.include_srt"], hint: "Расшифровку, итоги, звук", keywords: ["выгрузка", "аудио"] },
  { section: "export", label: "Выгружать автоматически после расшифровки", keys: ["export.auto_export"], keywords: ["выгрузка", "автоматически"] },
  { section: "export", label: "Ссылки на задачи Jira", keys: ["transcript_view.jira"], hint: "Задачи из встречи открываются в Jira", keywords: ["jira", "задачи"] },
  { section: "export", label: "Адрес Jira", keys: ["integrations.jira_base_url"], keywords: ["url", "jira"] },
  { section: "export", label: "Проекты", hint: "Ключи проектов Jira и как их произносят", keywords: ["jira", "ключ проекта"] },
  { section: "export", label: "Проект по умолчанию", keys: ["integrations.jira_default_project"], keywords: ["jira"] },
  { section: "export", label: "Шаблон ключа для текста", keys: ["integrations.jira_pattern"], hint: "Регулярное выражение для ключей задач", keywords: ["jira", "regex"], advanced: true },
  // Система
  { section: "advanced", label: "Запускать команду после записи", keys: ["hooks.post_record"], keywords: ["хук", "hook", "скрипт"] },
  { section: "advanced", label: "Команда", keys: ["hooks.command"], hint: "Программа и аргументы", keywords: ["хук", "hook"] },
  { section: "advanced", label: "Текст для {prompt}", keys: ["hooks.prompt"], keywords: ["хук", "prompt"] },
  { section: "advanced", label: "Окно регулярной встречи", keys: ["hooks.recurring_window"], keywords: ["хук", "регулярная"] },
  { section: "advanced", label: "Сообщать другим программам о занятости видеокарты", keys: ["integrations.gpu_marker"], keywords: ["маркер", "gpu", "видеокарта"] },
  { section: "advanced", label: "Путь к файлу-маркеру", hint: "gpu.lock", keywords: ["маркер", "gpu"], advanced: true },
  { section: "advanced", label: "Дополнительные параметры", hint: "Запуск агента во вкладке «Агент»", keywords: ["агент", "аргументы", "claude", "codex"] },
  { section: "advanced", label: "Переменные окружения", hint: "Запуск агента во вкладке «Агент»", keywords: ["агент", "env"] },
  { section: "diagnostics", label: "Папка данных", keywords: ["путь", "данные"] },
  { section: "diagnostics", label: "Папка записей", keywords: ["путь"] },
  { section: "diagnostics", label: "Файл настроек", keywords: ["config.json"] },
  { section: "diagnostics", label: "Журнал автозаписи", keywords: ["лог", "журнал"] },
  { section: "diagnostics", label: "Режим", hint: "Установленное приложение или запуск из репозитория" },
  { section: "about", label: "Версия" },
  { section: "about", label: "Обновления", hint: "Проверить обновления", keywords: ["обновить", "github"] },
  { section: "about", label: "Другие версии", hint: "Установить другую версию",
    keywords: ["откат", "откатить", "старая версия", "выпуски", "вернуть версию", "бэкап"] },
  { section: "about", label: "Журнал обновления", keywords: ["update.log", "лог"] },
  { section: "about", label: "Обновить вручную", keywords: ["установщик", "скачать"] },
  { section: "about", label: "Папка данных", hint: "Настройки, журналы и база голосов", keywords: ["путь", "данные"] },
  { section: "about", label: "Авторы", keywords: ["лицензия"] },
];

/** Сбрасываемые ключи раздела (`keys` его строк), без повторов; нет — пусто. */
export function sectionKeys(section: SectionId): string[] {
  return [...new Set(SETTINGS_INDEX.filter((e) => e.section === section).flatMap((e) => e.keys ?? []))];
}

/** Для сравнения: нижний регистр, «ё» как «е». */
const norm = (s: string) => s.toLowerCase().replace(/ё/g, "е");

/**
 * Подпись, разбитая на куски по совпавшим словам запроса (0.5: совпавшее —
 * жирным в выдаче). Регистр и «ё» не важны; пересекающиеся совпадения
 * сливаются в один кусок.
 */
export function matchParts(label: string, query: string): { text: string; hit: boolean }[] {
  const words = norm(query).split(/\s+/).filter(Boolean);
  const text = norm(label);
  const hit = new Array<boolean>(label.length).fill(false);
  for (const w of words) {
    for (let at = text.indexOf(w); at >= 0; at = text.indexOf(w, at + 1)) hit.fill(true, at, at + w.length);
  }
  const parts: { text: string; hit: boolean }[] = [];
  for (let i = 0; i < label.length; i++) {
    const last = parts.at(-1);
    if (last && last.hit === hit[i]) last.text += label[i];
    else parts.push({ text: label[i]!, hit: hit[i]! });
  }
  return parts;
}

/**
 * Поиск по индексу: все слова запроса должны найтись в подписи, пояснении,
 * ключевых словах или названии раздела. Выше — совпадение в начале подписи,
 * затем в подписи, затем остальное; порядок внутри — как в окне.
 */
export function searchSettings(query: string, limit = 50): SettingsEntry[] {
  const words = norm(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  const scored: { entry: SettingsEntry; score: number; order: number }[] = [];
  SETTINGS_INDEX.forEach((entry, order) => {
    const label = norm(entry.label);
    const text = [label, norm(entry.hint ?? ""), ...(entry.keywords ?? []).map(norm), norm(sectionTitle(entry.section))].join(" ");
    if (!words.every((w) => text.includes(w))) return;
    const first = words[0]!;
    const score = label.startsWith(first) ? 0 : words.every((w) => label.includes(w)) ? 1 : 2;
    scored.push({ entry, score, order });
  });
  return scored.sort((a, b) => a.score - b.score || a.order - b.order).slice(0, limit).map((s) => s.entry);
}
