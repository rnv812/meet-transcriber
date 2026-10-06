/**
 * Перетаскивание встреч в группы и групп в списке — только на pointer-событиях.
 *
 * HTML5 drag-and-drop в окне не годится: на Windows его перехватывает
 * нативный file-drop Tauri, а импорт файлов перетаскиванием из Проводника
 * (ImportZone) должен работать. Здесь нет ни `draggable`, ни `dragstart`.
 *
 * Как это устроено:
 * - нажатие на строку только запоминает точку; перетаскивание начинается,
 *   когда указатель ушёл дальше порога (DRAG_THRESHOLD) — щелчки, выбор и
 *   клавиатура строк не меняются;
 * - цель — элемент под указателем: группа в левой панели (`data-drop-group`,
 *   «Без группы» — NO_GROUP) для встреч или строка группы (`data-group-row`)
 *   для порядка групп — верхняя или нижняя половина строки задаёт линию вставки;
 * - отпустили на цели — `onDrop`; вне цели, Esc, потеря указателя — отмена;
 * - у краёв прокручиваемых областей (список, панель групп) — автопрокрутка;
 * - щелчок, который браузер пришлёт после отпускания, гасится: перетаскивание
 *   не открывает встречу и не меняет область.
 *
 * Логика — `DragSession` и чистые функции (их проверяют тесты без DOM);
 * `createGroupDrag` вешает их на документ и раздаёт состояние подписчикам
 * (тень и подсветка цели перерисовываются без перерисовки всего окна).
 */

import { meetingsText, NO_GROUP } from "../../lib/groups";

/** Сколько пикселей пройти с нажатой кнопкой, чтобы началось перетаскивание. */
export const DRAG_THRESHOLD = 5;
/** Полоса у края области, где идёт автопрокрутка, px. */
export const EDGE_ZONE = 36;
/** Наибольший шаг автопрокрутки за кадр, px. */
export const EDGE_SPEED = 14;
/** Где искать прокручиваемые области под указателем. */
export const SCROLL_AREAS = ".pane-list, [data-drag-scroll]";

/** Встречи (одна или все выбранные) и их группы до переноса — для «Отменить». */
export type MeetingsPayload = { kind: "meetings"; ids: string[]; prev: Record<string, string | null>; label: string };
/** Группа в левой панели — меняется её место в списке. */
export type GroupPayload = { kind: "group"; id: string; label: string };
export type DragPayload = MeetingsPayload | GroupPayload;

/** Куда отпустить: на группу (`group: null` — «Без группы») или место в списке групп (перед `index`). */
export type DropTarget = { kind: "group"; group: string | null } | { kind: "slot"; index: number };

export type Point = { x: number; y: number };
export type DragView = { payload: DragPayload; x: number; y: number; target: DropTarget | null };

/** Указатель ушёл от точки нажатия на порог и дальше. */
export const passedThreshold = (from: Point, to: Point, threshold = DRAG_THRESHOLD) =>
  Math.hypot(to.x - from.x, to.y - from.y) >= threshold;

/** Подпись тени: несколько встреч — «3 встречи», одна — её название, группа — её имя. */
export const ghostText = (p: DragPayload) => (p.kind === "meetings" && p.ids.length > 1 ? meetingsText(p.ids.length) : p.label);

/**
 * Цель под указателем: `el` — элемент в точке (elementFromPoint), `y` — для
 * половины строки, `order` — нынешний порядок групп. Отпускание ничего бы не
 * поменяло (все встречи уже в этой группе, группа на своём месте) — не цель.
 */
export function targetAt(el: Element | null, payload: DragPayload, y: number, order: string[]): DropTarget | null {
  if (!el) return null;
  if (payload.kind === "meetings") {
    const box = el.closest<HTMLElement>("[data-drop-group]");
    const value = box?.dataset.dropGroup;
    if (!value) return null;
    const group = value === NO_GROUP ? null : value;
    if (payload.ids.every((id) => (payload.prev[id] ?? null) === group)) return null;
    return { kind: "group", group };
  }
  const row = el.closest<HTMLElement>("[data-group-row]");
  const id = row?.dataset.groupRow;
  if (!row || !id) return null;
  const at = order.indexOf(id);
  const from = order.indexOf(payload.id);
  if (at < 0 || from < 0) return null;
  const r = row.getBoundingClientRect();
  const index = y < r.top + r.height / 2 ? at : at + 1;
  if (index === from || index === from + 1) return null;
  return { kind: "slot", index };
}

/**
 * Шаг автопрокрутки области `rect` при указателе на высоте `y`: у верхнего
 * края — вверх (отрицательный), у нижнего — вниз, чем ближе к краю (и за ним),
 * тем быстрее; в середине — 0.
 */
