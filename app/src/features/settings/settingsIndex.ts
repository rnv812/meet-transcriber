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

import { OS_TEXT } from "../../lib/platform";

export type SectionId =
  | "appearance" | "app" | "sound" | "auto" | "asr" | "speakers" | "dictionary" | "engine"
  | "models" | "assistant" | "analysis" | "categories" | "export" | "jira"
  | "advanced" | "diagnostics" | "about";

/** Меню: группы и их разделы (порядок — как в окне). */
export const MENU_GROUPS: { title: string; items: { id: SectionId; title: string }[] }[] = [
  { title: "Общее", items: [{ id: "appearance", title: "Оформление" }, { id: "app", title: "Приложение" }] },
  { title: "Запись", items: [{ id: "sound", title: "Звук" }, { id: "auto", title: "Автозапись" }] },
  {
    title: "Расшифровка",
    items: [
      { id: "asr", title: "Распознавание" }, { id: "speakers", title: "Спикеры" },
      { id: "dictionary", title: "Словарь" }, { id: "engine", title: "Движок и модели" },
    ],
  },
  {
    title: "ИИ",
    items: [{ id: "models", title: "Модели ИИ" }, { id: "assistant", title: "Ассистент" }, { id: "analysis", title: "Анализ встречи" }],
  },
  {
    title: "Встречи",
    items: [{ id: "categories", title: "Категории" }, { id: "export", title: "Экспорт" }, { id: "jira", title: "Jira" }],
  },
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
};

