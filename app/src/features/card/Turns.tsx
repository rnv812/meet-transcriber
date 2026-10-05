import { memo, useContext, useState, type KeyboardEvent, type MouseEvent } from "react";
import type { ChapterView, TurnRow } from "../../lib/analysisView";
import { clock, plural } from "../../lib/format";
import { JiraLinks, type JiraMatch } from "../../lib/jira";
import { nfc, type Range } from "../../lib/search";
import { NO_SPEAKER, isUnnamed, type Turn } from "../../lib/speakers";
import type { PhraseType } from "../../lib/types";
import { AskAgentButton } from "../../ui/AskAgent";
import { Highlight } from "../../ui/Highlight";
import { LinkedText } from "../../ui/LinkedText";
import { TypeIcon } from "./markup";

export type PersonColor = { name: string; color: string; has_avatar: boolean };

const NO_LINKS: JiraMatch[] = [];

/** Подсветка поиска: что выделить в реплике и номер её первого совпадения. */
export type TurnMarks = Map<number, { ranges: Range[]; first: number }>;

export type { TurnRow };

/** Разметка анализа у реплик: тип (значок), важная ли (полоса слева), главы. */
export type TurnAnnotations = {
  types?: (PhraseType | null)[] | null;
  key?: boolean[] | null;
  chapters?: ChapterView[];
};

