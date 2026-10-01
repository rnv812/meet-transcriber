import { placePopover } from "./Popover";

const view = { width: 1000, height: 700 };
const box = { width: 260, height: 200 };

test("снизу есть место — под якорем", () => {
  expect(placePopover({ left: 100, top: 100, bottom: 120 }, box, view)).toEqual({ left: 100, top: 126 });
});

test("снизу не влезает — над якорем", () => {
  expect(placePopover({ left: 100, top: 600, bottom: 620 }, box, view)).toEqual({ left: 100, top: 394 });
});

test("не влезает ни снизу, ни сверху — прижато к нижнему краю окна", () => {
  const tall = { width: 260, height: 650 };
  expect(placePopover({ left: 100, top: 300, bottom: 320 }, tall, view)).toEqual({ left: 100, top: 42 });
});

test("окно выше экрана — верх остаётся виден", () => {
  const huge = { width: 260, height: 900 };
  expect(placePopover({ left: 100, top: 300, bottom: 320 }, huge, view).top).toBe(8);
});

test("по горизонтали не вылезает за правый край", () => {
  expect(placePopover({ left: 900, top: 100, bottom: 120 }, box, view).left).toBe(1000 - 260 - 8);
});
