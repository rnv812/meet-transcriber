/**
 * Действия карточки записи: две группы вместо сплошной полосы.
 *
 * Слева — главное, со значком и подписью: «Экспорт» (меню форматов) и «В базу
 * знаний». Справа — значками с подсказками: «Открыть папку» и «Ещё действия»
 * (⋯) — редкое и опасное: «Переразделить на спикеров…», «Перерасшифровать…»,
 * ✦ «Улучшить расшифровку», «Переанализировать», «Предложить название» и за
 * чертой «Удалить…».
 * Подтверждения — общим окном (ui/ConfirmDialog): фокус на «Отмена», Esc — отмена.
 *
 * Узкая карточка (меньше COMPACT_PX) — подписи свёрнуты в значки; имя кнопки
 * для экранного диктора и подсказка при наведении остаются.
 */

import { useRef, useState, type ReactNode } from "react";
import {
  BookOpen, ChevronDown, Download, Ellipsis, FolderOpen, RotateCcw, ScanSearch, Sparkles, Trash2, Users, WandSparkles,
} from "lucide-react";
import { inTauri } from "../../lib/shell";
import { useWide } from "../../live/useWide";
import { Button } from "../../ui/Button";
import { ConfirmDialog, type ConfirmOptions } from "../../ui/ConfirmDialog";
import { ItemMenu, type MenuItem } from "../recordings/ItemMenu";

/** Уже этого — подписи главных кнопок свёрнуты в значки. */
export const COMPACT_PX = 360;

const FORMATS: { id: string; label: string; hint: string }[] = [
  { id: "md", label: "Markdown (.md)", hint: "Названия, спикеры и таймкоды" },
  { id: "txt", label: "Текст (.txt)", hint: "Простой текст" },
  { id: "srt", label: "Субтитры (.srt)", hint: "Субтитры для видеоплеера" },
];

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

/** Тексты подтверждений: что именно пропадёт или будет заменено. */
export const CONFIRMS: Record<"delete" | "retranscribe" | "reanalyze", ConfirmOptions> = {
  delete: {
    title: "Удалить запись?", confirmLabel: "Удалить",
    message: "Звук, расшифровка, итоги и разметка будут удалены с диска. Это действие нельзя отменить.",
  },
  retranscribe: {
    title: "Перерасшифровать запись?", confirmLabel: "Перерасшифровать",
    message: "Расшифровка будет создана заново: ручные правки и имена, не сохранённые в базе голосов, будут потеряны.",
  },
  reanalyze: {
    title: "Разметить встречу заново?", confirmLabel: "Переанализировать", danger: false,
    message: "Главы, наблюдения и важные фрагменты будут заменены новой разметкой. "
      + "Файлы, уже выгруженные в базу знаний, не изменятся.",
  },
};

type Menu = { kind: "export" | "more"; at: { x: number; y: number } };
type Confirm = keyof typeof CONFIRMS | null;

/** Где раскрыть меню: под кнопкой, по её левому (или правому) краю. */
function below(el: HTMLElement | null, alignRight: boolean): { x: number; y: number } {
  const r = el?.getBoundingClientRect();
  if (!r) return { x: 0, y: 0 };
  return { x: alignRight ? r.right - 240 : r.left, y: r.bottom + 4 };
}