/** Плоский список без компонента на реплику: 2 часа записи — около тысячи блоков. */
export const Turns = memo(function Turns({
  turns, colors, playable, onPlay, onNameSpeaker, onSpeaker, selected, onSelect, onSplitAt, marks, onAskAgent,
  rows, annotations, onAskChapter, onExpand, textPhase = false,
}: {
  turns: Turn[];
  colors: Map<string, string>;
  playable: boolean;
  onPlay: (turn: Turn) => void;
  onNameSpeaker?: (label: string) => void;
  /** Щелчок по имени у реплики: меню правки спикера (иначе — `onNameSpeaker`). */
  onSpeaker?: (turn: number, anchor: HTMLElement) => void;
  /** Выбранные реплики (Ctrl/Shift+щелчок). */
  selected?: ReadonlySet<number>;
  onSelect?: (turn: number, how: "toggle" | "range") => void;
  /** Правый щелчок по тексту: «Разделить реплику здесь». */
  onSplitAt?: (turn: number, event: MouseEvent<HTMLElement>) => void;
  marks?: TurnMarks;
  /** ✦ «Спросить агента» (кнопка при наведении и фокусе, клавиша A): номера реплик. */
  onAskAgent?: (turns: number[]) => void;
  /** Что показывать и в каком порядке (фильтры, главы); нет — все реплики подряд. */
  rows?: TurnRow[];
  annotations?: TurnAnnotations | null;
  /** ✦ «Обсудить главу с агентом»: номер главы. */
  onAskChapter?: (chapter: number) => void;
  /** Развернуть свёрнутые реплики «… N реплик» (по первой из них). */
  onExpand?: (from: number) => void;
  /**
   * Текст до спикеров (Р4): подписи — просто текст, не кнопки; у собеседников подписи нет вовсе
   * (ни «Неизвестный», ни «Спикер N» — их скажет диаризация).
   */
  textPhase?: boolean;
}) {
  // Ctrl/Shift+щелчок по реплике — выбор; простой щелчок по тексту остаётся выделением текста.
  const pick = (e: MouseEvent, i: number) => {
    if (!onSelect) return false;
    if (e.ctrlKey || e.metaKey) onSelect(i, "toggle");
    else if (e.shiftKey) onSelect(i, "range");
    else return false;
    return true;
  };
  // Выбор с клавиатуры: реплики — одна точка Tab (roving tabindex), стрелки
  // ходят по ним, Пробел — выбрать или снять, Shift+Пробел — диапазон.
  const [focusAt, setFocusAt] = useState(0);
  const rowKey = (e: KeyboardEvent<HTMLDivElement>, i: number) => {
    if (!onSelect || e.target !== e.currentTarget) return;
    // A (по коду клавиши — и в русской раскладке): спросить агента об этой реплике
    // или, если она среди выбранных, обо всех выбранных.
    if (e.code === "KeyA" && onAskAgent && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      e.preventDefault();
      onAskAgent(selected?.has(i) && selected.size > 1 ? [...selected].sort((a, b) => a - b) : [i]);
      return;
    }
    const step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
    if (step || e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const rows = [...(e.currentTarget.parentElement?.querySelectorAll<HTMLElement>("[data-turn]") ?? [])];
      const here = rows.indexOf(e.currentTarget);
      const next = e.key === "Home" ? rows[0] : e.key === "End" ? rows.at(-1) : rows[here + step];
      next?.focus();
      next?.scrollIntoView?.({ block: "nearest" });
    } else if (e.key === " ") {
      e.preventDefault();
      onSelect(i, e.shiftKey ? "range" : "toggle");
    }
  };
  // Точка Tab — среди показанных реплик: свёрнутая фильтром не должна её забрать.
  const shown = (i: number) => turns[i] !== undefined && turns[i]!.kind !== "break";
  const rendered = rows ? new Set(rows.flatMap((r) => (r.kind === "turn" && shown(r.i) ? [r.i] : []))) : null;
  const first = rendered ? (rendered.values().next().value ?? -1) : turns.findIndex((t) => t.kind !== "break");
  const roving = shown(focusAt) && (!rendered || rendered.has(focusAt)) ? focusAt : first;
  // Задачи Jira в тексте — ссылки (настройка «Ссылки на задачи Jira»): что
  // сказано во встрече, находит резидент (jira.turns); без его ответа — ключи текстом.
  const jira = useContext(JiraLinks);
  const types = annotations?.types;
  const key = annotations?.key;
  const renderTurn = (i: number) => {
        const t = turns[i]!;
        if (t.kind === "break") {
          return (
            <div className="turn-break" role="separator" aria-label={t.texts.join(" ")} key={`t${i}`}>
              <span className="turn-break__text">{t.texts.join(" ")}</span>
            </div>
          );
        }
        const unnamed = isUnnamed(t.speaker);
        const color = colors.get(t.speaker);
        // Как в поиске (lib/search.ts, prepare): подсветка — по тексту в NFC.
        const text = nfc(t.texts.join(" "));
        const mark = marks?.get(i);
        const on = selected?.has(i) ?? false;
        return (
          <div className={`turn${mark ? " turn--found" : ""}${on ? " turn--selected" : ""}${key?.[i] ? " turn--key" : ""}`}
            key={`t${i}`}
            data-selected={on || undefined} data-turn={i}
            tabIndex={onSelect ? (i === roving ? 0 : -1) : undefined}
            role={onSelect ? "group" : undefined}
            aria-label={onSelect ? `Реплика ${clock(t.start)}, ${t.speaker}${on ? ", выбрана" : ""}` : undefined}
            aria-keyshortcuts={onSelect ? (onAskAgent ? "Space Shift+Space A" : "Space Shift+Space") : undefined}
            onFocus={onSelect ? () => setFocusAt(i) : undefined}
            onKeyDown={onSelect ? (e) => rowKey(e, i) : undefined}
            onMouseDown={onSelect ? (e) => { if (e.shiftKey) e.preventDefault(); } : undefined}
            onClick={onSelect ? (e) => { if (pick(e, i)) e.preventDefault(); } : undefined}>
            {on && <span className="sr-only">Выбрано.</span>}
            {playable ? (
              <button type="button" className="turn__time num" aria-label={`Слушать с ${clock(t.start)}`}
                onClick={(e) => { if (!(onSelect && (e.ctrlKey || e.metaKey || e.shiftKey))) onPlay(t); }}>
                {clock(t.start)}
              </button>
            ) : (
              <span className="turn__time num">{clock(t.start)}</span>
            )}
            <div className="turn__body">
              <div className="turn__head">
                {types?.[i] && <TypeIcon type={types[i]!} />}
                {textPhase && t.speaker === NO_SPEAKER ? (
                  <span className="sr-only">Спикер ещё не определён</span>
                ) : textPhase ? (
                  <span className="turn__speaker">{t.speaker}</span>
                ) : t.speaker === NO_SPEAKER ? (
                  <span className="turn__speaker turn__speaker--unnamed">{t.speaker}</span>
                ) : (
                  <button type="button" className={`turn__speaker${unnamed ? " turn__speaker--unnamed" : ""}`}
                    style={!unnamed && color ? { color } : undefined} aria-haspopup={onSpeaker ? "dialog" : undefined}
                    title={onSpeaker ? "Исправить спикера реплики" : undefined}
                    onClick={(e) => {
                      if (onSelect && (e.ctrlKey || e.metaKey || e.shiftKey)) return;
                      if (onSpeaker) onSpeaker(i, e.currentTarget);
                      else onNameSpeaker?.(t.speaker);
                    }}>{t.speaker}</button>
                )}
                {t.uncertain && <span className="turn__flag">(нахлёст)</span>}
                {onAskAgent && (
                  <AskAgentButton className="turn__ask" label="Спросить агента об этой реплике"
                    title="Спросить агента об этой реплике (A)" aria-keyshortcuts="A"
                    onClick={(e) => { if (!(onSelect && (e.ctrlKey || e.metaKey || e.shiftKey))) onAskAgent([i]); }} />
                )}
              </div>
              {/* Реплика, найденная только по спикеру, — совпадение целиком. */}
              <p className="turn__text" data-hit={mark && !mark.ranges.length ? mark.first : undefined}
                onContextMenu={onSplitAt ? (e) => onSplitAt(i, e) : undefined}>
                {jira ? <LinkedText text={text} ranges={mark?.ranges} firstHit={mark?.first} linker={jira}
                  links={jira.turns ? jira.turns.get(i) ?? NO_LINKS : undefined} />
                  : mark?.ranges.length ? <Highlight text={text} ranges={mark.ranges} firstHit={mark.first} /> : text}
              </p>
            </div>
          </div>
        );
  };
  const chapters = annotations?.chapters;
  const renderRow = (row: TurnRow) => {
    if (row.kind === "turn") return renderTurn(row.i);
    if (row.kind === "chapter") {
      const c = chapters?.[row.c];
      if (!c) return null;
      return (
        <div className="chapter-head" key={`c${row.c}`} data-chapter={row.c}>
          <h3 className="chapter-head__title">
            <span className="chapter-head__n">Глава {c.n}</span>
            <span className="chapter-head__sep"> · </span>
            {c.title}
          </h3>
          <span className="chapter-head__time num">{clock(c.start)}–{clock(c.end)}</span>
          {onAskChapter && (
            <AskAgentButton className="chapter-head__ask" label={`Обсудить главу «${c.title}» с агентом`}
              title="Обсудить главу с агентом" onClick={() => onAskChapter(row.c)} />
          )}
        </div>
      );
    }
    return (
      <button type="button" className="turns-more" key={`m${row.from}`} data-more={row.from}
        onClick={() => onExpand?.(row.from)} title="Показать скрытые фильтром реплики">
        … {row.count} {plural(row.count, "реплика", "реплики", "реплик")}
      </button>
    );
  };
  return (
    <div className="turns">
      {rows ? rows.map(renderRow) : turns.map((_, i) => renderTurn(i))}
    </div>
  );
});
