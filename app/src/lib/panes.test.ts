import { afterEach, expect, test, vi } from "vitest";
import {
  CARD_MIN, dragWidth, fitShell, LIST, loadShell, loadWidth, NAV, navCommit, navMax, listMax, paneBounds, paneWidth,
  saveShell, saveWidth, stepWidth, type ShellPrefs,
} from "./panes";

const DEF: ShellPrefs = { nav: NAV.def, navMode: "auto", list: LIST.def };

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

test("широкое окно: навигация и список — своей ширины", () => {
  expect(fitShell(1400, DEF)).toEqual({ nav: 200, rail: false, list: 320 });
  expect(fitShell(1400, { ...DEF, nav: 260, list: 420 })).toEqual({ nav: 260, rail: false, list: 420 });
});

test("до 1000 px навигация — полоса значков, как раньше; карточке не меньше 520 px с 900 px", () => {
  expect(fitShell(1000, DEF)).toMatchObject({ nav: NAV.rail, rail: true });
  for (let win = 900; win <= 2000; win += 10) {
    const f = fitShell(win, { ...DEF, nav: 320, list: 560 });
    expect(win - f.nav - f.list).toBeGreaterThanOrEqual(CARD_MIN);
    expect(f.list).toBeGreaterThanOrEqual(LIST.min);
  }
  // Самое узкое окно (820 px): список не уже минимума, карточка — что осталось.
  expect(fitShell(820, DEF)).toEqual({ nav: NAV.rail, rail: true, list: LIST.min });
});

test("не хватает места карточке — навигация и список сжимаются пропорционально, потом навигация — в полосу", () => {
  const p: ShellPrefs = { nav: 300, navMode: "auto", list: 500 };
  const f = fitShell(1200, p); // 680 на двоих
  expect(f.rail).toBe(false);
  expect(f.nav + f.list).toBe(1200 - CARD_MIN);
  expect(f.nav / f.list).toBeCloseTo(300 / 500, 1);
  // Развёрнутая в узком окне навигация держится, пока помещается, — потом полоса.
  expect(fitShell(1000, { ...DEF, nav: 140, navMode: "open" })).toMatchObject({ rail: false, nav: 140 });
  expect(fitShell(900, { ...DEF, nav: 140, navMode: "open" })).toMatchObject({ rail: true, nav: NAV.rail });
});

test("свернули в полосу — полоса и в широком окне; запомненная ширина не теряется", () => {
  const p = navCommit(NAV.rail, 1400, { ...DEF, nav: 260 });
  expect(p).toEqual({ nav: 260, navMode: "rail", list: 320 });
  expect(fitShell(1600, p)).toMatchObject({ rail: true, nav: NAV.rail });
  // Вытянули обратно: в широком окне — обычный режим, в узком — «развёрнута».
  expect(navCommit(180, 1400, p)).toEqual({ nav: 180, navMode: "auto", list: 320 });
  expect(navCommit(180, 1000, p)).toEqual({ nav: 180, navMode: "open", list: 320 });
});

test("пределы разделителей оставляют карточке её минимум", () => {
  const f = fitShell(1100, DEF);
  expect(navMax(1100, f)).toBe(1100 - CARD_MIN - f.list);
  expect(listMax(1100, f)).toBe(1100 - CARD_MIN - f.nav);
  expect(listMax(3000, f)).toBe(LIST.max);
});

test("перетаскивание: в пределах; навигация уже порога — полоса, между порогом и минимумом — минимум", () => {
  expect(dragWidth(1000, 140, 320)).toBe(320);
  expect(dragWidth(10, 140, 320)).toBe(140);
  const snap = { below: NAV.snap, to: NAV.rail };
  expect(dragWidth(80, 140, 320, snap)).toBe(NAV.rail);
  expect(dragWidth(120, 140, 320, snap)).toBe(140);
  expect(dragWidth(201.6, 140, 320, snap)).toBe(202);
});

test("клавиши: шаг 16 px в пределах; из минимума — в полосу и обратно", () => {
  expect(stepWidth(200, 1, 140, 320)).toBe(216);
  expect(stepWidth(312, 1, 140, 320)).toBe(320);
  expect(stepWidth(150, -1, 140, 320)).toBe(140);
  const snap = { below: NAV.snap, to: NAV.rail };
  expect(stepWidth(150, -1, 140, 320, snap)).toBe(NAV.rail);
  expect(stepWidth(NAV.rail, -1, 140, 320, snap)).toBe(NAV.rail);
  expect(stepWidth(NAV.rail, 1, 140, 320, snap)).toBe(140);
});

test("панель в области: не шире области за вычетом соседа, даже если это меньше её минимума", () => {
  const spec = { def: 360, min: 300, max: 640, reserve: 200 };
  expect(paneWidth(spec, 600, 1000)).toBe(600);
  expect(paneWidth(spec, 900, 1000)).toBe(640);
  expect(paneWidth(spec, 600, 700)).toBe(500);
  expect(paneWidth(spec, 360, 400)).toBe(200);
  expect(paneBounds(spec, 0)).toEqual({ min: 300, max: 640 }); // ещё не измерена
  const spk = { def: 440, min: 320, max: 760, reserve: (room: number) => (room >= 880 ? 400 : 0) };
  expect(paneBounds(spk, 1000)).toEqual({ min: 320, max: 600 });
  expect(paneBounds(spk, 700)).toEqual({ min: 320, max: 700 });
});

test("панель без верхнего предела: только место области за вычетом соседа и свой минимум", () => {
  const spec = { min: 160, reserve: 292 };
  expect(paneBounds(spec, 2000)).toEqual({ min: 160, max: 1708 });
  expect(paneWidth(spec, 1500, 2000)).toBe(1500);
  expect(paneWidth(spec, 1500, 900)).toBe(608);            // окно сузили — соседу его минимум
  expect(paneWidth(spec, 100, 900)).toBe(160);             // не уже своего минимума
  expect(paneBounds(spec, 0)).toEqual({ min: 160, max: 160 }); // ещё не измерена…
  expect(paneWidth(spec, 1500, 0)).toBe(1500);             // …размер — как просили, до замера
});

test("ширины запоминаются; по умолчанию — ничего не хранится; мусор и недоступное хранилище не мешают", () => {
  saveShell({ nav: 240, navMode: "rail", list: 400 });
  expect(loadShell()).toEqual({ nav: 240, navMode: "rail", list: 400 });
  saveShell(DEF);
  expect(Object.keys(localStorage).filter((k) => k.startsWith("meet.pane."))).toEqual([]);
  localStorage.setItem("meet.pane.list", "abc");
  expect(loadWidth("list")).toBeNull();
  expect(loadShell()).toEqual(DEF);

  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
  expect(loadShell()).toEqual(DEF);
  expect(() => saveWidth("list", 300)).not.toThrow();
  expect(() => saveShell({ ...DEF, navMode: "rail" })).not.toThrow();
});