export function edgeDelta(rect: { top: number; bottom: number }, y: number, zone = EDGE_ZONE, speed = EDGE_SPEED): number {
  const height = rect.bottom - rect.top;
  const band = Math.min(zone, height / 3);
  if (band <= 0) return 0;
  const k = (d: number) => Math.ceil(speed * Math.min(1, Math.max(0, (band - d) / band)));
  if (y < rect.top + band) return -k(y - rect.top);
  if (y > rect.bottom - band) return k(rect.bottom - y);
  return 0;
}

export type SessionHooks = {
  /** Что тащим — спрашивается, только когда порог пройден; null — нечего тащить. */
  payload: () => DragPayload | null;
  /** Цель в точке. */
  resolve: (x: number, y: number, payload: DragPayload) => DropTarget | null;
  onMove: (view: DragView) => void;
  onDrop: (payload: DragPayload, target: DropTarget) => void;
  /** Перетаскивание кончилось (отпустили или отменили) — только если оно началось. */
  onEnd: () => void;
};

/** Одно нажатие: до порога — ничего, дальше — перетаскивание до отпускания или отмены. */
export class DragSession {
  active = false;
  done = false;
  payload: DragPayload | null = null;
  target: DropTarget | null = null;
  private last: Point;

  constructor(private readonly start: Point, private readonly hooks: SessionHooks,
    private readonly threshold = DRAG_THRESHOLD) {
    this.last = start;
  }

  /** Указатель сдвинулся. */
  move(x: number, y: number): void {
    if (this.done) return;
    this.last = { x, y };
    if (!this.active) {
      if (!passedThreshold(this.start, this.last, this.threshold)) return;
      const payload = this.hooks.payload();
      if (!payload) { this.finish(); return; }
      this.active = true;
      this.payload = payload;
    }
    this.update();
  }

  /** Цель могла смениться без движения указателя (автопрокрутка). */
  refresh(): void {
    if (this.active && !this.done) this.update();
  }

  /** Отпустили: на цели — перенос. true — это было перетаскивание (щелчок после него гасится). */
  up(x: number, y: number): boolean {
    if (this.done) return false;
    const was = this.active;
    if (was && this.payload) {
      const target = this.hooks.resolve(x, y, this.payload);
      this.finish();
      if (target) this.hooks.onDrop(this.payload, target);
      return true;
    }
    this.finish();
    return false;
  }

  /** Esc, потеря указателя, окно ушло из фокуса. true — шло перетаскивание. */
  cancel(): boolean {
    if (this.done) return false;
    const was = this.active;
    this.finish();
    return was;
  }

  get point(): Point { return this.last; }

  private update() {
    const payload = this.payload!;
    this.target = this.hooks.resolve(this.last.x, this.last.y, payload);
    this.hooks.onMove({ payload, x: this.last.x, y: this.last.y, target: this.target });
  }

  private finish() {
    const was = this.active;
    this.done = true;
    this.active = false;
    if (was) this.hooks.onEnd();
  }
}

/** Нажатие, с которого может начаться перетаскивание (React или DOM-событие). */
export type PointerStart = Pick<PointerEvent, "button" | "pointerType" | "pointerId" | "clientX" | "clientY"
  | "ctrlKey" | "metaKey" | "shiftKey" | "altKey" | "target">;

export type GroupDrag = {
  /** Нажатие на источник; `payload` спросится, когда пройден порог. */
  begin: (e: PointerStart, payload: () => DragPayload | null) => void;
  /** Прервать идущее перетаскивание. */
  cancel: () => void;
  subscribe: (fn: () => void) => () => void;
  /** Что сейчас тащат, где указатель и цель; null — не тащат. */
  view: () => DragView | null;
};

export type GroupDragOptions = {
  onDrop: (payload: DragPayload, target: DropTarget) => void;
  /** Нынешний порядок групп (для линии вставки). */
  order: () => string[];
  /** Элемент в точке; по умолчанию document.elementFromPoint (в тестах — подмена). */
  elementAt?: (x: number, y: number) => Element | null;
  threshold?: number;
};

/** Класс на <html> во время перетаскивания: без выделения текста, курсор «тащу». */
export const DRAGGING_CLASS = "group-dragging";

/**
 * Перетаскивание на документе: одно на окно. Состояние — вне React
 * (`subscribe`/`view`): тень и подсветка подписываются сами, остальное окно
 * на каждое движение указателя не перерисовывается.
 */
