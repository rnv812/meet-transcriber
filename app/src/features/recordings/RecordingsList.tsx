import { X } from "lucide-react";
import "./recordings.css";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { Resident } from "../../state/useResident";
import type { Library } from "../../state/useLibrary";
import { deleteRecording, kbExport, mergeRecordings, patchRecording, setRecordingCategory } from "../../lib/api";
import {
  daysBefore, defaultOpen, groupBySection, loadSectionPrefs, saveSectionPrefs, sectionRange, withPref, type DateSection,
  type SectionPrefs,
} from "../../lib/dateSections";
import { errorText, plural } from "../../lib/format";
import {
  addChip, commit, effectiveQuery, removeChip, titleRanges, toggleChip, whoWords, withGroupScope, type Chip, type ChipKind,
  type GroupRef, type Suggestion,
} from "../../lib/libraryQuery";
import { searchable } from "../../lib/search";
import { agentKillRecording, inTauri, openFolder } from "../../lib/shell";
import { statusOf, type RecStatus } from "../../lib/status";
import type { Category } from "../../lib/types";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { DateSections } from "./DateSections";
import { FiltersButton } from "./FilterPanel";
import { QueryChips } from "./QueryChips";
import { ImportZone } from "./ImportZone";
import { RecordingItem, type ItemActions, type PickHow } from "./RecordingItem";
import { SearchSuggest } from "./SearchSuggest";
import { IconButton } from "../../ui/IconButton";
import { Skeleton } from "../../ui/Loading";
import { GroupHeader, groupEmptyState } from "../groups/GroupHeader";
import { dragPayload, GroupPickbar, useMoveToGroup, type PickState } from "../groups/listGroups";
import type { GroupsUi } from "../groups/useGroupsUi";

type Props = {
  selected: string | null;
  onSelect: (id: string) => void;
  library: Library;
  resident: Pick<Resident, "endpoint" | "snapshot">;
  /**
   * Текст строки поиска (с ещё не ставшими метками префиксами — lib/libraryQuery). Живёт в App;
   * то, что из него следует (`effectiveQuery`), уходит в useLibrary (задержка — там).
   */
  q: string;
  onQ: (q: string) => void;
  /** Метки условий этого сеанса (кроме категорий — те в `categoryFilter` и запоминаются). */
  chips?: Chip[];
  onChips?: (chips: Chip[]) => void;
  /** Группы для меток `группа:`, подсказок и «Фильтров»; null — резидент без групп. */
  groups?: GroupRef[] | null;
  /** Подсказка в пустой строке поиска («Поиск в «Проект Альфа»»). */
  searchPlaceholder?: string;
  /** Фрагмент из поиска: открыть запись на этой реплике. */
  onOpenHit?: (id: string, t: number) => void;
  /** Запись изменили из списка (название, выгрузка): открытая карточка перечитывается. */
  onChanged?: (id: string) => void;
  /** Перед удалением: открытую карточку закрыть — её плеер держит файл записи. */
  onDeleting?: (id: string) => void;
  /** Категории встреч из настроек: метки у записей, фильтр, пункт «Категория» в меню. */
  categories?: Category[];
  /**
   * Фильтр по категориям (id и NO_CATEGORY): его применяет резидент, `library.items` уже
   * отфильтрованы. Живёт в App вместе с поиском.
   */
  categoryFilter?: string[];
  onCategoryFilter?: (keys: string[]) => void;
  /** Перейти в раздел настроек («Настроить категории…»). */
  onOpenSettings?: (section: string) => void;
  /**
   * Группы встреч (features/groups): область списка уже в фильтре `library`; здесь — заголовок
   * группы, «Переместить в группу ▸», «В группу ▾» на панели выбора и перетаскивание строк.
   */
  groupsUi?: GroupsUi;
};

const NO_CATEGORIES: Category[] = [];
const NO_FILTER: string[] = [];
const NO_CHIPS: Chip[] = [];

/** Итог действия из меню: строка над списком, закрывается «×». */
type Notice = { text: string; error: boolean };