export function CardActions({
  canExport, canRetranscribe, busy, onExport, onKbExport, onOpenFolder, onRetranscribe, onRediarize, onReanalyze,
  reanalyzeBlocked = null, reanalyzeLabel = "Переанализировать", onSuggestTitle, onImprove, improveBlocked = null,
  onDelete,
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
  /** Почему «Переанализировать» сейчас недоступно (анализ уже идёт, нет модели) — подсказкой; null — доступно. */
  reanalyzeBlocked?: string | null;
  /** Подпись пункта: «Анализировать», если анализа ещё не было. */
  reanalyzeLabel?: string;
  /** «Предложить название»; нет — пункта нет. */
  onSuggestTitle?: () => void;
  /** ✦ «Улучшить расшифровку» (ИИ находит неверно распознанные термины); нет — пункта нет. */
  onImprove?: () => void;
  /** Почему «Улучшить расшифровку» сейчас недоступно (нет модели) — подсказкой; null — доступно. */
  improveBlocked?: string | null;
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
    if (focusBack) (kind === "export" ? exportBtn : moreBtn).current?.focus();
  };
  const toggle = (kind: Menu["kind"]) => {
    if (menu?.kind === kind) { close(); return; }
    setMenu({ kind, at: below((kind === "export" ? exportBtn : moreBtn).current, kind === "more") });
  };
  const run = (fn: () => void) => () => { close(false); fn(); };

  const label = (text: string) => <span className={compact ? "sr-only" : "act__label"}>{text}</span>;

  /** Пункт меню, который сначала спрашивает: меню закрывается, открывается подтверждение. */
  const ask = (kind: NonNullable<Confirm>) => () => { close(false); setConfirm(kind); };
  const confirmed = () => {
    const kind = confirm;
    setConfirm(null);
    if (kind === "delete") onDelete();
    else if (kind === "retranscribe") onRetranscribe();
    else if (kind === "reanalyze") onReanalyze?.();
  };

  let items: MenuItem[] = [];
  if (menu?.kind === "export") {
    items = FORMATS.map((f) => ({ label: f.label, hint: f.hint, onSelect: run(() => onExport(f.id)) }));
  } else if (menu) {
    const opt = (on: boolean | undefined, item: MenuItem): MenuItem[] => (on ? [item] : []);
    items = [
      ...opt(!!onRediarize, {
        label: "Переразделить на спикеров…", icon: <Users {...ICON} />, disabled: busy,
        hint: "Заново определить, кто говорит, не распознавая речь повторно", onSelect: run(() => onRediarize?.()),
      }),
      ...opt(canRetranscribe, {
        label: "Перерасшифровать…", icon: <RotateCcw {...ICON} />, disabled: busy,
        hint: "Распознать запись заново", onSelect: ask("retranscribe"),
      }),
      ...opt(!!onImprove, {
        label: "Улучшить расшифровку", icon: <Sparkles {...ICON} />, disabled: busy || !!improveBlocked,
        hint: improveBlocked ?? "ИИ найдёт неверно распознанные термины и покажет короткий список замен",
        onSelect: run(() => onImprove?.()),
      }),
      ...opt(!!onReanalyze, {
        // Разметки ещё нет — заменять нечего, спрашивать незачем.
        label: reanalyzeLabel === "Анализировать" ? reanalyzeLabel : `${reanalyzeLabel}…`,
        icon: <ScanSearch {...ICON} />, disabled: busy || !!reanalyzeBlocked,
        hint: reanalyzeBlocked ?? "Заново разметить встречу агентом",
        onSelect: reanalyzeLabel === "Анализировать" ? run(() => onReanalyze?.()) : ask("reanalyze"),
      }),
      ...opt(!!onSuggestTitle, {
        label: "Предложить название", icon: <WandSparkles {...ICON} />, disabled: busy,
        hint: "Название по содержанию встречи", onSelect: run(() => onSuggestTitle?.()),
      }),
      {
        label: "Удалить…", danger: true, separator: true, icon: <Trash2 {...ICON} />,
        onSelect: ask("delete"),
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
          <Button ref={exportBtn} className="act" title="Сохранить расшифровку файлом: Markdown, текст или субтитры"
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
        <ItemMenu at={menu.at} items={items}
          label={menu.kind === "export" ? "Формат экспорта" : "Ещё действия с записью"}
          anchor={menu.kind === "export" ? exportBtn : moreBtn} onClose={() => close()} />
      )}
      {confirm && (
        <ConfirmDialog {...CONFIRMS[confirm]} onConfirm={confirmed}
          onCancel={() => { setConfirm(null); moreBtn.current?.focus(); }} />
      )}
    </div>
  );
}
