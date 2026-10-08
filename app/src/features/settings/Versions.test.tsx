import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  backupBefore: vi.fn(async () => ({ path: "C:\\data\\meet\\backups\\x.zip" })),
}));

vi.mock("../../lib/shell", () => ({
  openUrl: vi.fn(async () => {}),
  listReleases: vi.fn(),
  installUpdate: vi.fn(async () => ({ outcome: "installer", reason: null })),
  cancelUpdate: vi.fn(async () => {}),
  UPDATE_CANCELLED: "Загрузка обновления отменена",
  onUpdateProgress: vi.fn(async () => () => {}),
}));

import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { ReleaseRow } from "../../lib/shell";
import { VersionsRow, beforeAurora, downgradeText, releaseDate } from "./Versions";

const endpoint = { base: "http://127.0.0.1:1", token: "t" };
const row = (version: string, relation: ReleaseRow["relation"], installable = true): ReleaseRow => ({
  version, relation, installable, date: "2026-10-08", summary: `Кратко о ${version}`,
  notes_url: `https://github.com/example/releases/tag/v${version}`,
});

beforeEach(() => {
  vi.mocked(shell.listReleases).mockResolvedValue([
    row("0.5.1", "newer"), row("0.5.0", "current", false), row("0.4.0", "older"), row("0.3.9", "older"),
  ]);
  vi.mocked(shell.installUpdate).mockClear();
  vi.mocked(api.backupBefore).mockClear();
});

async function open() {
  render(<VersionsRow endpoint={endpoint} current="0.5.0" />);
  await userEvent.click(screen.getByRole("button", { name: "Показать выпуски" }));
  await screen.findByText("Кратко о 0.4.0");
}

const install = (i: number) => screen.getAllByRole("button", { name: "Установить эту версию" })[i]!;
const installButtons = () => screen.getAllByRole("button", { name: "Установить эту версию" });

test("выпуски — по кнопке; своя версия отмечена и не ставится", async () => {
  expect(shell.listReleases).not.toHaveBeenCalled();
  await open();
  expect(screen.getByText("установлена")).toBeInTheDocument();
  expect(installButtons()).toHaveLength(3);
  expect(screen.getAllByText("8 октября 2026 г.")).toHaveLength(4);
});

test("более новая ставится сразу, без копии", async () => {
  await open();
  await userEvent.click(install(0));
  await waitFor(() => expect(shell.installUpdate).toHaveBeenCalledWith(false, "0.5.1"));
  expect(api.backupBefore).not.toHaveBeenCalled();
});

test("откат — после подтверждения и копии настроек; отмена ничего не делает", async () => {
  await open();
  await userEvent.click(install(1));
  expect(screen.getByText(/сохранит копию настроек, базы голосов/)).toBeInTheDocument();
  expect(screen.queryByText(/Версии до 0.4/)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(api.backupBefore).not.toHaveBeenCalled();

  await userEvent.click(install(1));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить копию и установить 0.4.0" }));
  await waitFor(() => expect(shell.installUpdate).toHaveBeenCalledWith(false, "0.4.0"));
  expect(api.backupBefore).toHaveBeenCalledWith(endpoint, "0.4.0");
  expect(await screen.findByText(/Установка версии 0.4.0 запущена/)).toBeInTheDocument();
});

test("копия не сохранилась — версия не ставится", async () => {
  vi.mocked(api.backupBefore).mockRejectedValueOnce(new Error("резидент не отвечает"));
  await open();
  await userEvent.click(install(2));
  expect(screen.getByText(/Версии до 0.4 устроены заметно иначе/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить копию и установить 0.3.9" }));
  expect(await screen.findByText(/Копия настроек не сохранилась \(резидент не отвечает\)/)).toBeInTheDocument();
  expect(shell.installUpdate).not.toHaveBeenCalled();
});

test("ошибка списка — текстом, кнопка остаётся", async () => {
  vi.mocked(shell.listReleases).mockRejectedValueOnce(new Error("Не удалось проверить: нет связи с GitHub"));
  render(<VersionsRow endpoint={endpoint} current="0.5.0" />);
  await userEvent.click(screen.getByRole("button", { name: "Показать выпуски" }));
  expect(await screen.findByText("Не удалось проверить: нет связи с GitHub")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Показать выпуски" })).toBeInTheDocument();
});

test("особое предупреждение — только ниже 0.4", () => {
  expect(beforeAurora("0.3.9")).toBe(true);
  expect(beforeAurora("v0.3.0")).toBe(true);
  expect(beforeAurora("0.4.0")).toBe(false);
  expect(beforeAurora("1.0.0")).toBe(false);
  expect(downgradeText("0.4.0", "0.5.0")).toHaveLength(2);
  expect(downgradeText("0.3.7", "0.5.0")).toHaveLength(3);
  expect(releaseDate(null)).toBeNull();
  expect(releaseDate("мусор")).toBeNull();
});
