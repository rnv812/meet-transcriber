/**
 * «Исправить…»: неверно распознанное слово или фразу выделяют в реплике
 * (мышью или двойным щелчком по слову) — рядом появляется «Исправить…»; то же
 * по Ctrl+E, кнопкой «Исправить…» на панели над лентой (без выделения — подсказка,
 * как им пользоваться) и в меню правого щелчка по тексту. В окне — что распознано (его
 * можно прослушать), как правильно, добавить ли исправление в термины
 * распознавания и заменить ли во всей встрече. Замена — шаг истории встречи:
 * её отменяет «Отменить» здесь же, в панели «Спикеры» и Ctrl+Z там.
 */

import { Play, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import {
  applyTextFix, getSettings, patchSettings, previewTextFix, removeHotword, undoSpeakers, type Endpoint,
} from "../../lib/api";
import { clock, errorText, plural } from "../../lib/format";
import { nfc } from "../../lib/search";
import type { Turn } from "../../lib/speakers";
import { expandToWords, newTerms, segmentSpan, wordAt } from "../../lib/textfix";
import type { Segment, TextPreview } from "../../lib/types";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Popover } from "../../ui/Popover";
import { floatingStyle, useFloating } from "../../ui/floating";
import { rulesOf, withRule } from "../settings/ReplacementsEditor";
import { Icon } from "../../ui/Icon";

type Box = { left: number; top: number; bottom: number };
/** Что исправляем: реплика, сегмент и начало в его тексте; `split` — выделение через границу фраз. */
type Target = { turn: number; seg: number; offset: number; find: string; box: Box; split: boolean };
/** Итог над репликами; `step` — шаг истории: «Отменить» — только пока он последний. */
type Notice = { key: string; text: string; undo: (() => Promise<string>) | null; step?: string };

const POPOVER_W = 340;
const matchesWord = (n: number) => plural(n, "совпадение", "совпадения", "совпадений");

function textOffset(root: Node, node: Node, offset: number): number {
  const range = document.createRange();
  range.setStart(root, 0);
  range.setEnd(node, offset);
  return range.toString().length;
}

function boxOf(range: Range | null, fallback: HTMLElement): Box {
  const r = range && typeof range.getBoundingClientRect === "function" ? range.getBoundingClientRect() : null;
  const b = r && (r.width || r.height) ? r : fallback.getBoundingClientRect();
  return { left: b.left, top: b.top, bottom: b.bottom };
}

/** Место [start, end) в тексте реплики `t` → что исправлять; null — слов там нет. */
function targetOf(turns: Turn[], t: number, start: number, end: number, box: Box): Target | null {
  const turn = turns[t];
  if (!turn || turn.kind === "break" || !turn.idx?.length) return null;
  const text = nfc(turn.texts.join(" "));
  const span = expandToWords(text, start, end);
  if (!span) return null;
  const find = text.slice(span.start, span.end);
  const at = segmentSpan(turn.texts, span.start, span.end);
  if (!at) return { turn: t, seg: turn.idx[0]!, offset: 0, find, box, split: true };
  return { turn: t, seg: turn.idx[at.k] ?? turn.idx[0]!, offset: at.offset, find, box, split: false };
}

