/**
 * Подсказка Atlas Aurora (`.tooltip`) вместо системного `title`.
 *
 * Как пользоваться:
 *
 *   <Tip content="Открыть папку записи"><button …>…</button></Tip>
 *   <Tip content="Дождитесь окончания установки" side="right"><Button disabled>Пропустить</Button></Tip>
 *   <Tip content={label} describe={false}>…</Tip>   // подсказка повторяет имя элемента
 *
 * Ребёнок — один элемент с ref (DOM-элемент или компонент, передающий ref дальше:
 * Button, IconButton). Обёртки нет: раскладка и селекторы вокруг не меняются.
 * Показ — при наведении через TIP_DELAY_MS (400 мс), при фокусе с клавиатуры —
 * сразу; уход мыши, уход фокуса, нажатие и Esc прячут (Esc дальше идёт как
 * обычно: окно вокруг закроется тем же нажатием). После нажатия подсказка не
 * возвращается, пока мышь не уйдёт с элемента. Недоступной (disabled) кнопке
 * подсказка тоже показывается — так объясняется причина.
 *
 * Облачко выносится порталом в body (`position: fixed`, ui/floating
 * `placeTooltip`): не обрезается прокруткой и стеклом. Сторона — `top` (по
 * умолчанию; нет места — снизу) или `right` (рейка, узкие колонки; нет места —
 * слева).
 *
 * Диктору: текст подсказки — описание элемента (`aria-description`, для
 * разметки — скрытый узел и `aria-describedby`). Если подсказка лишь повторяет
 * доступное имя, передайте `describe={false}`. Само облачко — aria-hidden.
 *
 * Свой элемент без обёртки (IconButton): хук `useTip` — `ref`, `props` на
 * элемент и `node` рядом с ним.
 */

import {
  cloneElement, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
  type ReactElement, type ReactNode, type Ref, type RefCallback,
} from "react";
import { createPortal } from "react-dom";
import { placeTooltip, type TipSide } from "./floating";
import "./tip.css";

/** Задержка показа при наведении, мс. */
export const TIP_DELAY_MS = 400;

export type TipOptions = {
  /** Сторона облачка: сверху (по умолчанию) или справа; нет места — напротив. */
  side?: TipSide;
  /** Задержка показа при наведении, мс. */
  delay?: number;
  /** Текст подсказки — описание элемента для диктора (по умолчанию да). */
  describe?: boolean;
};

const isEmpty = (content: ReactNode) => content === undefined || content === null || content === false || content === "";

/**
 * Чем человек пользовался последним — клавиатурой или указателем (то же правило,
 * что у `:focus-visible`, но одинаковое в браузере и в тестах): фокус после
 * нажатия мышью подсказку не показывает, фокус с клавиатуры — сразу.
 */
let keyboardLast = false;
let tracking = false;
function trackModality() {
  if (tracking || typeof document === "undefined") return;
  tracking = true;
  document.addEventListener("keydown", (e) => { if (!e.metaKey && !e.altKey && !e.ctrlKey) keyboardLast = true; }, true);
  document.addEventListener("pointerdown", () => { keyboardLast = false; }, true);
  document.addEventListener("mousedown", () => { keyboardLast = false; }, true);
}

/** Ссылка на один элемент из нескольких ref (свой ref ребёнка + ref подсказки). */
export function useMergedRef<T>(...refs: (Ref<T> | undefined)[]): RefCallback<T> {
  // Зависимости — сами ref: пока они те же, ссылка та же (иначе React
  // переставлял бы ref на каждом рендере).
  return useMemo(() => (node: T | null) => {
    for (const r of refs) {
      if (typeof r === "function") r(node);
      else if (r) (r as { current: T | null }).current = node;
    }
  }, refs);
}

/** Облачко у якоря: замер, место, пересчёт при прокрутке и изменении окна. */
function Bubble({ anchor, side, children }: { anchor: HTMLElement; side: TipSide; children: ReactNode }) {
  const box = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; side: TipSide } | null>(null);

  const place = useCallback(() => {
    const el = box.current;
    if (!el || !anchor.isConnected) return;
    const r = el.getBoundingClientRect();
    const next = placeTooltip(anchor.getBoundingClientRect(), { width: r.width, height: r.height },
      { width: window.innerWidth, height: window.innerHeight }, side);
    setPos((p) => (p && p.left === next.left && p.top === next.top && p.side === next.side ? p : next));
  }, [anchor, side]);

  useLayoutEffect(() => {
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [place]);

  return (
    <span ref={box} className="tooltip tip is-open" data-side={pos?.side ?? side} aria-hidden="true"
      style={pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, opacity: 0 }}>
      {children}
    </span>
  );
}

