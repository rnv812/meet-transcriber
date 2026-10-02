import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoicesPane } from "./VoicesPane";
import { NOTES_SAVE_MS, ProfileNotes } from "./ProfileTab";
import * as api from "../../lib/api";
import type { Person, Profile, ProfileView } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getPerson: vi.fn(),
  getProfile: vi.fn(),
  makeProfile: vi.fn(),
  deleteProfile: vi.fn(),
  saveProfileNotes: vi.fn(),
}));

const ep = { base: "/api", token: null };
const people: Person[] = [
  { name: "Вера", samples: 3, meetings: 3, seconds: 1800, has_avatar: false, color: "#4b6bd6" },
];

/** Выдуманный профиль: три встречи, ссылки на реплики. */
export const PROFILE: Profile = {
  version: 1, person_id: "0123456789abcdef", name: "Вера", updated_at: Date.parse("2026-09-30T14:05:00") / 1000,
  meetings: 3, turns: 42, summary: "Предпочитает конкретику: цифры, сроки и владельцев задач.",
  sections: {
    style: [{ text: "Формулирует коротко и начинает с вывода.", refs: [
      { m: "2026-09-29_10-00", i: 12, t: 331, q: "Итог такой: релиз в пятницу." },
      { m: "2026-09-20_15-30", i: 4, t: 65 }] }],
    values: [{ text: "Ясные сроки и ответственные.", refs: [{ m: "2026-09-29_10-00", i: 30, t: 900 }] }],
    how_to_talk: [{ text: "Приходить с вариантами и цифрами.", refs: [{ m: "2026-09-20_15-30", i: 7, t: 120 }] }],
    avoid: [{ text: "Обсуждать без повестки.", refs: [{ m: "2026-09-10_11-00", i: 2, t: 20 }] }],
    topics: [],
  },
  sources: {
    "2026-09-29_10-00": { title: "Планирование релиза", date: "2026-09-29" },
    "2026-09-20_15-30": { title: "Ретро спринта", date: "2026-09-20" },
    "2026-09-10_11-00": { title: "Знакомство с командой", date: "2026-09-10" },
  },
};

const ready: ProfileView = {
  enabled: true, name: "Вера", self: false, stats: { turns: 42, meetings: 3 }, level: "full", state: "ready",
  profile: PROFILE, notes: "", latest_meeting: "2026-09-29_10-00", has_new: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getPerson).mockImplementation(async (_e, name) => ({
    name, color: "#000", has_avatar: false, samples: 1, meetings: [],
  }));
  vi.mocked(api.saveProfileNotes).mockImplementation(async (_e, _n, text) => ({ notes: text }));
});

function open(view: ProfileView, extra: Partial<Parameters<typeof VoicesPane>[0]> = {}) {
  vi.mocked(api.getProfile).mockResolvedValue(view);
  const utils = render(
    <VoicesPane endpoint={ep} people={people} avatarVersion={{}} onAvatar={() => {}} onChanged={() => {}}
      onOpenRecording={() => {}} {...extra} />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Вера/ }));
  return utils;
}

test("профили выключены — вкладки «Профиль» нет", async () => {
  open({ enabled: false });
  await waitFor(() => expect(api.getProfile).toHaveBeenCalledWith(ep, "Вера"));
  expect(screen.queryByRole("tab", { name: "Профиль" })).toBeNull();
  expect(screen.getByRole("button", { name: /Прослушать образец/ })).toBeInTheDocument();
});

