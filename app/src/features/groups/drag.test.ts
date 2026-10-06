import { fireEvent } from "@testing-library/react";
import { NO_GROUP } from "../../lib/groups";
import {
  createGroupDrag, DRAG_THRESHOLD, DRAGGING_CLASS, DragSession, edgeDelta, ghostText, passedThreshold, targetAt,
  type DragPayload, type DropTarget, type MeetingsPayload, type SessionHooks,
} from "./drag";

const meetings = (ids: string[], prev: Record<string, string | null> = {}): MeetingsPayload =>
  ({ kind: "meetings", ids, prev, label: "Планёрка" });

/** Левая панель: строки групп (по 26 px от y=100) и «Без группы». */
function panel() {
  document.body.innerHTML = `
    <ul>
      <li data-group-row="g-a" data-drop-group="g-a"><button><span class="name">Альфа</span></button></li>
      <li data-group-row="g-b" data-drop-group="g-b"><button>Бета</button></li>
      <li data-group-row="g-c" data-drop-group="g-c"><button>Гамма</button></li>
      <li data-drop-group="${NO_GROUP}"><button>Без группы</button></li>
      <li><button id="all">Все записи</button></li>
    </ul>`;
  const rows = [...document.querySelectorAll<HTMLElement>("li")];
  rows.forEach((el, i) => {
    el.getBoundingClientRect = () => ({ top: 100 + i * 26, bottom: 126 + i * 26, left: 0, right: 180,
      height: 26, width: 180, x: 0, y: 100 + i * 26, toJSON: () => ({}) });
  });
  return rows;
}

afterEach(async () => {
  document.body.innerHTML = "";
  // Гашение щелчка после перетаскивания снимается таймером 0 мс.
  await new Promise((resolve) => setTimeout(resolve, 0));
});

test("порог: перетаскивание — с 5 px в любую сторону", () => {
  expect(DRAG_THRESHOLD).toBe(5);
  expect(passedThreshold({ x: 0, y: 0 }, { x: 4, y: 0 })).toBe(false);
  expect(passedThreshold({ x: 0, y: 0 }, { x: 3, y: 3 })).toBe(false);
  expect(passedThreshold({ x: 0, y: 0 }, { x: 0, y: -5 })).toBe(true);
  expect(passedThreshold({ x: 10, y: 10 }, { x: 14, y: 13 })).toBe(true);
});

test("тень: несколько встреч — «3 встречи», одна — её название, группа — имя", () => {
  expect(ghostText(meetings(["a", "b", "c"]))).toBe("3 встречи");
  expect(ghostText(meetings(["a"]))).toBe("Планёрка");
  expect(ghostText({ kind: "group", id: "g-a", label: "Альфа" })).toBe("Альфа");
});

test("цель для встреч — группа или «Без группы»; «Все записи» и пустое место — не цель", () => {
  panel();
  const p = meetings(["r1", "r2"], { r1: "g-a", r2: null });
  expect(targetAt(document.querySelector(".name"), p, 110, [])).toEqual({ kind: "group", group: "g-a" });
  expect(targetAt(document.querySelector("[data-drop-group=g-b] button"), p, 130, [])).toEqual({ kind: "group", group: "g-b" });
  expect(targetAt(document.querySelector(`[data-drop-group=${NO_GROUP}]`), p, 190, [])).toEqual({ kind: "group", group: null });
  expect(targetAt(document.getElementById("all"), p, 210, [])).toBeNull();
  expect(targetAt(document.body, p, 0, [])).toBeNull();
  expect(targetAt(null, p, 0, [])).toBeNull();
});

test("все встречи уже в этой группе — отпускание ничего не меняет, это не цель", () => {
  panel();
  const p = meetings(["r1", "r2"], { r1: "g-a", r2: "g-a" });
  expect(targetAt(document.querySelector("[data-drop-group=g-a]"), p, 110, [])).toBeNull();
  expect(targetAt(document.querySelector(`[data-drop-group=${NO_GROUP}]`), p, 190, [])).toEqual({ kind: "group", group: null });
  const none = meetings(["r3"], { r3: null });
  expect(targetAt(document.querySelector(`[data-drop-group=${NO_GROUP}]`), none, 190, [])).toBeNull();
});

