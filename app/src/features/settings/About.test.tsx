import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getDiagnostics: vi.fn(async () => ({ paths: { data_dir: "C:\\data\\meet" } })),
}));

let progressListener: ((p: { done: number; total: number }) => void) | null = null;
vi.mock("../../lib/shell", () => ({
  openUrl: vi.fn(async () => {}),
  checkUpdate: vi.fn(),
  installUpdate: vi.fn(),
  onUpdateProgress: vi.fn(async (cb: (p: { done: number; total: number }) => void) => {
    progressListener = cb;
    return () => { progressListener = null; };
  }),
}));

import * as shell from "../../lib/shell";
import { About, RELEASES_URL, megabytes } from "./About";

const endpoint = { base: "http://127.0.0.1:1", token: "t" };
const check = vi.mocked(shell.checkUpdate);
const install = vi.mocked(shell.installUpdate);

const newer = {
  current: "0.1.1",
  latest: "0.2.0",
  newer: true,
  notes_url: "https://github.com/rnv812/meet-transcriber/releases/tag/v0.2.0",
  asset_name: "meet_0.2.0_x64-setup.exe",
  size: 52428800,
};

beforeEach(() => {
  check.mockReset();
  install.mockReset();
  vi.mocked(shell.openUrl).mockClear();
});

async function renderAbout() {
  render(<About endpoint={endpoint} />);
  await waitFor(() => expect(screen.getByText("C:\\data\\meet")).toBeInTheDocument());
}

const checkButton = () => screen.getByRole("button", { name: "Проверить обновления" });

test("«Скачать новую версию» открывает выпуски репозитория обновлений", async () => {
  await renderAbout();
  await userEvent.click(screen.getByRole("button", { name: "Скачать новую версию" }));
  expect(shell.openUrl).toHaveBeenCalledWith("https://github.com/rnv812/meet-transcriber/releases");
  expect(RELEASES_URL).toBe("https://github.com/rnv812/meet-transcriber/releases");
});

test("рассказывает, как обновиться, и что данные сохранятся", async () => {
  await renderAbout();
  expect(
    screen.getByText("Скачайте новый установщик и запустите его — данные сохранятся"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Как работает обновление" })).toBeInTheDocument();
});

test("последняя версия", async () => {
  check.mockResolvedValue({ ...newer, latest: "0.1.1", newer: false });
  await renderAbout();
  await userEvent.click(checkButton());
  expect(await screen.findByText("У вас последняя версия (0.1.1)")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Скачать и установить" })).toBeNull();
});

test("выпусков ещё нет — не ошибка", async () => {
  check.mockResolvedValue({
    current: "0.1.1", latest: null, newer: false, notes_url: null, asset_name: null, size: null,
  });
  await renderAbout();
  await userEvent.click(checkButton());
  expect(await screen.findByText("Обновления пока не опубликованы")).toBeInTheDocument();
});

test("нет связи — текст ошибки оболочки", async () => {
  check.mockRejectedValue("Не удалось проверить: нет связи с GitHub");
  await renderAbout();
  await userEvent.click(checkButton());
  expect(await screen.findByText("Не удалось проверить: нет связи с GitHub")).toBeInTheDocument();
  expect(checkButton()).toBeEnabled();
});

test("новая версия: «Что нового» и «Скачать и установить» с ходом загрузки", async () => {
  check.mockResolvedValue(newer);
  let finish: () => void = () => {};
  install.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
  await renderAbout();
  await userEvent.click(checkButton());
  expect(await screen.findByText(/Доступна версия 0\.2\.0/)).toBeInTheDocument();
  expect(screen.getByText(/50 МБ/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Что нового" }));
  expect(shell.openUrl).toHaveBeenCalledWith(newer.notes_url);

  await userEvent.click(screen.getByRole("button", { name: "Скачать и установить" }));
  expect(install).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Начинаю загрузку…")).toBeInTheDocument();
  expect(checkButton()).toBeDisabled();
  act(() => progressListener?.({ done: 10485760, total: 52428800 }));
  expect(screen.getByText("Скачиваю: 10 МБ из 50 МБ")).toBeInTheDocument();

  await act(async () => finish());
  expect(await screen.findByText("Установщик запущен, приложение закрывается…")).toBeInTheDocument();
});

test("отказ во время записи виден рядом с кнопкой", async () => {
  check.mockResolvedValue(newer);
  install.mockRejectedValue("Остановите запись, чтобы обновиться");
  await renderAbout();
  await userEvent.click(checkButton());
  await userEvent.click(await screen.findByRole("button", { name: "Скачать и установить" }));
  expect(await screen.findByText("Остановите запись, чтобы обновиться")).toBeInTheDocument();
  // Можно попробовать снова, когда запись закончится.
  expect(screen.getByRole("button", { name: "Скачать и установить" })).toBeEnabled();
});

test("выпуск без установщика — только ссылка на выпуски", async () => {
  check.mockResolvedValue({ ...newer, asset_name: null, size: null });
  await renderAbout();
  await userEvent.click(checkButton());
  expect(
    await screen.findByText("В выпуске нет установщика — скачайте его со страницы выпусков"),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Скачать и установить" })).toBeNull();
});

test("размер в мегабайтах", () => {
  expect(megabytes(52428800)).toBe("50 МБ");
  expect(megabytes(1572864)).toBe("1,5 МБ");
});
