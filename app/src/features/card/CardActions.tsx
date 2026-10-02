/**
 * Действия карточки записи: две группы вместо сплошной полосы.
 *
 * Слева — главное, со значком и подписью: «Экспорт» (меню форматов) и «В базу
 * знаний». Справа — значками с подсказками: «Открыть папку» и «Ещё действия»
 * (⋯) — редкое и опасное: «Переразделить на спикеров…», «Перерасшифровать…»,
 * «Переанализировать», «Предложить название» и за чертой «Удалить…».
 * Подтверждения — в том же меню, как у записи в списке.
 *
 * Узкая карточка (меньше COMPACT_PX) — подписи свёрнуты в значки; имя кнопки
 * для экранного диктора и подсказка при наведении остаются.
 */

import { useRef, useState, type ReactNode } from "react";
import {
  BookOpen, ChevronDown, Download, Ellipsis, FolderOpen, RotateCcw, ScanSearch, Trash2, Users, WandSparkles,
} from "lucide-react";
import { inTauri } from "../../lib/shell";
import { useWide } from "../../live/useWide";
import { Button } from "../../ui/Button";
import { ItemMenu, type MenuItem } from "../recordings/ItemMenu";

/** Уже этого — подписи главных кнопок свёрнуты в значки. */
export const COMPACT_PX = 360;

const FORMATS: { id: string; hint: string }[] = [
  { id: "md", hint: "Markdown: названия, спикеры и таймкоды" },
  { id: "txt", hint: "Простой текст" },
  { id: "srt", hint: "Субтитры для видеоплеера" },
];

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

const DELETE_NOTE = "Удалить запись и расшифровку? Это действие нельзя отменить.";
const RETRANSCRIBE_NOTE = "Расшифровка будет создана заново: ручные правки и имена, не сохранённые в базе голосов, "
  + "будут потеряны. Продолжить?";

type Menu = { kind: "export" | "more"; at: { x: number; y: number } };
type Confirm = "delete" | "retranscribe" | null;

/** Где раскрыть меню: под кнопкой, по её левому (или правому) краю. */
function below(el: HTMLElement | null, alignRight: boolean): { x: number; y: number } {
  const r = el?.getBoundingClientRect();
  if (!r) return { x: 0, y: 0 };
  return { x: alignRight ? r.right - 240 : r.left, y: r.bottom + 4 };
}