test("порядок групп: верхняя половина строки — линия перед ней, нижняя — после; на своём месте — не цель", () => {
  const rows = panel();
  const order = ["g-a", "g-b", "g-c"];
  const g: DragPayload = { kind: "group", id: "g-a", label: "Альфа" };
  // Над «Гаммой» (y 152–178): верх — перед ней (место 2), низ — после (место 3).
  expect(targetAt(rows[2]!, g, 155, order)).toEqual({ kind: "slot", index: 2 });
  expect(targetAt(rows[2]!, g, 175, order)).toEqual({ kind: "slot", index: 3 });
  // Альфа над собой или сразу под собой — порядок тот же.
  expect(targetAt(rows[0]!, g, 105, order)).toBeNull();
  expect(targetAt(rows[1]!, g, 128, order)).toBeNull();
  // «Без группы» — не строка группы.
  expect(targetAt(rows[3]!, g, 180, order)).toBeNull();
});

test("автопрокрутка: у краёв — быстрее к краю, в середине — нет", () => {
  const rect = { top: 100, bottom: 500 };
  expect(edgeDelta(rect, 300)).toBe(0);
  expect(edgeDelta(rect, 101)).toBeLessThan(0);
  expect(edgeDelta(rect, 499)).toBeGreaterThan(0);
  expect(Math.abs(edgeDelta(rect, 101))).toBeGreaterThan(Math.abs(edgeDelta(rect, 130)));
  expect(edgeDelta(rect, 60)).toBe(-14); // за краем — наибольший шаг
  expect(edgeDelta({ top: 0, bottom: 0 }, 0)).toBe(0);
});

function hooks(over: Partial<SessionHooks> = {}) {
  const log: string[] = [];
  const dropped: [DragPayload, DropTarget][] = [];
  const h: SessionHooks = {
    payload: () => meetings(["r1"], { r1: null }),
    resolve: (x) => (x > 100 ? { kind: "group", group: "g-a" } : null),
    onMove: (v) => log.push(`move ${v.x},${v.y} ${v.target ? "over" : "-"}`),
    onDrop: (p, t) => dropped.push([p, t]),
    onEnd: () => log.push("end"),
    ...over,
  };
  return { h, log, dropped };
}

test("сессия: до порога — ничего (щелчок остаётся щелчком)", () => {
  const { h, log, dropped } = hooks();
  const payload = vi.fn(h.payload);
  const s = new DragSession({ x: 10, y: 10 }, { ...h, payload });
  s.move(12, 13);
  expect(s.active).toBe(false);
  expect(s.up(12, 13)).toBe(false);
  expect(payload).not.toHaveBeenCalled();
  expect(log).toEqual([]);
  expect(dropped).toEqual([]);
});

test("сессия: за порогом — тащим; отпустили на цели — перенос", () => {
  const { h, log, dropped } = hooks();
  const s = new DragSession({ x: 10, y: 10 }, h);
  s.move(20, 10);
  expect(s.active).toBe(true);
  s.move(150, 40);
  expect(s.up(150, 40)).toBe(true);
  expect(log).toEqual(["move 20,10 -", "move 150,40 over", "end"]);
  expect(dropped).toEqual([[meetings(["r1"], { r1: null }), { kind: "group", group: "g-a" }]]);
});

test("сессия: отпустили вне цели или отменили (Esc) — без переноса", () => {
  const a = hooks();
  const s1 = new DragSession({ x: 0, y: 0 }, a.h);
  s1.move(50, 0);
  expect(s1.up(50, 0)).toBe(true);
  expect(a.dropped).toEqual([]);
  expect(a.log.at(-1)).toBe("end");

  const b = hooks();
  const s2 = new DragSession({ x: 0, y: 0 }, b.h);
  s2.move(150, 0);
  expect(s2.cancel()).toBe(true);
  expect(s2.up(150, 0)).toBe(false);
  s2.move(160, 0);
  expect(b.dropped).toEqual([]);
  expect(b.log.filter((l) => l === "end")).toHaveLength(1);
});

