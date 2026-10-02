import "./recordings.css";
import { useCallback, useMemo, useState, type KeyboardEvent } from "react";
import type { Resident } from "../../state/useResident";
import type { Library } from "../../state/useLibrary";
import { deleteRecording, kbExport, mergeRecordings, patchRecording, setRecordingCategory } from "../../lib/api";
import { errorText } from "../../lib/format";
import { agentKillRecording, inTauri, openFolder } from "../../lib/shell";
import { statusOf, type RecStatus } from "../../lib/status";
import type { Category } from "../../lib/types";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { CategoryFilter, CategoryFilterChips } from "./CategoryFilter";
import { ImportZone } from "./ImportZone";
import { RecordingItem, type ItemActions, type PickHow } from "./RecordingItem";
import { SearchBox } from "./SearchBox";

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

  // --- выбор нескольких записей ---------------------------------------------------

  const ids = visible.map((r) => r.id);
  // Пропавшие из списка (удалены, отфильтрованы поиском) отмеченными не считаются.
  const chosen = ids.filter((id) => picked.includes(id));
  const picking = chosen.length > 0;
  const statuses = new Map(library.items.map((rec) => [rec.id, statusOf(rec, library.jobs, snapshot)]));

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
  const select = (id: string) => { clearPicks(); onSelect(id); };
  const onListKey = (e: KeyboardEvent<HTMLUListElement>) => {
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
    <div className="rec-list">
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
          <button type="button" className="import__close" aria-label="Скрыть сообщение" onClick={() => setNotice(null)}>×</button>
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
      <ul aria-label="Записи" aria-multiselectable={picking || undefined} className="rec-list__items" onKeyDown={onListKey}>
        {visible.map((rec) => (
          <RecordingItem
            key={rec.id}
            rec={rec.id in pending ? { ...rec, title: pending[rec.id] ?? null } : rec}
            categories={categories}
            status={statuses.get(rec.id) ?? statusOf(rec, library.jobs, snapshot)}
            selected={rec.id === selected}
            onSelect={select}
            onOpenHit={onOpenHit}
            actions={actions}
            picking={picking}
            picked={chosen.includes(rec.id)}
            onPick={endpoint ? pick : undefined}
          />
        ))}
      </ul>
      {library.items.length === 0 && !library.loading && resident.endpoint && categoryFilter.length > 0 && (
        <EmptyState title={q ? "Ничего не найдено в выбранных категориях" : "Нет записей в выбранных категориях"}
          action={<Button onClick={() => setFilter([])}>Показать все категории</Button>} />
      )}
      {library.items.length === 0 && !library.loading && resident.endpoint && categoryFilter.length === 0 && (
        q ? <EmptyState title="Ничего не найдено" />
          : <EmptyState title="Записей пока нет" hint="Нажмите «Начать запись» или перетащите файл" />
      )}
    </div>
  );
}
