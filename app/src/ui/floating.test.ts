import { placeFloating, pointAnchor, MARGIN } from "./floating";

const view = { width: 1000, height: 700 };
const box = { width: 260, height: 200 };
/** Кнопка 24×20 с левым краем в x, верхним в y. */
const btn = (x: number, y: number) => ({ left: x, right: x + 24, top: y, bottom: y + 20 });
const inside = (p: { left: number; top: number }, b = box) =>
  p.left >= MARGIN && p.top >= MARGIN && p.left + b.width <= view.width - MARGIN && p.top + b.height <= view.height - MARGIN;

describe("по вертикали", () => {
  test("снизу есть место — под якорем", () => {
    expect(placeFloating(btn(100, 100), box, view)).toEqual({ left: 100, top: 126 });
  });

  test("снизу не влезает — над якорем (переворот)", () => {
    expect(placeFloating(btn(100, 600), box, view)).toEqual({ left: 100, top: 394 });
  });

  test("не влезает ни снизу, ни сверху — прижато к нижнему краю окна", () => {
    const tall = { width: 260, height: 650 };
    expect(placeFloating(btn(100, 300), tall, view)).toEqual({ left: 100, top: 42 });
  });

  test("окно выше экрана — верх остаётся виден", () => {
    expect(placeFloating(btn(100, 300), { width: 260, height: 900 }, view).top).toBe(MARGIN);
  });

  test("якорь у самого низа окна — окно над ним и целиком в окне", () => {
    const p = placeFloating(btn(100, 690), box, view);
    expect(p.top).toBe(690 - 6 - 200);
    expect(inside(p)).toBe(true);
  });
});

describe("по горизонтали", () => {
  test("align: start — левым краем к якорю", () => {
    expect(placeFloating(btn(100, 100), box, view).left).toBe(100);
  });

  test("align: end — правым краем к правому краю якоря (раскрытие влево)", () => {
    expect(placeFloating(btn(600, 100), box, view, { align: "end" }).left).toBe(624 - 260);
  });

  test("start у правого края не влезает — переворачивается: правым краем к якорю", () => {
    expect(placeFloating(btn(900, 100), box, view).left).toBe(924 - 260);
  });

  test("end у левого края не влезает — переворачивается: левым краем к якорю", () => {
    expect(placeFloating(btn(20, 100), box, view, { align: "end" }).left).toBe(20);
  });

  test("не влезает ни так, ни так — сдвиг к краю с отступом 8 px", () => {
    const wide = { width: 900, height: 100 };
    expect(placeFloating(btn(500, 100), wide, view).left).toBe(1000 - 900 - MARGIN);
    expect(placeFloating(btn(200, 100), wide, view, { align: "end" }).left).toBe(MARGIN);
  });

  test("окно шире экрана — левый край виден", () => {
    expect(placeFloating(btn(500, 100), { width: 1200, height: 100 }, view).left).toBe(MARGIN);
  });

  test("якорь у самого правого края (кнопка «⋯» в 4 px от края) — окно целиком в окне", () => {
    const p = placeFloating(btn(1000 - 28, 100), { width: 180, height: 40 }, view, { align: "end" });
    expect(p.left).toBe(1000 - 180 - MARGIN); // правый край якоря ближе 8 px к краю — сдвиг до отступа
    expect(p.left + 180).toBeLessThanOrEqual(1000 - MARGIN);
  });

  test("якорь частично за правым краем — прижато к краю с отступом", () => {
    expect(placeFloating({ left: 990, right: 1030, top: 100, bottom: 120 }, box, view, { align: "end" }).left)
      .toBe(1000 - 260 - MARGIN);
  });
});

describe("точка (контекстное меню)", () => {
  test("в середине — от указателя вправо и вниз", () => {
    expect(placeFloating(pointAnchor(300, 200), box, view, { gap: 0 })).toEqual({ left: 300, top: 200 });
  });

  test("в правом нижнем углу — влево и вверх от указателя", () => {
    expect(placeFloating(pointAnchor(950, 650), box, view, { gap: 0 })).toEqual({ left: 950 - 260, top: 650 - 200 });
  });
});

test("в любом углу и при любом выравнивании окно, которое помещается, целиком в окне", () => {
  for (const x of [0, 4, 120, 500, 870, 980, 996]) {
    for (const y of [0, 4, 300, 560, 680, 696]) {
      for (const align of ["start", "end"] as const) {
        expect(inside(placeFloating(btn(x, y), box, view, { align }))).toBe(true);
      }
    }
  }
});
