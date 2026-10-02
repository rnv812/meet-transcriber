import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoicesPane } from "./VoicesPane";
import { NOTES_SAVE_MS, ProfileNotes, ProfileTab } from "./ProfileTab";
import * as api from "../../lib/api";
import type { Person, Profile, ProfileView } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getPerson: vi.fn(),
  getProfile: vi.fn(),
  makeProfile: vi.fn(),
  deleteProfile: vi.fn(),
  saveProfileNotes: vi.fn(),
  hideProfileStatement: vi.fn(),
}));

const ep = { base: "/api", token: null };
const people: Person[] = [
  { name: "Вера", samples: 3, meetings: 3, seconds: 1800, has_avatar: false, color: "#4b6bd6" },
];

/** Выдуманный профиль: три встречи, ссылки на реплики. */
export const PROFILE: Profile = {
  version: 1, person_id: "0123456789abcdef", name: "Вера", updated_at: Date.parse("2026-09-30T14:05:00") / 1000,
  meetings: 3, turns: 42, summary: "Предпочитает конкретику: цифры, сроки и владельцев задач.",
  summary_refs: [{ m: "2026-09-29_10-00", i: 12, t: 331 }],
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
  expect(onOpenAt).toHaveBeenCalledWith("2026-09-20_15-30", 7, 120, "Вера");
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
  expect(screen.getByText("3 реплики в 1 встрече — нужно от 5 реплик")).toBeInTheDocument();
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
    await act(async () => {});
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

// --- «Модель PCM» -------------------------------------------------------------------------

const PCM_PROFILE: Profile = {
  ...PROFILE,
  pcm: {
    base: { type: "thinker", confidence: 0.62, refs: [{ m: "2026-09-29_10-00", i: 12, t: 331 }] },
    phase: { type: "promoter", confidence: 0.4, refs: [{ m: "2026-09-20_15-30", i: 4, t: 65 }] },
    floors: { thinker: 5, persister: 2, harmonizer: 1, imaginer: 0, rebel: 2, promoter: 4 },
    perception: { value: "мысли", refs: [{ m: "2026-09-29_10-00", i: 30, t: 900 }] },
    channel: { value: "запрашивающий", examples: ["Какие данные у нас есть по срокам?"] },
    needs: { value: "признание за работу и время", how_to_recognize: "Отмечать точность расчётов." },
    stress_signs: [{ text: "Уходит в детали, когда сроки не ясны.", refs: [{ m: "2026-09-10_11-00", i: 2, t: 20 }] }],
    back_to_constructive: ["Предложить план с цифрами."],
    conversation: ["Начинать с цели и фактов.", "Просить решение с вариантами и сроком."],
  },
};

test("PCM: «этажи» — шесть строк, база внизу, фаза отмечена, текстовая замена", async () => {
  open({ ...ready, profile: PCM_PROFILE, pcm_enabled: true });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  const section = screen.getByRole("region", { name: "Модель PCM" });
  expect(within(section).getByText("Гипотеза по репликам во встречах, не сертифицированная оценка")).toBeInTheDocument();
  const chart = within(section).getByRole("img");
  const rows = [...chart.querySelectorAll<HTMLElement>(".pcm-floor")];
  // сверху вниз: слабые выше, база — последняя (нижняя)
  expect(rows.map((r) => r.dataset.type)).toEqual(["imaginer", "harmonizer", "rebel", "persister", "promoter", "thinker"]);
  expect(rows.at(-1)).toHaveClass("pcm-floor--base");
  expect(rows[4]).toHaveClass("pcm-floor--phase");
  expect(rows.at(-1)!.querySelectorAll(".pcm-floor__cell--on")).toHaveLength(5);
  expect(rows.at(-1)!.textContent).toContain("Логик (Thinker)");
  expect(chart).toHaveAttribute("aria-label", "Этажи модели PCM снизу вверх. 1: Логик (Thinker) — 5 из 5 (база); "
    + "2: Деятель (Promoter) — 4 из 5 (фаза); 3: Упорный (Persister) — 2 из 5; 4: Бунтарь (Rebel) — 2 из 5; "
    + "5: Гармонизатор (Harmonizer) — 1 из 5; 6: Мечтатель (Imaginer) — 0 из 5.");
  expect(within(section).getByText("уверенность 62 %")).toBeInTheDocument();
  expect(within(section).getByText("«Какие данные у нас есть по срокам?»")).toBeInTheDocument();
  expect(within(section).getByRole("region", { name: "Как давать признание" })).toHaveTextContent("Отмечать точность");
  expect(within(section).getByRole("region", { name: "Признаки напряжения" })).toHaveTextContent("Как вернуть в конструктив");
  expect(within(section).getByRole("region", { name: "Как строить разговор" })).toHaveTextContent("Начинать с цели");
  expect(within(section).getByText(/товарный знак Kahler Communications/)).toBeInTheDocument();
});

test("PCM: мало данных — понятная строка вместо «этажей»", async () => {
  open({ ...ready, profile: { ...PROFILE, reduced: true }, pcm_enabled: true, level: "reduced",
    pcm_note: "Недостаточно данных: 11 реплик в 2 встречах — нужно от 15 реплик в 3 встречах" });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  const section = screen.getByRole("region", { name: "Модель PCM" });
  expect(section).toHaveTextContent("Недостаточно данных: 11 реплик в 2 встречах — нужно от 15 реплик в 3 встречах");
  expect(within(section).queryByRole("img")).toBeNull();
});

test("PCM выключен в настройках — раздела нет и в заготовке его нет", async () => {
  const onAskAgent = vi.fn();
  open({ ...ready, profile: PCM_PROFILE, pcm_enabled: false }, { onAskAgent });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.queryByRole("region", { name: "Модель PCM" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Подготовиться к разговору/ }));
  expect(onAskAgent.mock.calls[0]![1]).not.toContain("PCM");
});