export function createGroupDrag(opts: GroupDragOptions): GroupDrag {
  let view: DragView | null = null;
  const subs = new Set<() => void>();
  const emit = (next: DragView | null) => {
    view = next;
    for (const fn of [...subs]) fn();
  };
  let stop: (() => void) | null = null;

  const elementAt = (x: number, y: number) => {
    if (opts.elementAt) return opts.elementAt(x, y);
    return typeof document.elementFromPoint === "function" ? document.elementFromPoint(x, y) : null;
  };

  const begin: GroupDrag["begin"] = (e, payload) => {
    // Только основная кнопка мыши: палец прокручивает, перо в Chromium на Windows без touch-action
    // уходит в прокрутку (pointercancel). Ctrl/Shift+щелчок — выбор записей.
    if (e.button !== 0 || e.pointerType !== "mouse" || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    stop?.();
    const source = e.target instanceof Element ? e.target : null;
    let frame = 0;
    let captured = false;

    const session = new DragSession({ x: e.clientX, y: e.clientY }, {
      payload,
      resolve: (x, y, p) => targetAt(elementAt(x, y), p, y, opts.order()),
      onMove: (next) => {
        if (!view) {
          document.documentElement.classList.add(DRAGGING_CLASS);
          // Указатель — за источником, даже если ушёл за край окна: отпускание не потеряется.
          try { (source as HTMLElement | null)?.setPointerCapture?.(e.pointerId); captured = true; } catch { /* нет такого указателя */ }
        }
        emit(next);
        if (!frame) frame = requestAnimationFrame(scroll);
      },
      onDrop: opts.onDrop,
      onEnd: () => {
        document.documentElement.classList.remove(DRAGGING_CLASS);
        emit(null);
      },
    }, opts.threshold);

    // Автопрокрутка: пока указатель у края области — кадр за кадром.
    function scroll() {
      frame = 0;
      if (!session.active) return;
      const { x, y } = session.point;
      let moved = false;
      for (const el of document.querySelectorAll<HTMLElement>(SCROLL_AREAS)) {
        const r = el.getBoundingClientRect();
        if (x < r.left || x > r.right) continue;
        const d = edgeDelta(r, y);
        if (!d) continue;
        const before = el.scrollTop;
        el.scrollTop = before + d;
        if (el.scrollTop !== before) moved = true;
      }
      if (moved) {
        session.refresh();
        frame = requestAnimationFrame(scroll);
      }
    }

    const onMove = (ev: PointerEvent) => {
      if (ev.pointerId !== e.pointerId) return;
      // Кнопку отпустили, а pointerup потерялся (источник исчез, указатель был за окном):
      // не тащить тень дальше и не уронить на следующий обычный щелчок.
      if (ev.buttons === 0) { onCancel(); return; }
      session.move(ev.clientX, ev.clientY);
      if (session.active) ev.preventDefault();
    };
    /** Перетаскивание было (и, может быть, отменено Esc): щелчок после отпускания гасится. */
    let dragged = false;
    const onUp = (ev: PointerEvent) => {
      if (ev.pointerId !== e.pointerId) return;
      if (session.up(ev.clientX, ev.clientY)) dragged = true;
      cleanup();
      if (dragged) swallowClick();
    };
    const onCancel = () => {
      session.cancel();
      cleanup();
    };
    const onPointerCancel = (ev: PointerEvent) => { if (ev.pointerId === e.pointerId) onCancel(); };
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape" || !session.active) return;
      // Esc отменяет только перетаскивание: выбор записей и меню остаются. Кнопку ещё держат —
      // ждём отпускания, чтобы погасить щелчок за ним.
      ev.preventDefault();
      ev.stopPropagation();
      if (session.cancel()) dragged = true;
    };
    const onLost = () => { if (captured) onCancel(); };

    function cleanup() {
      window.removeEventListener("pointermove", onMove, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("pointercancel", onPointerCancel, true);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("blur", onCancel);
      source?.removeEventListener("lostpointercapture", onLost);
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      if (captured) {
        try { (source as HTMLElement | null)?.releasePointerCapture?.(e.pointerId); } catch { /* уже отпущен */ }
        captured = false;
      }
      if (stop === halt) stop = null;
    }
    function halt() { onCancel(); }

    window.addEventListener("pointermove", onMove, true);
    window.addEventListener("pointerup", onUp, true);
    window.addEventListener("pointercancel", onPointerCancel, true);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("blur", onCancel);
    source?.addEventListener("lostpointercapture", onLost);
    stop = halt;
  };

  return {
    begin,
    cancel: () => stop?.(),
    subscribe: (fn) => { subs.add(fn); return () => { subs.delete(fn); }; },
    view: () => view,
  };
}

/**
 * Щелчок, который браузер пришлёт сразу после отпускания (на источник —
 * указатель был захвачен — или на общего предка), не должен открыть встречу
 * или сменить область.
 */
function swallowClick() {
  const stop = (ev: Event) => { ev.preventDefault(); ev.stopPropagation(); };
  window.addEventListener("click", stop, { capture: true, once: true });
  setTimeout(() => window.removeEventListener("click", stop, { capture: true }), 0);
}
