/**
 * Настройки: слева меню — заголовок «Настройки», поиск, шесть групп («Общее»,
 * «Запись», «Расшифровка», «ИИ», «Встречи», «Система») и 17 узких разделов
 * (0.4); справа — колонка раздела (по центру, не шире 760 px; шапка с
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
 * Прежние id разделов (глубокие ссылки трея, карточек, оболочки) ведут в новые
 * через `LEGACY_SECTION`.
 *
 * Над меню — «Поиск по настройкам» (SettingsSearch, Ctrl+F): выдача вместо
 * меню; выбор открывает раздел, раскрывает «Тонкую настройку» (если строка в
 * ней), прокручивает к строке и подсвечивает её на 1,5 с. Меню и индекс —
 * `settingsIndex.ts`.
 */

import { useCallback, useEffect, useId, useRef, useState, type MutableRefObject } from "react";
import {
  type Devices, type Endpoint, type Processes,
  NoResidentError, getDevices, getProcesses, getSettings, patchSettings, setAutoRecord,
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
import { RevealFineTuning, SeeAlsoSlot, SettingsCard, type Raw, type SetFn } from "./Section";
import { SettingsSearch } from "./SettingsSearch";
import { MENU, MENU_GROUPS, type SectionId, type SettingsEntry } from "./settingsIndex";
import { SoundSection } from "./SoundSection";
import { SpeakersSection } from "./SpeakersSection";
import "./settings.css";

export { modelUsage } from "./AsrSection";
export type { SectionId } from "./settingsIndex";

/**
 * Id разделов 0.3.7 → разделы 0.4: глубокие ссылки трея (`{section:"assistant"}`),
 * карточек (`onOpenSettings(...)`), `StorageNotices` (`"engine"`) и событие
 * оболочки `open-section` продолжают работать.
 */
export const LEGACY_SECTION: Record<string, SectionId> = {
  appearance: "appearance",
  recording: "app",
  sound: "sound",
  auto: "auto",
  asr: "asr",
  engine: "engine",
  export: "export",
  assistant: "assistant",
  analysis: "analysis",
  categories: "categories",
  markup: "analysis",
  diagnostics: "diagnostics",
  about: "about",
  advanced: "advanced",
};

/** Раздел по id (новому или прежнему); неизвестный — undefined. */
function sectionOf(id: string | undefined): SectionId | undefined {
  if (!id) return undefined;
  return MENU.find((m) => m.id === id)?.id ?? (Object.hasOwn(LEGACY_SECTION, id) ? LEGACY_SECTION[id] : undefined);
}

const NO_DRAFT: SectionId[] = ["appearance", "diagnostics", "about"];

/**
 * Разделы, в которых видна правка ключа `group.key`: точка у раздела в меню.
 * Движок и модели распознавания выбираются только в «Распознавании»; «Движок и
 * модели» — установка и загрузка.
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
    case "assistant": return key === "auto_title" ? ["analysis"] : ["assistant"];
    case "analysis": return ["analysis"];
    case "categories": return ["categories"];
    case "transcript_view": return key === "jira" ? ["analysis", "jira"] : ["analysis"];
    case "integrations": return key.startsWith("jira") ? ["jira"] : ["advanced"];
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
/** Сколько раз и как часто искать строку, пока раздел догружает данные. */
const FIND_TRIES = 20;
const FIND_RETRY_MS = 100;
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
  /** Открыть сразу этот раздел (id из MENU или прежний из LEGACY_SECTION); неизвестный — «Приложение». */
  initial?: string;
  /** Новый номер — снова перейти в `initial` (повторная просьба оболочки или карточки). */
  initialTick?: number;
  /** «Запустить мастер» в «Приложении» (сам мастер после «Пропустить» не открывается);
   *  "engine" — сразу на шаг движка (переустановка в «Движке и моделях»). */
  onRunWizard?: (step?: "hardware" | "engine") => void;
}) {
  const [section, setSectionState] = useState<SectionId>(() => sectionOf(initial) ?? "app");
  // Поиск по настройкам: запрос, найденная строка (прокрутить и подсветить) и
  // просьба раскрыть «Тонкую настройку» (номер; 0 — нет, см. RevealFineTuning).
  const [query, setQuery] = useState("");
  const [jump, setJump] = useState<{ entry: SettingsEntry; tick: number } | null>(null);
  const [reveal, setReveal] = useState(0);
  const jumps = useRef(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  /** Перейти в раздел из меню, ссылки или по просьбе извне: «Тонкая настройка» — как была. */
  const setSection = (id: SectionId) => {
    setSectionState(id);
    setReveal(0);
  };
  /** Выбор в выдаче поиска: раздел, при `advanced` — раскрыть «Тонкую настройку», затем строка. */
  const pick = (entry: SettingsEntry) => {
    const tick = ++jumps.current;
    setQuery("");
    setSectionState(entry.section);
    setReveal(entry.advanced ? tick : 0);
    setJump({ entry, tick });
  };
  const [settings, setSettings] = useState<Raw | null>(null);
  const [draft, setDraft] = useState<Raw>({});
  const [processes, setProcesses] = useState<Processes | null>(null);
  const [devices, setDevices] = useState<Devices | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [askReset, setAskReset] = useState(false);
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
  // Поиск программы звонков перечитывает запущенные при каждом открытии.
  const loadProcesses = useCallback(() => getProcesses(endpoint), [endpoint]);

  // Повторная просьба открыть раздел (новый `initialTick`) — даже если он уже был запрошен.
  useEffect(() => {
    const asked = sectionOf(initial);
    if (asked) {
      setSectionState(asked);
      setReveal(0);
    }
  }, [initial, initialTick]);

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

  // Найденная строка: дождаться раздела (и данных), прокрутить к строке, подсветить на 1,5 с, фокус — на её элемент.
  useEffect(() => {
    if (!jump) return;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const attempt = () => {
      const row = contentRef.current ? findRow(contentRef.current, jump.entry.label) : null;
      if (!row) {
        // Раздел ещё грузит свои данные (движок, «О программе») — подождать немного;
        // строки так и нет (скрыта переключателем) — раздел открыт, и хватит.
        if (++tries < FIND_TRIES) timer = setTimeout(attempt, FIND_RETRY_MS);
        else setJump((cur) => (cur?.tick === jump.tick ? null : cur));
        return;
      }
      row.scrollIntoView?.({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
      const control = row.querySelector<HTMLElement>(CHECKED_CONTROL) ?? row.querySelector<HTMLElement>(FOCUSABLE_CONTROL)
        ?? row.querySelector<HTMLElement>(FOCUSABLE);
      control?.focus({ preventScroll: true });
      row.classList.add(FOUND_CLASS);
      setTimeout(() => row.classList.remove(FOUND_CLASS), HIGHLIGHT_MS);
      setJump((cur) => (cur?.tick === jump.tick ? null : cur));
    };
    attempt();
    return () => clearTimeout(timer);
  }, [jump, section, settings]);

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
          {error && <p className="error" role="alert">{error}</p>}
          <div className="settings__content" ref={contentRef}>
          <SeeAlsoSlot.Provider value={seeSlot}>
          <RevealFineTuning.Provider value={reveal}>
          {!settings && section !== "about" && section !== "diagnostics" && section !== "appearance" ? (
            error ? <EmptyState title="Настройки недоступны" /> : <Loading label="Загружаю настройки…" />
          ) : section === "appearance" ? (
            <SettingsCard>
              <AppearanceSection endpoint={endpoint} value={appearance ?? DEFAULT_APPEARANCE}
                onPreview={onAppearance ?? (() => {})} />
            </SettingsCard>
          ) : section === "app" ? (
            <AppSection draft={draft} set={set} recordingsDir={recordingsDir}
              onRunWizard={onRunWizard && (() => onRunWizard("hardware"))} />
          ) : section === "sound" ? (
            <SoundSection draft={draft} set={set} devices={devices} endpoint={endpoint}
              onOpenSpeakers={() => setSection("speakers")} />
          ) : section === "auto" ? (
            <AutoSection draft={draft} set={set} processes={processes} loadProcesses={loadProcesses}
              onToggle={(v) => void toggleAuto(v)} />
          ) : section === "asr" ? (
            <AsrSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint}
              onOpenEngine={() => setSection("engine")} />
          ) : section === "speakers" ? (
            <SpeakersSection draft={draft} set={set} endpoint={endpoint} />
          ) : section === "dictionary" ? (
            <DictionarySection draft={draft} set={set} endpoint={endpoint} />
          ) : section === "engine" ? (
            <EngineSection endpoint={endpoint} draft={draft} onReinstall={onRunWizard && (() => onRunWizard("engine"))}
              onOpenAsr={() => setSection("asr")} onOpenSpeakers={() => setSection("speakers")} />
          ) : section === "models" ? (
            <ModelsSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint} />
          ) : section === "assistant" ? (
            <AssistantSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint}
              onOpenModels={() => setSection("models")} />
          ) : section === "analysis" ? (
            <AnalysisSection draft={draft} set={set} onOpenJira={() => setSection("jira")} />
          ) : section === "categories" ? (
            <SettingsCard>
              <CategoriesSection value={draft.categories} onChange={setCategories} endpoint={endpoint} />
            </SettingsCard>
          ) : section === "export" ? (
            <SettingsCard>
              <ExportSection draft={draft} set={set} endpoint={endpoint} />
            </SettingsCard>
          ) : section === "jira" ? (
            <JiraSection draft={draft} set={set} />
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
          </div>
        </div>
      </div>
    </div>
  );
}