test("заготовка разговора с PCM: база, фаза, канал и как давать признание", async () => {
  const onAskAgent = vi.fn();
  open({ ...ready, profile: PCM_PROFILE, pcm_enabled: true }, { onAskAgent });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  fireEvent.click(screen.getByRole("button", { name: /Подготовиться к разговору/ }));
  const text = onAskAgent.mock.calls[0]![1] as string;
  expect(text).toContain("Гипотеза по модели PCM (не оценка): база — Логик (Thinker), фаза — Деятель (Promoter), "
    + "канал общения — запрашивающий. Как давать признание: Отмечать точность расчётов.");
  expect(text.endsWith("Тема разговора:")).toBe(true);
});

// --- fix round 1 ---------------------------------------------------------------------------

test("ссылка на изменившуюся реплику неактивна и никуда не ведёт", async () => {
  const onOpenAt = vi.fn();
  const stale: Profile = { ...PROFILE, sections: { style: [{ text: "Формулирует коротко.", refs: [
    { m: "2026-09-29_10-00", i: 12, t: 331, q: "Итог такой.", stale: true }] }] } };
  open({ ...ready, profile: stale }, { onOpenAt });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  const chip = screen.getByRole("button", { name: /^Реплика изменилась/ });
  expect(chip).toHaveAttribute("aria-disabled", "true");
  expect(chip.textContent).toContain("реплика изменилась");
  fireEvent.click(chip);
  expect(onOpenAt).not.toHaveBeenCalled();
});

test("«Скрыть» утверждение и «Показать снова»", async () => {
  vi.mocked(api.hideProfileStatement).mockResolvedValue({ hidden: 1 });
  open(ready);
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  const style = screen.getByRole("region", { name: "Стиль общения" });
  vi.mocked(api.getProfile).mockResolvedValue({ ...ready, hidden: 1 });
  fireEvent.click(within(style).getByRole("button", { name: "Скрыть утверждение" }));
  await waitFor(() => expect(api.hideProfileStatement).toHaveBeenCalledWith(
    ep, "Вера", "Формулирует коротко и начинает с вывода.", true));
  fireEvent.click(await screen.findByRole("button", { name: "Показать снова" }));
  await waitFor(() => expect(api.hideProfileStatement).toHaveBeenLastCalledWith(ep, "Вера", null, false));
});

test("проверка утверждений не завершена — пометка и «Повторить»", async () => {
  open({ ...ready, profile: { ...PROFILE, review: { checked: false, blocked: 0, error: "таймаут" } } });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.getByText(/Проверка утверждений не завершена/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
});

test("профиль без опоры не составился — честная строка", async () => {
  open({ ...ready, profile: null, state: "failed",
    error: "Не удалось составить профиль с опорой на реплики — попробуйте позже или после новых встреч" });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.getByText(/с опорой на реплики — попробуйте позже/)).toBeInTheDocument();
});

