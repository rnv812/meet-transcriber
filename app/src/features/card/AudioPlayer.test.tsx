import { act, createRef } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import { AudioPlayer, type AudioPlayerHandle, SPEEDS, SEEK_STEP_S } from "./AudioPlayer";

const ep = { base: "http://127.0.0.1:8766", token: "секрет" };

beforeEach(() => {
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
  HTMLMediaElement.prototype.load = vi.fn();
});

function setup(props: Partial<Parameters<typeof AudioPlayer>[0]> = {}) {
  const ref = createRef<AudioPlayerHandle>();
  const view = render(<AudioPlayer ref={ref} endpoint={ep} id="r1" {...props} />);
  const audio = view.container.querySelector("audio")!;
  return { ref, audio, ...view };
}

/** Как браузер: метаданные пришли — длительность известна. */
function loaded(audio: HTMLAudioElement, duration: number) {
  Object.defineProperty(audio, "duration", { configurable: true, value: duration });
  fireEvent.loadedMetadata(audio);
}

test("играет сведённую дорожку playback (обе стороны звонка), токен — в query", () => {
  const { audio } = setup();
  expect(audio.getAttribute("src")).toBe(api.audioUrl(ep, "r1", "playback"));
  expect(audio.getAttribute("src")).toContain("track=playback");
  expect(audio.getAttribute("src")).toContain("token=");
  expect(audio).not.toHaveAttribute("controls");
  // Открытая карточка файл не запрашивает: сведение — по первому «▶» (или заранее, в фоне резидента).
  expect(audio).toHaveAttribute("preload", "none");
});

test("время: текущее и общее; до метаданных — длительность из карточки", () => {
  const { audio } = setup({ durationHint: 1800 });
  expect(screen.getByText("30:00")).toBeInTheDocument();
  loaded(audio, 125);
  expect(screen.getByText("02:05")).toBeInTheDocument();
  audio.currentTime = 61;
  fireEvent.timeUpdate(audio);
  expect(screen.getByText("01:01")).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: "Позиция" })).toHaveAttribute("aria-valuetext", "01:01 из 02:05");
});

test("кнопка воспроизведения: play, затем «Пауза» — pause", async () => {
  const { audio } = setup();
  await userEvent.click(screen.getByRole("button", { name: "Воспроизвести" }));
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
  fireEvent.play(audio);
  await userEvent.click(screen.getByRole("button", { name: "Пауза" }));
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  fireEvent.pause(audio);
  expect(screen.getByRole("button", { name: "Воспроизвести" })).toBeInTheDocument();
});

test("полоса перемотки ставит позицию (щелчок по полосе)", () => {
  const { audio } = setup();
  loaded(audio, 300);
  const slider = screen.getByRole("slider", { name: "Позиция" });
  slider.getBoundingClientRect = () => ({ left: 0, right: 600, width: 600, top: 0, bottom: 20, height: 20 }) as DOMRect;
  fireEvent.pointerDown(slider, { clientX: 240, button: 0, pointerId: 1 });
  fireEvent.pointerUp(slider, { clientX: 240, pointerId: 1 });
  expect(audio.currentTime).toBe(120);
});

