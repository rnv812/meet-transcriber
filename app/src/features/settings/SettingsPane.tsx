/**
 * Настройки: слева меню — заголовок «Настройки», поиск, шесть групп («Общее»,
 * «Запись», «Расшифровка», «ИИ», «Встречи», «Система») и 13 разделов
 * (0.6.2; в 0.4 — 17: «Оформление» и «Приложение» сведены в «Общее», «Движок и
 * модели» — в «Распознавание», «Анализ встречи» — в «Модели и анализ», «Jira» —
 * в «Экспорт и Jira»; ключи настроек прежние); справа — колонка раздела (по
 * центру, не шире 760 px; шапка с
 * «Сохранить» и строкой «см. также» — над той же колонкой), внутри —
 * подгруппы-карточки. Плотность обычная: строки, поля и кнопки — 32 px по
 * одной линейке (доводка 0.4, C1).
 *
 * Черновик по группам (`auto_record`, `asr`, …) + «Сохранить/Сбросить»; PATCH
 * принимает секции целиком. Исключение — переключатель автозаписи: резидент
 * применяет его на лету (`POST /auto-record`). Всё остальное в `auto_record`
 * читается при старте — честно говорим, что нужен перезапуск.
 *
 * Черновик общий на все разделы, но видно, где именно правки: точка у раздела
 * в меню. Уйти из настроек с несохранённым — вопрос (`guardRef`, его задаёт
 * App); «Сбросить…» тоже спрашивает.
 *
 * «Изменено» и «Сбросить раздел» (0.6.2): ключи строк раздела — `keys` в
 * индексе; умолчания этого пользователя (с его историей и согласиями) и какие
 * ключи от них отличаются, отвечает резидент (`GET /settings/defaults`), сам
 * он ничего не пишет. Строка, у которой значение в черновике не по умолчанию,
 * помечена словом «изменено» (`ChangedLabels`). «Сбросить раздел» показывает
 * список опций, которые вернутся, и кладёт умолчания в черновик; пишет их
 * обычное «Сохранить».
 *
 * Прежние id разделов (глубокие ссылки трея, карточек, оболочки) ведут в новые
 * через `LEGACY_SECTION`; если прежний раздел стал блоком, окно прокручивает к
 * нему (`data-block` у первой карточки блока).
 *
 * Над меню — «Поиск по настройкам» (SettingsSearch, Ctrl+F): выдача вместо
 * меню; выбор открывает раздел, раскрывает «Тонкую настройку» (если строка в
 * ней), прокручивает к строке и подсвечивает её на 1,5 с. Пока раздел грузит
 * свои данные (индикатор загрузки, `aria-busy`), поиск ждёт их, а не
 * фиксированное время. Строка, скрытая выключенным переключателем (`requires`
 * в индексе): раздел открыт, первый выключенный переключатель подсвечен, под
 * ним строка «Включите «…», чтобы увидеть «…»» (`aria-live`); она держится,
 * пока переключатель не изменён или не выбран другой результат. Меню и индекс —
 * `settingsIndex.ts`.
 *
 * «Как сейчас настроено» (0.6.2, `NowLine`): часть строки ведёт к своей опции
 * тем же путём, что выбор в поиске (`OpenOption`); `scope` — блок, внутри
 * которого искать строку, если подпись в разделе повторяется.
 */