test("пока считаются реплики — «Подсчитываю реплики…», затем обычный вид", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    open({ ...ready, profile: null, state: "none", indexing: true, stats: null, level: null });
    fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
    expect(screen.getByText("Подсчитываю реплики…")).toBeInTheDocument();
    vi.mocked(api.getProfile).mockResolvedValue({ ...ready, profile: null, state: "none", level: "full" });
    await act(async () => { vi.advanceTimersByTime(2100); });
    expect(await screen.findByRole("button", { name: /Составить профиль/ })).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

test("«Мои заметки» не пересоздаются, когда появляется профиль", async () => {
  const props = { endpoint: ep, name: "Вера", reload: async () => {}, onOpenAt: () => {}, onAskAgent: () => {} };
  const { rerender } = render(
    <ProfileTab {...props} view={{ ...ready, profile: null, state: "running", level: "full" }} />);
  const box = screen.getByRole("textbox", { name: "Мои заметки" });
  fireEvent.change(box, { target: { value: "Набираю мысль" } });
  rerender(<ProfileTab {...props} view={ready} />);
  expect(screen.getByRole("textbox", { name: "Мои заметки" })).toBe(box);
  expect(box).toHaveValue("Набираю мысль");
});

test("удаление профиля в паузе перед сохранением заметок — заметки не досохраняются", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    vi.mocked(api.deleteProfile).mockResolvedValue({ ok: true });
    open(ready);
    fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Мои заметки" }), { target: { value: "Черновик" } });
    fireEvent.click(screen.getByRole("button", { name: "Ещё действия с профилем" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Удалить профиль/ }));
    fireEvent.click(within(screen.getByRole("alertdialog", { name: "Удалить профиль" }))
      .getByRole("button", { name: "Удалить" }));
    await act(async () => { vi.advanceTimersByTime(NOTES_SAVE_MS * 2); });
    await waitFor(() => expect(api.deleteProfile).toHaveBeenCalled());
    expect(api.saveProfileNotes).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});

test("кнопка подготовки — с именем; без общей встречи — в последнюю встречу", async () => {
  const onAskAgent = vi.fn();
  open({ ...ready, latest_meeting: null, latest_any: "2026-10-01_09-00" }, { onAskAgent });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  fireEvent.click(screen.getByRole("button", { name: "Подготовиться к разговору: Вера" }));
  const [meeting, text] = onAskAgent.mock.calls[0]!;
  expect(meeting).toBe("2026-10-01_09-00");
  expect(text).not.toContain("материалы этой встречи");
});

test("вкладки человека связаны с панелью", async () => {
  open(ready);
  const tab = await screen.findByRole("tab", { name: "Профиль" });
  expect(tab).toHaveAttribute("aria-controls", "ptab-panel");
  expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "ptab-voice");
});

// --- fix round 2 ---------------------------------------------------------------------------

test("«Коротко» без своей опоры не показывается", async () => {
  open({ ...ready, profile: { ...PROFILE, summary_refs: [] } });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.queryByText("Коротко:")).toBeNull();
});

test("непроверенное обновление — показан прежний профиль и «Повторить»", async () => {
  open({ ...ready, kept_previous: "таймаут проверки" });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.getByText("Проверка не завершена — показан прежний профиль")).toHaveAttribute("title", "таймаут проверки");
  expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
});

test("после «Удалить профиль» поле заметок пустое и старое не досохраняется", async () => {
  vi.mocked(api.deleteProfile).mockResolvedValue({ ok: true });
  open({ ...ready, notes: "Старые заметки" });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  expect(screen.getByRole("textbox", { name: "Мои заметки" })).toHaveValue("Старые заметки");
  vi.mocked(api.getProfile).mockResolvedValue({ ...ready, profile: null, state: "none", notes: "" });
  fireEvent.click(screen.getByRole("button", { name: "Ещё действия с профилем" }));
  fireEvent.click(screen.getByRole("menuitem", { name: /Удалить профиль/ }));
  fireEvent.click(within(screen.getByRole("alertdialog", { name: "Удалить профиль" }))
    .getByRole("button", { name: "Удалить" }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Мои заметки" })).toHaveValue(""));
  expect(api.saveProfileNotes).not.toHaveBeenCalled();
});

test("гипотеза PCM без живой опоры не показывается", async () => {
  const pcm = { ...PCM_PROFILE.pcm!, base: { ...PCM_PROFILE.pcm!.base, refs: [
    { m: "2026-09-29_10-00", i: 12, t: 331, stale: true }] } };
  open({ ...ready, profile: { ...PCM_PROFILE, pcm }, pcm_enabled: true });
  fireEvent.click(await screen.findByRole("tab", { name: "Профиль" }));
  const section = screen.getByRole("region", { name: "Модель PCM" });
  expect(within(section).queryByRole("img")).toBeNull();
  expect(section).toHaveTextContent("Гипотеза появится после обновления профиля.");
});