test("скорость (0.5) — меню 0,75 / 1 / 1,25 / 1,5 / 2× с отметкой текущей; выбор закрывает меню", async () => {
  const { audio } = setup();
  const button = () => screen.getByRole("button", { name: /Скорость воспроизведения/ });
  expect(SPEEDS).toEqual([0.75, 1, 1.25, 1.5, 2]);
  expect(button()).toHaveTextContent("1×");
  expect(button()).toHaveAttribute("aria-haspopup", "menu");
  await userEvent.click(button());
  const menu = screen.getByRole("menu", { name: "Скорость воспроизведения" });
  const items = within(menu).getAllByRole("menuitemradio");
  expect(items.map((i) => i.textContent)).toEqual(["0,75×", "1×", "1,25×", "1,5×", "2×"]);
  expect(within(menu).getByRole("menuitemradio", { name: "1×" })).toHaveAttribute("aria-checked", "true");
  expect(within(menu).getByRole("menuitemradio", { name: "1×" })).toHaveFocus();
  await userEvent.keyboard("{ArrowUp}");
  expect(within(menu).getByRole("menuitemradio", { name: "0,75×" })).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  expect(audio.playbackRate).toBe(0.75);
  expect(screen.queryByRole("menu")).toBeNull();
  expect(button()).toHaveTextContent("0,75×");
  await userEvent.click(button());
  await userEvent.click(screen.getByRole("menuitemradio", { name: "1,5×" }));
  expect(audio.playbackRate).toBe(1.5);
  await userEvent.click(button());
  expect(screen.getByRole("menuitemradio", { name: "1,5×" })).toHaveAttribute("aria-checked", "true");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("звук: выключить и включить", async () => {
  const { audio } = setup();
  await userEvent.click(screen.getByRole("button", { name: "Выключить звук" }));
  expect(audio.muted).toBe(true);
  expect(screen.getByRole("button", { name: "Включить звук" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(screen.getByRole("button", { name: "Включить звук" }));
  expect(audio.muted).toBe(false);
});

test("клавиатура: ←/→ — на 5 секунд, пробел — пуск и пауза", async () => {
  const { audio } = setup();
  loaded(audio, 300);
  audio.currentTime = 100;
  const slider = screen.getByRole("slider", { name: "Позиция" });
  slider.focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(audio.currentTime).toBe(100 + SEEK_STEP_S);
  await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
  expect(audio.currentTime).toBe(100 - SEEK_STEP_S);
  await userEvent.keyboard(" ");
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
  fireEvent.play(audio);
  await userEvent.keyboard(" ");
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
});

test("перемотка не выходит за начало и конец", async () => {
  const { audio } = setup();
  loaded(audio, 8);
  audio.currentTime = 2;
  screen.getByRole("slider", { name: "Позиция" }).focus();
  await userEvent.keyboard("{ArrowLeft}");
  expect(audio.currentTime).toBe(0);
  await userEvent.keyboard("{ArrowRight}{ArrowRight}");
  expect(audio.currentTime).toBe(8);
});

test("seek(at, true) из реплики: позиция и воспроизведение", () => {
  const { ref, audio } = setup();
  act(() => ref.current!.seek(42, true));
  expect(audio.currentTime).toBe(42);
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
});

test("seek(at, true, until): фраза спикера играет до отметки и останавливается", () => {
  const { ref, audio } = setup();
  loaded(audio, 600);
  act(() => ref.current!.seek(42, true, 48));
  expect(audio.currentTime).toBe(42);
  audio.currentTime = 45;
  fireEvent.timeUpdate(audio);
  expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled();
  audio.currentTime = 48.1;
  fireEvent.timeUpdate(audio);
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  // Дальше — обычное воспроизведение: отметка снята.
  act(() => ref.current!.seek(10, true));
  audio.currentTime = 60;
  fireEvent.timeUpdate(audio);
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
});

describe("точная остановка фрагмента — по таймеру, а не по timeupdate", () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] }); });
  afterEach(() => { vi.useRealTimers(); });

  /** Как браузер: воспроизведение пошло (play() сработал, данные есть). */
  function playing(audio: HTMLAudioElement) {
    Object.defineProperty(audio, "paused", { configurable: true, value: false });
    fireEvent.playing(audio);
  }

  test("фрагмент 3,0–8,0 с: пауза ровно на 8,0 с без единого timeupdate", () => {
    const onPhrase = vi.fn();
    const { ref, audio } = setup({ onPhrase });
    loaded(audio, 600);
    act(() => ref.current!.seek(3, true, 8));
    expect(onPhrase).toHaveBeenLastCalledWith({ start: 3, until: 8 });
    act(() => playing(audio));
    act(() => { vi.advanceTimersByTime(4999); });
    expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
    expect(onPhrase).toHaveBeenLastCalledWith(null);
  });

  test("пока данных нет, таймер не идёт: отсчёт — с начала воспроизведения", () => {
    const { ref, audio } = setup();
    loaded(audio, 600);
    act(() => ref.current!.seek(3, true, 8));
    act(() => { vi.advanceTimersByTime(30_000); }); // «Подготовка аудио…»
    expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled();
    act(() => playing(audio));
    act(() => { vi.advanceTimersByTime(5000); });
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  });

  test("скорость 2× — таймер на 2,5 с", () => {
    const { ref, audio } = setup();
    loaded(audio, 600);
    audio.playbackRate = 2;
    act(() => ref.current!.seek(3, true, 8));
    act(() => playing(audio));
    act(() => { vi.advanceTimersByTime(2499); });
    expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  });

  test("смена скорости посреди фрагмента переставляет таймер", () => {
    const { ref, audio } = setup();
    loaded(audio, 600);
    act(() => ref.current!.seek(3, true, 8));
    act(() => playing(audio));
    act(() => { vi.advanceTimersByTime(2000); });
    audio.currentTime = 5;
    act(() => { audio.playbackRate = 2; }); // ratechange: осталось 3 с записи — 1,5 с
    act(() => { vi.advanceTimersByTime(1499); });
    expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  });

  test("timeupdate перепроверяет: конец пройден — пауза сразу", () => {
    const { ref, audio } = setup();
    loaded(audio, 600);
    act(() => ref.current!.seek(3, true, 8));
    act(() => playing(audio));
    audio.currentTime = 8.02;
    fireEvent.timeUpdate(audio);
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  });

  test("перемотка во время фрагмента отменяет таймер", () => {
    const onPhrase = vi.fn();
    const { ref, audio } = setup({ onPhrase });
    loaded(audio, 600);
    act(() => ref.current!.seek(3, true, 8));
    act(() => playing(audio));
    fireEvent.keyDown(screen.getByRole("slider", { name: "Позиция" }), { key: "ArrowRight" });
    expect(onPhrase).toHaveBeenLastCalledWith(null);
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled();
  });

  test("новый фрагмент снимает прежний: остановка — по концу нового", () => {
    const onPhrase = vi.fn();
    const { ref, audio } = setup({ onPhrase });
    loaded(audio, 600);
    act(() => ref.current!.seek(3, true, 8));
    act(() => playing(audio));
    act(() => { vi.advanceTimersByTime(1000); });
    act(() => ref.current!.seek(20, true, 30));
    expect(onPhrase.mock.calls.map((c) => c[0])).toEqual([{ start: 3, until: 8 }, null, { start: 20, until: 30 }]);
    act(() => { vi.advanceTimersByTime(9999); });
    expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  });

  test("stop() — «Остановить» у образца: пауза, фрагмент снят", () => {
    const onPhrase = vi.fn();
    const { ref, audio } = setup({ onPhrase });
    loaded(audio, 600);
    act(() => ref.current!.seek(3, true, 8));
    act(() => playing(audio));
    act(() => ref.current!.stop());
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
    expect(onPhrase).toHaveBeenLastCalledWith(null);
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  });

  test("пауза кнопкой плеера снимает фрагмент", () => {
    const onPhrase = vi.fn();
    const { ref, audio } = setup({ onPhrase });
    loaded(audio, 600);
    act(() => ref.current!.seek(3, true, 8));
    act(() => playing(audio));
    fireEvent.play(audio);
    fireEvent.click(screen.getByRole("button", { name: "Пауза" }));
    expect(onPhrase).toHaveBeenLastCalledWith(null);
  });
});

