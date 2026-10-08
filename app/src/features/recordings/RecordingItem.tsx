import { memo, useEffect, useRef, useState } from "react";
import { TITLE_MAX } from "../../lib/api";
import { clock, dayLabel, duration } from "../../lib/format";
import { categoryOf, NO_CATEGORY_NAME } from "../../lib/categories";
import { jobFraction } from "../../lib/progress";
import { jobStageKey } from "../../ui/JobProgress";
import { useSmoothProgress } from "../../ui/ProgressBar";
import { stageLabel, type RecStatus } from "../../lib/status";
import type { Category, LibraryItem } from "../../lib/types";
import { AgentMark } from "../../ui/AgentMark";
import { AiBadge } from "../../ui/AiBadge";
import { BADGE_CLASS } from "../../ui/badge";
import { CategoryDot, CategoryMark } from "../../ui/Category";
import { Highlight } from "../../ui/Highlight";
import { Tip } from "../../ui/Tip";
import { whoRanges } from "../../lib/libraryQuery";
import { nfc } from "../../lib/search";
import {
  BookOpen, ChevronLeft, ChevronRight, Ellipsis, FolderInput, FolderOpen, Pencil, Settings2, Tag, Trash2,
} from "lucide-react";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { ItemMenu, type MenuItem } from "./ItemMenu";
import { Icon } from "../../ui/Icon";
import { useAgentLive } from "../card/agentSessions";
import { moveItems, type MoveToGroup } from "../groups/moveMenu";
import { meetingsText } from "../../lib/groups";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

/**
 * Текст и вид бейджа; у готовой записи бейджа нет. `shown` — доля, которую
 * сейчас показывает полоска этой задачи (сглаженная и продлённая между
 * событиями): бейдж и карточка говорят одно и то же число.
 */