export const SETTINGS_INDEX: SettingsEntry[] = [
  // Общее
  { section: "appearance", label: "Тема", hint: "Системная, светлая или тёмная", keywords: ["тёмная", "светлая", "dark", "light"] },
  { section: "appearance", label: "Палитра сияния", hint: "Цвет сияния, знака ИИ и главной кнопки", keywords: ["цвет", "акцент"] },
  { section: "appearance", label: "Вид сияния", hint: "Сияние или волны", keywords: ["волны", "фон"] },
  { section: "appearance", label: "Живое сияние", hint: "Медленный дрейф сияния", keywords: ["анимация", "движение"] },
  { section: "app", label: OS_TEXT.autostart, hint: "Приложение запускается и отслеживает звонки", keywords: ["автозапуск", "при входе"] },
  { section: "app", label: "Уведомления", hint: "Все, только важные или никаких", keywords: ["оповещения"] },
  { section: "speakers", label: "Ваше имя в расшифровке", hint: "Как подписаны ваши реплики", keywords: ["имя", "спикер"] },
  { section: "asr", label: "Расшифровывать сразу после записи", keywords: ["автоматически", "расшифровка"] },
  { section: "app", label: "Папка записей", hint: "Где лежат записи встреч", keywords: ["путь", "каталог"] },
  { section: "app", label: "Мастер первого запуска", keywords: ["мастер", "первая настройка"] },
  { section: "advanced", label: "Пройти мастер первого запуска заново", keywords: ["мастер", "первая настройка", "заново"] },
  // Запись
  { section: "sound", label: "Микрофон", hint: "Устройство и проверка уровня", keywords: ["mic", "устройство"] },
  { section: "sound", label: "Звук собеседников (вывод)", hint: "Откуда записывается звук звонка", keywords: ["динамики", "наушники", "loopback"] },
  { section: "auto", label: "Записывать звонки автоматически", keywords: ["автозапись"] },
  { section: "auto", label: "Программы звонков", hint: "Zoom, Teams, Telegram и свои", keywords: ["zoom", "teams", "telegram", "мессенджеры"] },
  { section: "auto", label: "Звонки в браузере", hint: "Браузеры, в которых вы созваниваетесь", keywords: ["chrome", "edge", "firefox", "яндекс"] },
  { section: "auto", label: "Ждать повторного подключения", hint: "Минуты до остановки записи после звонка", keywords: ["ожидание", "пауза"] },
  { section: "auto", label: "Минимальная длительность звонка", hint: "Короткие записи не расшифровываются сами" },
  {
    section: "auto", label: "Только если в заголовке окна сайт звонка", hint: "Строгий режим для браузеров",
    keywords: ["строгий режим", "диктовка"], advanced: true,
  },
  { section: "auto", label: "Сайты звонков", hint: "Google Meet, Телемост и другие", keywords: ["meet", "телемост", "заголовок окна"], advanced: true },
  // Расшифровка
  { section: "asr", label: "Устройство", hint: "Видеокарта или процессор", keywords: ["gpu", "cpu", "cuda", "видеокарта", "процессор"] },
  { section: "asr", label: "Движок", hint: "Whisper или GigaAM", keywords: ["whisper", "gigaam"] },
  { section: "asr", label: "Модель Whisper", keywords: ["whisper", "large", "small"] },
  { section: "asr", label: "Модель Whisper для записей не на русском", keywords: ["whisper", "английский", "язык"] },
  { section: "asr", label: "Модель GigaAM", keywords: ["gigaam", "русский"] },
  { section: "asr", label: "Язык речи", hint: "ru, en или auto", keywords: ["язык", "language"] },
  {
    section: "asr", label: "Уточнять время каждого слова", hint: "Точнее границы реплик",
    keywords: ["выравнивание", "align", "таймкоды"], advanced: true,
  },
  { section: "speakers", label: "Токен Hugging Face", hint: "Для разделения на спикеров", keywords: ["hf", "hugging face", "pyannote", "диаризация"] },
  { section: "speakers", label: "Мой голос", hint: "Образец голоса, чтобы узнавать вас", keywords: ["образец", "голос"] },
  { section: "speakers", label: "Порог узнавания голоса", keywords: ["голоса", "узнавание", "спикеры"] },
  { section: "speakers", label: "Различать голоса в микрофоне", hint: "Люди рядом с вами — отдельными спикерами",
    keywords: ["один микрофон", "переговорная", "коллега рядом", "несколько человек", "комната"] },
  { section: "speakers", label: "Убирать повторы соседа и эхо", hint: "Дубли слов из микрофона и звонка",
    keywords: ["дубли", "эхо", "повторы", "сосед"] },
  { section: "speakers", label: "Отмечать одновременную речь", hint: "Пометка «нахлёст»", keywords: ["нахлёст", "перебивают"] },
  { section: "dictionary", label: "Термины распознавания", hint: "Слова, которые модель должна знать", keywords: ["hotwords", "словарь", "термины"] },
  { section: "dictionary", label: "Исправления для будущих расшифровок", hint: "Как распознаётся → как правильно", keywords: ["замены", "правила"] },
  { section: "engine", label: "Состояние", hint: "Установлен ли движок расшифровки", keywords: ["движок", "переустановить"] },
  { section: "engine", label: "Видеокарта", keywords: ["gpu", "cuda"] },
  { section: "engine", label: "ffmpeg", keywords: ["аудио"] },
  { section: "engine", label: "Компоненты", keywords: ["pytorch", "cuda"] },
  { section: "engine", label: "Расположение", hint: "Где установлен движок", keywords: ["путь"] },
  { section: "engine", label: "Где хранить движок и модели", hint: "Перенос на другой диск", keywords: ["диск", "перенос", "место"] },
  { section: "engine", label: "Папка моделей", keywords: ["кэш", "hugging face"] },
  { section: "engine", label: "Папка моделей GigaAM", keywords: ["gigaam"] },
  // ИИ
  { section: "models", label: "Модели", hint: "Какие включены и какая по умолчанию", keywords: ["провайдер", "claude", "codex", "opencode", "локальная"] },
  { section: "models", label: "Модель Claude Code", keywords: ["sonnet", "opus", "haiku", "уровень рассуждений", "effort"] },
  { section: "models", label: "Уровень рассуждений Codex", keywords: ["effort", "reasoning", "думает"] },
  { section: "models", label: "Модель OpenCode" },
  { section: "models", label: "Адрес сервера", hint: "Локальная модель: LM Studio, Ollama, vLLM", keywords: ["ollama", "lm studio", "url"] },
  { section: "models", label: "Имя модели", hint: "Локальная модель", keywords: ["ollama", "qwen"] },
  { section: "models", label: "Прокси для подключения к моделям", keywords: ["proxy", "сеть"] },
  { section: "models", label: "Локальную модель — через прокси", keywords: ["proxy"], advanced: true },
  { section: "assistant", label: "Ассистент — участник встречи", hint: "Агент, который пишет во время встречи", keywords: ["участник", "агент"] },
  { section: "assistant", label: "Профиль по умолчанию", hint: "Рабочая встреча или личный", keywords: ["личный", "рабочая"] },
  { section: "assistant", label: "Как часто писать", keywords: ["частота", "реже", "чаще"] },
  {
    section: "assistant", label: "Расширенные возможности ассистента (файлы вне встречи, MCP, веб) — по согласию",
    keywords: ["mcp", "веб", "свобода", "согласие"],
  },
  {
    section: "assistant", label: "Нажимать кнопки ассистента голосом", hint: "Сказать надпись кнопки вслух",
    keywords: ["голос", "кнопки", "voice_buttons", "вслух"],
  },
  {
    section: "assistant", label: "Действия ассистента", hint: "Действует сам или спрашивает каждое действие",
    keywords: ["автомод", "разрешить", "подтверждение", "agent_mode"],
  },
  { section: "assistant", label: "База знаний для ассистента", hint: "Папка с материалами", keywords: ["obsidian", "папка", "kb"] },
  { section: "assistant", label: "Показывать ассистенту карту: базу знаний и прошлые встречи группы", keywords: ["карта", "прошлые встречи"] },
  { section: "assistant", label: "Не показывать ассистенту", hint: "Исключённые папки базы знаний", keywords: ["исключения", "исключённые"] },
  {
    section: "assistant", label: "Окно живой расшифровки, с", hint: "Как часто расшифровывается новый звук",
    keywords: ["живая расшифровка", "секунды"], advanced: true,
  },
  { section: "assistant", label: "Не отвлекать по умолчанию", hint: "Без подсветки и счётчиков", keywords: ["тихо", "колокольчик"], advanced: true },
  { section: "assistant", label: "Активность подсказок", hint: "Прежний режим (участник выключен)", keywords: ["подсказки", "сводка"], advanced: true },
  { section: "assistant", label: "Модель для живых подсказок", hint: "Прежний режим (участник выключен)", keywords: ["подсказки", "быстрее"], advanced: true },
  { section: "assistant", label: "Сколько подсказок держать", hint: "Прежний режим (участник выключен)", keywords: ["подсказки"], advanced: true },
  { section: "analysis", label: "Анализировать встречу после расшифровки", keywords: ["анализ", "автоматически"] },
  { section: "analysis", label: "Типы реплик", hint: "Размечать и показывать: вопрос, решение, задача, риск", keywords: ["значки", "разметка", "фильтры"] },
  { section: "analysis", label: "Важность", hint: "Размечать и показывать: полоса у важных реплик", keywords: ["важные реплики", "разметка"] },
  { section: "analysis", label: "Главы", hint: "Размечать и показывать: заголовки глав", keywords: ["разметка", "темы"] },
  { section: "analysis", label: "Наблюдения", hint: "Размечать и показывать: блок «Наблюдения»", keywords: ["разметка", "противоречия"] },
  { section: "analysis", label: "Ссылки на задачи", hint: "Размечать и показывать: задачи Jira", keywords: ["jira", "разметка"] },
  { section: "analysis", label: "Кривая важности над плеером", keywords: ["плеер", "кривая"] },
  { section: "analysis", label: "Подписи глав на полосе плеера", keywords: ["плеер", "главы"] },
  { section: "analysis", label: "Улучшать расшифровку автоматически после распознавания", keywords: ["улучшить", "исправления"] },
  { section: "analysis", label: "Название встречи", hint: "Модель предлагает название", keywords: ["заголовок"] },
  { section: "analysis", label: "Придумывать название встречи", hint: "Название ставится само", keywords: ["заголовок", "автоматически"] },
  { section: "analysis", label: "Определять категорию автоматически", keywords: ["категория"] },
  // Встречи
  { section: "categories", label: "Категории встреч", hint: "Названия, цвета и описания для ИИ", keywords: ["категория", "цвет"] },
  { section: "export", label: "Папка для встреч", hint: "Куда выгружаются встречи в базе знаний", keywords: ["obsidian", "база знаний", "выгрузка"] },
  { section: "export", label: "Шаблон имени папки", keywords: ["подстановки", "выгрузка"] },
  { section: "export", label: "Имя файла расшифровки", keywords: ["выгрузка"] },
  { section: "export", label: "Имя файла итогов", keywords: ["выгрузка"] },
  { section: "export", label: "Что выгружать", hint: "Расшифровку, итоги, звук", keywords: ["выгрузка", "аудио"] },
  { section: "export", label: "Выгружать автоматически после расшифровки", keywords: ["выгрузка", "автоматически"] },
  { section: "jira", label: "Ссылки на задачи Jira", hint: "Задачи из встречи открываются в Jira", keywords: ["jira", "задачи"] },
  { section: "jira", label: "Адрес Jira", keywords: ["url", "jira"] },
  { section: "jira", label: "Проекты", hint: "Ключи проектов Jira и как их произносят", keywords: ["jira", "ключ проекта"] },
  { section: "jira", label: "Проект по умолчанию", keywords: ["jira"] },
  { section: "jira", label: "Шаблон ключа для текста", hint: "Регулярное выражение для ключей задач", keywords: ["jira", "regex"], advanced: true },
  // Система
  { section: "advanced", label: "Запускать команду после записи", keywords: ["хук", "hook", "скрипт"] },
  { section: "advanced", label: "Команда", hint: "Программа и аргументы", keywords: ["хук", "hook"] },
  { section: "advanced", label: "Текст для {prompt}", keywords: ["хук", "prompt"] },
  { section: "advanced", label: "Окно регулярной встречи", keywords: ["хук", "регулярная"] },
  { section: "advanced", label: "Сообщать другим программам о занятости видеокарты", keywords: ["маркер", "gpu", "видеокарта"] },
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
