/**
 * «Звук» на macOS (0.6.1): в «Звук собеседников» — «Системный звук» и все устройства ввода
 * (ради BlackHole). Выбирают свои наушники с микрофоном — и запись пишет микрофон вместо
 * собеседников. Подпись без «(вывод)», под списком — что выбрано на самом деле. Данные выдуманные.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import { SoundSection } from "./SoundSection";
import type { Raw } from "./Section";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  checkProcessTap: vi.fn(),
}));

vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  openPrivacyPane: vi.fn(async () => {}),
}));

beforeEach(() => vi.clearAllMocks());

const ep = { base: "/api", token: null };
const devices: api.Devices = {
  available: true, pinning: true,
  inputs: [{ name: "Микрофон MacBook", default: true }, { name: "AirPods Анны", default: false },
    { name: "BlackHole 2ch", default: false }],
  outputs: [{ name: "Системный звук", default: true }, { name: "Микрофон MacBook", default: false },
    { name: "AirPods Анны", default: false }, { name: "BlackHole 2ch", default: false }],
};

function Harness({ recording }: { recording: Record<string, unknown> }) {
  const [draft] = useState<Raw>({ recording });
  return <SoundSection draft={draft} devices={devices} endpoint={ep} set={() => {}} />;
}

const other = () => screen.getByRole("group", { name: "Звук собеседников" });

test("подпись — «Звук собеседников», без «(вывод)»: на Mac это не устройство вывода", () => {
  render(<Harness recording={{ mic_device: null, output_device: null }} />);
  expect(screen.getByRole("combobox", { name: "Звук собеседников" })).toHaveTextContent("Как в системе (сейчас: Системный звук)");
  expect(other()).not.toHaveTextContent(/микрофон:/);
});

test("выбран тот же микрофон, что пишет голос (системный или выбранный), — предупреждение", () => {
  const { unmount } = render(<Harness recording={{ mic_device: null, output_device: { name: "Микрофон MacBook" } }} />);
  expect(other()).toHaveTextContent(
    "«Микрофон MacBook» — ваш микрофон: запишется ваш голос, а не собеседники. Во время записи Meet возьмёт «Системный звук».");
  unmount();
  render(<Harness recording={{ mic_device: { name: "AirPods Анны" }, output_device: { name: "AirPods Анны" } }} />);
  expect(other()).toHaveTextContent("«AirPods Анны» — ваш микрофон");
});

test("другое устройство ввода — тихая строка: подходит только то, куда направлен звук звонка", () => {
  render(<Harness recording={{ mic_device: null, output_device: { name: "AirPods Анны" } }} />);
  expect(other()).toHaveTextContent(
    "Это устройство ввода: сюда должен приходить звук звонка (BlackHole и т. п.). Микрофон наушников не подойдёт — выберите «Системный звук».");
  expect(other()).not.toHaveTextContent("ваш микрофон");
});

/** Системный микрофон — AirPods (Bluetooth), рядом — встроенный. */
const bluetooth: api.Devices = {
  ...devices,
  inputs: [{ name: "AirPods Анны", default: true, transport: "bluetooth" },
    { name: "Микрофон MacBook", default: false, transport: "builtin" }, { name: "BlackHole 2ch", default: false }],
};

function Editable({ recording, list = bluetooth, onDraft }: {
  recording: Record<string, unknown>; list?: api.Devices; onDraft?: (d: Raw) => void;
}) {
  const [draft, setDraft] = useState<Raw>({ recording });
  return (
    <SoundSection draft={draft} devices={list} endpoint={ep}
      set={(g, k, v) => setDraft((cur) => {
        const next = { ...cur, [g]: { ...(cur[g] ?? {}), [k]: v } };
        onDraft?.(next);
        return next;
      })} />
  );
}

const mic = () => screen.getByRole("group", { name: "Микрофон" });
const BT_SWITCH = "Не брать микрофон Bluetooth-наушников";

test("системный микрофон — Bluetooth-наушники: строка, что Meet запишет встроенный", () => {
  render(<Editable recording={{ mic_device: null }} />);
  expect(mic()).toHaveTextContent(
    "«AirPods Анны» — Bluetooth-наушники. Если программа звонка не слушает их микрофон, Meet запишет "
    + "«Микрофон MacBook»: так звук собеседников в наушниках не станет хуже.");
  expect(screen.getByRole("switch", { name: BT_SWITCH })).toHaveAttribute("aria-checked", "true");
});

test("переключатель выключен — предупреждение вместо подмены; включение пишет в черновик", async () => {
  const drafts: Raw[] = [];
  render(<Editable recording={{ mic_device: null, skip_bluetooth_mic: false }} onDraft={(d) => drafts.push(d)} />);
  expect(mic()).toHaveTextContent(
    "«AirPods Анны» — Bluetooth-наушники: пока Meet пишет их микрофон, собеседники в наушниках могут звучать хуже.");
  await userEvent.click(screen.getByRole("switch", { name: BT_SWITCH }));
  expect(drafts.at(-1)?.recording?.skip_bluetooth_mic).toBe(true);
  expect(mic()).toHaveTextContent("Meet запишет «Микрофон MacBook»");
});