/**
 * Сегодняшняя дата для разделов; в полночь — новая («Сегодня» становится
 * «Вчера»). После сна машины таймер опаздывает — поэтому и сверка, когда окно
 * снова видно или в фокусе.
 */
export function useToday(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const check = () => setNow((cur) => { const at = new Date(); return daysBefore(cur, at) === 0 ? cur : at; });
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 1);
    const timer = setTimeout(check, Math.max(1000, midnight.getTime() - Date.now()));
    const onVisible = () => { if (document.visibilityState === "visible") check(); };
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [now]);
  return now;
}

/** «Найдено: 3 встречи, 12 мест»; мест нет — без них (резидент без поиска по тексту). */
function foundLine(n: number, places: number | null): string {
  const meetings = `${n} ${plural(n, "встреча", "встречи", "встреч")}`;
  return `Найдено: ${meetings}${places ? `, ${places} ${plural(places, "место", "места", "мест")}` : ""}`;
}

/**
 * Где Ctrl+K не уводит к поиску списка: в любом поле ввода текста (там это
 * клавиша самого поля), в терминале агента (xterm: «стереть до конца строки»)
 * и под открытым окном или меню.
 */
const KEEP_CTRL_K = "textarea, select, [contenteditable=''], [contenteditable='true'], .xterm, [data-agent-terminal], "
  + "[role=dialog], [role=alertdialog], [aria-modal=true], [role=menu]";
/** Поля, которые не про текст: из них Ctrl+K к поиску уводит. */
const NOT_TEXT = new Set(["checkbox", "radio", "button", "submit", "reset", "range", "color", "file", "image"]);

function keepsCtrlK(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target instanceof HTMLInputElement && !NOT_TEXT.has(target.type)) return true;
  return target.closest(KEEP_CTRL_K) !== null;
}

/** Запись ещё пишется или обрабатывается — объединять её нельзя: звук не окончательный. */
const busy = (st: RecStatus) => st.kind === "recording" || st.kind === "queued" || st.kind === "running";