import { useCallback, useEffect, useId, useRef, useState, type MutableRefObject } from "react";
import { createPortal } from "react-dom";
import {
  type Devices, type Endpoint, type Processes,
  type SettingsDefaults,
  NoResidentError, getDevices, getProcesses, getSettings, getSettingsDefaults, patchSettings, setAutoRecord,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { EmptyState } from "../../ui/EmptyState";
import { Loading, StatusSlot } from "../../ui/Loading";
import { PaneResizer } from "../../ui/PaneResizer";
import { Tip } from "../../ui/Tip";
import { About } from "./About";
import { type Appearance, DEFAULT_APPEARANCE } from "../../theme/appearance";
import { AdvancedSection } from "./AdvancedSection";
import { agentLaunchChangesInvalid } from "./AgentLaunchSection";
import { AnalysisSection } from "./AnalysisSection";
import { AppearanceSection } from "./AppearanceSection";
import { AppSection } from "./AppSection";
import { AsrSection } from "./AsrSection";
import { AssistantSection, assistantChangesInvalid } from "./AssistantSection";
import { AutoSection } from "./AutoSection";
import {
  CategoriesSection, categoriesChanged, categoriesError, categoriesToSave, draftCategories, type DraftCategory,
} from "./CategoriesSection";
import { DiagnosticsPane } from "./DiagnosticsPane";
import { DictionarySection } from "./DictionarySection";
import { EngineSection } from "./EnginePane";
import { ExportSection, cleanSetting, exportChangesInvalid } from "./ExportSection";
import { JiraSection } from "./JiraSection";
import { dropHiddenJiraChanges, jiraChangesInvalid } from "./JiraSettings";
import { ModelsSection, modelsChangesInvalid } from "./ModelsSection";
import {
  ChangedLabels, OpenOption, RevealFineTuning, SeeAlsoSlot, SettingsCard, type OptionTarget, type Raw, type SetFn,
} from "./Section";
import { SettingsSearch } from "./SettingsSearch";
import {
  type BlockId, MENU, MENU_GROUPS, SETTINGS_INDEX, type SectionId, type SettingsEntry, sectionKeys, sectionTitle,
} from "./settingsIndex";
import { SoundSection } from "./SoundSection";
import { SpeakersSection } from "./SpeakersSection";
import "./settings.css";

export { modelUsage } from "./AsrSection";
export type { SectionId } from "./settingsIndex";

/** Куда ведёт ссылка на настройки: раздел и, если нужно, блок в нём («asr#engine»). */
type Place = SectionId | `${SectionId}#${BlockId}`;

/**
 * Id разделов 0.3.7 и 0.4 → место в 13 разделах 0.6.2 (раздел#блок): глубокие
 * ссылки трея (`{section:"assistant"}`), карточек (`onOpenSettings(...)`),
 * `StorageNotices` (`"engine"`) и событие оболочки `open-section` продолжают
 * работать. «app» — прежнее «Приложение»: теперь блок в «Общем» (раздел с тем
 * же id из меню открывается сверху).
 */
export const LEGACY_SECTION: Record<string, Place> = {
  appearance: "app#appearance",
  app: "app#app",
  recording: "app#app",
  sound: "sound",
  auto: "auto",
  asr: "asr",
  engine: "asr#engine",
  export: "export",
  jira: "export#jira",
  assistant: "assistant",
  analysis: "models#analysis",
  categories: "categories",
  markup: "models#analysis",
  diagnostics: "diagnostics",
  about: "about",
  advanced: "advanced",
};

const BLOCKS: readonly string[] = ["appearance", "app", "engine", "analysis", "jira"] satisfies BlockId[];

/**
 * Место по id: прежний id (LEGACY_SECTION), id раздела или «раздел#блок»;
 * неизвестный — undefined.
 */
function placeOf(id: string | undefined): { section: SectionId; block: BlockId | null } | undefined {
  if (!id) return undefined;
  const place: string = Object.hasOwn(LEGACY_SECTION, id) ? LEGACY_SECTION[id]! : id;
  const [name, block] = place.split("#");
  const section = MENU.find((m) => m.id === name)?.id;
  if (!section) return undefined;
  return { section, block: block && BLOCKS.includes(block) ? block as BlockId : null };
}

const NO_DRAFT: SectionId[] = ["diagnostics", "about"];

/**
 * Разделы, в которых видна правка ключа `group.key`: точка у раздела в меню.
 * Движок и модели распознавания выбираются в «Распознавании» (там же блок
 * установки и загрузки).
 */
export function sectionsOf(group: string, key: string): SectionId[] {
  switch (group) {
    case "recording":
      if (key === "mic_device" || key === "output_device") return ["sound"];
      // 0.5: имя — в «Спикерах», расшифровка сразу после записи — в «Распознавании».
      if (key === "speaker_name") return ["speakers"];
      return key === "auto_transcribe" ? ["asr"] : ["app"];
    case "ui":
      // Оформление пишется сразу, мимо черновика; в «Приложении» — уведомления.
      return ["theme", "aurora", "aurora_style", "motion"].includes(key) ? [] : ["app"];
    case "auto_record": return ["auto"];
    case "asr":
      if (["voice_threshold", "overlap", "fast_diarization", "mic_speakers", "mic_dedupe"].includes(key)) return ["speakers"];
      return key === "replacements" ? ["dictionary"] : ["asr"];
    case "export": return ["export"];
    case "llm": return ["models"];
    case "assist": return ["assistant"];
    case "agent": return ["advanced"];
    case "assistant": return key === "auto_title" ? ["models"] : ["assistant"];
    case "analysis": return ["models"];
    case "categories": return ["categories"];
    case "transcript_view": return key === "jira" ? ["models", "export"] : ["models"];
    case "integrations": return key.startsWith("jira") ? ["export"] : ["advanced"];
    case "hooks": return ["advanced"];
    default: return [];
  }
}

/** Что App спрашивает у настроек, прежде чем уйти в другой раздел окна. */
export type SettingsGuard = {
  /** Названия разделов с несохранёнными правками; пусто — уходить можно молча. */
  dirty: string[];
  /** Можно ли сохранить прямо сейчас (нет ошибок в полях). */
  canSave: boolean;
  /** Сохранить; true — получилось. */
  save: () => Promise<boolean>;
};

/** Где Ctrl+F не уводит к поиску по настройкам: диалоги и всплывающие окна. */
const FIND_IGNORED = "[role=dialog], [role=alertdialog], [aria-modal=true], .popover";
/** Раздел ещё грузит данные (ui/Loading): поиск строки ждёт, пока индикатор не уйдёт. */
const BUSY = "[aria-busy=true]";
/** Подсветка найденной строки (0.5: `--surface-2`, гаснет за 2 с — settings.css). */
export const HIGHLIGHT_MS = 2000;
const FOUND_CLASS = "setting-found";
const FOCUSABLE = "input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])";
const FOCUSABLE_CONTROL = ["input", "select", "textarea", "button"]
  .map((t) => `.srow__control ${t}:not([disabled])`).join(", ") + ", .amark__cell button:not([disabled])";
/** В группе радио-кнопок (сегменты) — фокус на выбранный вариант, а не на первый. */
const CHECKED_CONTROL = ".srow__control [role=radio][aria-checked=true]:not([disabled])";

const reducedMotion = () => typeof window.matchMedia === "function"
  && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Строка раздела по подписи (как в индексе поиска): `Row`/`Switch`/`Radio`,
 * строка таблицы «Анализа встречи» или составной блок. Нет — null.
 */
export function findRow(root: ParentNode, label: string): HTMLElement | null {
  for (const el of root.querySelectorAll<HTMLElement>(".srow__label")) {
    if ((el.textContent ?? "").trim() !== label) continue;
    return el.closest<HTMLElement>(".srow, .amark__row, .callapps, [role=group]") ?? el;
  }
  // Раздел без строк-подписей (список «Категории встреч») — по имени элемента.
  for (const el of root.querySelectorAll<HTMLElement>("[aria-label]")) {
    if (el.getAttribute("aria-label") === label) return el;
  }
  return null;
}

/**
 * Первый выключенный переключатель из цепочки условий строки (от внешнего к
 * внутреннему); нет такого — null. Переключателя нет в окне (его самого
 * скрыл внешний) — пропускается.
 */
function firstOffSwitch(root: ParentNode, requires: readonly string[]): string | null {
  for (const label of requires) {
    const sw = findRow(root, label)?.querySelector<HTMLElement>("[role=switch]");
    if (sw?.getAttribute("aria-checked") === "false") return label;
  }
  return null;
}

/** Меню разделов: 220 px по макету, шире 280 — пустое место; тексту настроек справа — не меньше 420 px. */
const SETTINGS_MENU = { def: 220, min: 180, max: 280, reserve: 420 };

export function SettingsPane({ endpoint, recordingsDir, initial, initialTick, onRunWizard, guardRef, onDirtyChange, appearance, onAppearance }: {
  endpoint: Endpoint;
  /** Текущее оформление окна и как его применить (useAppearance в App). */
  appearance?: Appearance;
  onAppearance?: (a: Appearance) => void;
  /** Появились или пропали несохранённые правки (оболочке: «Выход» из трея спрашивает). */
  onDirtyChange?: (dirty: boolean) => void;
  /** Сюда настройки кладут, есть ли несохранённое и как его сохранить (вопрос при уходе — в App). */
  guardRef?: MutableRefObject<SettingsGuard | null>;
  recordingsDir: string | null;
  /** Открыть сразу это место (id из MENU, «раздел#блок» или прежний из LEGACY_SECTION); неизвестный — «Общее». */
  initial?: string;
  /** Новый номер — снова перейти в `initial` (повторная просьба оболочки или карточки). */
  initialTick?: number;
  /** «Запустить мастер» в «Приложении» (сам мастер после «Пропустить» не открывается);
   *  "engine" — сразу на шаг движка (переустановка в «Движке и моделях»). */
  onRunWizard?: (step?: "hardware" | "engine") => void;
}) {
  const [section, setSectionState] = useState<SectionId>(() => placeOf(initial)?.section ?? "app");
  // Прокрутить к блоку раздела (переход по прежнему id): блок и номер просьбы.
  const [block, setBlock] = useState<{ id: BlockId; tick: number } | null>(() => {
    const id = placeOf(initial)?.block;
    return id ? { id, tick: 0 } : null;
  });
  // Поиск по настройкам: запрос, найденная строка (прокрутить и подсветить) и
  // просьба раскрыть «Тонкую настройку» (номер; 0 — нет, см. RevealFineTuning).
  const [query, setQuery] = useState("");
  const [jump, setJump] = useState<{ entry: SettingsEntry; tick: number; scope?: string } | null>(null);
  const [reveal, setReveal] = useState(0);
  // Строка найдена, но скрыта выключенным переключателем: запись и место под ним для подсказки.
  const [needs, setNeeds] = useState<{ entry: SettingsEntry; label: string } | null>(null);
  const [needsSlot, setNeedsSlot] = useState<HTMLElement | null>(null);
  const jumps = useRef(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  /** Перейти в раздел из меню, ссылки или по просьбе извне: «Тонкая настройка» — как была. */
  const setSection = (id: SectionId) => {
    setSectionState(id);
    setReveal(0);
    setBlock(null);
    setNeeds(null);
  };
  /** Раздел и прокрутка к его блоку (ссылки на бывший раздел, ставший блоком). */
  const openBlock = (id: SectionId, to: BlockId) => {
    setSectionState(id);
    setReveal(0);
    setBlock({ id: to, tick: ++jumps.current });
  };
  /** Выбор в выдаче поиска: раздел, при `advanced` — раскрыть «Тонкую настройку», затем строка. */
  const pick = (entry: SettingsEntry, scope?: string) => {
    const tick = ++jumps.current;
    setQuery("");
    setSectionState(entry.section);
    setBlock(null);
    setReveal(entry.advanced ? tick : 0);
    setNeeds(null);
    setJump({ entry, tick, scope });
  };
  /** Ссылка из строки «Как сейчас»: опция по подписи — как выбор в поиске. */
  const openOption = ({ to, section: where, scope }: OptionTarget) => {
    const target = where ?? section;
    pick(SETTINGS_INDEX.find((e) => e.section === target && e.label === to) ?? { section: target, label: to }, scope);
  };
  const [settings, setSettings] = useState<Raw | null>(null);
  const [draft, setDraft] = useState<Raw>({});
  const [processes, setProcesses] = useState<Processes | null>(null);
  const [devices, setDevices] = useState<Devices | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [askReset, setAskReset] = useState(false);
  // Умолчания открытого раздела от резидента (для «изменено» и «Сбросить раздел»).
  const [sectionDefaults, setSectionDefaults] = useState<({ section: SectionId } & SettingsDefaults) | null>(null);
  const [askDefaults, setAskDefaults] = useState(false);
  // Место в шапке раздела для «см. также» (SeeAlso head): узел после отрисовки.
  const [seeSlot, setSeeSlot] = useState<HTMLElement | null>(null);

  const reload = useCallback(async () => {
    try {
      const next = (await getSettings(endpoint)) as Raw;
      setSettings(next);
      setDraft(next);
      setError(null);
      setNotice(null);
    } catch (e) {
      setError(e instanceof NoResidentError ? "Служба записи не отвечает" : errorText(e));
    }
    // Справочные данные: их отсутствие не мешает править настройки.
    setProcesses(await getProcesses(endpoint).catch(() => null));
    setDevices(await getDevices(endpoint).catch(() => null));
  }, [endpoint]);

  useEffect(() => { void reload(); }, [reload]);

  // Умолчания раздела: при входе в раздел и после каждой загрузки или сохранения настроек.
  useEffect(() => {
    const keys = sectionKeys(section);
    if (!settings || keys.length === 0) return;
    let alive = true;
    getSettingsDefaults(endpoint, keys).then(
      (got) => { if (alive) setSectionDefaults({ section, ...got }); },
      () => { if (alive) setSectionDefaults(null); }, // нет ответа — нет и меток
    );
    return () => { alive = false; };
  }, [endpoint, section, settings]);
  // Поиск программы звонков перечитывает запущенные при каждом открытии.
  const loadProcesses = useCallback(() => getProcesses(endpoint), [endpoint]);

  // Повторная просьба открыть раздел (новый `initialTick`) — даже если он уже был запрошен.
  useEffect(() => {
    const asked = placeOf(initial);
    if (asked) {
      setSectionState(asked.section);
      setReveal(0);
      setBlock(asked.block ? { id: asked.block, tick: ++jumps.current } : null);
    }
  }, [initial, initialTick]);

  // Блок прежнего раздела: когда раздел отрисован с настройками — прокрутить к его первой карточке.
  useEffect(() => {
    if (!block) return;
    const card = contentRef.current?.querySelector<HTMLElement>(`[data-block="${block.id}"]`);
    if (!card) return; // настройки ещё грузятся — эффект повторится с ними
    card.scrollIntoView?.({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
    setBlock((cur) => (cur?.tick === block.tick ? null : cur));
  }, [block, section, settings]);

  // Ctrl+F (⌘F) на панели настроек — к поиску по настройкам (по коду клавиши: и в русской раскладке).
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.code !== "KeyF") return;
      if (e.target instanceof Element && e.target.closest(FIND_IGNORED)) return;
      e.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Найденная строка: дождаться раздела и его данных, прокрутить к строке, подсветить, фокус — на её элемент.
  useEffect(() => {
    const root = contentRef.current;
    if (!jump || !root) return;
    let watch: MutationObserver | undefined;
    const finish = () => {
      watch?.disconnect();
      setJump((cur) => (cur?.tick === jump.tick ? null : cur));
    };
    /** true — дело сделано: строка найдена или её нет, а ждать уже нечего. */
    const attempt = (): boolean => {
      // Строка внутри блока (`scope`): пока блока нет — как будто нет и строки.
      const base = jump.scope ? root.querySelector<HTMLElement>(jump.scope) : root;
      const row = base ? findRow(base, jump.entry.label) : null;
      if (!row) {
        // Раздел ещё грузит свои данные (настройки, движок, каталог моделей) — ждать их;
        // данные пришли, а строки так и нет (скрыта переключателем) — раздел открыт, и хватит.
        if (root.querySelector(BUSY)) return false;
        const off = jump.entry.requires ? firstOffSwitch(root, jump.entry.requires) : null;
        setNeeds(off ? { entry: jump.entry, label: off } : null);
        finish();
        return true;
      }
      row.scrollIntoView?.({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
      const control = row.querySelector<HTMLElement>(CHECKED_CONTROL) ?? row.querySelector<HTMLElement>(FOCUSABLE_CONTROL)
        ?? row.querySelector<HTMLElement>(FOCUSABLE);
      control?.focus({ preventScroll: true });
      row.classList.add(FOUND_CLASS);
      setTimeout(() => row.classList.remove(FOUND_CLASS), HIGHLIGHT_MS);
      finish();
      return true;
    };
    if (!attempt()) {
      watch = new MutationObserver(() => { attempt(); });
      watch.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-busy"] });
    }
    return () => watch?.disconnect();
  }, [jump, section, settings]);

  // Подсказка под выключенным переключателем-условием: подсветить его, дописать строку под ним;
  // переключатель изменён — строка уходит и поиск повторяется (искомая строка или следующее условие).
  useEffect(() => {
    const root = contentRef.current;
    if (!needs || !root) return;
    const row = findRow(root, needs.label);
    const sw = row?.querySelector<HTMLElement>("[role=switch]");
    if (!row || !sw || needs.entry.section !== section) {
      setNeeds(null);
      return;
    }
    const slot = document.createElement("div");
    slot.className = "setting-needs";
    slot.setAttribute("role", "status");
    slot.setAttribute("aria-live", "polite");
    row.after(slot);
    setNeedsSlot(slot);
    row.scrollIntoView?.({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
    sw.focus({ preventScroll: true });
    row.classList.add(FOUND_CLASS);
    const fade = setTimeout(() => row.classList.remove(FOUND_CLASS), HIGHLIGHT_MS);
    const watch = new MutationObserver(() => {
      if (sw.getAttribute("aria-checked") !== "false") {
        setNeeds(null);
        setJump({ entry: needs.entry, tick: ++jumps.current });
      }
    });
    watch.observe(sw, { attributes: true, attributeFilter: ["aria-checked"] });
    return () => {
      clearTimeout(fade);
      watch.disconnect();
      row.classList.remove(FOUND_CLASS);
      slot.remove();
      setNeedsSlot(null);
    };
  }, [needs, section]);

  const set: SetFn = (group, key, value) => {
    setNotice(null);
    setDraft((cur) => ({ ...cur, [group]: { ...(cur[group] ?? {}), [key]: value } }));
  };
  /** Категории — не секция, а список целиком (`categories`). */
  const setCategories = (list: DraftCategory[]) => {
    setNotice(null);
    setDraft((cur) => ({ ...cur, categories: list as unknown as Raw[string] }));
  };

  // Only changed keys are sent, so edits made elsewhere (e.g. the tray) are not clobbered.
  // auto_record.enabled lives outside the draft: it goes through /auto-record.
  const changes: Record<string, Record<string, unknown>> = {};
  if (settings) {
    for (const g of Object.keys(draft)) {
      if (g === "categories") continue; // список, а не секция: ниже
      for (const [k, raw] of Object.entries(draft[g] ?? {})) {
        if (g === "auto_record" && k === "enabled") continue;
        const val = cleanSetting(g, k, raw);
        if (JSON.stringify(val) !== JSON.stringify(settings[g]?.[k])) (changes[g] ??= {})[k] = val;
      }
    }
  }
  dropHiddenJiraChanges(changes, draft);
  const categoriesDirty = settings !== null && categoriesChanged(draft.categories, settings.categories);
  const dirty = [...Object.keys(changes), ...(categoriesDirty ? ["categories"] : [])];
  const invalid = modelsChangesInvalid(changes, draft) || assistantChangesInvalid(changes)
    || agentLaunchChangesInvalid(changes) || exportChangesInvalid(changes, settings ?? {})
    || jiraChangesInvalid(changes) || (categoriesDirty && categoriesError(draftCategories(draft.categories)) !== null);
  // Разделы с правками — точки в меню и список в вопросе при уходе.
  const dirtySections = new Set<SectionId>(categoriesDirty ? ["categories"] : []);
  for (const [g, keys] of Object.entries(changes)) {
    for (const k of Object.keys(keys)) for (const s of sectionsOf(g, k)) dirtySections.add(s);
  }
  const dirtyTitles = MENU.filter((m) => dirtySections.has(m.id)).map((m) => m.title);

  // «Изменено»: значение черновика отличается от умолчания (нет его в черновике — ответ резидента).
  const known = sectionDefaults?.section === section ? sectionDefaults : null;
  const defaultOf = (key: string): { value: unknown } | null => {
    const [g, k] = key.split(".") as [string, string];
    const group = known?.defaults[g];
    return group && Object.hasOwn(group, k) ? { value: group[k] } : null;
  };
  const differs = (key: string): boolean => {
    const def = defaultOf(key);
    if (!def) return false;
    const [g, k] = key.split(".") as [string, string];
    const value = draft[g]?.[k];
    if (value === undefined) return known!.changed.includes(key);
    return JSON.stringify(cleanSetting(g, k, value)) !== JSON.stringify(def.value);
  };
  const changedLabels = [...new Set(SETTINGS_INDEX
    .filter((e) => e.section === section && (e.keys ?? []).some(differs)).map((e) => e.label))];
  const hasDefaults = sectionKeys(section).length > 0;
  /** «Вернуть»: умолчания отличающихся ключей раздела — в черновик; пишет «Сохранить». */
  const applyDefaults = () => {
    const back = sectionKeys(section).filter(differs).map((key) => ({ key, value: defaultOf(key)!.value }));
    setAskDefaults(false);
    setNotice(null);
    setDraft((cur) => {
      const next = { ...cur };
      for (const { key, value } of back) {
        const [g, k] = key.split(".") as [string, string];
        next[g] = { ...(next[g] ?? {}), [k]: value };
      }
      return next;
    });
  };

  const save = async (): Promise<boolean> => {
    if (dirty.length === 0) return true;
    if (invalid) return false;
    setPending(true);
    try {
      const result = await patchSettings(endpoint, categoriesDirty
        ? { ...changes, categories: categoriesToSave(draftCategories(draft.categories)) } : changes);
      setSettings(result.settings as Raw);
      setDraft(result.settings as Raw);
      setError(null);
      setNotice(result.restart_required.length > 0
        ? "Сохранено. Часть параметров применится после перезапуска приложения."
        : "Сохранено.");
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setPending(false);
    }
  };

  // Вопрос при уходе из настроек задаёт App — ему нужны свежие правки и save.
  useEffect(() => {
    if (!guardRef) return;
    guardRef.current = { dirty: dirtyTitles, canSave: !invalid, save };
  });
  useEffect(() => () => { if (guardRef) guardRef.current = null; }, [guardRef]);
  const anyDirty = dirtyTitles.length > 0;
  const dirtyCb = useRef(onDirtyChange);
  dirtyCb.current = onDirtyChange;
  useEffect(() => { dirtyCb.current?.(anyDirty); }, [anyDirty]);
  useEffect(() => () => dirtyCb.current?.(false), []);

  /** Переключатель живёт вне черновика: применяется и сохраняется сразу. */
  const toggleAuto = async (enabled: boolean) => {
    try {
      await setAutoRecord(endpoint, enabled);
      const patch = (cur: Raw): Raw => ({ ...cur, auto_record: { ...cur.auto_record, enabled } });
      setSettings((cur) => (cur ? patch(cur) : cur));
      setDraft(patch);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const showBar = !NO_DRAFT.includes(section);
  const dirtyNoteId = "settings-dirty-note";
  const menuId = useId();
  const isDirty = dirty.length > 0;

  return (
    <div className="settings">
      <nav className="settings__menu" aria-label="Разделы настроек">
        <span id={dirtyNoteId} className="sr-only">Есть несохранённые изменения</span>
        <span className="type-h3 settings__menu-title">Настройки</span>
        <SettingsSearch query={query} onQuery={setQuery} onPick={pick} inputRef={searchRef} />
        {/* Пока в поиске текст — выдача вместо меню. */}
        {query.trim() === "" && MENU_GROUPS.map((g, i) => (
          <div key={g.title} role="group" aria-labelledby={`${menuId}-${i}`} className="settings__group">
            <span id={`${menuId}-${i}`} className="settings__group-title">{g.title}</span>
            {g.items.map((m) => (
              <Tip key={m.id} side="right" describe={false}
                content={dirtySections.has(m.id) ? "Есть несохранённые изменения" : null}>
                <button type="button" className="settings__item"
                  aria-describedby={dirtySections.has(m.id) ? dirtyNoteId : undefined}
                  aria-current={m.id === section ? "page" : undefined} onClick={() => setSection(m.id)}>
                  <span className="settings__item-title">{m.title}</span>
                  {dirtySections.has(m.id) && <span className="settings__dirty" data-dirty aria-hidden="true" />}
                </button>
              </Tip>
            ))}
          </div>
        ))}
      </nav>
      <PaneResizer name="settings-menu" cssVar="--settings-menu-w" spec={SETTINGS_MENU}
        panel="before" label="Ширина меню настроек" />
      <div className="settings__body">
        <div className="settings__column">
          {/* Шапка постоянной высоты и над той же колонкой, что и строки. */}
          <header className="settings__head">
            <div className="settings__heading">
              <h2 className="type-h3 settings__title">{MENU.find((m) => m.id === section)?.title}</h2>
              {/* «См. также» раздела (SeeAlso head) — сюда, под заголовок. */}
              <p className="settings__see" ref={setSeeSlot} />
            </div>
            <div className="settings__actions">
              {showBar && (
                <>
                  <StatusSlot className="settings__state" tone={isDirty ? "muted" : notice ? "ok" : undefined}>
                    {isDirty ? (dirtySections.has(section) ? "Есть несохранённые изменения" : `Не сохранено: ${dirtyTitles.join(", ")}`)
                      : notice}
                  </StatusSlot>
                  <Button variant="ghost" onClick={() => setAskReset(true)} disabled={pending || !isDirty}>Сбросить…</Button>
                  {hasDefaults && (
                    <Button variant="ghost" onClick={() => setAskDefaults(true)}
                      disabled={pending || changedLabels.length === 0}>Сбросить раздел</Button>
                  )}
                  <Button variant="primary" onClick={save} busy={pending} disabled={!isDirty || invalid}>
                    Сохранить
                  </Button>
                </>
              )}
            </div>
          </header>
          {askReset && (
            <ConfirmDialog title="Отменить несохранённые изменения?" confirmLabel="Сбросить" cancelLabel="Оставить правки"
              message={`Правки в ${dirtyTitles.length === 1 ? "разделе" : "разделах"} ${dirtyTitles.map((t) => `«${t}»`).join(", ")} будут отменены.`}
              onCancel={() => setAskReset(false)} onConfirm={() => { setAskReset(false); void reload(); }} />
          )}
          {askDefaults && (
            <ConfirmDialog title={`Вернуть умолчания в разделе «${sectionTitle(section)}»?`}
              confirmLabel="Вернуть" danger={false}
              message={<>
                <p>Вернутся значения по умолчанию:</p>
                <ul>{changedLabels.map((l) => <li key={l}>{l}</li>)}</ul>
                <p>Согласия, пути к папкам и закрытые от ассистента папки остаются как есть. Применится после «Сохранить».</p>
              </>}
              onCancel={() => setAskDefaults(false)} onConfirm={applyDefaults} />
          )}
          {error && <p className="error" role="alert">{error}</p>}
          <div className="settings__content" ref={contentRef}>
          <OpenOption.Provider value={openOption}>
          <ChangedLabels.Provider value={new Set(changedLabels)}>
          <SeeAlsoSlot.Provider value={seeSlot}>
          <RevealFineTuning.Provider value={reveal}>
          {needs && needsSlot && createPortal(
            <p className="setting-needs__text">
              Включите «{needs.label}», чтобы увидеть «{needs.entry.label}»
            </p>, needsSlot)}
          {!settings && section !== "about" && section !== "diagnostics" ? (
            error ? <EmptyState title="Настройки недоступны" /> : <Loading label="Загружаю настройки…" />
          ) : section === "app" ? (
            <>
              {/* Оформление — мимо черновика: применяется и пишется сразу. */}
              <SettingsCard title="Оформление" block="appearance">
                <p className="muted sdesc">Оформление применяется сразу. Остальное в этом разделе — по кнопке «Сохранить».</p>
                <AppearanceSection endpoint={endpoint} value={appearance ?? DEFAULT_APPEARANCE}
                  onPreview={onAppearance ?? (() => {})} />
              </SettingsCard>
              <AppSection draft={draft} set={set} recordingsDir={recordingsDir}
                onRunWizard={onRunWizard && (() => onRunWizard("hardware"))} />
            </>
          ) : section === "sound" ? (
            <SoundSection draft={draft} set={set} devices={devices} endpoint={endpoint}
              onOpenSpeakers={() => setSection("speakers")} />
          ) : section === "auto" ? (
            <AutoSection draft={draft} set={set} processes={processes} loadProcesses={loadProcesses}
              onToggle={(v) => void toggleAuto(v)} />
          ) : section === "asr" ? (
            <AsrSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint}
              onOpenEngine={() => openBlock("asr", "engine")}>
              <EngineSection endpoint={endpoint} draft={draft} onReinstall={onRunWizard && (() => onRunWizard("engine"))}
                onOpenSpeakers={() => setSection("speakers")} />
            </AsrSection>
          ) : section === "speakers" ? (
            <SpeakersSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint} />
          ) : section === "dictionary" ? (
            <DictionarySection draft={draft} set={set} endpoint={endpoint} />
          ) : section === "models" ? (
            <ModelsSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint}>
              <AnalysisSection draft={draft} set={set} onOpenJira={() => openBlock("export", "jira")} />
            </ModelsSection>
          ) : section === "assistant" ? (
            <AssistantSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint}
              onOpenModels={() => setSection("models")} />
          ) : section === "categories" ? (
            <SettingsCard>
              <CategoriesSection value={draft.categories} onChange={setCategories} endpoint={endpoint} />
            </SettingsCard>
          ) : section === "export" ? (
            <>
              <SettingsCard title="Выгрузка в базу знаний">
                <ExportSection draft={draft} set={set} endpoint={endpoint} />
              </SettingsCard>
              <JiraSection draft={draft} set={set} />
            </>
          ) : section === "diagnostics" ? (
            <SettingsCard>
              <DiagnosticsPane endpoint={endpoint} />
            </SettingsCard>
          ) : section === "about" ? (
            <SettingsCard>
              <About endpoint={endpoint} />
            </SettingsCard>
          ) : (
            <AdvancedSection draft={draft} set={set} onRunWizard={onRunWizard && (() => onRunWizard("hardware"))} />
          )}
          </RevealFineTuning.Provider>
          </SeeAlsoSlot.Provider>
          </ChangedLabels.Provider>
          </OpenOption.Provider>
          </div>
        </div>
      </div>
    </div>
  );
}
