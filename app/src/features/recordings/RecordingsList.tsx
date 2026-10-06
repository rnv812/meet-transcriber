import { X } from "lucide-react";
import "./recordings.css";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { Resident } from "../../state/useResident";
import type { Library } from "../../state/useLibrary";
import { deleteRecording, kbExport, mergeRecordings, patchRecording, setRecordingCategory } from "../../lib/api";
import {
  daysBefore, defaultOpen, groupBySection, loadSectionPrefs, saveSectionPrefs, withPref, type DateSection, type SectionPrefs,
} from "../../lib/dateSections";
import { errorText, plural } from "../../lib/format";
import { searchable } from "../../lib/search";
import { agentKillRecording, inTauri, openFolder } from "../../lib/shell";
import { statusOf, type RecStatus } from "../../lib/status";
import type { Category } from "../../lib/types";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { CategoryFilter, CategoryFilterChips } from "./CategoryFilter";
import { DateSections } from "./DateSections";
import { ImportZone } from "./ImportZone";
import { RecordingItem, type ItemActions, type PickHow } from "./RecordingItem";
import { SearchBox } from "./SearchBox";
import { Icon } from "../../ui/Icon";
import { Skeleton } from "../../ui/Loading";

type Props = {
  selected: string | null;
  onSelect: (id: string) => void;
  library: Library;
  resident: Pick<Resident, "endpoint" | "snapshot">;
  /** Строка поиска живёт в App и уходит в useLibrary (задержка — там). */
  q: string;
  onQ: (q: string) => void;
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
};

const NO_CATEGORIES: Category[] = [];
const NO_FILTER: string[] = [];

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
  categoryFilter = NO_FILTER, onCategoryFilter, onOpenSettings,
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

  const run = useCallback(async (fn: () => Promise<string | null>) => {
    setNotice(null);
    try {
      const text = await fn();
      if (text) setNotice({ text, error: false });
    } catch (cause) {
      setNotice({ text: errorText(cause), error: true });
    }
  }, []);

  const actions = useMemo<ItemActions | undefined>(() => {
    if (!endpoint) return undefined;
    return {
      onRename: (id, title) => run(async () => {
        setPending((cur) => ({ ...cur, [id]: title }));
        try {
          await patchRecording(endpoint, id, { title });
          onChanged?.(id);
          await library.refresh();
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
          onChanged?.(id);
          await library.refresh();
        } finally {
          setPendingCat((cur) => {
            const { [id]: _, ...rest } = cur;
            return rest;
          });
        }
        return null;
      }),
      onOpenCategories: onOpenSettings ? () => onOpenSettings("categories") : undefined,
      onOpenFolder: inTauri() ? (rec) => void run(async () => { await openFolder(rec.path); return null; }) : undefined,
      onKbExport: meetingsDir ? (id) => void run(async () => {
        const done = await kbExport(endpoint, id);
        onChanged?.(id);
        return `Выгружено в базу знаний: ${done.path}`;
      }) : undefined,
      onDelete: (id) => void run(async () => {
        onDeleting?.(id);
        // Карточка закрывается в этом же кадре: её плеер отпускает файл до запроса.
        await new Promise((resolve) => setTimeout(resolve, 0));
        // Агент во вкладке «Агент» работает в папке записи — Windows не удалит её, пока он жив.
        await agentKillRecording(id);
        await deleteRecording(endpoint, id);
        await library.refresh();
        return null;
      }),
    };
  }, [endpoint, meetingsDir, library, onChanged, onDeleting, onOpenSettings, run]);

  // --- фильтр по категориям -------------------------------------------------------

  const visible = useMemo(() => library.items.map((rec) => (rec.id in pendingCat
    ? { ...rec, category: { id: pendingCat[rec.id] ?? null, source: "user" as const } } : rec)),
  [library.items, pendingCat]);
  const setFilter = (keys: string[]) => onCategoryFilter?.(keys);

  // --- разделы по датам -------------------------------------------------------------

  const now = useToday();
  const groups = useMemo(() => groupBySection(visible, now), [visible, now]);
  const searching = searchable(q);
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
  const places = visible.some((r) => typeof r.total === "number")
    ? visible.reduce((sum, r) => sum + (r.total ?? r.hits?.length ?? 0), 0) : null;

  // Ctrl+K — к поиску списка (Ctrl+F занят поиском в карточке).
  const root = useRef<HTMLDivElement>(null);
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
    <div className="rec-list" ref={root}>
      <ImportZone endpoint={endpoint} onImported={() => void library.refresh?.()} />
      <div className="rec-list__search">
        <SearchBox value={q} onChange={onQ} />
        {onCategoryFilter && (categories.length > 0 || categoryFilter.length > 0) && (
          <CategoryFilter list={categories} endpoint={endpoint} q={q} selected={categoryFilter} onChange={setFilter} />
        )}
      </div>
      {categoryFilter.length > 0 && (
        <CategoryFilterChips list={categories} selected={categoryFilter} onChange={setFilter} />
      )}
      {library.error && <div className="import__error">{library.error}</div>}
      {notice && (
        <div className={`rec-notice${notice.error ? " rec-notice--error" : ""}`} role={notice.error ? "alert" : "status"}>
          <span className="rec-notice__text">{notice.text}</span>
          <button type="button" className="import__close" aria-label="Скрыть сообщение" onClick={() => setNotice(null)}><Icon as={X} size="sm" /></button>
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
        picking={picking}
        picked={pickedSet}
        onPickSection={endpoint ? pickSection : undefined}
        onKeyDown={onListKey}
        multiselectable={picking}
        renderItem={(rec) => (
          <RecordingItem
            key={rec.id}
            rec={rec.id in pending ? { ...rec, title: pending[rec.id] ?? null } : rec}
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
      {library.items.length === 0 && !library.loading && resident.endpoint && categoryFilter.length > 0 && (
        <EmptyState title={q ? "Ничего не найдено в выбранных категориях" : "Нет записей в выбранных категориях"}
          action={<Button onClick={() => setFilter([])}>Показать все категории</Button>} />
      )}
      {library.items.length === 0 && !library.loading && resident.endpoint && categoryFilter.length === 0 && (
        q ? <EmptyState title="Ничего не найдено" hint="Проверьте написание или ищите по слову из расшифровки"
          action={<Button variant="link" onClick={() => onQ("")}>Сбросить поиск</Button>} />
          : <EmptyState title="Записей пока нет" hint="Нажмите «Начать запись» или перетащите файл" />
      )}
    </div>
  );
}
