import { afterEach, expect, test, vi } from "vitest";
import {
  CARD_MIN, dragWidth, fitList, LIST, listMax, loadList, loadWidth, paneBounds, paneWidth, RAIL, saveList, saveWidth,
  stepWidth,
} from "./panes";

/** Порог сворачивания панели разделителем (пример: прежняя навигация до 0.4). */
const SNAP = { below: 100, to: 56 };

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

test("рейка — постоянные 60 px; список — своей ширины, пока карточке хватает места", () => {
  expect(RAIL).toBe(60);
  expect(fitList(1400, LIST.def)).toBe(300);
  expect(fitList(1400, 420)).toBe(420);
  expect(fitList(1400, 9999)).toBe(LIST.max);
  for (let win = 900; win <= 2000; win += 10) {
    const list = fitList(win, LIST.max);
    expect(win - RAIL - list).toBeGreaterThanOrEqual(CARD_MIN);
    expect(list).toBeGreaterThanOrEqual(LIST.min);
  }
  // Самое узкое окно (820 px): список не уже минимума, карточка — что осталось.
  expect(fitList(820, LIST.def)).toBe(LIST.min);
});

test("предел разделителя списка оставляет карточке её минимум", () => {
  expect(listMax(1100)).toBe(1100 - CARD_MIN - RAIL);
  expect(listMax(3000)).toBe(LIST.max);
  expect(listMax(700)).toBe(LIST.min);
});

test("перетаскивание: в пределах; уже порога — свёрнута, между порогом и минимумом — минимум", () => {
  expect(dragWidth(1000, 140, 320)).toBe(320);
  expect(dragWidth(10, 140, 320)).toBe(140);
  expect(dragWidth(80, 140, 320, SNAP)).toBe(SNAP.to);
  expect(dragWidth(120, 140, 320, SNAP)).toBe(140);
  expect(dragWidth(201.6, 140, 320, SNAP)).toBe(202);
});

test("клавиши: шаг 16 px в пределах; из минимума — свёрнута и обратно", () => {
  expect(stepWidth(200, 1, 140, 320)).toBe(216);
  expect(stepWidth(312, 1, 140, 320)).toBe(320);
  expect(stepWidth(150, -1, 140, 320)).toBe(140);
  expect(stepWidth(150, -1, 140, 320, SNAP)).toBe(SNAP.to);
  expect(stepWidth(SNAP.to, -1, 140, 320, SNAP)).toBe(SNAP.to);
  expect(stepWidth(SNAP.to, 1, 140, 320, SNAP)).toBe(140);
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

test("ширина списка запоминается; по умолчанию — ничего не хранится; мусор и недоступное хранилище не мешают", () => {
  saveList(400);
  expect(loadList()).toBe(400);
  saveList(LIST.def);
  expect(Object.keys(localStorage).filter((k) => k.startsWith("meet.pane."))).toEqual([]);
  localStorage.setItem("meet.pane.list", "abc");
  expect(loadWidth("list")).toBeNull();
  expect(loadList()).toBe(LIST.def);

  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("denied"); });
  expect(loadList()).toBe(LIST.def);
  expect(() => saveWidth("list", 300)).not.toThrow();
  expect(() => saveList(400)).not.toThrow();
});