export function CardActions({
  canExport, canRetranscribe, busy, onExport, onKbExport, onOpenFolder, onRetranscribe, onRediarize, onReanalyze,
  onSuggestTitle, onDelete,
}: {
  canExport: boolean;
  canRetranscribe: boolean;
  busy: boolean;
  onExport: (format: string) => void;
  /** «В базу знаний»; нет — кнопки нет (папка для встреч не задана или нечего выгружать). */
  onKbExport?: () => void;
  onOpenFolder: () => void;
  onRetranscribe: () => void;
  /** «Переразделить на спикеров…» (только разделение, без распознавания); нет — пункта нет. */
  onRediarize?: () => void;
  /** «Переанализировать» (анализ встречи агентом); нет — пункта нет. */
  onReanalyze?: () => void;
  /** «Предложить название»; нет — пункта нет. */
  onSuggestTitle?: () => void;
  onDelete: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const exportBtn = useRef<HTMLButtonElement>(null);
  const moreBtn = useRef<HTMLButtonElement>(null);
  const compact = !useWide(root, COMPACT_PX);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);

  const close = (focusBack = true) => {
    const kind = menu?.kind;
    setMenu(null);
    setConfirm(null);
    if (focusBack) (kind === "export" ? exportBtn : moreBtn).current?.focus();
  };
  const toggle = (kind: Menu["kind"]) => {
    if (menu?.kind === kind) { close(); return; }
    setConfirm(null);
    setMenu({ kind, at: below((kind === "export" ? exportBtn : moreBtn).current, kind === "more") });
  };
  const run = (fn: () => void) => () => { close(false); fn(); };

  const label = (text: string) => <span className={compact ? "sr-only" : "act__label"}>{text}</span>;

  let items: MenuItem[] = [];
  let note: string | undefined;
  if (menu?.kind === "export") {
    items = FORMATS.map((f) => ({ label: f.id, hint: f.hint, onSelect: run(() => onExport(f.id)) }));
  } else if (confirm === "delete") {
    note = DELETE_NOTE;
    items = [
      { label: "Удалить", danger: true, icon: <Trash2 {...ICON} />, onSelect: run(onDelete) },
      { label: "Отмена", autoFocus: true, onSelect: () => setConfirm(null) },
    ];
  } else if (confirm === "retranscribe") {
    note = RETRANSCRIBE_NOTE;
    items = [
      { label: "Перерасшифровать", icon: <RotateCcw {...ICON} />, disabled: busy, onSelect: run(onRetranscribe) },
      { label: "Отмена", autoFocus: true, onSelect: () => setConfirm(null) },
    ];
  } else if (menu) {
    const opt = (on: boolean | undefined, item: MenuItem): MenuItem[] => (on ? [item] : []);
    items = [
      ...opt(!!onRediarize, {
        label: "Переразделить на спикеров…", icon: <Users {...ICON} />, disabled: busy,
        hint: "Заново определить, кто говорит, не распознавая речь повторно", onSelect: run(() => onRediarize?.()),
      }),
      ...opt(canRetranscribe, {
        label: "Перерасшифровать…", icon: <RotateCcw {...ICON} />, disabled: busy,
        hint: "Распознать запись заново", onSelect: () => setConfirm("retranscribe"),
      }),
      ...opt(!!onReanalyze, {
        label: "Переанализировать", icon: <ScanSearch {...ICON} />, disabled: busy,
        hint: "Заново разметить встречу агентом", onSelect: run(() => onReanalyze?.()),
      }),
      ...opt(!!onSuggestTitle, {
        label: "Предложить название", icon: <WandSparkles {...ICON} />, disabled: busy,
        hint: "Название по содержанию встречи", onSelect: run(() => onSuggestTitle?.()),
      }),
      {
        label: "Удалить…", danger: true, separator: true, icon: <Trash2 {...ICON} />,
        onSelect: () => setConfirm("delete"),
      },
    ];
  }

  const iconButton = (name: string, hint: string, icon: ReactNode, onClick: () => void, extra = {}) => (
    <Button className="act act--icon" aria-label={name} title={hint} onClick={onClick} {...extra}>{icon}</Button>
  );

  return (
    <div ref={root} className={`card__actions${compact ? " card__actions--compact" : ""}`}>
      <div className="act-group" role="group" aria-label="Главные действия">
        {canExport && (
          <Button ref={exportBtn} className="act" title="Сохранить расшифровку файлом: md, txt или srt"
            aria-haspopup="menu" aria-expanded={menu?.kind === "export"} onClick={() => toggle("export")}>
            <Download {...ICON} />{label("Экспорт")}<ChevronDown {...ICON} size={14} className="act__chevron" />
          </Button>
        )}
        {onKbExport && (
          <Button className="act" title="Выгрузить расшифровку и итоги в папку встреч базы знаний"
            onClick={onKbExport} disabled={busy}>
            <BookOpen {...ICON} />{label("В базу знаний")}
          </Button>
        )}
      </div>
      <div className="act-group act-group--end" role="group" aria-label="Другие действия">
        {inTauri() && iconButton("Открыть папку", "Открыть папку записи в проводнике",
          <FolderOpen {...ICON} />, onOpenFolder)}
        {iconButton("Ещё действия", "Ещё действия: переразделить, перерасшифровать, удалить",
          <Ellipsis {...ICON} />, () => toggle("more"),
          { ref: moreBtn, "aria-haspopup": "menu", "aria-expanded": menu?.kind === "more" })}
      </div>
      {menu && (
        <ItemMenu at={menu.at} items={items} note={note}
          label={menu.kind === "export" ? "Формат экспорта" : "Ещё действия с записью"}
          anchor={menu.kind === "export" ? exportBtn : moreBtn} onClose={() => close()} />
      )}
    </div>
  );
}
