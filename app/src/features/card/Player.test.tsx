/**
 * Плеер как у YouTube (M3): полоса с главами, подписи, кривая важности,
 * пузырь при наведении, клавиши, «Только важное», список глав. Данные выдуманные.
 */

import { act, createRef } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildView, LOW_IMPORTANCE } from "../../lib/analysisView";
import { mergeTurns } from "../../lib/speakers";
import type { Analysis, Segment } from "../../lib/types";
import { AudioPlayer, bubbleText, BUBBLE_TEXT_MAX, playerKey, type AudioPlayerHandle } from "./AudioPlayer";

const ep = { base: "http://127.0.0.1:8766", token: "секрет" };
const seg = (start: number, end: number, speaker: string, text: string): Segment =>
  ({ start, end, speaker, text, uncertain: false });
// 10 минут: вступление до 2:00, бюджет до 6:00, итоги до конца.
const SEGMENTS: Segment[] = [
  seg(0, 20, "Анна", "Добрый день, начинаем планирование."),
  seg(60, 80, "Борис", "Коротко о том, что успели за неделю."),
  seg(120, 140, "Анна", "Бюджет на квартал: облако и подрядчики, это главный вопрос встречи сегодня, обсудим подробно."),
  seg(200, 230, "Борис", "Решили: облако в пределах прошлого квартала."),
  seg(300, 320, "Анна", "Подрядчикам — только после согласования."),
  seg(360, 380, "Борис", "Итак, итоги."),
  seg(420, 440, "Анна", "Расчёт к пятнице готовит Борис."),
  seg(500, 520, "Борис", "Спасибо всем."),
];
const ANALYSIS: Analysis = {
  version: 1, model: "test", created_at: 1, fingerprint: "f", segments: SEGMENTS.length,
  features: ["importance", "chapters"],
  importance: { 2: 0.95, 3: 0.9, 6: 0.85 },
  chapters: [
    { start_i: 0, end_i: 1, title: "Вступление", short: "Вступление" },
    { start_i: 2, end_i: 4, title: "Бюджет на квартал", short: "Бюджет" },
    { start_i: 5, end_i: 7, title: "Итоги и задачи", short: "Итоги" },
  ],
};
const TURNS = mergeTurns(SEGMENTS);
const VIEW = buildView(TURNS, ANALYSIS, SEGMENTS.length, { types: false, importance: true, chapters: true, insights: false })!;
const WIDTH = 900;

let rect: typeof Element.prototype.getBoundingClientRect;
beforeEach(() => {
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
  HTMLMediaElement.prototype.load = vi.fn();
  rect = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function () {
    return { left: 0, right: WIDTH, width: WIDTH, top: 0, bottom: 20, height: 20, x: 0, y: 0, toJSON() {} } as DOMRect;
  };
  try { window.localStorage.clear(); } catch { /* нет хранилища */ }
});
afterEach(() => { Element.prototype.getBoundingClientRect = rect; });

function setup(props: Partial<Parameters<typeof AudioPlayer>[0]> = {}) {
  const ref = createRef<AudioPlayerHandle>();
  const view = render(<AudioPlayer ref={ref} endpoint={ep} id="r1" durationHint={600} turns={TURNS}
    chapters={VIEW.chapters} importance={VIEW.importance} {...props} />);
  const audio = view.container.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { configurable: true, value: 600 });
  fireEvent.loadedMetadata(audio);
  return { ref, audio, ...view };
}
const bar = () => screen.getByRole("slider", { name: "Позиция" });
const playing = (audio: HTMLAudioElement) => {
  Object.defineProperty(audio, "paused", { configurable: true, value: false });
  fireEvent.play(audio);
};