test("страница профиля: шапка, «Коротко», карточки разделов, ссылки", async () => {
  const { container } = open(ready);
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.getByRole("tab", { name: "Профиль" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText(/по 3 встречам · обновлено/)).toBeInTheDocument();
  expect(screen.getByText("Коротко:")).toBeInTheDocument();
  expect(screen.getByText(PROFILE.summary)).toBeInTheDocument();
  const cards = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label") ?? r.textContent?.slice(0, 12));
  expect(cards.slice(0, 4)).toEqual(["Стиль общения", "Что для человека важно", "Как лучше строить разговор",
    "Чего избегать"]);
  expect(screen.queryByRole("region", { name: "Типичные темы" })).toBeNull(); // пустой раздел не показан
  const style = screen.getByRole("region", { name: "Стиль общения" });
  const chips = within(style).getAllByRole("button", { name: /^Открыть реплику/ });
  expect(chips.map((c) => c.textContent)).toEqual(["Планирование релиза · 05:31", "Ретро спринта · 01:05"]);
  expect(chips[0]).toHaveAttribute("title", "«Итог такой: релиз в пятницу.»");
  expect(container.querySelector(".voices__card--wide")).not.toBeNull();
  expect(screen.getByText(/не оценка личности/)).toBeInTheDocument();
});

test("ссылка открывает встречу на реплике", async () => {
  const onOpenAt = vi.fn();
  open(ready, { onOpenAt });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  fireEvent.click(screen.getByRole("button", { name: "Открыть реплику: Ретро спринта · 02:00" }));
  expect(onOpenAt).toHaveBeenCalledWith("2026-09-20_15-30", 7);
});

test("«Подготовиться к разговору» и «Обсудить с агентом» — текст во «Агент» общей встречи", async () => {
  const onAskAgent = vi.fn();
  open(ready, { onAskAgent });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  fireEvent.click(screen.getByRole("button", { name: /Подготовиться к разговору/ }));
  const [meeting, text] = onAskAgent.mock.calls[0]!;
  expect(meeting).toBe("2026-09-29_10-00");
  expect(text).toContain("Помоги подготовиться к разговору с человеком «Вера».");
  expect(text).toContain("Коротко: Предпочитает конкретику");
  expect(text).toContain("Как лучше строить разговор: Приходить с вариантами и цифрами.");
  expect(text).toContain("Чего избегать: Обсуждать без повестки.");
  expect(text.endsWith("Тема разговора:")).toBe(true);
  expect(text).not.toMatch(/[\r\n]/);
  fireEvent.click(screen.getByRole("button", { name: /Обсудить с агентом/ }));
  expect(onAskAgent.mock.calls[1]![1]).toContain("[Планирование релиза · 05:31; Ретро спринта · 01:05]");
});

test("недостаточно данных — понятное состояние без кнопки", async () => {
  open({ ...ready, profile: null, state: "none", level: "none", stats: { turns: 3, meetings: 1 },
    note: "Недостаточно данных: 3 реплики в 1 встрече — нужно от 5 реплик" });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.getByText("Недостаточно данных")).toBeInTheDocument();
  expect(screen.getByText("Недостаточно данных: 3 реплики в 1 встрече — нужно от 5 реплик")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Составить профиль/ })).toBeNull();
});

test("профиля ещё нет — «Составить профиль» ставит задачу, идёт — видно", async () => {
  vi.mocked(api.makeProfile).mockResolvedValue({ id: "p1", kind: "profile", folder: "x", state: "queued" } as never);
  open({ ...ready, profile: null, state: "none", level: "reduced", stats: { turns: 8, meetings: 2 } });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.getByText(/Есть 8 реплик в 2 встречах\. Реплик пока немного/)).toBeInTheDocument();
  vi.mocked(api.getProfile).mockResolvedValue({ ...ready, profile: null, state: "running", level: "reduced" });
  fireEvent.click(screen.getByRole("button", { name: /Составить профиль/ }));
  await waitFor(() => expect(api.makeProfile).toHaveBeenCalledWith(ep, "Вера"));
  expect(await screen.findByText("Профиль составляется…")).toBeInTheDocument();
});

test("не удалось — «Повторить»; удаление профиля через меню с подтверждением", async () => {
  vi.mocked(api.deleteProfile).mockResolvedValue({ ok: true });
  open({ ...ready, state: "failed", error: "таймаут" });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.getByText("Не удалось составить профиль")).toHaveAttribute("title", "таймаут");
  expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ещё действия с профилем" }));
  fireEvent.click(screen.getByRole("menuitem", { name: /Удалить профиль/ }));
  const dialog = screen.getByRole("alertdialog", { name: "Удалить профиль" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Удалить" }));
  await waitFor(() => expect(api.deleteProfile).toHaveBeenCalledWith(ep, "Вера"));
});

test("«Мои заметки» сохраняются сами после паузы и при уходе", async () => {
  vi.useFakeTimers();
  try {
    const { unmount } = render(<ProfileNotes endpoint={ep} name="Вера" initial="" />);
    const box = screen.getByRole("textbox", { name: "Мои заметки" });
    fireEvent.change(box, { target: { value: "Любит письменные итоги" } });
    fireEvent.change(box, { target: { value: "Любит письменные итоги." } });
    expect(api.saveProfileNotes).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(NOTES_SAVE_MS + 10); });
    expect(api.saveProfileNotes).toHaveBeenCalledTimes(1);
    expect(api.saveProfileNotes).toHaveBeenLastCalledWith(ep, "Вера", "Любит письменные итоги.");
    expect(screen.getByText("Сохранено")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "Ещё мысль" } });
    unmount();
    expect(api.saveProfileNotes).toHaveBeenLastCalledWith(ep, "Вера", "Ещё мысль");
  } finally {
    vi.useRealTimers();
  }
});

test("«Вы» — пометка, что профиль обновляется только вручную; новые реплики — подсказка", async () => {
  open({ ...ready, self: true, has_new: true });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.getByText("Это вы: ваш профиль обновляется только вручную.")).toBeInTheDocument();
  expect(screen.getByText("Есть новые реплики — профиль можно обновить.")).toBeInTheDocument();
});
