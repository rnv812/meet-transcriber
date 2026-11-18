/**
 * Плеер карточки на macOS: звук — всегда WebM (WebKit не играет Ogg или играет его с неверным
 * временем). Пока резидент перепаковывает — «Готовлю звук…», сбой — «Не удалось подготовить звук»
 * с причиной от резидента.
 */
import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AudioPlayer, type AudioPlayerHandle } from "./AudioPlayer";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});

const ep = { base: "http://127.0.0.1:8766", token: "секрет" };

beforeEach(() => {
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
  HTMLMediaElement.prototype.load = vi.fn();
});
afterEach(() => { vi.unstubAllGlobals(); });

function setup(props: Partial<Parameters<typeof AudioPlayer>[0]> = {}) {
  const ref = createRef<AudioPlayerHandle>();
  const view = render(<AudioPlayer ref={ref} endpoint={ep} id="r1" {...props} />);
  return { ref, audio: view.container.querySelector("audio")!, ...view };
}

test("все дорожки плеера — в WebM", async () => {
  const { audio } = setup({ sides: { them: true, you: true } });
  const src = () => new URL(audio.getAttribute("src")!);
  expect(src().searchParams.get("track")).toBe("playback");
  expect(src().searchParams.get("format")).toBe("webm");
  await userEvent.click(screen.getByRole("radio", { name: "Собеседники" }));
  expect(src().searchParams.get("track")).toBe("them");
  expect(src().searchParams.get("format")).toBe("webm");
  await userEvent.click(screen.getByRole("radio", { name: "Вы" }));
  expect(src().searchParams.get("track")).toBe("you");
  expect(src().searchParams.get("format")).toBe("webm");
});

test("перепаковка дольше первой загрузки — «Готовлю звук…», потом играет", async () => {
  const { audio } = setup();
  await userEvent.click(screen.getByRole("button", { name: "Воспроизвести" }));
  expect(screen.getByRole("status")).toHaveTextContent("Готовлю звук…");
  fireEvent.waiting(audio);
  expect(screen.getByRole("status")).toHaveTextContent("Готовлю звук…");
  fireEvent.play(audio);
  fireEvent.playing(audio);
  expect(screen.queryByText("Готовлю звук…")).toBeNull();
  expect(screen.getByRole("button", { name: "Пауза" })).toBeInTheDocument();
});

test("сбой перепаковки — «Не удалось подготовить звук» с причиной от резидента", async () => {
  const fetch = vi.fn(async () => new Response(
    JSON.stringify({ error: "ffmpeg не найден — дорожки записи не перепакованы" }), { status: 503 }));
  vi.stubGlobal("fetch", fetch);
  const onAvailable = vi.fn();
  const { audio } = setup({ onAvailable });
  fireEvent.error(audio);
  expect(screen.getByRole("status")).toHaveTextContent("Не удалось подготовить звук");
  expect(await screen.findByText("ffmpeg не найден — дорожки записи не перепакованы")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("format=webm"), expect.anything());
  expect(screen.queryByText("Аудио недоступно")).toBeNull();
  expect(screen.queryByRole("button", { name: "Воспроизвести" })).toBeNull();
  expect(onAvailable).toHaveBeenLastCalledWith(false);
});