test("сессия: тащить нечего (нажали не на строку) — перетаскивания нет", () => {
  const { h, log } = hooks({ payload: () => null });
  const s = new DragSession({ x: 0, y: 0 }, h);
  s.move(30, 0);
  expect(s.active).toBe(false);
  expect(s.done).toBe(true);
  expect(log).toEqual([]);
});

test("сессия: несколько выбранных встреч уходят вместе", () => {
  const p = meetings(["r1", "r2", "r3"], { r1: null, r2: "g-b", r3: null });
  const { h, dropped } = hooks({ payload: () => p });
  const s = new DragSession({ x: 0, y: 0 }, h);
  s.move(150, 0);
  s.up(150, 0);
  expect(dropped[0]![0]).toBe(p);
});

// --- на документе: pointer-события -----------------------------------------------------

function pointer(type: string, init: PointerEventInit & { target?: Element | Window } = {}) {
  const { target = window, ...rest } = init;
  fireEvent(target, new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, pointerType: "mouse",
    button: 0, buttons: type === "pointerup" ? 0 : 1, ...rest }));
}

function setup(order = ["g-a", "g-b", "g-c"]) {
  const rows = panel();
  const source = document.createElement("button");
  source.textContent = "Планёрка";
  document.body.append(source);
  const onDrop = vi.fn();
  let over: Element | null = null;
  const drag = createGroupDrag({ onDrop, order: () => order, elementAt: () => over });
  const views: (string | null)[] = [];
  drag.subscribe(() => {
    const v = drag.view();
    views.push(v ? `${v.target ? JSON.stringify(v.target) : "-"}` : null);
  });
  const down = (init: Partial<PointerEventInit> = {}, payload: () => DragPayload | null = () => meetings(["r1"], { r1: null })) => {
    const e = new PointerEvent("pointerdown", { pointerId: 1, pointerType: "mouse", button: 0, clientX: 10, clientY: 10, ...init });
    Object.defineProperty(e, "target", { value: source });
    drag.begin(e, payload);
  };
  return { rows, source, drag, onDrop, views, down, hover: (el: Element | null) => { over = el; } };
}

test("на документе: отпустили над группой — перенос, тень исчезает, щелчок после — погашен", () => {
  const { rows, source, drag, onDrop, views, down, hover } = setup();
  down();
  hover(rows[1]!);
  pointer("pointermove", { clientX: 40, clientY: 130 });
  expect(document.documentElement).toHaveClass(DRAGGING_CLASS);
  expect(drag.view()?.target).toEqual({ kind: "group", group: "g-b" });
  pointer("pointerup", { clientX: 40, clientY: 130 });
  expect(onDrop).toHaveBeenCalledWith(meetings(["r1"], { r1: null }), { kind: "group", group: "g-b" });
  expect(drag.view()).toBeNull();
  expect(document.documentElement).not.toHaveClass(DRAGGING_CLASS);
  expect(views.at(-1)).toBeNull();
  // Браузер пришлёт click на источник — он не должен открыть встречу.
  const clicked = vi.fn();
  source.addEventListener("click", clicked);
  fireEvent.click(source);
  expect(clicked).not.toHaveBeenCalled();
});

test("на документе: Esc отменяет перетаскивание и не доходит до списка (выбор не сбрасывается)", () => {
  const { rows, onDrop, drag, down, hover } = setup();
  const listKeys = vi.fn();
  document.addEventListener("keydown", listKeys);
  down();
  hover(rows[0]!);
  pointer("pointermove", { clientX: 40, clientY: 110 });
  fireEvent.keyDown(document.body, { key: "Escape" });
  expect(listKeys).not.toHaveBeenCalled();
  expect(drag.view()).toBeNull();
  pointer("pointermove", { clientX: 60, clientY: 110 });
  expect(drag.view()).toBeNull();
  pointer("pointerup", { clientX: 40, clientY: 110 });
  expect(onDrop).not.toHaveBeenCalled();
  // Отпустили над той же строкой — щелчок не откроет встречу.
  const clicked = vi.fn();
  document.body.addEventListener("click", clicked);
  fireEvent.click(document.body);
  expect(clicked).not.toHaveBeenCalled();
  document.removeEventListener("keydown", listKeys);
});

