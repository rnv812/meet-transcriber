import { useRef, useState, type ReactNode } from "react";
import { categoryOf, NO_CATEGORY_NAME } from "../../lib/categories";
import { TITLE_MAX, type Endpoint } from "../../lib/api";
import { dayLabel, duration, plural } from "../../lib/format";
import { llmLabel } from "../../lib/llm";
import { isUnnamed } from "../../lib/speakers";
import { speakerTone } from "../../lib/tones";
import type { Category, MergeInfo, Recording } from "../../lib/types";
import { AiBadge } from "../../ui/AiBadge";
import { Button } from "../../ui/Button";
import { CategoryDot, CategoryMenu } from "../../ui/Category";
import { Popover } from "../../ui/Popover";
import { Skeleton } from "../../ui/Loading";
import { Tip } from "../../ui/Tip";
import { Truncate } from "../../ui/Truncate";
import { PersonMark } from "./PersonMark";
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
 * Категория встречи под названием: малая контурная кнопка с точкой цвета, по
 * ней — меню выбора. Выбор человека модель больше не меняет; «Без категории» — тоже выбор.
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
      <Tip content={hint}>
        <Button ref={button} variant="ghost" size="sm" className={`card__cat${category ? "" : " card__cat--none"}`}
          aria-haspopup="menu" aria-expanded={open}
          aria-label={`Категория: ${category?.name ?? NO_CATEGORY_NAME}. Изменить`}
          onClick={() => setOpen((v) => !v)}>
          <CategoryDot color={category?.color} />
          <Truncate className="card__cat-name">{category?.name ?? NO_CATEGORY_NAME}</Truncate>
        </Button>
      </Tip>
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

/** Сколько имён участников писать текстом (дальше — «и ещё N») и сколько знаков в стопке. */
const SHOWN_NAMES = 3;
const STACK = 5;

/**
 * Участники в строке под названием (0.6.1): знаки стопкой и имена текстом, без
 * рамки у каждого. Знак — инициал или фото в кольце цвета спикера (lib/tones —
 * тот же цвет, что у точки в ленте и в панели «Спикеры»); щелчок — «Спикеры»
 * на этом человеке, в подсказке — «Кто это» из базы голосов (роль), без неё —
 * «Кто это?». Имена — одна кнопка: панель «Спикеры» целиком.
 */
function PeopleLine({ speakers, people, tones, endpoint, avatarVersion, onNameSpeaker, onOpenSpeakers, speakersOpen }: {
  speakers: string[];
  people: PersonColor[];
  tones?: ReadonlyMap<string, string>;
  endpoint: Endpoint;
  avatarVersion?: Record<string, number>;
  onNameSpeaker?: (label: string) => void;
  onOpenSpeakers?: () => void;
  speakersOpen: boolean;
}) {
  const rest = speakers.length - SHOWN_NAMES;
  const text = speakers.slice(0, SHOWN_NAMES).join(", ") + (rest > 0 ? ` и ещё ${rest}` : "");
  return (
    <span className="card__people">
      <span className="card__people-stack">
        {speakers.slice(0, STACK).map((name, i) => {
          const person = people.find((x) => x.name === name);
          return (
            // Узнанное автоматически имя тоже бывает ошибочным: исправить можно любое.
            <Tip key={name} content={(!isUnnamed(name) && person?.role) || "Кто это?"}>
              <button type="button" className="card__person" aria-label={name} onClick={() => onNameSpeaker?.(name)}>
                <PersonMark name={name} tone={tones ? tones.get(name) : speakerTone(name, i, person)}
                  hasAvatar={!!person?.has_avatar} version={avatarVersion?.[name]} endpoint={endpoint} />
              </button>
            </Tip>
          );
        })}
      </span>
      {onOpenSpeakers ? (
        <Tip content="Имена, голоса и история изменений">
          <button type="button" className="card__people-names" aria-expanded={speakersOpen}
            aria-label={`Спикеры: ${speakers.join(", ")}`} onClick={onOpenSpeakers}>
            {text}
          </button>
        </Tip>
      ) : (
        <span className="card__people-names">{text}</span>
      )}
    </span>
  );
}

export function CardHeader({
  rec, durationS = rec.duration_s, speakers, people, tones, endpoint, avatarVersion, onRename, onNameSpeaker,
  onOpenSpeakers, speakersOpen = false, categories, onCategory, onOpenCategories, actions,
}: {
  rec: Recording;
  /** Длительность для подписи: у импорта без неё — конец последней реплики. */
  durationS?: number | null;
  speakers: string[];
  people: PersonColor[];
  /** Цвета спикеров встречи (lib/tones, одна карта с лентой и панелью); нет — по порядку в шапке. */
  tones?: ReadonlyMap<string, string>;
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
  /** Действия карточки (CardActions) — в строке названия, справа. */
  actions?: ReactNode;
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
  const category = onCategory && (categories ? (
    <CategoryButton rec={rec} list={categories} onPick={onCategory} onSettings={onOpenCategories} />
  ) : (
    // Категории ещё читаются: заготовка размером с метку — строка не дёргается, когда она появится.
    <Skeleton width={120} height={32} className="card__cat-skel" />
  ));

  return (
    <header className="card__header">
      <div className="card__title-row">
        {editing ? (
          <input
            className="field card__title-input"
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
          <Tip content="Нажмите, чтобы переименовать">
            <h1 className="card__title" onClick={begin}>
              {shown}{rec.title_source === "ai" && rec.title && <AiBadge onClick={begin} by={llmLabel(rec.title_llm)} />}
            </h1>
          </Tip>
        )}
        {actions}
      </div>
      {(meta || category || speakers.length > 0) && (
        // Одна строка текста (0.6.1): «дата · длительность · ● Категория · участники».
        <div className="card__meta-row">
          {meta && <span className="card__meta num">{meta}</span>}
          {meta && category && <span className="card__meta-dot" aria-hidden="true">·</span>}
          {category}
          {speakers.length > 0 && (meta || category) && <span className="card__meta-dot" aria-hidden="true">·</span>}
          {speakers.length > 0 && (
            <PeopleLine speakers={speakers} people={people} tones={tones} endpoint={endpoint} avatarVersion={avatarVersion}
              onNameSpeaker={onNameSpeaker} onOpenSpeakers={onOpenSpeakers} speakersOpen={speakersOpen} />
          )}
        </div>
      )}
      {rec.merge && <MergeNote info={rec.merge} />}
    </header>
  );
}
