/**
 * «?» с пояснением: открывается по наведению, фокусу или нажатию; Esc и клик
 * снаружи закрывают. Пояснение связано с кнопкой через aria-describedby.
 *
 * Положение — fixed, по координатам кнопки: подсказка не обрезается краем
 * прокручиваемой области и не уходит за край окна (раскрывается туда, где
 * есть место, как Popover).
 */

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import "./helptip.css";

const WIDTH = 300;
const GAP = 6;
const MARGIN = 8;
/** Пауза перед закрытием по уходу мыши: успеть перевести курсор на текст подсказки. */
export const CLOSE_DELAY_MS = 150;

type Box = { left: number; top: number; right: number; bottom: number };

/** Где показать подсказку у кнопки: под ней, а если снизу тесно — над ней; по горизонтали — в пределах окна. */
export function placeTip(anchor: Box, tip: { width: number; height: number }, view: { width: number; height: number }) {
  // Левым краем к кнопке (раскрытие вправо, «?» обычно сразу после подписи); у правого края окна — сдвиг влево.
  const left = Math.max(MARGIN, Math.min(anchor.left, view.width - tip.width - MARGIN));
  const below = anchor.bottom + GAP;
  const above = anchor.top - GAP - tip.height;
  const top = below + tip.height <= view.height - MARGIN || above < MARGIN ? below : above;
  return { left, top };
}

export function HelpTip({ label, title, children }: {
  /** Доступное имя кнопки «?»: о чём пояснение. */
  label: string;
  /** Заголовок внутри подсказки. */
  title?: string;
  children: ReactNode;
}) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const id = useId();
  const shown = hover || focus || pinned;

  const close = useCallback(() => { setHover(false); setFocus(false); setPinned(false); }, []);
  useEffect(() => () => clearTimeout(timer.current), []);

  const place = useCallback(() => {
    const a = button.current?.getBoundingClientRect();
    const t = tip.current?.getBoundingClientRect();
    if (!a || !t) return;
    setPos(placeTip(a, { width: t.width || WIDTH, height: t.height }, { width: window.innerWidth, height: window.innerHeight }));
  }, []);

  useLayoutEffect(() => {
    if (!shown) { setPos(null); return; }
    place();
  }, [shown, place]);

  // Открытая подсказка: Esc закрывает её откуда угодно — и только её (не
  // всплывающее окно или диалог вокруг: перехват на window, дальше событие не
  // идёт); клик снаружи — тоже. Прокрутка контейнера, в котором стоит «?»,
  // уводит кнопку из-под подсказки — закрываем; прокрутка соседнего списка или
  // самой подсказки её не касается. Окно изменило размер — пересчитать место.
  useEffect(() => {
    if (!shown) return;
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      close();
    };
    const down = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) close(); };
    const scroll = (e: Event) => {
      const target = e.target;
      if (target instanceof Node && root.current && target.contains(root.current)) close();
    };
    window.addEventListener("keydown", key, true);
    document.addEventListener("mousedown", down);
    window.addEventListener("scroll", scroll, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("keydown", key, true);
      document.removeEventListener("mousedown", down);
      window.removeEventListener("scroll", scroll, true);
      window.removeEventListener("resize", place);
    };
  }, [shown, close, place]);

  return (
    <span ref={root} className="help"
      onMouseEnter={() => { clearTimeout(timer.current); setHover(true); }}
      onMouseLeave={() => { clearTimeout(timer.current); timer.current = setTimeout(() => setHover(false), CLOSE_DELAY_MS); }}>
      <button ref={button} type="button" className="help__button" aria-label={label}
        aria-expanded={shown} aria-describedby={shown ? id : undefined}
        onClick={() => setPinned((v) => !v)}
        onFocus={() => setFocus(true)}
        onBlur={(e) => { if (!root.current?.contains(e.relatedTarget as Node | null)) { setFocus(false); setPinned(false); } }}>
        ?
      </button>
      {shown && (
        <span ref={tip} role="tooltip" id={id} className="help__tip"
          style={pos ? { left: pos.left, top: pos.top } : { visibility: "hidden", left: 0, top: 0 }}>
          {title && <span className="help__title">{title}</span>}
          {children}
        </span>
      )}
    </span>
  );
}

/** Абзац внутри подсказки. */
export function TipLine({ children }: { children: ReactNode }) {
  return <span className="help__line">{children}</span>;
}