test("без анализа — простая полоса: один отрезок, без кривой, глав, подписей и «Только важного»", () => {
  const { container } = setup({ chapters: [], importance: null });
  expect(container.querySelectorAll(".pbar__seg")).toHaveLength(1);
  expect(container.querySelector(".pbar__curve, .pbar__labels")).toBeNull();
  expect(screen.queryByRole("button", { name: "Главы" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Только важное" })).toBeNull();
  // Остальное — на месте.
  for (const name of ["Воспроизвести", "Выключить звук", "Компактный плеер"]) {
    expect(screen.getByRole("button", { name })).toBeInTheDocument();
  }
  expect(screen.getByRole("button", { name: /Скорость воспроизведения/ })).toBeInTheDocument();
});

test("полоса делится на главы с зазорами; подписи «N. название» помещаются по ширине", () => {
  const { container } = setup();
  const segs = [...container.querySelectorAll<HTMLElement>(".pbar__seg")];
  expect(segs).toHaveLength(3);
  expect(segs.map((s) => s.style.left)).toEqual(["0%", "20%", "60%"]);
  expect(segs[0]!.style.width).toBe("calc(20% - 3px)");
  expect(segs[2]!.style.width).toBe("40%"); // у последней зазора нет
  const labels = [...container.querySelectorAll<HTMLElement>(".pbar__label")];
  expect(labels.map((l) => l.textContent)).toEqual(["1. Вступление", "2. Бюджет", "3. Итоги"]);
  expect(labels[1]).toHaveAttribute("title", "Бюджет на квартал");
});

test("узкий плеер — подписи глав только номерами; выключенные подписи не рисуются", () => {
  Element.prototype.getBoundingClientRect = function () {
    return { left: 0, right: 300, width: 300, top: 0, bottom: 20, height: 20, x: 0, y: 0, toJSON() {} } as DOMRect;
  };
  const { container, unmount } = setup();
  expect([...container.querySelectorAll(".pbar__label")].map((l) => l.textContent)).toEqual(["1", "2", "3"]);
  unmount();
  const again = setup({ barLabels: false });
  expect(again.container.querySelector(".pbar__labels")).toBeNull();
});

test("текущая глава рядом со временем «00:00 / 10:00 · Вступление ›»; время — текущее и общее", () => {
  const { audio } = setup();
  expect(screen.getByRole("button", { name: "Глава 1: Вступление. Список глав" })).toBeInTheDocument();
  audio.currentTime = 250;
  fireEvent.timeUpdate(audio);
  expect(screen.getByText("04:10")).toBeInTheDocument();
  expect(screen.getByText("10:00")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Глава 2: Бюджет на квартал. Список глав" })).toBeInTheDocument();
  expect(bar()).toHaveAttribute("aria-valuetext", "04:10 из 10:00, глава «Бюджет на квартал»");
});

test("наведение: полоса толще, пузырь «мм:сс · глава», аватар спикера и начало реплики", () => {
  const { container } = setup();
  fireEvent.pointerEnter(bar(), { clientX: 0 });
  fireEvent.pointerMove(bar(), { clientX: 195 }); // 130 с
  expect(bar()).toHaveClass("is-hover");
  expect(bar().style.getPropertyValue("--hover")).toBe(String(195 / WIDTH));
  const bubble = container.querySelector<HTMLElement>(".pbar__bubble")!;
  expect(bubble.querySelector(".pbar__bubble-head")).toHaveTextContent("02:10 · Бюджет на квартал");
  expect(bubble.querySelector(".avatar")).toHaveTextContent("А");
  const text = bubble.querySelector(".pbar__bubble-text")!.textContent!;
  expect(text.startsWith("Анна: Бюджет на квартал: облако")).toBe(true);
  expect(text.endsWith("…")).toBe(true);
  fireEvent.pointerLeave(bar());
  expect(bar()).not.toHaveClass("is-hover");
});

test("щелчок и перетаскивание по полосе перематывают; положение — CSS-переменной, без перерисовки", () => {
  const { audio } = setup();
  fireEvent.pointerDown(bar(), { clientX: 450, button: 0, pointerId: 1 });
  expect(bar().style.getPropertyValue("--pos")).toBe("0.5");
  fireEvent.pointerMove(bar(), { clientX: 540, pointerId: 1 });
  expect(bar().style.getPropertyValue("--pos")).toBe("0.6");
  fireEvent.pointerUp(bar(), { clientX: 540, pointerId: 1 });
  expect(audio.currentTime).toBe(360);
});

test("кривая важности: при наведении (по умолчанию), всегда или нет; один путь SVG", () => {
  const hover = setup();
  const svg = hover.container.querySelector(".pbar__curve")!;
  expect(svg.querySelectorAll("path")).toHaveLength(1);
  expect(hover.container.querySelector(".player")).toHaveClass("player--curve-hover");
  hover.unmount();
  const always = setup({ curveMode: "always" });
  expect(always.container.querySelector(".player")).toHaveClass("player--curve-always");
  always.unmount();
  const off = setup({ curveMode: "off" });
  expect(off.container.querySelector(".pbar__curve")).toBeNull();
});

test("«Главы»: список, текущая отмечена, щелчок — к началу главы и показать её в расшифровке", async () => {
  const onChapter = vi.fn();
  const { audio } = setup({ onChapter });
  await userEvent.click(screen.getByRole("button", { name: "Главы" }));
  const list = screen.getByRole("dialog", { name: "Главы встречи" });
  const items = within(list).getAllByRole("button");
  expect(items.map((b) => b.textContent)).toEqual(["1Вступление00:00", "2Бюджет на квартал02:00", "3Итоги и задачи06:00"]);
  expect(items[0]).toHaveAttribute("aria-current", "true");
  await userEvent.click(items[2]!);
  expect(audio.currentTime).toBe(360);
  expect(onChapter).toHaveBeenCalledWith(2);
  expect(screen.queryByRole("dialog", { name: "Главы встречи" })).toBeNull();
  // Та же кнопка ещё раз — открывает и закрывает; отмечена только она.
  const button = screen.getByRole("button", { name: "Главы" });
  await userEvent.click(button);
  expect(button).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: /Список глав/ })).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(button);
  expect(screen.queryByRole("dialog", { name: "Главы встречи" })).toBeNull();
});

test("«Только важное»: отметка «N фрагментов», неважное пропускается, после последнего — пауза, ✕ выключает", async () => {
  const { audio } = setup();
  await userEvent.click(screen.getByRole("button", { name: "Только важное" }));
  expect(screen.getByRole("button", { name: "Только важное" })).toHaveAttribute("aria-pressed", "true");
  // Верхние 30 % из восьми реплик — реплики 2 и 3 (зазор 60 с — отдельно), с запасом по секунде.
  expect(screen.getByText("Только важное · 2 фрагмента")).toBeInTheDocument();
  playing(audio);
  audio.currentTime = 30; // неважное — к первому фрагменту
  fireEvent.timeUpdate(audio);
  expect(audio.currentTime).toBe(119);
  audio.currentTime = 150; // между фрагментами — к следующему
  fireEvent.timeUpdate(audio);
  expect(audio.currentTime).toBe(199);
  audio.currentTime = 210; // внутри — играем дальше
  fireEvent.timeUpdate(audio);
  expect(audio.currentTime).toBe(210);
  audio.currentTime = 450; // после последнего — пауза
  fireEvent.timeUpdate(audio);
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Выключить «Только важное»" }));
  expect(screen.queryByText(/Только важное ·/)).toBeNull();
});

test("«Только важное»: перемотали сами в неважное — играем до следующего важного, дальше снова пропуски", async () => {
  const { ref, audio } = setup();
  await userEvent.click(screen.getByRole("button", { name: "Только важное" }));
  playing(audio);
  act(() => ref.current!.seek(60, true)); // реплика Бориса — не важная, но её попросили
  audio.currentTime = 70;
  fireEvent.timeUpdate(audio);
  expect(audio.currentTime).toBe(70);
  audio.currentTime = 125; // дошли до важного
  fireEvent.timeUpdate(audio);
  audio.currentTime = 150; // и после него — снова пропуск
  fireEvent.timeUpdate(audio);
  expect(audio.currentTime).toBe(199);
});

test("без важных реплик «Только важное» не показывается", () => {
  setup({ importance: TURNS.map(() => LOW_IMPORTANCE) });
  expect(screen.queryByRole("button", { name: "Только важное" })).toBeNull();
});

test("клавиши как на YouTube: K/Пробел, J/L, ←/→, Shift+←/→ — главы, Ctrl+←/→ — реплики, M, цифры", () => {
  const { audio } = setup();
  const key = (init: KeyboardEventInit) => fireEvent.keyDown(document.body, init);
  audio.currentTime = 100;
  key({ key: "l", code: "KeyL" });
  expect(audio.currentTime).toBe(110);
  key({ key: "о", code: "KeyJ" }); // русская раскладка
  expect(audio.currentTime).toBe(100);
  key({ key: "ArrowRight" });
  expect(audio.currentTime).toBe(105);
  key({ key: "ArrowRight", shiftKey: true });
  expect(audio.currentTime).toBe(120); // следующая глава
  key({ key: "ArrowRight", shiftKey: true });
  expect(audio.currentTime).toBe(360);
  key({ key: "ArrowLeft", shiftKey: true }); // в самом начале главы — к предыдущей
  expect(audio.currentTime).toBe(120);
  key({ key: "ArrowRight", ctrlKey: true });
  expect(audio.currentTime).toBe(200); // следующая реплика
  key({ key: "ArrowLeft", ctrlKey: true });
  expect(audio.currentTime).toBe(120);
  key({ key: "5", code: "Digit5" });
  expect(audio.currentTime).toBe(300);
  key({ key: "0", code: "Numpad0" });
  expect(audio.currentTime).toBe(0);
  key({ key: "m", code: "KeyM" });
  expect(audio.muted).toBe(true);
  key({ key: "k", code: "KeyK" });
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
  key({ key: " ", code: "Space" });
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
});

test("клавиши не забираются у полей ввода, терминала агента, окон и кнопок (пробел)", () => {
  const { audio } = setup();
  const host = document.createElement("div");
  host.innerHTML = `<input id="f" /><textarea id="t"></textarea><div data-agent-terminal><textarea id="x"></textarea></div>
    <div role="dialog"><button id="d">ok</button></div><button id="b">b</button><div role="tablist"><button id="tab">t</button></div>`;
  document.body.appendChild(host);
  try {
    audio.currentTime = 100;
    for (const id of ["f", "t", "x", "d"]) {
      const el = document.getElementById(id)!;
      fireEvent.keyDown(el, { key: "l", code: "KeyL" });
      fireEvent.keyDown(el, { key: "ArrowRight" });
      fireEvent.keyDown(el, { key: " ", code: "Space" });
    }
    fireEvent.keyDown(document.getElementById("tab")!, { key: "ArrowRight" });
    fireEvent.keyDown(document.getElementById("b")!, { key: " ", code: "Space" }); // пробел нажимает кнопку
    expect(audio.currentTime).toBe(100);
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
    // А K на кнопке — плеера.
    fireEvent.keyDown(document.getElementById("b")!, { key: "k", code: "KeyK" });
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
    // Ctrl+F, Ctrl+E и прочие сочетания — не плеера.
    expect(playerKey({ key: "f", code: "KeyF", ctrlKey: true, shiftKey: false, altKey: false, metaKey: false,
      defaultPrevented: false, target: document.body })).toBeNull();
    // Уже обработанное (реплика в фокусе: пробел — выбор) — не трогаем.
    expect(playerKey({ key: " ", code: "Space", ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
      defaultPrevented: true, target: document.body })).toBeNull();
  } finally {
    host.remove();
  }
});

test("компактный вид: одна строка, без подписей; выбор запоминается", async () => {
  const first = setup();
  await userEvent.click(screen.getByRole("button", { name: "Компактный плеер" }));
  expect(first.container.querySelector(".player")).toHaveClass("player--compact");
  expect(first.container.querySelector(".pbar__labels")).toBeNull();
  first.unmount();
  const again = setup();
  expect(again.container.querySelector(".player")).toHaveClass("player--compact");
  await userEvent.click(screen.getByRole("button", { name: "Развернуть плеер" }));
  expect(again.container.querySelector(".player")).not.toHaveClass("player--compact");
});

test("громкость: ползунок меняет громкость, 0 — без звука", () => {
  const { audio } = setup();
  fireEvent.change(screen.getByRole("slider", { name: "Громкость" }), { target: { value: "0.4" } });
  expect(audio.volume).toBeCloseTo(0.4);
  fireEvent.change(screen.getByRole("slider", { name: "Громкость" }), { target: { value: "0" } });
  expect(audio.muted).toBe(true);
  expect(screen.getByRole("button", { name: "Включить звук" })).toBeInTheDocument();
});

test("начало реплики в пузыре — не длиннее 60 символов", () => {
  expect(bubbleText("  Коротко  и\nясно ")).toBe("Коротко и ясно");
  const long = "а".repeat(100);
  expect(Array.from(bubbleText(long))).toHaveLength(BUBBLE_TEXT_MAX);
  expect(bubbleText(long).endsWith("…")).toBe(true);
});