test("строки нет: микрофон выбран вручную, системный не Bluetooth или встроенного нет", () => {
  const { unmount } = render(<Editable recording={{ mic_device: { name: "AirPods Анны" } }} />);
  expect(mic()).not.toHaveTextContent("Bluetooth-наушники");
  unmount();
  const wired = { ...bluetooth, inputs: [{ name: "Микрофон MacBook", default: true, transport: "builtin" }] };
  const second = render(<Editable recording={{ mic_device: null }} list={wired} />);
  expect(mic()).not.toHaveTextContent("Bluetooth-наушники");
  second.unmount();
  const mini = { ...bluetooth, inputs: [{ name: "AirPods Анны", default: true, transport: "bluetooth" }] };
  render(<Editable recording={{ mic_device: null }} list={mini} />);
  expect(mic()).not.toHaveTextContent("Meet запишет");
});

/** macOS 14.4+: резидент разрешает выбрать process tap. */
const tapReady: api.Devices = { ...devices, process_tap: true };
const TAP_SWITCH = "Записывать звук собеседников без «Записи экрана»";
const tapCheck = () => screen.getByRole("group", { name: "Проверка записи системного звука" });

test("macOS старше 14.4 (резидент не разрешает process tap) — выключателя нет", () => {
  render(<Editable recording={{}} list={devices} />);
  expect(screen.queryByRole("switch", { name: TAP_SWITCH })).not.toBeInTheDocument();
});

test("включение — сначала проверка: звук есть — выключатель включён, в черновике process_tap", async () => {
  vi.mocked(api.checkProcessTap).mockResolvedValue(
    { ok: true, result: "sound", notice: "Звук есть — Meet будет писать звук собеседников без «Записи экрана».", pane: null });
  const drafts: Raw[] = [];
  render(<Editable recording={{}} list={tapReady} onDraft={(d) => drafts.push(d)} />);
  const toggle = screen.getByRole("switch", { name: TAP_SWITCH });
  expect(toggle).toHaveAttribute("aria-checked", "false");
  await userEvent.click(toggle);
  // Сам по себе выключатель не включается: просим включить звук и проверить.
  expect(drafts).toEqual([]);
  expect(toggle).toHaveAttribute("aria-checked", "false");
  expect(tapCheck()).toHaveTextContent("Включите любой звук (например, видео) и нажмите «Проверить»");
  await userEvent.click(within(tapCheck()).getByRole("button", { name: "Проверить" }));
  expect(api.checkProcessTap).toHaveBeenCalledTimes(1);
  expect(drafts.at(-1)?.recording?.mac_capture).toBe("process_tap");
  expect(screen.getByRole("switch", { name: TAP_SWITCH })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByText(/Звук есть/)).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Проверка записи системного звука" })).not.toBeInTheDocument();
});

test("проверка услышала только нули — выключатель выключен, подсказка открывает «Запись системного звука»", async () => {
  vi.mocked(api.checkProcessTap).mockResolvedValue({
    ok: false, result: "silence", pane: "system_audio",
    notice: "Meet услышал только тишину. Если звук играл — разрешите Meet «Запись системного звука».",
  });
  const drafts: Raw[] = [];
  render(<Editable recording={{}} list={tapReady} onDraft={(d) => drafts.push(d)} />);
  await userEvent.click(screen.getByRole("switch", { name: TAP_SWITCH }));
  await userEvent.click(within(tapCheck()).getByRole("button", { name: "Проверить" }));
  expect(drafts).toEqual([]);
  expect(screen.getByRole("switch", { name: TAP_SWITCH })).toHaveAttribute("aria-checked", "false");
  expect(tapCheck()).toHaveTextContent("«Запись системного звука»");
  await userEvent.click(within(tapCheck()).getByRole("button", { name: "Открыть «Запись системного звука»" }));
  expect(shell.openPrivacyPane).toHaveBeenCalledWith("system_audio");
});

test("сбой проверки — текст ошибки, выключатель выключен", async () => {
  vi.mocked(api.checkProcessTap).mockRejectedValue(new Error("Проверка уже идёт"));
  const drafts: Raw[] = [];
  render(<Editable recording={{}} list={tapReady} onDraft={(d) => drafts.push(d)} />);
  await userEvent.click(screen.getByRole("switch", { name: TAP_SWITCH }));
  await userEvent.click(within(tapCheck()).getByRole("button", { name: "Проверить" }));
  expect(tapCheck()).toHaveTextContent("Проверка уже идёт");
  expect(drafts).toEqual([]);
});

test("выключение — сразу, без проверки: обратно на ScreenCaptureKit", async () => {
  const drafts: Raw[] = [];
  render(<Editable recording={{ mac_capture: "process_tap" }} list={tapReady} onDraft={(d) => drafts.push(d)} />);
  const toggle = screen.getByRole("switch", { name: TAP_SWITCH });
  expect(toggle).toHaveAttribute("aria-checked", "true");
  await userEvent.click(toggle);
  expect(api.checkProcessTap).not.toHaveBeenCalled();
  expect(drafts.at(-1)?.recording?.mac_capture).toBe("screencapturekit");
});