test("дорожка не загрузилась — «Аудио недоступно» вместо плеера", () => {
  const onAvailable = vi.fn();
  const { audio } = setup({ onAvailable });
  fireEvent.error(audio);
  expect(screen.getByText("Аудио недоступно")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Воспроизвести" })).toBeNull();
  expect(screen.queryByRole("slider")).toBeNull();
  expect(onAvailable).toHaveBeenLastCalledWith(false);
});

test("пока данных нет — «Подготовка аудио…», с готовностью — снова время", async () => {
  const { audio } = setup();
  await userEvent.click(screen.getByRole("button", { name: "Воспроизвести" }));
  expect(screen.getByRole("status")).toHaveTextContent("Подготовка аудио…");
  fireEvent.canPlay(audio);
  expect(screen.queryByText("Подготовка аудио…")).toBeNull();
  fireEvent.waiting(audio);
  expect(screen.getByText("Подготовка аудио…")).toBeInTheDocument();
  fireEvent.playing(audio);
  expect(screen.queryByText("Подготовка аудио…")).toBeNull();
  expect(screen.getAllByText("00:00")).toHaveLength(2); // текущее и общее время
});

test("release() отпускает файл: src снят, load(), без «Аудио недоступно»", () => {
  const { ref, audio } = setup();
  act(() => ref.current!.release());
  expect(audio).not.toHaveAttribute("src");
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  expect(HTMLMediaElement.prototype.load).toHaveBeenCalled();
  fireEvent.error(audio); // пустой src браузер считает ошибкой — это не сбой дорожки
  expect(screen.queryByText("Аудио недоступно")).toBeNull();
});
