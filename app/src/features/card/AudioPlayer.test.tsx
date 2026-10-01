import { act, createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
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

test("полоса перемотки ставит позицию", () => {
  const { audio } = setup();
  loaded(audio, 300);
  fireEvent.change(screen.getByRole("slider", { name: "Позиция" }), { target: { value: "120" } });
  expect(audio.currentTime).toBe(120);
});

test("скорость: 1× → 1,25× → 1,5× → 2× → 1×", async () => {
  const { audio } = setup();
  const button = () => screen.getByRole("button", { name: /Скорость воспроизведения/ });
  expect(button()).toHaveTextContent("1×");
  const seen: number[] = [];
  for (let i = 0; i < SPEEDS.length; i++) {
    await userEvent.click(button());
    seen.push(audio.playbackRate);
  }
  expect(seen).toEqual([1.25, 1.5, 2, 1]);
  await userEvent.click(button());
  expect(button()).toHaveTextContent("1,25×");
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