export type TipProps = {
  "aria-description"?: string;
  "aria-describedby"?: string;
};

/**
 * Подсказка для своего элемента: `ref` — на элемент, `props` — на него же
 * (описание для диктора), `node` — отрисовать рядом (портал, вне раскладки).
 */
export function useTip<E extends HTMLElement = HTMLElement>(content: ReactNode,
  { side = "top", delay = TIP_DELAY_MS, describe = true }: TipOptions = {}) {
  const [el, setEl] = useState<E | null>(null);
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  /** Было нажатие — не показывать снова, пока мышь не уйдёт (или фокус). */
  const pressed = useRef(false);
  const descId = useId();
  const empty = isEmpty(content);
  const ref = useCallback((node: E | null) => setEl(node), []);

  useEffect(() => {
    if (!el || empty) return;
    trackModality();
    const enter = () => {
      if (pressed.current) return;
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setHover(true), delay);
    };
    const leave = () => {
      clearTimeout(timer.current);
      pressed.current = false;
      setHover(false);
    };
    const press = () => {
      clearTimeout(timer.current);
      pressed.current = true;
      setHover(false);
      setFocus(false);
    };
    const onFocus = () => { if (!pressed.current && keyboardLast) setFocus(true); };
    const onBlur = () => { pressed.current = false; setFocus(false); };
    // Родные события, не React: React не отдаёт onMouseEnter недоступной кнопке,
    // а подсказка с причиной нужна именно ей.
    el.addEventListener("mouseenter", enter);
    el.addEventListener("pointerenter", enter);
    el.addEventListener("mouseleave", leave);
    el.addEventListener("pointerleave", leave);
    el.addEventListener("pointerdown", press);
    el.addEventListener("mousedown", press);
    el.addEventListener("focus", onFocus);
    el.addEventListener("blur", onBlur);
    return () => {
      clearTimeout(timer.current);
      el.removeEventListener("mouseenter", enter);
      el.removeEventListener("pointerenter", enter);
      el.removeEventListener("mouseleave", leave);
      el.removeEventListener("pointerleave", leave);
      el.removeEventListener("pointerdown", press);
      el.removeEventListener("mousedown", press);
      el.removeEventListener("focus", onFocus);
      el.removeEventListener("blur", onBlur);
      setHover(false);
      setFocus(false);
    };
  }, [el, empty, delay]);

  const open = !empty && !!el && (hover || focus);

  // Esc прячет подсказку; клавиша идёт дальше — окно вокруг закрывается тем же нажатием.
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      clearTimeout(timer.current);
      setHover(false);
      setFocus(false);
    };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, [open]);

  const text = typeof content === "string" || typeof content === "number" ? String(content) : null;
  const props: TipProps = {};
  if (describe && !empty) {
    if (text !== null) props["aria-description"] = text;
    else props["aria-describedby"] = descId;
  }
  const node = (
    <>
      {describe && !empty && text === null && createPortal(<span hidden id={descId}>{content}</span>, document.body)}
      {open && el && createPortal(<Bubble anchor={el} side={side}>{content}</Bubble>, document.body)}
    </>
  );
  return { ref, props, node, open };
}

/** Подсказка у одного элемента-ребёнка (см. описание файла). */
export function Tip({ content, side, delay, describe, children }: TipOptions & {
  /** Текст подсказки; пусто — ребёнок как есть. */
  content: ReactNode;
  children: ReactElement<{ ref?: Ref<HTMLElement>; "aria-describedby"?: string }>;
}) {
  const tip = useTip<HTMLElement>(content, { side, delay, describe });
  const ref = useMergedRef(children.props.ref, tip.ref);
  if (isEmpty(content)) return children;
  const own = children.props["aria-describedby"];
  const describedBy = [own, tip.props["aria-describedby"]].filter(Boolean).join(" ") || undefined;
  return (
    <>
      {cloneElement(children, { ...tip.props, "aria-describedby": describedBy, ref })}
      {tip.node}
    </>
  );
}
