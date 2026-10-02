import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoicesPane } from "./VoicesPane";
import * as api from "../../lib/api";
import { usePeople } from "../../state/usePeople";
import { CardHeader } from "../card/CardHeader";
import type { Person } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getPeople: vi.fn(),
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

const noop = () => {};
const setup = (p = people, extra: Partial<Parameters<typeof VoicesPane>[0]> = {}) =>
  render(
    <VoicesPane endpoint={ep} people={p} avatarVersion={{}} onAvatar={noop} onChanged={noop}
      onOpenRecording={noop} {...extra} />,
  );

const rec = {
  id: "r1", path: "p", started_at: null, duration_s: 60, tracks: {}, has_transcript: true,
  has_voices: false, title: "Встреча", source: "record",
};

/** Как App: одна usePeople на окно, аватар-версия уходит и в «Голоса», и в карточку записи. */
function Harness() {
  const { people: list, refresh, avatarVersion, bumpAvatar } = usePeople(ep);
  return (
    <>
      <VoicesPane endpoint={ep} people={list} avatarVersion={avatarVersion} onChanged={noop}
        onAvatar={(n) => { bumpAvatar(n); void refresh(); }} onOpenRecording={noop} />
      <div data-testid="rec">
        <CardHeader rec={rec} speakers={["Демьян"]} people={list} endpoint={ep} avatarVersion={avatarVersion}
          onRename={noop} />
      </div>
    </>
  );
}

test("сетка людей с заголовком и сводкой", () => {
  setup(people.slice(0, 2));
  expect(screen.getByText("2 человека · узнаются автоматически")).toBeInTheDocument();
  expect(screen.getByText("2 встречи · 20 мин речи")).toBeInTheDocument();
  expect(screen.getByText("1 встреча · 4 мин речи")).toBeInTheDocument();
});

test("пустое состояние", () => {
  setup([]);
  expect(screen.getByText("База голосов пуста"))
    .toBeInTheDocument();
});

test("клик по человеку открывает карточку со встречами; встреча открывает запись", async () => {
  const onOpenRecording = vi.fn();
  setup(people, { onOpenRecording });
  await userEvent.click(screen.getByText("Демьян"));
  await userEvent.click(await screen.findByText("Планёрка"));
  expect(onOpenRecording).toHaveBeenCalledWith("r1");
});

test("загрузка файла: putAvatar, refresh, аватар везде с картинкой и новой версией", async () => {
  vi.mocked(api.putAvatar).mockResolvedValue();
  vi.mocked(api.getPeople)
    .mockResolvedValueOnce({ items: people })
    .mockResolvedValue({ items: people.map((p) => (p.name === "Демьян" ? { ...p, has_avatar: true } : p)) });
  render(<Harness />);
  await userEvent.click(await screen.findByText("Демьян", { selector: ".person__name" }));
  const before = screen.getByTestId("rec").querySelector("img");
  expect(before).toBeNull();
  const file = new File(["x"], "a.png", { type: "image/png" });
  await userEvent.upload(await screen.findByTestId("avatar-file"), file);
  await waitFor(() => expect(api.putAvatar).toHaveBeenCalledWith(ep, "Демьян", file));
  await waitFor(() => expect(screen.getByTestId("rec").querySelector("img")).not.toBeNull());
  expect(api.getPeople).toHaveBeenCalledTimes(2);
  const src = screen.getByTestId("rec").querySelector("img")!.getAttribute("src")!;
  expect(src).toMatch(/\/avatar\?v=\d{5,}$/);
  expect(document.querySelector(".pcard img")!.getAttribute("src")).toBe(src);
});

test("слишком большой файл не отправляется", async () => {
  setup();
  await userEvent.click(screen.getByText("Демьян"));
  const big = new File(["x"], "big.png", { type: "image/png" });
  Object.defineProperty(big, "size", { value: 11 * 1024 * 1024 });
  await userEvent.upload(await screen.findByTestId("avatar-file"), big);
  expect(await screen.findByText("Файл больше 10 МБ")).toBeInTheDocument();
  expect(api.putAvatar).not.toHaveBeenCalled();
});

test("сброс к инициалам: deleteAvatar и onAvatar", async () => {
  vi.mocked(api.deleteAvatar).mockResolvedValue({ ok: true });
  const onAvatar = vi.fn();
  setup([{ ...people[0]!, has_avatar: true }, ...people.slice(1)], { onAvatar });
  await userEvent.click(screen.getByText("Демьян"));
  await userEvent.click(await screen.findByRole("button", { name: "Аватар" }));
  await userEvent.click(screen.getByRole("button", { name: "Сбросить к инициалам" }));
  // Сначала подтверждение: фотография пропадёт.
  expect(api.deleteAvatar).not.toHaveBeenCalled();
  await userEvent.click(within(screen.getByRole("alertdialog", { name: "Убрать фотографию?" }))
    .getByRole("button", { name: "Убрать" }));
  await waitFor(() => expect(api.deleteAvatar).toHaveBeenCalledWith(ep, "Демьян"));
  expect(onAvatar).toHaveBeenCalledWith("Демьян");
});

test("Escape в имени отменяет переименование", async () => {
  setup();
  await userEvent.click(screen.getByText("Демьян"));
  const input = await screen.findByLabelText("Имя");
  await userEvent.clear(input);
  await userEvent.type(input, "Пётр{Escape}");
  expect(api.renamePerson).not.toHaveBeenCalled();
  expect(input).toHaveValue("Демьян");
});

test("карточка получает фокус для Ctrl+V", async () => {
  setup();
  await userEvent.click(screen.getByText("Демьян"));
  await waitFor(() => expect(document.querySelector(".pcard")).toHaveFocus());
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
  expect(screen.getByRole("alertdialog")).toHaveAccessibleName(/^Объединить «.+» с «Аркадий»\?$/);
  expect(api.mergePerson).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: /^(Объединить|Удалить)$/ }));
  await waitFor(() => expect(api.mergePerson).toHaveBeenCalledWith(ep, "Аркаша", "Аркадий"));
  expect(onChanged).toHaveBeenCalled();
});

test("удаление: подтверждение, deletePerson, карточка закрыта", async () => {
  vi.mocked(api.deletePerson).mockResolvedValue({});
  setup();
  await userEvent.click(screen.getByText("Демьян"));
  await userEvent.click(await screen.findByRole("button", { name: "Удалить голос" }));
  await userEvent.click(screen.getByRole("button", { name: /^(Объединить|Удалить)$/ }));
  await waitFor(() => expect(api.deletePerson).toHaveBeenCalledWith(ep, "Демьян"));
  expect(document.querySelector(".pcard")).toBeNull();
});

test("переименование: ошибка дубликата текстом", async () => {
  vi.mocked(api.renamePerson).mockRejectedValue(new api.ApiError(400, "человек с таким именем уже есть"));
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
  await userEvent.click(await screen.findByRole("button", { name: "Прослушать образец" }));
  await waitFor(() => expect(document.querySelector("audio")!.getAttribute("src")).toMatch(/track=sys#t=5,9$/));
});
