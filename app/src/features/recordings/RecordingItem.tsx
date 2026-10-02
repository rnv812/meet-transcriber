import { useEffect, useRef, useState } from "react";
import { TITLE_MAX } from "../../lib/api";
import { clock, dayLabel, duration } from "../../lib/format";
import { categoryOf, NO_CATEGORY_NAME } from "../../lib/categories";
import { jobFraction } from "../../lib/progress";
import { stageLabel, type RecStatus } from "../../lib/status";
import type { Category, LibraryItem } from "../../lib/types";
import { AiBadge } from "../../ui/AiBadge";
import { CategoryDot, CategoryMark } from "../../ui/Category";
import { Highlight } from "../../ui/Highlight";
import { BookOpen, ChevronLeft, ChevronRight, FolderOpen, Pencil, Settings2, Tag, Trash2 } from "lucide-react";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { ItemMenu, type MenuItem } from "./ItemMenu";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

/** Текст и вид бейджа; у готовой записи бейджа нет. */
export function badgeOf(st: RecStatus): { text: string; tone: "run" | "err" | "" } | null {
  switch (st.kind) {
    case "recording":
      return { text: "Идёт запись", tone: "err" };
    case "queued":
      return { text: "В очереди", tone: "" };
    case "running": {
      // Общая доля задачи (новые резиденты) или доля этапа; неизвестно — многоточие, а не «100%».
      const f = st.job ? jobFraction(st.job) : st.total ? (st.done ?? 0) / st.total : null;
      const label = st.job ? stageLabel(st.job) : st.label;
      return { text: `${label}${f !== null ? ` ${Math.floor(f * 100)}%` : "…"}`, tone: "run" };
    }
    case "failed":
      return { text: "Ошибка", tone: "err" };
    case "untranscribed":
      return { text: "Не расшифровано", tone: "" };
    case "ready":
      return null;
  }
}

/** Действия записи из меню; нет — нет и пункта. */
export type ItemActions = {
  /** Новое название; null — вернуть автоматическое. */
  onRename: (id: string, title: string | null) => Promise<void>;
  /** Категория, выбранная человеком; null — «Без категории». */
  onCategory?: (id: string, category: string | null) => Promise<void>;
  /** «Настроить категории…» — раздел настроек. */
  onOpenCategories?: () => void;
  onOpenFolder?: (rec: LibraryItem) => void;
  onKbExport?: (id: string) => void;
  onDelete?: (id: string) => void;
};

const NO_CATEGORIES: Category[] = [];

/** Как отметить запись для групповых действий: Ctrl+щелчок — переключить, Shift+щелчок — диапазон. */
export type PickHow = "toggle" | "range";

