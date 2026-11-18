/**
 * Мастер на macOS: шаг «Разрешения» после «Запись» — «Микрофон» и «Запись экрана» с состоянием,
 * «Разрешить» (системный вопрос через резидент) и «Открыть настройки» (раздел конфиденциальности).
 * Состояние перечитывается, когда окно получает фокус: человек вернулся из «Системных настроек».
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Wizard } from "./Wizard";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { EngineStatus } from "../../lib/shell";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  resolveEndpoint: vi.fn(),
  getState: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  setAutoRecord: vi.fn(),
  getOwnerVoice: vi.fn(),
  getPermissions: vi.fn(),
  requestPermission: vi.fn(),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: () => true,
  residentStatus: vi.fn(async () => "running"),
  openPrivacyPane: vi.fn(async () => {}),
}));

const ep = { base: "http://127.0.0.1:5000", token: "t" };
const engine: EngineStatus = {
  installed: true, version: "0.1.0", env_dir: "/Users/me/Library/Application Support/meet/engine/0.1.0",
  profile: "mac", gpu: null, free_gb: 120.5, needs_gb: 5,
};
const perms = (microphone: api.MicPermission | null, screen: api.ScreenPermission | null): api.Permissions =>
  ({ supported: true, microphone, screen });

const show = (start: Parameters<typeof Wizard>[0]["start"] = "permissions") =>
  render(<Wizard start={start} engine={engine} endpoint={ep} recording={false}
    onClose={vi.fn()} onRefreshEngine={vi.fn(async () => {})} />);
const row = (name: string) => screen.getByRole("group", { name });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue({ auto_record: { enabled: false, processes: [] } });
  vi.mocked(api.getDevices).mockResolvedValue({ available: true, pinning: true, system: { name: "Системный звук", rate: 48000 }, mic: { name: "Микрофон MacBook", rate: 48000 } });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: true, running: [], selected: [], known: [] });
  vi.mocked(api.getPermissions).mockResolvedValue(perms("notDetermined", "denied"));
});

test("после «Запись» — шаг «Разрешения»: девять шагов, «Далее» ведёт к нему", async () => {
  show("devices");
  const list = screen.getByRole("list", { name: "Шаги мастера" });
  const titles = within(list).getAllByRole("listitem").map((li) => li.textContent);
  expect(titles).toHaveLength(9);
  const at = titles.findIndex((t) => t?.includes("Запись"));
  expect(titles[at + 1]).toContain("Разрешения");
  await userEvent.click(await screen.findByRole("button", { name: "Далее" }));
  expect(screen.getByRole("heading", { name: "Разрешения", level: 1 })).toBeInTheDocument();
  expect(screen.getByText("Шаг 6 из 9")).toBeInTheDocument();
});

test("«Микрофон» и «Запись экрана» с состоянием; «Разрешить» задаёт системный вопрос", async () => {
  vi.mocked(api.requestPermission).mockResolvedValue({ kind: "microphone", state: "granted", ...perms("granted", "denied") });
  show();
  expect(await within(row("Микрофон")).findByText("Ещё не спрашивали")).toBeInTheDocument();
  expect(within(row("Запись экрана")).getByText("Не разрешено")).toBeInTheDocument();
  await userEvent.click(within(row("Микрофон")).getByRole("button", { name: "Разрешить" }));
  expect(api.requestPermission).toHaveBeenCalledWith(ep, "microphone");
  expect(await within(row("Микрофон")).findByText("Разрешено")).toBeInTheDocument();
  expect(within(row("Микрофон")).queryByRole("button", { name: "Разрешить" })).toBeNull();
  expect(within(row("Микрофон")).queryByRole("button", { name: "Открыть настройки" })).toBeNull();
});

test("«Открыть настройки» — свой раздел «Конфиденциальности» у каждой строки", async () => {
  vi.mocked(api.getPermissions).mockResolvedValue(perms("denied", "denied"));
  show();
  await within(row("Микрофон")).findByText("Не разрешено");
  // Отказ macOS не переспрашивает — «Разрешить» у микрофона бесполезна.
  expect(within(row("Микрофон")).queryByRole("button", { name: "Разрешить" })).toBeNull();
  await userEvent.click(within(row("Микрофон")).getByRole("button", { name: "Открыть настройки" }));
  await userEvent.click(within(row("Запись экрана")).getByRole("button", { name: "Открыть настройки" }));
  expect(vi.mocked(shell.openPrivacyPane).mock.calls).toEqual([["microphone"], ["screen_recording"]]);
});

test("окно получило фокус — состояние перечитывается", async () => {
  show();
  await within(row("Запись экрана")).findByText("Не разрешено");
  vi.mocked(api.getPermissions).mockResolvedValue(perms("granted", "granted"));
  fireEvent.focus(window);
  await waitFor(() => expect(within(row("Запись экрана")).getByText("Разрешено")).toBeInTheDocument());
  expect(within(row("Микрофон")).getByText("Разрешено")).toBeInTheDocument();
  expect(api.getPermissions).toHaveBeenCalledTimes(2);
});

test("пояснение: «Завершить и открыть снова» здесь безопасно; «Далее» без разрешений не заперта", async () => {
  show();
  expect(await screen.findByText(/«Завершить и открыть снова»/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Далее" })).toBeEnabled();
});

test("состояние не узнать (прежний помощник) — «Неизвестно», настройки открыть можно", async () => {
  vi.mocked(api.getPermissions).mockResolvedValue(perms(null, null));
  show();
  expect(await within(row("Микрофон")).findByText("Неизвестно")).toBeInTheDocument();
  expect(within(row("Запись экрана")).getByRole("button", { name: "Открыть настройки" })).toBeInTheDocument();
});
