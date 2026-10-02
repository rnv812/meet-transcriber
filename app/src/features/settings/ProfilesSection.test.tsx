import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import { PRIVACY_NOTE } from "./ProfilesSection";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getProfilesInfo: vi.fn(),
  deleteAllProfiles: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = { profiles: { enabled: false } };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({
    settings: { ...structuredClone(settings), ...(u as object) } as Record<string, unknown>, restart_required: [],
  }));
  vi.mocked(api.getDevices).mockResolvedValue({ available: false, pinning: false });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: false });
  vi.mocked(api.getProfilesInfo).mockResolvedValue({ enabled: false, count: 2 });
  vi.mocked(api.deleteAllProfiles).mockResolvedValue({ deleted: 2 });
});

const open = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="profiles" />);

test("по умолчанию выключено; включение — только после подтверждения с пояснением", async () => {
  open();
  expect(screen.getByRole("button", { name: "Профили людей" })).toHaveAttribute("aria-current", "page");
  const sw = await screen.findByRole("switch", { name: "Составлять профили людей" });
  expect(sw).toHaveAttribute("aria-checked", "false");
  expect(screen.getAllByText(PRIVACY_NOTE).length).toBeGreaterThan(0);
  expect(PRIVACY_NOTE).toBe(
    "Профили хранятся только на этом компьютере. Это описание стиля общения по репликам, а не оценка личности.");
  await userEvent.click(sw);
  const dialog = screen.getByRole("alertdialog", { name: "Включить профили людей" });
  expect(within(dialog).getByText(PRIVACY_NOTE)).toBeInTheDocument();
  // честно: гарантий нет, есть инструкция, фильтр и «Скрыть»
  expect(dialog).toHaveTextContent("ИИ получает инструкцию не делать таких выводов, а результат дополнительно "
    + "фильтруется; если что-то лишнее всё же появилось — скройте это утверждение");
  expect(dialog).not.toHaveTextContent(/нет диагнозов|не может появиться/);
  // «Отмена» — ничего не меняется
  await userEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
  expect(screen.queryByRole("alertdialog", { name: "Включить профили людей" })).toBeNull();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  // «Включить» — в черновик, затем «Сохранить»
  await userEvent.click(sw);
  await userEvent.click(screen.getByRole("button", { name: "Включить" }));
  expect(sw).toHaveAttribute("aria-checked", "true");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { profiles: { enabled: true } }));
  // выключение — без подтверждения
  await userEvent.click(sw);
  expect(screen.queryByRole("alertdialog", { name: "Включить профили людей" })).toBeNull();
  expect(sw).toHaveAttribute("aria-checked", "false");
});

test("«Удалить все профили» — с подтверждением, сразу, без «Сохранить»", async () => {
  open();
  expect(await screen.findByText(/Сохранено: 2 профиля/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Удалить все профили…" }));
  const confirm = screen.getByRole("alertdialog", { name: "Удалить все профили?" });
  expect(confirm).toHaveTextContent("2 профиля, ваши заметки о людях и индекс реплик будут удалены без возврата.");
  vi.mocked(api.getProfilesInfo).mockResolvedValue({ enabled: false, count: 0 });
  await userEvent.click(within(confirm).getByRole("button", { name: "Удалить" }));
  await waitFor(() => expect(api.deleteAllProfiles).toHaveBeenCalledWith(ep));
  expect(await screen.findByText("Удалено профилей: 2")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Удалить все профили…" })).toBeDisabled();
  expect(api.patchSettings).not.toHaveBeenCalled();
});

test("переключатель раздела «Модель PCM» — только при включённых профилях", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({ profiles: { enabled: true, pcm: true } });
  open();
  const pcm = await screen.findByRole("switch", { name: "Раздел «Модель PCM»" });
  expect(pcm).toHaveAttribute("aria-checked", "true");
  expect(screen.getByRole("button", { name: "Что такое раздел «Модель PCM»" })).toBeInTheDocument();
  await userEvent.click(pcm);
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { profiles: { pcm: false } }));
  await userEvent.click(screen.getByRole("switch", { name: "Составлять профили людей" }));
  expect(screen.queryByRole("switch", { name: "Раздел «Модель PCM»" })).toBeNull();
});
