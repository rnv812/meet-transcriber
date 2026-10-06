import { useRef, useState } from "react";
import { categoryOf } from "../../lib/categories";
import { TITLE_MAX, type Endpoint } from "../../lib/api";
import { dayLabel, duration, plural } from "../../lib/format";
import { llmLabel } from "../../lib/llm";
import { isUnnamed } from "../../lib/speakers";
import type { Category, MergeInfo, Recording } from "../../lib/types";
import { AiBadge } from "../../ui/AiBadge";
import { Avatar } from "../../ui/Avatar";
import { CategoryChip, CategoryMenu } from "../../ui/Category";
import { Popover } from "../../ui/Popover";
import { Skeleton } from "../../ui/Loading";
import type { PersonColor } from "./Turns";

/** Подпись объединённой встречи: из скольких записей и что стало с исходными. */
function MergeNote({ info }: { info: MergeInfo }) {
  const parts = `Объединена из ${info.parts} ${plural(info.parts, "записи", "записей", "записей")}`;
  const step = info.state === "pending" ? "собирается звук"
    : info.state === "done" && info.deleted ? "исходные записи удалены" : "";
  return (
    <div className="card__merge muted">
      <div>{[parts, step].filter(Boolean).join(" · ")}</div>
      {info.state === "done" && info.kb_left.length > 0 && (
        <div>
          Прежние папки частей в базе знаний не изменены — удалите их, если они больше не нужны:{" "}
          {info.kb_left.map((p) => <code key={p} className="path">{p}</code>)}
        </div>
      )}
    </div>
  );
}

/**
 * Категория встречи под названием: метка-кнопка, по ней — меню выбора. Выбор
 * человека модель больше не меняет; «Без категории» — тоже выбор.
 */
function CategoryButton({ rec, list, onPick, onSettings }: {
  rec: Recording;
  list: Category[];
  onPick: (id: string | null) => void;
  onSettings?: () => void;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const category = categoryOf(rec, list);
  const close = () => { setOpen(false); button.current?.focus(); };
  const hint = rec.category?.source === "ai" && category
    ? "Категорию определил ИИ — нажмите, чтобы выбрать другую" : "Категория встречи — нажмите, чтобы выбрать";
  return (
    <>
      <button ref={button} type="button" className="cat-button" aria-haspopup="menu" aria-expanded={open}
        aria-label={`Категория: ${category?.name ?? "Без категории"}. Изменить`} title={hint}
        onClick={() => setOpen((v) => !v)}>
        <CategoryChip category={category} />
      </button>
      {open && button.current && (
        <Popover anchor={button.current} label="Категория встречи" width={240} onClose={close} anchorToggles>
          <CategoryMenu list={list} current={category?.id ?? null}
            onPick={(id) => { close(); if (id !== (category?.id ?? null) || rec.category?.source !== "user") onPick(id); }}
            onSettings={onSettings ? () => { setOpen(false); onSettings(); } : undefined} />
        </Popover>
      )}
    </>
  );
}

export function CardHeader({
  rec, durationS = rec.duration_s, speakers, people, endpoint, avatarVersion, onRename, onNameSpeaker,
  onOpenSpeakers, speakersOpen = false, categories, onCategory, onOpenCategories,
}: {
  rec: Recording;
  /** Длительность для подписи: у импорта без неё — конец последней реплики. */
  durationS?: number | null;
  speakers: string[];
  people: PersonColor[];
  endpoint: Endpoint;
  avatarVersion?: Record<string, number>;
  /** Новое название; null — вернуть автоматическое. */
  onRename: (title: string | null) => void;
  /** Клик по участнику: панель «Спикеры» на его строке. */
  onNameSpeaker?: (label: string) => void;
  /** Открыть панель «Спикеры» (нет — запись не готова, кнопки нет). */
  onOpenSpeakers?: () => void;
  speakersOpen?: boolean;
  /** Категории встреч из настроек; нет обработчика выбора — нет и метки. */
  categories?: Category[];
  onCategory?: (id: string | null) => void;
  /** «Настроить категории…» — раздел настроек. */
  onOpenCategories?: () => void;
}) {
  const when = rec.started_at ? dayLabel(rec.started_at) : "";
  const shown = rec.title ?? (when || rec.id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const cancelled = useRef(false);

  const begin = () => { cancelled.current = false; setDraft(rec.title ?? ""); setEditing(true); };
  const finish = () => {
    if (cancelled.current) return;
    setEditing(false);
    const v = draft.trim().slice(0, TITLE_MAX);
    if (v !== (rec.title ?? "")) onRename(v || null);
  };
  const meta = [when, durationS ? duration(durationS) : ""].filter(Boolean).join(" · ");

  return (
    <header className="card__header">
      {editing ? (
        <input
          className="card__title-input"
          aria-label="Название записи"
          autoFocus
          maxLength={TITLE_MAX}
          placeholder={when || "Название"}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={finish}
          onKeyDown={(e) => {
            if (e.key === "Enter") finish();
            if (e.key === "Escape") { cancelled.current = true; setEditing(false); }
          }}
        />
      ) : (
        <h2 className="card__title" title="Нажмите, чтобы переименовать" onClick={begin}>
          {shown}{rec.title_source === "ai" && rec.title && <AiBadge onClick={begin} by={llmLabel(rec.title_llm)} />}
        </h2>
      )}
      <div className="card__meta-row">
        {meta && <div className="card__meta muted num">{meta}</div>}
        {onCategory && (categories ? (
          <CategoryButton rec={rec} list={categories} onPick={onCategory} onSettings={onOpenCategories} />
        ) : (
          // Категории ещё читаются: заготовка размером с метку — строка не дёргается, когда она появится.
          <Skeleton width={104} height={22} className="card__cat-skel" />
        ))}
      </div>
      {rec.merge && <MergeNote info={rec.merge} />}
      {speakers.length > 0 && (
        <div className="card__people">
          {speakers.map((name) => {
            const p = people.find((x) => x.name === name);
            const chip = (
              <>
                <Avatar name={name} color={p?.color} hasAvatar={p?.has_avatar} version={avatarVersion?.[name]} size={20} endpoint={endpoint} />
                <span>{name}</span>
              </>
            );
            // Узнанное автоматически имя тоже бывает ошибочным: исправить можно любое.
            return (
              <button key={name} type="button" className={isUnnamed(name) ? "chip chip--unnamed" : "chip"}
                title="Кто это?" onClick={() => onNameSpeaker?.(name)}>{chip}</button>
            );
          })}
          {onOpenSpeakers && (
            <button type="button" className="chip chip--action" aria-expanded={speakersOpen}
              title="Имена, голоса и история изменений" onClick={onOpenSpeakers}>
              {`Спикеры (${speakers.length})`}
            </button>
          )}
        </div>
      )}
    </header>
  );
}
