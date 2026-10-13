/**
 * Карта разметки (0.6.1): узкая полоса справа от ленты «Расшифровки» — вся встреча по
 * времени, сверху вниз. Отметки — реплики с типом (вопрос, решение…) и наблюдения анализа
 * на своих местах; цвет акцента — у наблюдений и типов выбранного фильтра, остальные
 * нейтральные. Рамка — что сейчас видно в ленте. Щелчок по отметке — переход к реплике,
 * по полосе мимо отметок (и протяжка) — прокрутка к этому месту встречи.
 */

import { useCallback, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { TYPE_LABEL, turnAt, type InsightView } from "../../lib/analysisView";
import { clock } from "../../lib/format";
import { scrollParent } from "../../lib/keepPlace";
import type { Turn } from "../../lib/speakers";
import type { PhraseType } from "../../lib/types";
import "./markupMap.css";

type Props = {
  turns: Turn[];
  /** Тип каждой реплики (анализ встречи); null — без типа. */
  types: (PhraseType | null)[] | null;
  insights: InsightView[];
  /** Выбранные в легенде типы — их отметки цвета акцента. */
  filter: ReadonlySet<PhraseType>;
  insightsOn: boolean;
  /** Перейти к реплике: показать, прокрутить, подсветить. */
  onJump: (turn: number) => void;
  /** Прокрутить ленту к реплике в этом месте встречи — без подсветки (щелчок мимо отметок, протяжка). */
  onScrollTo: (turn: number) => void;
};

type Mark = { key: string; turn: number; at: number; label: string; insight: boolean; on: boolean; type?: PhraseType };

/** Подпись отметки — не длиннее этого (дальше многоточие). */
const LABEL_CHARS = 80;
const short = (s: string) => (s.length > LABEL_CHARS ? `${s.slice(0, LABEL_CHARS - 1).trimEnd()}…` : s);
/** Полоса не выше видимой части прокрутки минус этот запас. */
const MAP_GAP_PX = 16;

export function MarkupMap({ turns, types, insights, filter, insightsOn, onJump, onScrollTo }: Props) {
  const total = useMemo(() => turns.reduce((m, t) => Math.max(m, t.end), 0), [turns]);
  const marks = useMemo<Mark[]>(() => {
    if (total <= 0) return [];
    const out: Mark[] = [];
    for (const x of insights) {
      const i = x.refs[0];
      const t = i === undefined ? undefined : turns[i];
      if (!t) continue;
      out.push({ key: `i:${x.id}`, turn: i!, at: t.start, insight: true, on: true,
        label: `Наблюдение, ${clock(t.start)}: ${short(x.text)}` });
    }
    types?.forEach((type, i) => {
      const t = turns[i];
      if (!type || type === "statement" || !t || t.kind === "break") return;
      out.push({ key: `t:${i}`, turn: i, at: t.start, insight: false, on: filter.has(type), type,
        label: `${TYPE_LABEL[type]}, ${clock(t.start)}: ${short(t.texts.join(" "))}` });
    });
    // По времени; в одном месте — сначала наблюдение (оно о реплике).
    return out.sort((a, b) => a.at - b.at || Number(b.insight) - Number(a.insight) || a.turn - b.turn);
  }, [turns, types, insights, filter, total]);

  const strip = useRef<HTMLDivElement>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const [active, setActive] = useState(0);
  const [hint, setHint] = useState<number | null>(null);
  const [frame, setFrame] = useState<{ top: number; height: number }>({ top: 0, height: 0 });
  const [height, setHeight] = useState<number | null>(null);
  const remeasure = useRef<() => void>(() => {});

  // Рамка видимого: первая и последняя реплики в окне прокрутки → доли встречи.
  useLayoutEffect(() => {
    const el = strip.current;
    const root = el?.closest(".transcript");
    if (!el || !root || total <= 0) return;
    const scroller = scrollParent(root);
    if (!scroller) return;
    const target: HTMLElement | Window = scroller === document.scrollingElement ? window : scroller;
    let raf = 0;
    const measure = () => {
      raf = 0;
      const view = scroller === document.scrollingElement
        ? { top: 0, bottom: window.innerHeight }
        : scroller.getBoundingClientRect();
      const h = Math.round(Math.max(120, Math.min(view.bottom - view.top, window.innerHeight) - MAP_GAP_PX));
      setHeight((old) => (old === h ? old : h));
      const rows = root.querySelectorAll<HTMLElement>("[data-turn]");
      if (!rows.length) return;
      // Реплики идут сверху вниз — двоичный поиск первой, чей низ ниже кромки.
      const first = (edge: number) => {
        let lo = 0;
        let hi = rows.length - 1;
        let found = rows.length - 1;
        while (lo <= hi) {
          const mid = (lo + hi) >> 1;
          if (rows[mid]!.getBoundingClientRect().bottom > edge) { found = mid; hi = mid - 1; } else lo = mid + 1;
        }
        return rows[found]!;
      };
      const a = turns[Number(first(view.top).dataset.turn)];
      const b = turns[Number(first(view.bottom).dataset.turn)];
      if (!a || !b) return;
      const top = a.start / total;
      const next = { top, height: Math.max(0, Math.min(1, b.end / total) - top) };
      // Без изменений — без перерисовки: замер идёт после каждой отрисовки.
      setFrame((old) => (old.top === next.top && old.height === next.height ? old : next));
    };
    const ask = () => { if (!raf) raf = requestAnimationFrame(measure); };
    remeasure.current = ask;
    measure();
    target.addEventListener("scroll", ask, { passive: true });
    window.addEventListener("resize", ask);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      remeasure.current = () => {};
      target.removeEventListener("scroll", ask);
      window.removeEventListener("resize", ask);
    };
  }, [turns, total]);
  // Лента перестроилась (фильтр, развёрнутые реплики, заметки) — рамку пересчитать в следующем кадре.
  useLayoutEffect(() => { remeasure.current(); });

  const pick = (i: number) => {
    const m = marks[i];
    if (!m) return;
    setActive(i);
    onJump(m.turn);
  };

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    let next: number | null = null;
    if (e.key === "ArrowDown" || e.key === "ArrowRight") next = Math.min(marks.length - 1, i + 1);
    else if (e.key === "ArrowUp" || e.key === "ArrowLeft") next = Math.max(0, i - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = marks.length - 1;
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(i);
      return;
    }
    if (next === null) return;
    e.preventDefault();
    setActive(next);
    buttons.current[next]?.focus();
  };

  // Мимо отметок: прокрутка к месту встречи под указателем; с зажатой кнопкой — протяжка.
  const dragging = useRef(false);
  const scrollAt = useCallback((clientY: number) => {
    const r = strip.current?.getBoundingClientRect();
    if (!r || r.height <= 0) return;
    const at = Math.min(1, Math.max(0, (clientY - r.top) / r.height)) * total;
    const i = turnAt(turns, at);
    if (i >= 0) onScrollTo(i);
  }, [turns, total, onScrollTo]);
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as Element).closest(".markup-map__mark")) return;
    e.preventDefault();
    dragging.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    scrollAt(e.clientY);
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => { if (dragging.current) scrollAt(e.clientY); };
  const onUp = () => { dragging.current = false; };

  if (!marks.length) return null;
  const current = Math.min(active, marks.length - 1);
  const pct = (x: number) => `${Math.round(x * 10000) / 100}%`;
  return (
    <div ref={strip} className={`markup-map${insightsOn || filter.size ? " markup-map--filtered" : ""}`}
      role="group" aria-label="Карта разметки" style={height ? { height } : undefined}
      onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
      <span className="markup-map__frame" aria-hidden="true" style={{ top: pct(frame.top), height: pct(frame.height) }} />
      {marks.map((m, i) => (
        <button key={m.key} ref={(b) => { buttons.current[i] = b; }} type="button"
          className={`markup-map__mark${m.insight ? " markup-map__mark--wide" : ""}${m.on ? " markup-map__mark--on" : ""}`}
          style={{ top: pct(m.at / total) }} aria-label={m.label} tabIndex={i === current ? 0 : -1}
          onClick={() => pick(i)} onKeyDown={(e) => onKey(e, i)}
          onPointerEnter={() => setHint(i)} onPointerLeave={() => setHint((h) => (h === i ? null : h))}
          onFocus={() => setHint(i)} onBlur={() => setHint((h) => (h === i ? null : h))} />
      ))}
      {hint !== null && marks[hint] && (
        <span className="markup-map__hint" aria-hidden="true" style={{ top: pct(marks[hint].at / total) }}>
          {marks[hint].label}
        </span>
      )}
    </div>
  );
}