export function RecordingsList({
  selected, onSelect, library, resident, q, onQ, onOpenHit, onChanged, onDeleting, categories = NO_CATEGORIES,
  categoryFilter = NO_FILTER, onCategoryFilter, onOpenSettings, chips = NO_CHIPS, onChips, groups: groupList = null,
  searchPlaceholder, groupsUi,
}: Props) {
  const snapshot = resident.snapshot ?? null;
  const endpoint = resident.endpoint ?? null;
  const meetingsDir = snapshot?.meetings_dir ?? null;
  const [notice, setNotice] = useState<Notice | null>(null);
  /** Новые названия, пока резидент не ответил и список не перечитан: видны сразу, при ошибке — откат. */
  const [pending, setPending] = useState<Record<string, string | null>>({});
  /** Отмеченные для групповых действий (Ctrl/Shift+щелчок, Ctrl+A); пусто — обычный режим. */
  const [picked, setPicked] = useState<string[]>([]);
  const [anchor, setAnchor] = useState<string | null>(null);
  const [keepOriginals, setKeepOriginals] = useState(false);
  const [merging, setMerging] = useState(false);
  /** Новые категории, пока резидент не ответил: видны сразу, при ошибке — откат. */
  const [pendingCat, setPendingCat] = useState<Record<string, string | null>>({});

  const root = useRef<HTMLDivElement>(null);

  const run = useCallback(async (fn: () => Promise<string | null>) => {
    setNotice(null);
    try {
      const text = await fn();
      if (text) setNotice({ text, error: false });
    } catch (cause) {
      setNotice({ text: errorText(cause), error: true });
    }
  }, []);

  // Группы: выбранные и их группы — через ref (меню строк мемоизированы и не перерисовываются от выбора).
  const pickRef = useRef<PickState>({ chosen: [], items: [] });
  const move = useMoveToGroup(groupsUi, pickRef);

  // Действия строк — постоянные: библиотека и обработчики App (новые на каждую его отрисовку) —
  // через ref, иначе каждая отрисовка окна (и перечитывание групп) перерисовывает все строки.
  const cb = useRef({ library, onChanged, onDeleting, onOpenSettings });
  cb.current = { library, onChanged, onDeleting, onOpenSettings };
  const hasSettings = Boolean(onOpenSettings);
  const actions = useMemo<ItemActions | undefined>(() => {
    if (!endpoint) return undefined;
    return {
      groups: move,
      onRename: (id, title) => run(async () => {
        setPending((cur) => ({ ...cur, [id]: title }));
        try {
          await patchRecording(endpoint, id, { title });
          cb.current.onChanged?.(id);
          await cb.current.library.refresh();
        } finally {
          setPending((cur) => {
            const { [id]: _, ...rest } = cur;
            return rest;
          });
        }
        return null;
      }),
      onCategory: (id, category) => run(async () => {
        setPendingCat((cur) => ({ ...cur, [id]: category }));
        try {
          await setRecordingCategory(endpoint, id, category);
          cb.current.onChanged?.(id);
          await cb.current.library.refresh();
        } finally {
          setPendingCat((cur) => {
            const { [id]: _, ...rest } = cur;
            return rest;
          });
        }
        return null;
      }),
      onOpenCategories: hasSettings ? () => cb.current.onOpenSettings?.("categories") : undefined,
      onOpenFolder: inTauri() ? (rec) => void run(async () => { await openFolder(rec.path); return null; }) : undefined,
      onKbExport: meetingsDir ? (id) => void run(async () => {
        const done = await kbExport(endpoint, id);
        cb.current.onChanged?.(id);
        return `Выгружено в базу знаний: ${done.path}`;
      }) : undefined,
      onDelete: (id) => void run(async () => {
        cb.current.onDeleting?.(id);
        // Карточка закрывается в этом же кадре: её плеер отпускает файл до запроса.
        await new Promise((resolve) => setTimeout(resolve, 0));
        // Агент во вкладке «Агент» работает в папке записи — Windows не удалит её, пока он жив.
        await agentKillRecording(id);
        await deleteRecording(endpoint, id);
        await cb.current.library.refresh();
        return null;
      }),
    };
  }, [endpoint, meetingsDir, hasSettings, run, move]);

  // --- фильтр по категориям -------------------------------------------------------

  const visible = useMemo(() => library.items.map((rec) => (rec.id in pendingCat
    ? { ...rec, category: { id: pendingCat[rec.id] ?? null, source: "user" as const } } : rec)),
  [library.items, pendingCat]);
  const setFilter = (keys: string[]) => onCategoryFilter?.(keys);

  // --- строка поиска, метки, «Фильтры» ---------------------------------------------

  const now = useToday();
  const ctx = useMemo(() => ({ categories, groups: groupList ?? [], now }), [categories, groupList, now]);
  /** Что ищется: текст, метки сеанса, запомненные категории, префиксы, ещё не ставшие метками. */
  const query = useMemo(() => effectiveQuery(q, chips, categoryFilter, ctx), [q, chips, categoryFilter, ctx]);
  // Счётчики «Фильтров» — в области группы из кнопки-списка, как и список. Внутри области
  // измерения «Группа» в панели нет: другие группы дали бы пустое пересечение.
  const scopedFilter = useMemo(() => withGroupScope(query.filter, groupsUi?.libraryScope),
    [query.filter, groupsUi?.libraryScope]);
  /** Метки под поиском: запомненные категории и метки сеанса (префиксы в поле видны в самом поле). */
  const shownChips = useMemo(() => [...categoryFilter.map((value): Chip => ({ kind: "category", value })), ...chips],
    [categoryFilter, chips]);
  const canChip = onChips !== undefined;
  /** Поставить или снять метку: категории — в запоминаемый фильтр, прочее — в метки сеанса. */
  const toggle = (chip: Chip) => {
    if (chip.kind === "category") {
      setFilter(categoryFilter.includes(chip.value) ? categoryFilter.filter((k) => k !== chip.value) : [...categoryFilter, chip.value]);
    } else onChips?.(toggleChip(chips, chip));
  };
  const add = (chip: Chip) => {
    if (chip.kind === "category") { if (!categoryFilter.includes(chip.value)) setFilter([...categoryFilter, chip.value]); }
    else onChips?.(addChip(chips, chip, now));
  };
  const remove = (chip: Chip) => {
    if (chip.kind === "category") setFilter(categoryFilter.filter((k) => k !== chip.value));
    else onChips?.(removeChip(chips, chip));
  };
  const clearChips = () => { setFilter([]); onChips?.([]); };
  /** Enter: годные префиксы и числовая дата из поля — в метки. */
  const commitText = () => {
    if (!canChip) return;
    const done = commit(q, chips, categoryFilter, ctx);
    if (done.text !== q) onQ(done.text);
    if (done.chips !== chips) onChips?.(done.chips);
    if (done.categories !== categoryFilter) setFilter(done.categories);
  };
  const applySuggestion = (action: Suggestion["action"]) => {
    if (action.type === "commit") { commitText(); return; }
    onQ(action.text);
    if (action.chip) add(action.chip);
  };
  const searchBox = () => root.current?.querySelector<HTMLInputElement>("input[type=search]") ?? null;
  /** «ещё…» у участников в «Фильтрах»: в строку — `участник:`, фокус туда же (подсказки — участники). */
  const morePeople = () => {
    const base = q.trim();
    onQ(`${base ? `${base} ` : ""}участник:`);
    requestAnimationFrame(() => searchBox()?.focus());
  };
  const replaceKinds = (kinds: ChipKind[], next: Chip[]) =>
    onChips?.([...chips.filter((c) => !kinds.includes(c.kind)), ...next]);
  /** Подсветка участника в фрагментах: строкам — один и тот же массив, пока условия те же. */
  const whoKey = JSON.stringify(whoWords(query.q, query.filter));
  const who = useMemo(() => JSON.parse(whoKey) as string[][], [whoKey]);

  // --- разделы по датам -------------------------------------------------------------

  // Слова `название:` без текста поиска: резидент подсветку названия не присылает — своя, по тем же правилам.
  const titleTerms = query.filter.title ?? "";
  const withTitles = useMemo(() => (titleTerms ? visible.map((rec) => (rec.title && !rec.title_ranges
    ? { ...rec, title_ranges: titleRanges(rec.title, titleTerms) } : rec)) : visible), [visible, titleTerms]);
  const groups = useMemo(() => groupBySection(withTitles, now), [withTitles, now]);
  /** Поиск или условия сеанса: разделы развёрнуты, над списком «Найдено…». */
  const searching = query.active;
  /** Запомненные отклонения от умолчания (свёрнутый месяц, развёрнутый год). */
  const [prefs, setPrefs] = useState<SectionPrefs>(loadSectionPrefs);
  /** На этот сеанс, без запоминания: раздел открытой записи развёрнут. */
  const [session, setSession] = useState<SectionPrefs>({});
  /** С поиском разделы развёрнуты; свёрнутое — только до конца поиска. */
  const [inSearch, setInSearch] = useState<SectionPrefs>({});
  useEffect(() => { if (!searching) setInSearch({}); }, [searching]);
  const isOpen = (s: DateSection) => (searching ? inSearch[s.key] ?? true : session[s.key] ?? prefs[s.key] ?? defaultOpen(s));
  /** Свернуть или развернуть разделы: [раздел, развёрнут] — одним изменением. */
  const setOpen = (changes: [DateSection, boolean][]) => {
    if (searching) {
      setInSearch((cur) => ({ ...cur, ...Object.fromEntries(changes.map(([s, open]) => [s.key, open])) }));
      return;
    }
    const keys = new Set(changes.map(([s]) => s.key));
    setSession((cur) => Object.fromEntries(Object.entries(cur).filter(([k]) => !keys.has(k))));
    const next = changes.reduce((acc, [s, open]) => withPref(acc, s.key, open, defaultOpen(s)), prefs);
    setPrefs(next);
    saveSectionPrefs(next);
  };
  // Открытая запись (из трея, после объединения) в свёрнутом разделе — раздел разворачивается.
  const selectedKey = selected ? groups.find((g) => g.items.some((r) => r.id === selected))?.section.key : undefined;
  useEffect(() => {
    if (!selectedKey) return;
    setSession((cur) => (cur[selectedKey] ? cur : { ...cur, [selectedKey]: true }));
    setInSearch((cur) => {
      if (!(selectedKey in cur)) return cur;
      const { [selectedKey]: _, ...rest } = cur;
      return rest;
    });
  }, [selected, selectedKey]);
  // Мест — только при поиске по тексту (без него у карточек нет совпадений).
  const places = searchable(query.q) && visible.some((r) => typeof r.total === "number")
    ? visible.reduce((sum, r) => sum + (r.total ?? r.hits?.length ?? 0), 0) : null;

  // Ctrl+K — к поиску списка (Ctrl+F занят поиском в карточке).
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.code !== "KeyK" || e.defaultPrevented) return;
      if (keepsCtrlK(e.target)) return;
      const box = root.current?.querySelector<HTMLInputElement>("input[type=search]");
      if (!box) return;
      e.preventDefault();
      box.focus();
      box.select();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Открытая запись видна: при открытии и когда её раздел развернули (в том числе снова).
  const selectedOpen = selectedKey ? isOpen(groups.find((g) => g.section.key === selectedKey)!.section) : false;
  useEffect(() => {
    if (!selected || !selectedOpen) return;
    const row = [...(root.current?.querySelectorAll<HTMLElement>("[data-rec-id]") ?? [])]
      .find((el) => el.dataset.recId === selected);
    // Под прилипшим заголовком раздела не прячется: у .pane-list есть scroll-padding-top.
    row?.scrollIntoView?.({ block: "nearest" });
  }, [selected, selectedOpen]);

  // --- выбор нескольких записей ---------------------------------------------------

  // Shift-диапазон и Ctrl+A — по видимым строкам: свёрнутые разделы не задеваются.
  const ids = groups.filter((g) => isOpen(g.section)).flatMap((g) => g.items.map((r) => r.id));
  // Пропавшие из списка (удалены, отфильтрованы поиском) отмеченными не считаются; в свёрнутом разделе — считаются.
  const pickedNow = new Set(picked);
  const chosen = groups.flatMap((g) => g.items.map((r) => r.id)).filter((id) => pickedNow.has(id));
  const picking = chosen.length > 0;
  const pickedSet = new Set(chosen);
  pickRef.current = { chosen, items: visible };
  // Один раз на смену библиотеки и задач: строки мемоизированы и сравнивают статус по ссылке.
  const statuses = useMemo(() => new Map(library.items.map((rec) => [rec.id, statusOf(rec, library.jobs, snapshot)])),
    [library.items, library.jobs, snapshot]);

  const clearPicks = () => { setPicked([]); setAnchor(null); };
  const pick = (id: string, how: PickHow) => {
    // Первый Ctrl+щелчок берёт в выбор и открытую запись: так выбирают в проводнике.
    let base = chosen;
    if (!base.length && selected && selected !== id && ids.includes(selected)) base = [selected];
    if (how === "range") {
      const from = ids.indexOf(anchor && ids.includes(anchor) ? anchor : (base[0] ?? selected ?? id));
      const to = ids.indexOf(id);
      const range = from < 0 ? [id] : ids.slice(Math.min(from, to), Math.max(from, to) + 1);
      setPicked([...new Set([...base, ...range])]);
      return;
    }
    setPicked(base.includes(id) ? base.filter((x) => x !== id) : [...base, id]);
    setAnchor(id);
  };
  /** Флажок раздела: отметить все его записи (и в свёрнутом) или снять. */
  const pickSection = (items: { id: string }[], on: boolean) => {
    const these = new Set(items.map((r) => r.id));
    setPicked(on ? [...new Set([...chosen, ...these])] : chosen.filter((id) => !these.has(id)));
  };
  const select = (id: string) => { clearPicks(); onSelect(id); };
  // Строкам — постоянные обработчики (иначе memo строки бесполезен), внутри — свежие замыкания.
  const handlers = useRef({ pick, select });
  handlers.current = { pick, select };
  const stablePick = useCallback((id: string, how: PickHow) => handlers.current.pick(id, how), []);
  const stableSelect = useCallback((id: string) => handlers.current.select(id), []);
  const onListKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).tagName === "INPUT" && (e.target as HTMLInputElement).type === "text") return;
    if ((e.ctrlKey || e.metaKey) && e.code === "KeyA") {
      e.preventDefault();
      setPicked(ids);
    } else if (e.key === "Escape" && picking) {
      e.preventDefault();
      clearPicks();
    }
  };

  const anyBusy = chosen.some((id) => { const st = statuses.get(id); return st ? busy(st) : false; });
  const blocked = chosen.length < 2 ? "Выберите ещё хотя бы одну запись"
    : anyBusy ? "Записи, которые ещё пишутся или обрабатываются, объединить нельзя" : null;
  const doMerge = () => {
    if (!endpoint || blocked) return;
    const keep = keepOriginals;
    void run(async () => {
      setMerging(true);
      try {
        // Исходные удалятся после расшифровки: агенты в их папках не должны их держать.
        if (!keep) await Promise.all(chosen.map((rid) => agentKillRecording(rid)));
        const done = await mergeRecordings(endpoint, chosen, keep);
        clearPicks();
        await library.refresh();
        onSelect(done.recording);
        return keep ? "Встречи объединены" : "Встречи объединены. Исходные записи будут удалены после расшифровки";
      } finally {
        setMerging(false);
      }
    });
  };

  return (
    <div className="rec-list" ref={root} onPointerDown={move ? (e) => groupsUi?.drag.begin(e.nativeEvent,
      () => dragPayload(e.target, pickRef.current)) : undefined}>
      {/* Сверху — кнопка-список групп (область списка и поиска), под ней импорт и поиск (макет LIBRARY). */}
      {groupsUi && <GroupHeader ui={groupsUi} />}
      <ImportZone endpoint={endpoint} onImported={() => void library.refresh?.()} />
      <div className="rec-list__search">
        <SearchSuggest value={q} onChange={onQ} onApply={applySuggestion} endpoint={endpoint} ctx={ctx}
          placeholder={searchPlaceholder}
          onBackspaceEmpty={shownChips.length ? () => remove(shownChips[shownChips.length - 1]!) : undefined} />
        {(onCategoryFilter || canChip) && (
          <FiltersButton endpoint={endpoint} q={query.q} filter={scopedFilter} chips={shownChips}
            categories={categories} groups={groupsUi?.libraryScope ? null : groupList} now={now}
            scopeName={groupsUi?.libraryScope ? groupsUi.scopeName : null} onToggle={toggle} onReplace={replaceKinds}
            onClear={clearChips} onMorePeople={morePeople} onOpen={commitText} />
        )}
      </div>
      <QueryChips chips={shownChips} ctx={ctx} onRemove={remove} onClear={clearChips} />
      {library.error && <div className="import__error">{library.error}</div>}
      {notice && (
        <div className={`rec-notice${notice.error ? " rec-notice--error" : ""}`} role={notice.error ? "alert" : "status"}>
          <span className="rec-notice__text">{notice.text}</span>
          <IconButton icon={X} size="xs" label="Скрыть сообщение" onClick={() => setNotice(null)} />
        </div>
      )}
      {picking && endpoint && (
        <div className="rec-pickbar" role="toolbar" aria-label="Выбранные записи">
          <div className="rec-pickbar__row">
            <span className="rec-pickbar__count">Выбрано: {chosen.length}</span>
            <HelpTip label="Как объединяются встречи" title="Объединение встреч">
              <TipLine>
                Записи склеиваются по времени в одну встречу и расшифровываются заново целиком — спикеры
                получаются общими для всей встречи.
              </TipLine>
              <TipLine>Между частями в расшифровке появляется отметка «— перерыв N мин —».</TipLine>
              <TipLine>
                Имена спикеров заново определяются по базе голосов: имена, которые вы задавали в частях вручную,
                может понадобиться указать ещё раз.
              </TipLine>
              <TipLine>
                После расшифровки исходные записи удаляются, если не отмечено «Сохранить исходные записи».
              </TipLine>
            </HelpTip>
          </div>
          {move && <GroupPickbar move={move} chosen={chosen} />}
          <label className="rec-pickbar__keep">
            <input type="checkbox" checked={keepOriginals} onChange={(e) => setKeepOriginals(e.target.checked)} />
            Сохранить исходные записи
          </label>
          <div className="rec-pickbar__row">
            <Button variant="primary" disabled={blocked !== null || merging} onClick={doMerge}>
              Объединить ({chosen.length})
            </Button>
            <Button onClick={clearPicks}>Снять выделение</Button>
          </div>
          {blocked && <span className="muted rec-pickbar__note">{blocked}</span>}
        </div>
      )}
      {searching && visible.length > 0 && (
        <p className="rec-found muted" aria-live="polite">{foundLine(visible.length, places)}</p>
      )}
      <DateSections
        groups={groups}
        isOpen={isOpen}
        onToggle={(s, open) => setOpen([[s, open]])}
        onAll={(open) => setOpen(groups.map((g) => [g.section, open]))}
        onOthers={(s) => setOpen(groups.map((g) => [g.section, g.section.key === s.key]))}
        onOnly={canChip ? (s) => {
          const range = sectionRange(s, now);
          // «Сегодня», «Вчера» — относительные: после полуночи метка пересчитается (refreshDates).
          const expr = s.key === "today" ? "дата:сегодня" : s.key === "yesterday" ? "дата:вчера" : undefined;
          if (range) add({ kind: "date", value: `${range.from}..${range.to}`, label: s.label, ...(expr ? { expr } : {}) });
        } : undefined}
        picking={picking}
        picked={pickedSet}
        onPickSection={endpoint ? pickSection : undefined}
        onKeyDown={onListKey}
        multiselectable={picking}
        renderItem={(rec) => (
          <RecordingItem
            key={rec.id}
            // Новое название ещё не у резидента: подсветка совпадений была по старому.
            rec={rec.id in pending ? { ...rec, title: pending[rec.id] ?? null, title_ranges: undefined } : rec}
            categories={categories}
            status={statuses.get(rec.id) ?? statusOf(rec, library.jobs, snapshot)}
            selected={rec.id === selected}
            onSelect={stableSelect}
            onOpenHit={onOpenHit}
            actions={actions}
            picking={picking}
            picked={pickedSet.has(rec.id)}
            onPick={endpoint ? stablePick : undefined}
            now={now}
            who={who}
          />
        )}
      />
      {library.items.length === 0 && library.loading && (
        // Первая загрузка библиотеки бывает долгой (холодный старт): заготовки строк, а не пустота.
        <ul className="rec-list__items rec-skel" aria-busy="true" aria-label="Загрузка записей">
          {[72, 54, 64, 48, 60].map((w, i) => (
            <li key={i} className="rec-skel__row">
              <Skeleton width={`${w}%`} height={13} />
              <Skeleton width="38%" height={10} />
            </li>
          ))}
        </ul>
      )}
      {library.items.length === 0 && !library.loading && resident.endpoint && query.chips.length > categoryFilter.length && (
        <EmptyState title="Ничего не найдено" hint="Уберите часть условий или поищите по слову из расшифровки"
          action={<Button onClick={() => { onChips?.([]); onQ(query.find); }}>Сбросить условия</Button>} />
      )}
      {library.items.length === 0 && !library.loading && resident.endpoint && categoryFilter.length > 0
        && query.chips.length === categoryFilter.length && (
        <EmptyState title={q ? "Ничего не найдено в выбранных категориях" : "Нет записей в выбранных категориях"}
          action={<Button onClick={() => setFilter([])}>Показать все категории</Button>} />
      )}
      {library.items.length === 0 && !library.loading && resident.endpoint && query.chips.length === 0 && (
        (groupsUi ? groupEmptyState(groupsUi, q) : null) ?? (q ? <EmptyState title="Ничего не найдено" hint="Проверьте написание или ищите по слову из расшифровки"
          action={<Button variant="link" onClick={() => onQ("")}>Сбросить поиск</Button>} />
          : <EmptyState title="Записей пока нет" hint="Нажмите «Начать запись» или перетащите файл" />)
      )}
    </div>
  );
}
