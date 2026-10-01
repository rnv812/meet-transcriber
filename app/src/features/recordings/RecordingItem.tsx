import { useEffect, useRef, useState } from "react";
import { TITLE_MAX } from "../../lib/api";
import { clock, dayLabel, duration } from "../../lib/format";
import type { RecStatus } from "../../lib/status";
import type { LibraryItem } from "../../lib/types";
import { Highlight } from "../../ui/Highlight";
import { ItemMenu, type MenuItem } from "./ItemMenu";

/** Текст и вид бейджа; у готовой записи бейджа нет. */
export function badgeOf(st: RecStatus): { text: string; tone: "run" | "err" | "" } | null {
  switch (st.kind) {
    case "recording":
      return { text: "Идёт запись", tone: "err" };
    case "queued":
      return { text: "В очереди", tone: "" };
    case "running": {
      const pct = st.total ? ` ${Math.round(((st.done ?? 0) / st.total) * 100)}%` : "…";
      return { text: `${st.label}${pct}`, tone: "run" };
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
  onOpenFolder?: (rec: LibraryItem) => void;
  onKbExport?: (id: string) => void;
  onDelete?: (id: string) => void;
};

export function RecordingItem({
  rec,
  status,
  selected,
  onSelect,
  onOpenHit,
  actions,
}: {
  rec: LibraryItem;
  status: RecStatus;
  selected: boolean;
  onSelect: (id: string) => void;
  /** Фрагмент из поиска: открыть запись на этой реплике. */
  onOpenHit?: (id: string, t: number) => void;
  actions?: ItemActions;
}) {
  const when = rec.started_at ? dayLabel(rec.started_at) : "";
  const badge = badgeOf(status);
  const meta = [when, rec.duration_s ? duration(rec.duration_s) : ""].filter(Boolean).join(" · ");
  const title = rec.title ?? (when || rec.id);
  const hits = rec.hits ?? [];
  const more = (rec.total ?? 0) - hits.length;

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
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
    setConfirmDelete(false);
    if (focusBack) more_.current?.focus();
  };
  const openMenuAt = (x: number, y: number) => { setConfirmDelete(false); setMenu({ x, y }); };
  const openFromButton = () => {
    const r = more_.current?.getBoundingClientRect();
    openMenuAt(r ? r.right - 200 : 0, r ? r.bottom + 4 : 0);
  };

  const menuItems: MenuItem[] = confirmDelete ? [
    { label: "Удалить", danger: true, onSelect: () => { closeMenu(false); actions?.onDelete?.(rec.id); } },
    { label: "Отмена", onSelect: () => setConfirmDelete(false) },
  ] : [
    { label: "Переименовать", onSelect: () => { closeMenu(false); begin(); } },
    ...(actions?.onOpenFolder ? [{ label: "Открыть папку", onSelect: () => { closeMenu(); actions.onOpenFolder?.(rec); } }] : []),
    ...(actions?.onKbExport && rec.has_transcript
      ? [{ label: "Экспорт в базу знаний", onSelect: () => { closeMenu(); actions.onKbExport?.(rec.id); } }] : []),
    ...(actions?.onDelete ? [{ label: "Удалить…", danger: true, onSelect: () => setConfirmDelete(true) }] : []),
  ];

  return (
    <li className={`rec-item${selected ? " rec-item--selected" : ""}`}
      onContextMenu={actions ? (e) => {
        if (editing) return;
        e.preventDefault();
        // Shift+F10 / клавиша меню приходят без координат указателя.
        if (e.clientX === 0 && e.clientY === 0) openFromButton();
        else openMenuAt(e.clientX, e.clientY);
      } : undefined}>
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
          onClick={() => onSelect(rec.id)}
          onKeyDown={(e) => { if (e.key === "F2" && actions) { e.preventDefault(); begin(); } }}>
          <span className="rec-item__title" title={actions ? "Двойной щелчок или F2 — переименовать" : undefined}
            onDoubleClick={actions ? (e) => { e.preventDefault(); begin(); } : undefined}>{title}</span>
          <span className="rec-item__meta">
            <span className="muted num">{meta}</span>
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
        <ItemMenu at={menu} label={`Действия с записью «${title}»`} items={menuItems}
          note={confirmDelete ? "Удалить запись и расшифровку? Это действие нельзя отменить." : undefined}
          onClose={() => closeMenu()} />
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
