import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoicesPane } from "./VoicesPane";
import * as api from "../../lib/api";
import type { Person } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getPerson: vi.fn(),
  getSample: vi.fn(),
  putAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  renamePerson: vi.fn(),
  mergePerson: vi.fn(),
  deletePerson: vi.fn(),
}));

const ep = { base: "/api", token: null };
const people: Person[] = [
  { name: "Демьян", samples: 3, meetings: 2, seconds: 1200, has_avatar: false, color: "#4b6bd6" },
  { name: "Аркаша", samples: 1, meetings: 1, seconds: 240, has_avatar: false, color: "#3a9a6a" },
  { name: "Аркадий", samples: 1, meetings: 1, seconds: 600, has_avatar: false, color: "#c0793a" },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getPerson).mockImplementation(async (_e, name) => ({
    name, color: "#000", has_avatar: false, samples: 1,
    meetings: [{ recording: "r1", title: "Планёрка", started_at: "2026-09-30T10:00:00", seconds: 600 }],
  }));
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.load = vi.fn();
});

const setup = (p = people, extra: Partial<Parameters<typeof VoicesPane>[0]> = {}) =>
  render(<VoicesPane endpoint={ep} people={p} onChanged={() => {}} onOpenRecording={() => {}} {...extra} />);

test("сетка людей с заголовком и сводкой", () => {
  setup(people.slice(0, 2));
  expect(screen.getByText("2 человека · узнаются автоматически")).toBeInTheDocument();
  expect(screen.getByText("2 встречи · 20 мин речи")).toBeInTheDocument();
  expect(screen.getByText("1 встреча · 4 мин речи")).toBeInTheDocument();
});

test("пустое состояние", () => {
  setup([]);
  expect(screen.getByText("Пока никого. Назовите спикеров в карточке записи — голоса запомнятся здесь."))
    .toBeInTheDocument();
});

test("клик по человеку открывает карточку со встречами; встреча открывает запись", async () => {
  const onOpenRecording = vi.fn();
  setup(people, { onOpenRecording });
  await userEvent.click(screen.getByText("Демьян"));
  await userEvent.click(await screen.findByText("Планёрка"));
  expect(onOpenRecording).toHaveBeenCalledWith("r1");
});

test("загрузка файла: putAvatar, затем аватар с картинкой и новой версией", async () => {
  vi.mocked(api.putAvatar).mockResolvedValue();
  const { container } = setup();
  await userEvent.click(screen.getByText("Демьян"));
  const file = new File(["x"], "a.png", { type: "image/png" });
  await userEvent.upload(await screen.findByTestId("avatar-file"), file);
  await waitFor(() => expect(api.putAvatar).toHaveBeenCalledWith(ep, "Демьян", file));
  await waitFor(() => expect(container.querySelector(".pcard img")).not.toBeNull());
  expect(container.querySelector<HTMLImageElement>(".pcard img")!.src).toMatch(/voices\/%D0.*\/avatar\?v=\d+$/);
  expect(container.querySelector(".person img")).not.toBeNull();
});

test("ошибка загрузки: текст виден", async () => {
  vi.mocked(api.putAvatar).mockRejectedValue(new api.ApiError(400, "не изображение"));
  setup();
  await userEvent.click(screen.getByText("Демьян"));
  await userEvent.upload(await screen.findByTestId("avatar-file"), new File(["x"], "a.txt", { type: "image/png" }));
  expect(await screen.findByText("не изображение")).toBeInTheDocument();
});

test("вставка изображения из буфера", async () => {
  vi.mocked(api.putAvatar).mockResolvedValue();
  setup();
  await userEvent.click(screen.getByText("Демьян"));
  const card = document.querySelector(".pcard")!;
  const file = new File(["x"], "s.png", { type: "image/png" });
  const ev = new Event("paste", { bubbles: true, cancelable: true }) as Event & { clipboardData: unknown };
  ev.clipboardData = { files: [file], items: [] };
  card.dispatchEvent(ev);
  await waitFor(() => expect(api.putAvatar).toHaveBeenCalledWith(ep, "Демьян", file));
});

test("объединение: выбор, подтверждение, mergePerson", async () => {
  vi.mocked(api.mergePerson).mockResolvedValue({});
  const onChanged = vi.fn();
  setup(people, { onChanged });
  await userEvent.click(screen.getByText("Аркаша"));
  await userEvent.selectOptions(await screen.findByLabelText("Объединить с…"), "Аркадий");
  expect(api.mergePerson).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Да" }));
  await waitFor(() => expect(api.mergePerson).toHaveBeenCalledWith(ep, "Аркаша", "Аркадий"));
  expect(onChanged).toHaveBeenCalled();
});

test("удаление: подтверждение, deletePerson, карточка закрыта", async () => {
  vi.mocked(api.deletePerson).mockResolvedValue({});
  setup();
  await userEvent.click(screen.getByText("Демьян"));
  await userEvent.click(await screen.findByRole("button", { name: "Удалить голос" }));
  await userEvent.click(screen.getByRole("button", { name: "Да" }));
  await waitFor(() => expect(api.deletePerson).toHaveBeenCalledWith(ep, "Демьян"));
  expect(document.querySelector(".pcard")).toBeNull();
});

test("переименование: ошибка дубликата текстом", async () => {
  vi.mocked(api.renamePerson).mockRejectedValue(new api.ApiError(409, "человек с таким именем уже есть"));
  setup();
  await userEvent.click(screen.getByText("Демьян"));
  const input = await screen.findByLabelText("Имя");
  await userEvent.clear(input);
  await userEvent.type(input, "Аркаша{Enter}");
  expect(await screen.findByText("человек с таким именем уже есть")).toBeInTheDocument();
});

test("образец: getSample и src с #t=start,end", async () => {
  vi.mocked(api.getSample).mockResolvedValue({ recording: "r1", start: 5, end: 9, track: "sys" });
  setup();
  await userEvent.click(screen.getByText("Демьян"));
  await userEvent.click(await screen.findByRole("button", { name: "▶ Прослушать образец" }));
  await waitFor(() => expect(document.querySelector("audio")!.getAttribute("src")).toMatch(/track=sys#t=5,9$/));
});
