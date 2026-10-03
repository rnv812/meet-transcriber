/**
 * Строка «Профили людей убраны» в «Голосах» (0.3.2): одна, до «Понятно»;
 * «Открыть папку» — только когда заметки перенесены в файл.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { Person } from "../../lib/types";
import { VoicesPane } from "./VoicesPane";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getPerson: vi.fn(),
  getProfilesRemoved: vi.fn(),
  dismissProfilesRemoved: vi.fn(async () => ({ ok: true })),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: () => true,
  openFolder: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const people: Person[] = [{ name: "Демьян", samples: 3, meetings: 3, seconds: 1740, has_avatar: false, color: "#4b6bd6" }];
const noop = () => {};
const setup = (p = people) =>
  render(<VoicesPane endpoint={ep} people={p} avatarVersion={{}} onAvatar={noop} onChanged={noop} onOpenRecording={noop} />);
const NOTES = "D:/data/meet/Заметки о людях (из профилей).md";

beforeEach(() => vi.clearAllMocks());

test("заметки перенесены: путь, «Открыть папку» — папка данных, «Понятно» снимает строку", async () => {
  vi.mocked(api.getProfilesRemoved).mockResolvedValue({ notice: { notes: NOTES, folder: "D:/data/meet" } });
  setup();
  const line = await screen.findByRole("status");
  expect(line).toHaveTextContent("Профили людей убраны из Meet; сгенерированные профили удалены.");
  expect(line).toHaveTextContent(`Ваши заметки сохранены в ${NOTES}`);
  await userEvent.click(screen.getByRole("button", { name: "Открыть папку" }));
  expect(shell.openFolder).toHaveBeenCalledWith("D:/data/meet");
  await userEvent.click(screen.getByRole("button", { name: "Понятно" }));
  expect(api.dismissProfilesRemoved).toHaveBeenCalledWith(ep);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  // Голоса на месте.
  expect(screen.getByText("3 встречи · 29 мин речи")).toBeInTheDocument();
});

test("заметок не было: без пути и без «Открыть папку»; видно и при пустой базе голосов", async () => {
  vi.mocked(api.getProfilesRemoved).mockResolvedValue({ notice: { notes: null, folder: "D:/data" } });
  setup([]);
  const line = await screen.findByRole("status");
  expect(line).toHaveTextContent("Профили людей убраны из Meet; сгенерированные профили удалены.");
  expect(line).not.toHaveTextContent("заметки");
  expect(screen.queryByRole("button", { name: "Открыть папку" })).not.toBeInTheDocument();
  expect(screen.getByText("База голосов пуста")).toBeInTheDocument();
});

test("отметки нет (или старый резидент без этого API) — строки нет", async () => {
  vi.mocked(api.getProfilesRemoved).mockRejectedValue(new Error("404"));
  setup();
  await waitFor(() => expect(api.getProfilesRemoved).toHaveBeenCalled());
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  vi.mocked(api.getProfilesRemoved).mockResolvedValue({ notice: null });
  setup();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});