export function badgeOf(st: RecStatus, shown?: number | null): { text: string; tone: "run" | "err" | "" } | null {
  switch (st.kind) {
    case "recording":
      return { text: "Идёт запись", tone: "err" };
    case "queued":
      return { text: "В очереди", tone: "" };
    case "running": {
      // Общая доля задачи (новые резиденты) или доля этапа; неизвестно — многоточие, а не «100%».
      const f = shown ?? (st.job ? jobFraction(st.job) : st.total ? (st.done ?? 0) / st.total : null);
      const label = st.job ? stageLabel(st.job) : st.label;
      // Общая доля — впереди («62% · Разделение на спикеров»): это ход всей расшифровки, а не этапа.
      if (f !== null && typeof st.job?.fraction === "number") return { text: `${Math.floor(f * 100)}% · ${label}`, tone: "run" };
      return { text: `${label}${f !== null ? ` ${Math.floor(f * 100)}%` : "…"}`, tone: "run" };
    }
    case "text": {
      // Текст уже виден, идут спикеры: доля всей расшифровки и что сейчас делается.
      if (!st.job) return { text: "Без спикеров", tone: "" };
      if (st.job.state === "queued") return { text: "В очереди", tone: "" };
      if (st.job.state === "done") return { text: "Обновляю…", tone: "" };
      const f = shown ?? jobFraction(st.job);
      // Повтор прерванной: пока новый текст не готов, идёт распознавание — его и называем.
      const label = st.job.text_ready ? "Определяю спикеров" : stageLabel(st.job);
      return { text: f !== null ? `${Math.floor(f * 100)}% · ${label}` : `${label}…`, tone: "run" };
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
  /** «Переместить в группу ▸» (одну запись или все выбранные, если она среди них); нет — нет пункта. */
  groups?: MoveToGroup;
};

const NO_CATEGORIES: Category[] = [];
const NO_WHO: string[][] = [];
const RENAME_HINT = "Двойной щелчок или F2 — переименовать";

/** Длительность в списке и на главной: «1 ч 02 мин», короче минуты — «< 1 мин», неизвестна — пусто. */
export function listDuration(s: number | null | undefined): string {
  if (!s || s <= 0) return "";
  return s < 30 ? "< 1 мин" : duration(s);
}

/** Как отметить запись для групповых действий: Ctrl+щелчок — переключить, Shift+щелчок — диапазон. */
export type PickHow = "toggle" | "range";

/**
 * Строка записи. Мемоизирована: свернуть раздел или отметить запись — не
 * перерисовывать тысячу прочих строк (у каждой свои хуки прогресса и агента).
 */
export const RecordingItem = memo(function RecordingItem({
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
  now,
  who = NO_WHO,
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
  /**
   * Строка под разделом по дате (DateSections): дата короче — в разделах по
   * дням только время, в месяцах и годах «5 сен, 10:00». Нет — полная дата.
   */
  now?: Date;
  /** Участники из поиска (`участник:`, `спикер:`) — слова имён: подсветить их в подписи фрагмента. */
  who?: string[][];
}) {
  const when = rec.started_at ? dayLabel(rec.started_at) : "";
  const whenShort = rec.started_at && now ? dayLabel(rec.started_at, now, true) : when;
  /** Агент этой записи работает (вкладка «Агент»), хоть открыта и другая запись. */
  const agentLive = useAgentLive(rec.id);
  const job = status.kind === "running" ? status.job ?? null
    : status.kind === "text" && status.job?.state === "running" ? status.job : null;
  const shown = useSmoothProgress(job ? jobFraction(job) : null, job ? jobStageKey(job) : null,
    { extrapolate: job?.state === "running", cap: job?.cap ?? null });
  const badge = badgeOf(status, job ? shown : null);
  /** Идёт запись: точка и слово в мете (макет LIBRARY), без бейджа. */
  const live = status.kind === "recording";
  /** Ход расшифровки известен: полоса внизу строки, этап и доля — словами в мете, а не бейджем. */
  const progress = job !== null && shown !== null;
  const meta = [whenShort, listDuration(rec.duration_s)].filter(Boolean).join(" · ");
  const title = rec.title ?? (when || rec.id);
  const hits = rec.hits ?? [];
  const more = (rec.total ?? 0) - hits.length;
  const category = categoryOf(rec, categories);

  const [editing, setEditing] = useState(false);
  /** Название обрезано (замер при наведении): полное — в подсказке. */
  const [clipped, setClipped] = useState(false);
  const [draft, setDraft] = useState("");
  const [menu, setMenu] = useState<{ at: { x: number; y: number } | null } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  /** В меню открыт список категорий («Категория ▸»). */
  const [pickCategory, setPickCategory] = useState(false);
  /** Вернулись из списка категорий «Назад» — фокус на пункт «Категория». */
  const [backFromCategory, setBackFromCategory] = useState(false);
  /** В меню открыт выбор группы («Переместить в группу ▸») и для каких встреч. */
  const [pickGroup, setPickGroup] = useState<string[] | null>(null);
  const [backFromGroup, setBackFromGroup] = useState(false);
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
    setPickGroup(null);
    if (focusBack) more_.current?.focus();
  };
  const openMenuAt = (x: number, y: number) => {
    setPickCategory(false); setBackFromCategory(false); setPickGroup(null); setBackFromGroup(false);
    setMenu({ at: { x, y } });
  };
  const chooseCategory = (id: string | null) => { closeMenu(); void actions?.onCategory?.(rec.id, id); };
  // Под «⋯» у правого края строки: меню раскрывается влево (ui/floating).
  const openFromButton = () => {
    setPickCategory(false); setBackFromCategory(false); setPickGroup(null); setBackFromGroup(false);
    setMenu({ at: null });
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

  const moveTo = actions?.groups;
  const groupItems: MenuItem[] = moveTo && pickGroup ? moveItems(moveTo, pickGroup, closeMenu, [
    { label: "Назад", icon: <ChevronLeft {...ICON} />, onSelect: () => { setBackFromGroup(true); setPickGroup(null); } },
  ]) : [];

  const menuItems: MenuItem[] = pickCategory ? categoryItems : pickGroup ? groupItems : [
    { label: "Переименовать", icon: <Pencil {...ICON} />, onSelect: () => { closeMenu(false); begin(); } },
    ...(actions?.onCategory ? [{
      label: "Категория", icon: <Tag {...ICON} />, hint: `Сейчас: ${category?.name ?? NO_CATEGORY_NAME}`,
      autoFocus: backFromCategory,
      trailing: <ChevronRight {...ICON} />, onSelect: () => { setBackFromGroup(false); setPickCategory(true); },
    }] : []),
    ...(moveTo ? [{
      label: "Переместить в группу", icon: <FolderInput {...ICON} />, autoFocus: backFromGroup,
      trailing: <ChevronRight {...ICON} />, onSelect: () => { setBackFromCategory(false); setPickGroup(moveTo.targets(rec.id)); },
    }] : []),
    ...(actions?.onOpenFolder ? [{
      label: "Открыть папку", icon: <FolderOpen {...ICON} />, onSelect: () => { closeMenu(); actions.onOpenFolder?.(rec); },
    }] : []),
    ...(actions?.onKbExport && rec.has_transcript && rec.transcript_phase !== "text" ? [{
      label: "Экспорт в базу знаний", icon: <BookOpen {...ICON} />,
      onSelect: () => { closeMenu(); actions.onKbExport?.(rec.id); },
    }] : []),
    ...(actions?.onDelete ? [{
      label: "Удалить…", danger: true, separator: true, icon: <Trash2 {...ICON} />,
      onSelect: () => { closeMenu(false); setConfirmDelete(true); },
    }] : []),
  ];

  return (
    <li data-rec-id={rec.id} className={`rec-item${selected ? " rec-item--selected" : ""}${picking ? " rec-item--picking" : ""}${
      picked ? " rec-item--picked" : ""}${menu ? " rec-item--menu" : ""}`}
      onContextMenu={actions ? (e) => {
        if (editing) return;
        e.preventDefault();
        // Shift+F10 / клавиша меню приходят без координат указателя.
        if (e.clientX === 0 && e.clientY === 0) openFromButton();
        else openMenuAt(e.clientX, e.clientY);
      } : undefined}>
      {picking && !editing && (
        <input type="checkbox" className="cb rec-item__pick" aria-label={`Выбрать «${title}»`} checked={picked}
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
          <span className="rec-item__head">
            {/* Подсказка: полное название, если оно обрезано, и как переименовать (облачко Aurora). */}
            <Tip describe={false} content={clipped || actions ? (
              <>
                {clipped && <span className="rec-item__tip-line">{title}</span>}
                {actions && <span className="rec-item__tip-line">{RENAME_HINT}</span>}
              </>
            ) : null}>
              <span className="rec-item__title"
                onMouseEnter={(e) => {
                  const el = e.currentTarget;
                  setClipped(el.scrollWidth > el.clientWidth + 1);
                }}
                onDoubleClick={actions ? (e) => { e.preventDefault(); begin(); } : undefined}>
                {rec.title && rec.title_ranges?.length
                  // Подсветка — по названию в NFC (так считает резидент).
                  ? <Highlight text={nfc(rec.title)} ranges={rec.title_ranges} /> : title}
              </span>
            </Tip>
            {rec.title_source === "ai" && rec.title && <AiBadge onClick={actions ? begin : undefined} />}
            {/* Агент этой записи работает (вкладка «Агент») — и когда открыта другая запись. */}
            {agentLive && (
              <Tip content="Агент работает" describe={false}>
                <span className="rec-item__agent">
                  <AgentMark size={12} />агент<span className="sr-only"> работает</span>
                </span>
              </Tip>
            )}
            {/* Состояние — справа в строке названия, на месте «⋯» (он виден при наведении и у выбранной). */}
            {badge && !live && !progress && (
              <span className={`${BADGE_CLASS[badge.tone || "plain"]} rec-item__badge`}>{badge.text}</span>
            )}
          </span>
          <span className={`rec-item__meta${progress ? " rec-item__meta--progress" : ""}`}>
            <span className="rec-item__when">
              {live && <span className="rec-item__live" aria-hidden="true" />}
              <span className="num">{live ? ["Идёт запись", meta].filter(Boolean).join(" · ") : meta}</span>
              {category && <CategoryMark category={category} />}
            </span>
            {badge && progress && (
              <Tip content={badge.text} describe={false}>
                <span className="rec-item__status">{badge.text}</span>
              </Tip>
            )}
          </span>
        </button>
      )}
      {/* Ход расшифровки — полосой 4 px внизу строки; то же число, что в бейдже. */}
      {progress && !editing && (
        <div className="progress rec-item__progress" role="progressbar" aria-label="Ход расшифровки"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.floor(shown! * 100)}>
          <i style={{ width: `${Math.round(shown! * 1000) / 10}%` }} />
        </div>
      )}
      {actions && !editing && (
        <button ref={more_} type="button" className="rec-item__more" aria-label={`Действия с записью «${title}»`}
          aria-haspopup="menu" aria-expanded={menu !== null}
          onClick={() => (menu ? closeMenu() : openFromButton())}><Icon as={Ellipsis} /></button>
      )}
      {menu && (
        <ItemMenu at={menu.at} align="end"
          label={pickCategory ? `Категория записи «${title}»` : pickGroup
            ? (pickGroup.length > 1 ? `Переместить в группу: ${meetingsText(pickGroup.length)}` : `Группа записи «${title}»`)
            : `Действия с записью «${title}»`}
          items={menuItems}
          note={pickCategory ? "Категория встречи" : pickGroup
            ? (pickGroup.length > 1 ? "Группа встреч" : "Группа встречи") : undefined}
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
                  {/* Текст до спикеров: у собеседников подписи нет. */}
                  {h.speaker && (
                    <span className="rec-hit__who"><Highlight text={nfc(h.speaker)} ranges={whoRanges(h.speaker, who)} />: </span>
                  )}
                  <Highlight text={h.snippet} ranges={h.ranges} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {more > 0 && (
        // Открыть запись с этим поиском: в карточке — все совпадения.
        <Tip content="Открыть запись и показать все совпадения">
          <button type="button" className="link rec-hits__more" onClick={() => onSelect(rec.id)}>
            Ещё совпадений: {more}
          </button>
        </Tip>
      )}
    </li>
  );
});