test("на документе: без движения — обычный щелчок; Esc до порога списку не мешает", () => {
  const { source, onDrop, drag, down } = setup();
  down();
  const listKeys = vi.fn();
  document.addEventListener("keydown", listKeys);
  fireEvent.keyDown(document.body, { key: "Escape" });
  expect(listKeys).toHaveBeenCalledTimes(1);
  pointer("pointermove", { clientX: 12, clientY: 12 });
  pointer("pointerup", { clientX: 12, clientY: 12 });
  expect(drag.view()).toBeNull();
  const clicked = vi.fn();
  source.addEventListener("click", clicked);
  fireEvent.click(source);
  expect(clicked).toHaveBeenCalledTimes(1);
  expect(onDrop).not.toHaveBeenCalled();
  document.removeEventListener("keydown", listKeys);
});

test("на документе: Ctrl/Shift+нажатие (выбор записей), правая кнопка, палец и перо — не перетаскивание", () => {
  const { rows, onDrop, drag, down, hover } = setup();
  hover(rows[1]!);
  for (const init of [{ ctrlKey: true }, { shiftKey: true }, { button: 2 }, { pointerType: "touch" }, { pointerType: "pen" }]) {
    down(init);
    pointer("pointermove", { clientX: 60, clientY: 130 });
    expect(drag.view()).toBeNull();
    pointer("pointerup", { clientX: 60, clientY: 130 });
  }
  expect(onDrop).not.toHaveBeenCalled();
});

test("на документе: группа на новое место — цель с местом вставки", () => {
  const { rows, onDrop, down, hover } = setup();
  down({}, () => ({ kind: "group", id: "g-a", label: "Альфа" }));
  hover(rows[2]!);
  pointer("pointermove", { clientX: 40, clientY: 175 });
  pointer("pointerup", { clientX: 40, clientY: 175 });
  expect(onDrop).toHaveBeenCalledWith({ kind: "group", id: "g-a", label: "Альфа" }, { kind: "slot", index: 3 });
});

test("на документе: кнопку отпустили, а pointerup потерялся (buttons = 0) — отмена, щелчок не роняет", () => {
  const { rows, onDrop, drag, down, hover } = setup();
  down();
  hover(rows[1]!);
  pointer("pointermove", { clientX: 40, clientY: 130 });
  expect(drag.view()).not.toBeNull();
  pointer("pointermove", { clientX: 50, clientY: 130, buttons: 0 });
  expect(drag.view()).toBeNull();
  // Следующий обычный щелчок по группе — не перенос.
  pointer("pointerdown", { clientX: 50, clientY: 130 });
  pointer("pointerup", { clientX: 50, clientY: 130 });
  expect(onDrop).not.toHaveBeenCalled();
});

test("на документе: pointercancel чужого указателя не мешает", () => {
  const { rows, drag, down, hover } = setup();
  down();
  hover(rows[1]!);
  pointer("pointermove", { clientX: 40, clientY: 130 });
  pointer("pointercancel", { pointerId: 9 });
  expect(drag.view()).not.toBeNull();
  pointer("pointercancel");
  expect(drag.view()).toBeNull();
});

test("на документе: отмена потерей указателя (pointercancel) и сменой окна", () => {
  const { rows, onDrop, drag, down, hover } = setup();
  down();
  hover(rows[1]!);
  pointer("pointermove", { clientX: 40, clientY: 130 });
  pointer("pointercancel");
  expect(drag.view()).toBeNull();
  down();
  pointer("pointermove", { clientX: 40, clientY: 130 });
  fireEvent.blur(window);
  expect(drag.view()).toBeNull();
  pointer("pointerup", { clientX: 40, clientY: 130 });
  expect(onDrop).not.toHaveBeenCalled();
});