/** Выделение в тексте одной реплики → что исправлять. */
function fromSelection(turns: Turn[], onlyTurn?: number): Target | null {
  const sel = window.getSelection?.();
  if (!sel || !sel.rangeCount || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  const start = range.startContainer;
  const root = (start instanceof Element ? start : start.parentElement)?.closest<HTMLElement>(".turn__text");
  if (!root || !root.contains(range.endContainer)) return null;
  const t = Number(root.closest<HTMLElement>("[data-turn]")?.dataset.turn);
  if (!Number.isInteger(t) || (onlyTurn !== undefined && t !== onlyTurn)) return null;
  return targetOf(turns, t, textOffset(root, start, range.startOffset),
    textOffset(root, range.endContainer, range.endOffset), boxOf(range, root));
}

const TYPING = "input, textarea, select, [contenteditable=''], [contenteditable=true]";

export type TextFix = {
  /** Правый щелчок по тексту реплики: есть выделение в ней — «Исправить…» (true), иначе — не наше. */
  onContextMenu: (turn: number, event: MouseEvent<HTMLElement>) => boolean;
  /** «Исправить слово» из меню правого щелчка: слово в месте `at` текста реплики. */
  openWord: (turn: number, at: number, anchor: HTMLElement) => void;
  /** «Исправить…» на панели над лентой: выделение в реплике — окно, как по Ctrl+E; нет — подсказка у `anchor`. */
  openFromBar: (anchor: HTMLElement) => void;
  /** Кнопка у выделения и окно исправления. */
  node: ReactNode;
  /** Итог над репликами: что исправлено и что добавлено в термины, с «Отменить». */
  bar: ReactNode;
};

export function useTextFix({ endpoint, id, turns, segments, playable, head, onPlay, onChanged }: {
  endpoint: Endpoint;
  id: string;
  turns: Turn[];
  segments: Segment[];
  playable: boolean;
  /** Прослушать место: с `start` до `until` секунд. */
  onPlay: (start: number, until: number) => void;
  /** Последний применённый шаг истории встречи (`edit_head` записи). */
  head?: string | null;
  /** Исправлено или отменено: перечитать запись. */
  onChanged: () => void;
}): TextFix {
  const [target, setTarget] = useState<Target | null>(null);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  // «Исправить…» под выделением — по общему правилу (ui/floating): у правого и
  // нижнего края окна не уходит за край.
  const floatBtn = useRef<HTMLButtonElement | null>(null);
  const floatRef = useCallback((el: HTMLButtonElement | null) => { floatBtn.current = el; setAnchor(el); }, []);
  const tBox = target?.box;
  const selection = useMemo(() => (tBox ? { left: tBox.left, right: tBox.left, top: tBox.top, bottom: tBox.bottom } : null),
    [tBox]);
  const floatPos = useFloating(selection, floatBtn, { gap: 4 });
  const [replace, setReplace] = useState("");
  /** «Добавить в термины»: null — по умолчанию (включено, если исправление добавляет значимые слова). */
  const [hotwordSet, setHotword] = useState<boolean | null>(null);
  const [all, setAll] = useState(false);
  const [rule, setRule] = useState(false);
  const [preview, setPreview] = useState<TextPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);

  // Окно открыто — кнопку у выделения не трогаем (отложенный разбор выделения после
  // щелчка, который это окно и открыл, иначе закрыл бы его).
  const opened = useRef(false);
  /** Подсказка «как исправить» у кнопки панели (нажали без выделения). */
  const [hint, setHint] = useState<HTMLElement | null>(null);
  const close = useCallback(() => { opened.current = false; setOpen(false); setTarget(null); setError(null); }, []);
  const show = useCallback((t: Target) => {
    opened.current = true;
    setHint(null);
    setTarget(t);
    setReplace(t.find);
    setHotword(null);
    setAll(false);
    setRule(false);
    setPreview(null);
    setError(null);
    setOpen(true);
  }, []);

  // Расшифровку перечитали — номера сегментов и места могли сдвинуться.
  useEffect(() => { close(); }, [segments, close]);
  useEffect(() => { setNotices([]); }, [id]);

  // Кнопка «Исправить…» у выделения: после отпускания мыши (и двойного щелчка
  // по слову); снятое выделение и прокрутка её убирают. Ctrl+E — сразу окно.
  useEffect(() => {
    if (open) return;
    const up = (e: globalThis.MouseEvent) => {
      if (e.target instanceof Element && e.target.closest(".tfix-float")) return;
      setTimeout(() => { if (!opened.current) setTarget(fromSelection(turns)); }, 0);
    };
    const change = () => {
      if (!opened.current && (window.getSelection?.()?.isCollapsed ?? true)) setTarget(null);
    };
    const scroll = () => { if (!opened.current) setTarget(null); };
    const key = (e: globalThis.KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.code !== "KeyE") return;
      if (e.target instanceof Element && e.target.closest(TYPING)) return;
      const t = fromSelection(turns);
      if (!t) return;
      e.preventDefault();
      show(t);
    };
    document.addEventListener("mouseup", up);
    document.addEventListener("selectionchange", change);
    document.addEventListener("keydown", key);
    window.addEventListener("scroll", scroll, true);
    return () => {
      document.removeEventListener("mouseup", up);
      document.removeEventListener("selectionchange", change);
      document.removeEventListener("keydown", key);
      window.removeEventListener("scroll", scroll, true);
    };
  }, [open, turns, show]);

  // Сколько раз это во встрече и когда звучит выбранное место.
  useEffect(() => {
    if (!open || !target || target.split) return;
    let live = true;
    previewTextFix(endpoint, id, { find: target.find, segment: target.seg, offset: target.offset })
      .then((p) => { if (live) setPreview(p); })
      .catch((e) => { if (live) setError(errorText(e)); });
    return () => { live = false; };
  }, [open, target, endpoint, id]);

  const onContextMenu = useCallback((t: number, e: MouseEvent<HTMLElement>) => {
    const found = fromSelection(turns, t);
    if (!found) return false;
    e.preventDefault();
    show(found);
    return true;
  }, [turns, show]);

  const openFromBar = useCallback((el: HTMLElement) => {
    const found = fromSelection(turns);
    if (found) show(found);
    else setHint((cur) => (cur === el ? null : el));
  }, [turns, show]);

  const openWord = useCallback((t: number, at: number, el: HTMLElement) => {
    const turn = turns[t];
    if (!turn) return;
    const span = wordAt(nfc(turn.texts.join(" ")), at);
    const found = span && targetOf(turns, t, span.start, span.end, boxOf(null, el));
    if (found) show(found);
  }, [turns, show]);

  const notify = (n: Notice[]) => setNotices(n);
  const right = replace.trim().split(/\s+/).join(" ");
  const terms = target ? newTerms(target.find, right) : "";
  const hotword = hotwordSet ?? !!terms;
  const apply = async () => {
    if (!target || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await applyTextFix(endpoint, id, {
        find: target.find, replace: right, scope: all ? "all" : "one", segment: target.seg,
        offset: target.offset, count: segments.length, add_hotword: hotword, add_rule: rule && right !== target.find,
      });
      const next: Notice[] = [];
      const step = res.step?.id;
      if (res.changed > 0) {
        next.push({
          key: "text", text: `Исправлено: ${target.find} → ${right} (${res.changed}). Итоги не пересчитываются автоматически.`,
          step,
          undo: step ? async () => {
            await undoSpeakers(endpoint, id, step);
            onChanged();
            return "Исправление отменено";
          } : null,
        });
      }
      const term = res.hotword;
      if (term?.error) next.push({ key: "term", text: term.error, undo: null });
      else if (term?.added) {
        const over = term.over_budget ? " Список терминов длиннее лимита — самые ранние не будут учтены." : "";
        next.push({
          key: "term", text: `Добавлено в термины: ${term.term}.${over}`,
          undo: async () => {
            await removeHotword(endpoint, term.term);
            return `Убрано из терминов: ${term.term}`;
          },
        });
      } else if (term) next.push({ key: "term", text: `Уже в терминах: ${term.term}`, undo: null });
      const added = res.rule;
      if (added?.error) next.push({ key: "rule", text: added.error, undo: null });
      else if (added) {
        next.push({
          key: "rule", text: `Исправлять в будущих встречах: ${added.from} → ${added.to}`,
          undo: async () => {
            // Убрать это правило и вернуть то, которое оно заменило (то же «как распознаётся»).
            const asr = ((await getSettings(endpoint)).asr ?? {}) as { replacements?: unknown };
            const left = rulesOf(asr.replacements).filter((r) => !(r.from === added.from && r.to === added.to));
            const back = added.replaced ? withRule(left, added.replaced) : left;
            await patchSettings(endpoint, { asr: { replacements: back } });
            return added.replaced
              ? `Правило возвращено: ${added.replaced.from} → ${added.replaced.to}`
              : `Правило убрано: ${added.from} → ${added.to}`;
          },
        });
      }
      notify(next);
      window.getSelection?.()?.removeAllRanges();
      close();
      if (res.changed > 0) onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const undo = async (n: Notice) => {
    if (!n.undo) return;
    setBusy(true);
    try {
      const text = await n.undo();
      setNotices((cur) => cur.map((x) => (x.key === n.key ? { key: n.key, text, undo: null } : x)));
    } catch (e) {
      setNotices((cur) => cur.map((x) => (x.key === n.key ? { key: n.key, text: errorText(e), undo: null } : x)));
    } finally {
      setBusy(false);
    }
  };

  let node: ReactNode = null;
  if (target) {
    const same = right === target.find;
    const count = preview?.count ?? null;
    const seg = segments[target.seg];
    const here = preview?.here ?? (seg ? { start: seg.start, end: seg.end } : null);
    const canApply = !busy && !target.split && !!right && !(same && !hotword) && !(all && count === 0);
    node = (
      <>
        <button ref={floatRef} type="button" className={`tfix-float${open ? " tfix-float--anchor" : ""}`}
          style={floatingStyle(floatPos)} aria-hidden={open || undefined}
          tabIndex={open ? -1 : undefined} title="Исправить распознанное (Ctrl+E)" aria-keyshortcuts="Control+E"
          onMouseDown={(e) => e.preventDefault()} onClick={() => show(target)}>
          Исправить…
        </button>
        {open && anchor && (
          <Popover anchor={anchor} onClose={close} label="Исправить распознанное" width={POPOVER_W}>
            <form className="tmenu tfix" onSubmit={(e) => { e.preventDefault(); if (canApply) void apply(); }}>
              <div className="tmenu__title">Исправить распознанное</div>
              <div className="tfix__heard">
                <span>Распознано: <q className="tfix__find">{target.find}</q></span>
                {playable && here && (
                  <button type="button" className="spk-link" title="Прослушать это место"
                    aria-label={`Прослушать с ${clock(here.start)}`}
                    onClick={() => onPlay(Math.max(0, here.start - 0.3), here.end + 0.5)}>
                    <Icon as={Play} size="sm" />{clock(here.start)}
                  </button>
                )}
              </div>
              {target.split ? (
                <div className="muted tmenu__hint">
                  Выделение захватывает две фразы расшифровки. Выделите слова внутри одной фразы.
                </div>
              ) : (
                <>
                  <label className="tfix__field">
                    <span>Как правильно</span>
                    <input type="text" className="field field--sm" value={replace} autoFocus maxLength={200}
                      aria-label="Как правильно"
                      onFocus={(e) => e.currentTarget.select()} onChange={(e) => setReplace(e.target.value)} />
                  </label>
                  <label className="tfix__check">
                    <input type="checkbox" className="cb" checked={hotword} disabled={!right}
                      onChange={(e) => setHotword(e.target.checked)} />
                    <span>Добавить в термины распознавания</span>
                    <HelpTip label="Что такое термины распознавания" title="Термины распознавания">
                      <TipLine>Слова и имена, которые подсказываются распознаванию в каждой новой расшифровке: так их
                        реже путают.</TipLine>
                      <TipLine>Список — в разделе «Настройки → Словарь». Готовые расшифровки он не меняет.</TipLine>
                    </HelpTip>
                  </label>
                  {hotword && right && (
                    <div className="muted tmenu__hint tfix__term">Будет добавлено: {terms || right}</div>
                  )}
                  <label className="tfix__check">
                    <input type="checkbox" className="cb" checked={all} disabled={count === null || count < 2}
                      onChange={(e) => setAll(e.target.checked)} />
                    <span>
                      Заменить во всей встрече
                      {count === null ? " (подсчёт…)" : ` (${count} ${matchesWord(count)})`}
                    </span>
                  </label>
                  {all && preview && preview.samples.length > 0 && (
                    <ul className="tfix__samples" aria-label="Совпадения во встрече">
                      {preview.samples.map((s) => (
                        <li key={`${s.segment}:${s.offset}`}>
                          <span className="muted num">{clock(s.start)}</span>{" "}
                          {s.before}<mark className="hit">{s.match}</mark>{s.after}
                        </li>
                      ))}
                      {preview.count > preview.samples.length && (
                        <li className="muted">и ещё {preview.count - preview.samples.length}</li>
                      )}
                    </ul>
                  )}
                  {!all && <div className="muted tmenu__hint">Будет исправлено только это место.</div>}
                  <label className="tfix__check">
                    <input type="checkbox" className="cb" checked={rule} disabled={same}
                      onChange={(e) => setRule(e.target.checked)} />
                    <span>Исправлять так же в будущих встречах</span>
                    <HelpTip label="Как работает исправление в будущих встречах" title="Исправлять в будущих встречах">
                      <TipLine>Каждая новая расшифровка сразу после распознавания заменит «{target.find}» на
                        «{right || "…"}»: целые слова, без учёта регистра, «ё» и «е» не различаются.</TipLine>
                      <TipLine>Готовые встречи правило не меняет. Список правил — «Настройки → Словарь»,
                        там их можно удалить.</TipLine>
                    </HelpTip>
                  </label>
                </>
              )}
              {error && <div className="card__error tmenu__error" role="alert">{error}</div>}
              <div className="tfix__actions">
                <Button onClick={close}>Отмена</Button>
                <Button variant="primary" type="submit" disabled={!canApply}>Применить</Button>
              </div>
            </form>
          </Popover>
        )}
      </>
    );
  }

  const bar = notices.length ? (
    <div className="tfix-bar">
      {notices.map((n) => (
        <div className="tsel" role="status" aria-live="polite" key={n.key}>
          <span>{n.text}</span>
          {n.undo && (!n.step || n.step === head) && (
            <button type="button" className="spk-link" disabled={busy} onClick={() => void undo(n)}>Отменить</button>
          )}
          <button type="button" className="spk-link tsel__close" aria-label="Скрыть"
            onClick={() => setNotices((cur) => cur.filter((x) => x.key !== n.key))}><Icon as={X} size="sm" /></button>
        </div>
      ))}
    </div>
  ) : null;

  if (hint) {
    node = (
      <>
        {node}
        <Popover anchor={hint} onClose={() => setHint(null)} label="Как исправить распознанное" width={POPOVER_W}
          anchorToggles>
          <p className="tfix__hint">
            Выделите в реплике неверно распознанное слово или фразу (слово — двойным щелчком) и нажмите
            «Исправить…» или Ctrl+E.
          </p>
        </Popover>
      </>
    );
  }

  return { onContextMenu, openWord, openFromBar, node, bar };
}