export function RecordingItem({
  rec,
  status,
  selected,
  onSelect,
  onOpenHit,
  actions,
  picking = false,
  picked = false,
  onPick,
  categories = NO_CATEGORIES,
}: {
  rec: LibraryItem;
  /** Категории встреч из настроек: метка записи и пункт «Категория» в меню. */
  categories?: Category[];
  status: RecStatus;
  selected: boolean;
  onSelect: (id: string) => void;
  /** Фрагмент из поиска: открыть запись на этой реплике. */
  onOpenHit?: (id: string, t: number) => void;
  actions?: ItemActions;
  /** Режим выбора нескольких записей: у каждой — флажок. */
  picking?: boolean;
  picked?: boolean;
  onPick?: (id: string, how: PickHow) => void;
}) {
  const when = rec.started_at ? dayLabel(rec.started_at) : "";
  const badge = badgeOf(status);
  const meta = [when, rec.duration_s ? duration(rec.duration_s) : ""].filter(Boolean).join(" · ");
  const title = rec.title ?? (when || rec.id);
  const hits = rec.hits ?? [];
  const more = (rec.total ?? 0) - hits.length;
  const category = categoryOf(rec, categories);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  /** В меню открыт список категорий («Категория ▸»). */
  const [pickCategory, setPickCategory] = useState(false);
  /** Вернулись из списка категорий «Назад» — фокус на пункт «Категория». */
  const [backFromCategory, setBackFromCategory] = useState(false);
  const main = useRef<HTMLButtonElement>(null);
  const more_ = useRef<HTMLButtonElement>(null);
  const done = useRef(false);
  /** Вернуть фокус на запись после переименования с клавиатуры. */
  const refocus = useRef(false);

  useEffect(() => {
    if (!editing && refocus.current) { refocus.current = false; main.current?.focus(); }
  }, [editing]);

  const begin = () => {
    if (!actions) return;
    done.current = false;
    setDraft(rec.title ?? "");
    setEditing(true);
  };
  const finish = (save: boolean) => {
    if (done.current) return; // blur после Enter/Esc
    done.current = true;
    setEditing(false);
    const value = draft.trim().slice(0, TITLE_MAX);
    if (save && value !== (rec.title ?? "")) void actions?.onRename(rec.id, value || null);
  };

  const closeMenu = (focusBack = true) => {
    setMenu(null);
    setPickCategory(false);
    if (focusBack) more_.current?.focus();
  };
  const openMenuAt = (x: number, y: number) => {
    setPickCategory(false); setBackFromCategory(false); setMenu({ x, y });
  };
  const chooseCategory = (id: string | null) => { closeMenu(); void actions?.onCategory?.(rec.id, id); };
  const openFromButton = () => {
    const r = more_.current?.getBoundingClientRect();
    openMenuAt(r ? r.right - 200 : 0, r ? r.bottom + 4 : 0);
  };

  const categoryItems: MenuItem[] = [
    { label: NO_CATEGORY_NAME, icon: <CategoryDot />, checked: category === null, autoFocus: category === null,
      onSelect: () => chooseCategory(null) },
    ...categories.map((c) => ({
      label: c.name, icon: <CategoryDot color={c.color} />, checked: category?.id === c.id,
      autoFocus: category?.id === c.id, hint: c.description || undefined, onSelect: () => chooseCategory(c.id),
    })),
    ...(actions?.onOpenCategories ? [{
      label: "Настроить категории…", separator: true, icon: <Settings2 {...ICON} />,
      onSelect: () => { closeMenu(false); actions.onOpenCategories?.(); },
    }] : []),
    { label: "Назад", separator: !actions?.onOpenCategories, icon: <ChevronLeft {...ICON} />,
      onSelect: () => { setBackFromCategory(true); setPickCategory(false); } },
  ];

  const menuItems: MenuItem[] = pickCategory ? categoryItems : [
    { label: "Переименовать", icon: <Pencil {...ICON} />, onSelect: () => { closeMenu(false); begin(); } },
    ...(actions?.onCategory ? [{
      label: "Категория", icon: <Tag {...ICON} />, hint: `Сейчас: ${category?.name ?? NO_CATEGORY_NAME}`,
      autoFocus: backFromCategory,
      trailing: <ChevronRight {...ICON} />, onSelect: () => setPickCategory(true),
    }] : []),
    ...(actions?.onOpenFolder ? [{
      label: "Открыть папку", icon: <FolderOpen {...ICON} />, onSelect: () => { closeMenu(); actions.onOpenFolder?.(rec); },
    }] : []),
    ...(actions?.onKbExport && rec.has_transcript ? [{
      label: "Экспорт в базу знаний", icon: <BookOpen {...ICON} />,
      onSelect: () => { closeMenu(); actions.onKbExport?.(rec.id); },
    }] : []),
    ...(actions?.onDelete ? [{
      label: "Удалить…", danger: true, separator: true, icon: <Trash2 {...ICON} />,
      onSelect: () => { closeMenu(false); setConfirmDelete(true); },
    }] : []),
  ];

  return (
    <li className={`rec-item${selected ? " rec-item--selected" : ""}${picking ? " rec-item--picking" : ""}${picked ? " rec-item--picked" : ""}`}
      onContextMenu={actions ? (e) => {
        if (editing) return;
        e.preventDefault();
        // Shift+F10 / клавиша меню приходят без координат указателя.
        if (e.clientX === 0 && e.clientY === 0) openFromButton();
        else openMenuAt(e.clientX, e.clientY);
      } : undefined}>
      {picking && !editing && (
        <input type="checkbox" className="rec-item__pick" aria-label={`Выбрать «${title}»`} checked={picked}
          onChange={() => onPick?.(rec.id, "toggle")} />
      )}
      {editing ? (
        <div className="rec-item__main rec-item__main--editing">
          <input
            className="rec-item__input"
            aria-label="Название записи"
            autoFocus
            maxLength={TITLE_MAX}
            placeholder={when || "Название"}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onFocus={(e) => e.currentTarget.select()}
            onBlur={() => finish(true)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); refocus.current = true; finish(true); }
              if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); refocus.current = true; finish(false); }
            }}
          />
          <span className="rec-item__hint muted">Enter — сохранить, Esc — отменить, пусто — название по умолчанию</span>
        </div>
      ) : (
        <button ref={main} type="button" className="rec-item__main" aria-current={selected ? "true" : undefined}
          aria-keyshortcuts={actions ? "F2" : undefined}
          onClick={(e) => {
            if (onPick && (e.ctrlKey || e.metaKey)) onPick(rec.id, "toggle");
            else if (onPick && e.shiftKey) onPick(rec.id, "range");
            else onSelect(rec.id);
          }}
          onKeyDown={(e) => { if (e.key === "F2" && actions) { e.preventDefault(); begin(); } }}>
          <span className="rec-item__title" title={actions ? "Двойной щелчок или F2 — переименовать" : undefined}
            onDoubleClick={actions ? (e) => { e.preventDefault(); begin(); } : undefined}>
            {title}{rec.title_source === "ai" && rec.title && <AiBadge onClick={actions ? begin : undefined} />}
          </span>
          <span className="rec-item__meta">
            <span className="rec-item__when">
              <span className="muted num">{meta}</span>
              {category && <CategoryMark category={category} />}
            </span>
            {badge && <span className={`badge${badge.tone ? ` badge--${badge.tone}` : ""}`}>{badge.text}</span>}
          </span>
        </button>
      )}
      {actions && !editing && (
        <button ref={more_} type="button" className="rec-item__more" aria-label={`Действия с записью «${title}»`}
          aria-haspopup="menu" aria-expanded={menu !== null}
          onClick={() => (menu ? closeMenu() : openFromButton())}>⋯</button>
      )}
      {menu && (
        <ItemMenu at={menu} label={pickCategory ? `Категория записи «${title}»` : `Действия с записью «${title}»`}
          items={menuItems}
          note={pickCategory ? "Категория встречи" : undefined}
          anchor={more_} onClose={() => closeMenu()} />
      )}
      {confirmDelete && (
        <ConfirmDialog title="Удалить запись?" confirmLabel="Удалить"
          message={<>«{title}»: звук, расшифровка и итоги будут удалены с диска. Это действие нельзя отменить.</>}
          onCancel={() => { setConfirmDelete(false); more_.current?.focus(); }}
          onConfirm={() => { setConfirmDelete(false); actions?.onDelete?.(rec.id); }} />
      )}
      {hits.length > 0 && (
        <ul className="rec-hits" aria-label={`Найдено в записи «${title}»`}>
          {hits.map((h, i) => (
            <li key={i}>
              <button type="button" className="rec-hit" onClick={() => onOpenHit?.(rec.id, h.t)}>
                <span className="rec-hit__time num">{clock(h.t)}</span>
                <span className="rec-hit__text">
                  <span className="rec-hit__who">{h.speaker}: </span>
                  <Highlight text={h.snippet} ranges={h.ranges} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {more > 0 && <div className="rec-hits__more muted">Ещё совпадений: {more}</div>}
    </li>
  );
}
