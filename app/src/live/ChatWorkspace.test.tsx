import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  setAgentFrequency: vi.fn(async () => ({ frequency: "less", label: "реже", live: true })),
  setAgentProfile: vi.fn(async () => ({ profile: "personal", label: "Личный", live: true })),
  pasteChatImage: vi.fn(async () => ({ id: "a1", status: "ready", attachment: { id: "a1", kind: "attachment" } })),
  postChat: vi.fn(async () => ({ id: "m9", queued: false, attachments: [] })),
  newChatClientId: vi.fn(() => "c1"),
}));
import { TermsNotice } from "../features/legal/TermsNotice";
import { postChat, setAgentFrequency, setAgentProfile } from "../lib/api";
import { TERMS_NEEDED } from "../lib/terms";
import type { ChatSnapshot, LiveHint } from "../lib/types";
import { agentInfo, agentMsg } from "../test/chatFixtures";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  CHAT_COMPACT_PX, CHAT_FLOOR, CHAT_MIN, CHAT_PANES, CHAT_SIDE_MIN, TRANSCRIPT_FLOOR, TRANSCRIPT_PX, chatReserve,
} from "./ChatWorkspace";
import { LiveWorkspace, PARTICIPANT_KEY, useLiveView } from "./LiveWorkspace";
import { EMPTY_SUMMARY } from "./liveModel";
import { type Chat, useChat } from "./useChat";
import type { Live } from "./useLive";

const ep = { base: "http://h", token: "t" };
const lines = [
  { t: 60, speaker: "Ольга", text: "начнём с миграции", id: 0 },
  { t: 125, speaker: "Игорь", text: "миграцию сделаем к пятнице", id: 1 },
];
const hint: LiveHint = {
  id: "h1", kind: "risk", text: "У миграции нет ответственного", why: "", source_t: 125, ref: null, pinned: false,
  dismissed: false, created_at: 1, updated_at: 1,
};
function makeLive(o: Partial<Live> = {}): Live {
  return {
    status: null, lines, digest: "", summary: { ...EMPTY_SUMMARY, topic: "Миграция" }, hints: [hint], hintsEnabled: true,
    quietDefault: false, catchup: null, qa: [], loaded: true, error: null, asking: false, askError: null, hintError: null,
    agent: null, ask: vi.fn(async () => {}), hint: vi.fn(async () => {}), setTask: vi.fn(async () => {}), ...o,
  };
}
let chat: Chat;
function Host({ live, wide = false, open = true }: { live: Live; wide?: boolean; open?: boolean }) {
  chat = useChat(ep);
  const view = useLiveView(live, { open: true, wide, quiet: false });
  // `open` — как у панели: свёрнутая панель снимает рабочую область, `view` и чат остаются у владельца.
  return open ? <LiveWorkspace live={live} view={view} onAsk={() => {}} chat={chat} /> : <p>свёрнута</p>;
}
const load = () => act(() => chat.sink.onChatSnapshot({
  messages: [agentMsg("m1", { text: "Срок другой: [02:05]" })], seq: 3, agent: agentInfo(),
} as ChatSnapshot));

/** Ширина элементов в jsdom — 0: задать её, чтобы раскладка не была компактной. */
function width(px: number) {
  return vi.spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockReturnValue({ width: px, height: 600, top: 0, left: 0, right: px, bottom: 600, x: 0, y: 0, toJSON: () => ({}) });
}
afterEach(() => vi.restoreAllMocks());

function TermsHost({ live }: { live: Live }) {
  chat = useChat(ep);
  const view = useLiveView(live, { open: true, wide: false, quiet: false });
  return <LiveWorkspace live={live} view={view} onAsk={() => {}} chat={chat}
    notice={<TermsNotice id="live-terms" onOpen={openMeet} />} />;
}
const openMeet = vi.fn();

test("условия не приняты — на месте строки ввода пометка с «Открыть Meet», писать нельзя", async () => {
  width(600);
  render(<TermsHost live={makeLive({ agent: agentInfo() })} />);
  load();
  expect(screen.queryByRole("combobox", { name: "Сообщение ассистенту" })).toBeNull();
  const notice = screen.getByText(TERMS_NEEDED).closest<HTMLElement>(".terms-notice")!;
  expect(notice).toHaveAttribute("role", "status");
  expect(notice.closest(".chat-dock")).not.toBeNull();
  await userEvent.click(within(notice).getByRole("button", { name: "Открыть Meet" }));
  expect(openMeet).toHaveBeenCalledTimes(1);
});

test("условия не приняты, прежний режим — пометка вместо «Спросить»", async () => {
  render(<TermsHost live={makeLive()} />);
  await userEvent.click(screen.getByRole("tab", { name: "Спросить" }));
  expect(screen.queryByRole("textbox", { name: "Вопрос ассистенту" })).toBeNull();
  expect(screen.getByText(TERMS_NEEDED)).toBeInTheDocument();
});

test("участник выключен — прежняя раскладка без изменений (вкладки, подсказки)", () => {
  render(<Host live={makeLive()} />);
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Лента", "Сводка", "Подсказки", "Спросить"]);
  expect(screen.queryByRole("log", { name: "Чат с ассистентом" })).toBeNull();
});

test("участник включён — чат вместо «Подсказок»/«Спросить»/«Сводки»; шапка сессии", () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.queryByText("У миграции нет ответственного")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Вопрос ассистенту" })).toBeNull();
  expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toHaveTextContent("Срок другой");
  expect(screen.getByRole("group", { name: "Сессия ассистента" })).toHaveTextContent("Claude Code");
  expect(screen.getByRole("region", { name: "Расшифровка" })).toHaveTextContent("начнём с миграции");
  expect(screen.getByRole("separator", { name: "Ширина расшифровки" })).toBeInTheDocument();
});

test("таймкод в сообщении — к моменту в расшифровке", async () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  await userEvent.click(screen.getByRole("button", { name: "02:05" }));
  const target = screen.getByText("миграцию сделаем к пятнице").closest("li")!;
  expect(target).toHaveClass("is-target");
});

test("узкая панель (уже 560): по умолчанию только чат во всю ширину; расшифровка — кнопкой в строке сессии", () => {
  width(360);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  expect(TRANSCRIPT_PX).toBe(560);
  expect(screen.queryByRole("region", { name: "Расшифровка" })).toBeNull();
  expect(screen.queryByRole("separator", { name: "Ширина расшифровки" })).toBeNull();
  expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toBeVisible();
  const show = screen.getByRole("button", { name: "Показать расшифровку" });
  expect(show).toHaveAttribute("aria-expanded", "false");
  // Кнопка Aurora 32 px в строке сессии, а не самодельный кружок на разделителе.
  expect(show).toHaveClass("btn", "btn--sm", "btn--icon");
  expect(show.closest(".chat-ws__bar")).not.toBeNull();
  expect(screen.queryByText(/видит:/)).toBeNull();
});

test("строка ввода — в доке под чатом и расшифровкой, вне колонок", () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  const field = screen.getByRole("combobox", { name: "Сообщение ассистенту" });
  const dock = field.closest(".chat-dock")!;
  expect(dock).not.toBeNull();
  expect(dock.parentElement).toHaveClass("chat-ws");
  expect(dock.closest(".chat-ws__body")).toBeNull();
});

test("«Как часто писать» уходит setAgentFrequency и сразу видно выбранное", async () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  await userEvent.click(screen.getByRole("button", { name: "Что я знаю" }));
  const group = screen.getByRole("radiogroup", { name: "Как часто писать" });
  await userEvent.click(within(group).getByRole("radio", { name: "реже" }));
  expect(setAgentFrequency).toHaveBeenCalledWith(ep, "реже");
  expect(within(group).getByRole("radio", { name: "реже" })).toHaveAttribute("aria-checked", "true");
});

test("«Профиль» уходит setAgentProfile, чип и выбор меняются сразу; отказ — пометка и прежний профиль", async () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  await userEvent.click(screen.getByRole("button", { name: "Что я знаю" }));
  const group = screen.getByRole("radiogroup", { name: "Профиль" });
  await userEvent.click(within(group).getByRole("radio", { name: "личный" }));
  expect(setAgentProfile).toHaveBeenCalledWith(ep, "personal");
  expect(within(group).getByRole("radio", { name: "личный" })).toHaveAttribute("aria-checked", "true");
  expect(document.querySelector(".session-bar__profile")).toHaveTextContent("Личный");
  vi.mocked(setAgentProfile).mockRejectedValueOnce(new Error("ассистент не запущен"));
  await userEvent.click(within(group).getByRole("radio", { name: "рабочая встреча" }));
  expect(await screen.findByText(/Профиль не удалось сменить: ассистент не запущен/)).toBeInTheDocument();
  expect(document.querySelector(".session-bar__profile")).toHaveTextContent("Рабочая встреча");
});

test("нет связи — писать нельзя, причина видна", () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo(), error: "Нет связи с ассистентом — переподключаюсь…" })} />);
  expect(screen.getByRole("combobox", { name: "Сообщение ассистенту" })).toBeDisabled();
  expect(screen.getByText("Нет связи с ассистентом — переподключаюсь…")).toBeInTheDocument();
});

test("смена ширины (средняя → широкая → узкая) не теряет текст и вложения строки ввода", async () => {
  let px = 600;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(() => ({ width: px, height: 600, top: 0, left: 0, right: px, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }));
  const live = makeLive({ agent: agentInfo() });
  const { rerender } = render(<Host live={live} />);
  load();
  const field = screen.getByRole("combobox", { name: "Сообщение ассистенту" });
  await userEvent.type(field, "черновик");
  const png = new File([new Uint8Array([1])], "shot.png", { type: "image/png" });
  await act(async () => { fireEvent.paste(field, { clipboardData: { files: [png], getData: () => "" } }); });
  rerender(<Host live={live} wide />);
  expect(screen.getByRole("separator", { name: "Ширина расшифровки" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Сообщение ассистенту" })).toBe(field); // тот же элемент — не пересоздан
  px = 360;
  rerender(<Host live={{ ...live }} />);
  await act(async () => { window.dispatchEvent(new Event("resize")); });
  expect(screen.getByRole("combobox", { name: "Сообщение ассистенту" })).toHaveValue("черновик");
  expect(screen.getByRole("list", { name: "Вложения" })).toHaveTextContent("shot.png");
});

test("быстрые вопросы над пустой строкой: щелчок отправляет; при наборе — скрыты", async () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  const group = screen.getByRole("group", { name: "Быстрые вопросы" });
  expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Что я пропустил?", "Что ответить?", "Кратко итоги"]);
  await userEvent.click(within(group).getByRole("button", { name: "Что я пропустил?" }));
  expect(postChat).toHaveBeenCalledWith(ep, { text: "Что я пропустил?", client_id: "c1", attachments: [] });
  await userEvent.type(screen.getByRole("combobox", { name: "Сообщение ассистенту" }), "с");
  expect(screen.queryByRole("group", { name: "Быстрые вопросы" })).toBeNull();
});

test("до первого состояния — уже чат с «Подключаюсь к ассистенту…», не прежняя раскладка и не пустой экран", () => {
  render(<Host live={makeLive({ loaded: false })} />);
  expect(screen.getByText("Подключаюсь к ассистенту…")).toBeInTheDocument();
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Сообщение ассистенту" })).toBeDisabled();
});


// --- ширина колонки расшифровки: без верхнего предела, только минимумы -----------------------

/** Ширина тела широкой раскладки (jsdom не раскладывает); `room.px` можно менять — «окно» (и ширина области). */
function sideLayout(room: { px: number }, column = 300) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => (
    { width: room.px, height: 600, top: 0, left: 0, right: room.px, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }));
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("chat-ws__body") ? room.px : 0;
  });
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("chat-ws__transcript") ? column : 0;
  });
  const all: (() => void)[] = [];
  vi.stubGlobal("ResizeObserver", class {
    cb: () => void;
    constructor(cb: () => void) { this.cb = cb; }
    observe() { all.push(this.cb); }
    disconnect() { const at = all.indexOf(this.cb); if (at >= 0) all.splice(at, 1); }
  });
  return { resize: (px: number) => { room.px = px; act(() => { [...all].forEach((cb) => cb()); }); } };
}
const body = () => document.querySelector<HTMLElement>(".chat-ws__body")!;
const sideSplit = () => screen.getByRole("separator", { name: "Ширина расшифровки" });
/** Чату остаётся его минимум и промежуток сетки. */
const sideMax = (room: number) => room - CHAT_MIN - 12;

describe("ширина колонки расшифровки", () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  test("предел — только место: колонку можно расширить далеко за прежние 640 px, чату — его минимум", async () => {
    sideLayout({ px: 1800 });
    render(<Host live={makeLive({ agent: agentInfo() })} wide />);
    const split = sideSplit();
    expect(CHAT_PANES.side).not.toHaveProperty("max");
    expect(split).toHaveAttribute("aria-valuemin", String(CHAT_SIDE_MIN));
    expect(split).toHaveAttribute("aria-valuemax", String(sideMax(1800)));
    fireEvent.keyDown(split, { key: "End" });
    expect(body().style.getPropertyValue("--chat-side")).toBe(`${sideMax(1800)}px`);
    expect(localStorage.getItem("meet.pane.live-chat-side")).toBe(String(sideMax(1800)));
    // мышью — тоже до упора вправо, не дальше минимума чата
    fireEvent.keyDown(split, { key: "Home" });
    fireEvent.pointerDown(split, { button: 0, clientX: 300, pointerId: 1 });
    fireEvent.pointerMove(split, { clientX: 1400, pointerId: 1 });
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    fireEvent.pointerUp(split, { clientX: 1400, pointerId: 1 });
    expect(body().style.getPropertyValue("--chat-side")).toBe(`${CHAT_SIDE_MIN + 1100}px`);
    expect(CHAT_SIDE_MIN + 1100).toBeGreaterThan(640);
  });

  test("клавиши — шаг 16 px; двойной щелчок и Enter — как было", () => {
    sideLayout({ px: 1200 });
    render(<Host live={makeLive({ agent: agentInfo() })} wide />);
    const split = sideSplit();
    expect(split).toHaveAttribute("aria-valuenow", "300");      // по CSS, пока не тянули
    fireEvent.keyDown(split, { key: "ArrowRight" });             // колонка слева: → — шире
    expect(body().style.getPropertyValue("--chat-side")).toBe("316px");
    fireEvent.doubleClick(split);
    expect(body().style.getPropertyValue("--chat-side")).toBe("");
    expect(localStorage.getItem("meet.pane.live-chat-side")).toBeNull();
    fireEvent.keyDown(split, { key: "ArrowLeft" });
    expect(body().style.getPropertyValue("--chat-side")).toBe("284px");
    fireEvent.keyDown(split, { key: "Enter" });
    expect(body().style.getPropertyValue("--chat-side")).toBe("");
  });

  test("окно сузили — запомненная ширина ужимается, чат не уже минимума; шире — возвращается", () => {
    localStorage.setItem("meet.pane.live-chat-side", "1000");
    const room = { px: 1600 };
    const win = sideLayout(room);
    render(<Host live={makeLive({ agent: agentInfo() })} wide />);
    expect(body().style.getPropertyValue("--chat-side")).toBe("1000px");
    win.resize(900);
    expect(body().style.getPropertyValue("--chat-side")).toBe(`${sideMax(900)}px`);
    expect(sideSplit()).toHaveAttribute("aria-valuemax", String(sideMax(900)));
    win.resize(700);                                             // совсем узко — расшифровке её минимум
    expect(body().style.getPropertyValue("--chat-side")).toBe(`${Math.max(CHAT_SIDE_MIN, sideMax(700))}px`);
    expect(localStorage.getItem("meet.pane.live-chat-side")).toBe("1000");   // пожелание остаётся
    win.resize(1600);
    expect(body().style.getPropertyValue("--chat-side")).toBe("1000px");
  });
});


// --- одна раскладка: чат всегда на месте; расшифровка колонкой — от 560 px или по выбору человека ---

describe("одна раскладка", () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  const shape = () => ({
    body: body().className,
    children: [...body().children].map((el) => el.className.split(" ")[0]),
  });

  test("ширина 300–1600 px: от 560 — колонка и чат, уже — только чат; чат и строка ввода не пересоздаются", () => {
    const room = { px: 1600 };
    const win = sideLayout(room);
    const live = makeLive({ agent: agentInfo() });
    const { rerender } = render(<Host live={live} wide />);
    load();
    const chatLog = screen.getByRole("log", { name: "Чат с ассистентом" });
    const field = screen.getByRole("combobox", { name: "Сообщение ассистенту" });
    const first = shape();
    expect(first.children).toEqual(["chat-ws__transcript", "splitter", "chat-ws__main"]);
    for (const px of [300, 360, 420, 559, 560, 719, 720, 900, 1280, 1600]) {
      win.resize(px);
      rerender(<Host live={{ ...live }} wide={px >= 720} />);
      expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toBe(chatLog);
      expect(chatLog).toBeVisible();
      expect(screen.getByRole("combobox", { name: "Сообщение ассистенту" })).toBe(field);
      if (px >= TRANSCRIPT_PX) {
        expect(shape()).toEqual(first);
        expect(screen.getByRole("region", { name: "Расшифровка" })).toBeVisible();
      } else {
        expect(shape()).toEqual({ body: "chat-ws__body is-hidden", children: ["chat-ws__main"] });
        expect(screen.getByRole("button", { name: "Показать расшифровку" })).toBeInTheDocument();
      }
      expect(localStorage.getItem("meet.pane.live-chat-side-hidden")).toBeNull();   // ширина — не выбор человека
    }
  });

  test("узкая: открытая вручную колонка запоминается и остаётся; убранная вручную в широкой — тоже", async () => {
    const room = { px: 400 };
    const win = sideLayout(room);
    const live = makeLive({ agent: agentInfo() });
    const { unmount } = render(<Host live={live} />);
    await userEvent.click(screen.getByRole("button", { name: "Показать расшифровку" }));
    expect(screen.getByRole("region", { name: "Расшифровка" })).toBeInTheDocument();
    expect(localStorage.getItem("meet.pane.live-chat-side-hidden")).toBe("0");
    win.resize(360);
    expect(screen.getByRole("region", { name: "Расшифровка" })).toBeInTheDocument();
    unmount();
    render(<Host live={live} />);                                 // перезапуск — помнит
    expect(screen.getByRole("region", { name: "Расшифровка" })).toBeInTheDocument();
  });

  test("совсем узко (колонку открыли сами): обе колонки ужимаются до своих минимумов, ни одна не пропадает", () => {
    localStorage.setItem("meet.pane.live-chat-side-hidden", "0");
    localStorage.setItem("meet.pane.live-chat-side", "900");
    const room = { px: 1600 };
    const win = sideLayout(room);
    render(<Host live={makeLive({ agent: agentInfo() })} />);
    for (const px of [600, 420, 360, 300]) {
      win.resize(px);
      const side = parseInt(body().style.getPropertyValue("--chat-side"), 10);
      expect(side).toBeGreaterThanOrEqual(TRANSCRIPT_FLOOR);
      expect(px - side - 12).toBeGreaterThanOrEqual(Math.min(CHAT_MIN, px - TRANSCRIPT_FLOOR - 12));
      expect(px - side - 12).toBeGreaterThanOrEqual(CHAT_FLOOR);
      expect(screen.getByRole("region", { name: "Расшифровка" })).toBeInTheDocument();
    }
    win.resize(260);                                              // ещё уже — уступает колонка, чат не уже минимума
    expect(260 - parseInt(body().style.getPropertyValue("--chat-side"), 10) - 12).toBeGreaterThanOrEqual(CHAT_FLOOR);
    expect(localStorage.getItem("meet.pane.live-chat-side")).toBe("900");
  });

  test("расшифровку убирают только сами: «‹» на разделителе, выбор запоминается; «›» возвращает", async () => {
    const room = { px: 900 };
    const win = sideLayout(room);
    const live = makeLive({ agent: agentInfo() });
    const { unmount } = render(<Host live={live} />);
    load();
    await userEvent.click(screen.getByRole("button", { name: "Убрать расшифровку" }));
    expect(screen.queryByRole("region", { name: "Расшифровка" })).toBeNull();
    expect(screen.queryByRole("separator", { name: "Ширина расшифровки" })).toBeNull();
    expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toBeVisible();
    expect(localStorage.getItem("meet.pane.live-chat-side-hidden")).toBe("1");
    win.resize(1600);                                             // окно шире — сама не возвращается
    expect(screen.queryByRole("region", { name: "Расшифровка" })).toBeNull();
    unmount();

    render(<Host live={live} />);                                 // перезапуск — помнит
    expect(screen.queryByRole("region", { name: "Расшифровка" })).toBeNull();
    const show = screen.getByRole("button", { name: "Показать расшифровку" });
    expect(show).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(show);
    expect(screen.getByRole("region", { name: "Расшифровка" })).toBeInTheDocument();
    expect(localStorage.getItem("meet.pane.live-chat-side-hidden")).toBe("0");
  });

  test("при убранной расшифровке таймкод в сообщении возвращает её (действие человека)", async () => {
    localStorage.setItem("meet.pane.live-chat-side-hidden", "1");
    sideLayout({ px: 900 });
    render(<Host live={makeLive({ agent: agentInfo() })} />);
    load();
    expect(screen.queryByRole("region", { name: "Расшифровка" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "02:05" }));
    expect(screen.getByRole("region", { name: "Расшифровка" })).toBeInTheDocument();
  });

  test("чат не прячется: обрыв связи, агент пропал из состояния, смена «широкая» — лента на месте", () => {
    sideLayout({ px: 900 });
    const live = makeLive({ agent: agentInfo() });
    const { rerender } = render(<Host live={live} wide />);
    load();
    const chatLog = screen.getByRole("log", { name: "Чат с ассистентом" });
    rerender(<Host live={{ ...live, error: "Нет связи с ассистентом — переподключаюсь…" }} />);
    expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toBe(chatLog);
    rerender(<Host live={{ ...live, agent: null }} wide />);       // состояние без агента — чат остаётся
    expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toBe(chatLog);
    expect(screen.queryByRole("tablist")).toBeNull();
  });
});


/** chat.css как текст (в тестах CSS не подключается). */
const chatCss = readFileSync(join(process.cwd(), "src/live/chat.css"), "utf8").replace(/\r\n/g, "\n");

// --- ревью, исправления 1: переход по таймкоду, самая узкая панель, флаг участника, плотность ---

describe("исправления 1", () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  test("I1: таймкод нажали раньше, потом убрали расшифровку — свернули и развернули панель: остаётся убранной", async () => {
    sideLayout({ px: 900 });
    const live = makeLive({ agent: agentInfo() });
    const { rerender } = render(<Host live={live} />);
    load();
    await userEvent.click(screen.getByRole("button", { name: "02:05" }));          // переход — раньше
    await userEvent.click(screen.getByRole("button", { name: "Убрать расшифровку" }));
    expect(screen.queryByRole("region", { name: "Расшифровка" })).toBeNull();
    rerender(<Host live={live} open={false} />);
    rerender(<Host live={live} open />);
    expect(screen.queryByRole("region", { name: "Расшифровка" })).toBeNull();
    expect(localStorage.getItem("meet.pane.live-chat-side-hidden")).toBe("1");
    // А новый переход — возвращает (и без хранилища: читается состояние, не localStorage).
    localStorage.clear();
    await userEvent.click(screen.getByRole("button", { name: "02:05" }));
    expect(screen.getByRole("region", { name: "Расшифровка" })).toBeInTheDocument();
  });

  /** Поля панели вокруг рабочей области (шапка панели, отступы) — по снимку окна 380 px. */
  const PANEL_CHROME = 45;

  test("I2: окно 300 px (минимум панели, колонку открыли сами): чат не уже 160, колонка не уже 120 или прокручивается", () => {
    localStorage.setItem("meet.pane.live-chat-side-hidden", "0");
    localStorage.setItem("meet.pane.live-chat-side", "900");
    vi.stubGlobal("innerWidth", 300);
    const room = { px: window.innerWidth - PANEL_CHROME };
    const win = sideLayout(room);
    render(<Host live={makeLive({ agent: agentInfo() })} />);
    for (const px of [300, 320, 340, 360, 420, 480, 600]) {
      vi.stubGlobal("innerWidth", px);
      win.resize(window.innerWidth - PANEL_CHROME);
      const area = window.innerWidth - PANEL_CHROME;
      const side = parseInt(body().style.getPropertyValue("--chat-side"), 10);
      expect(area - side - 12).toBeGreaterThanOrEqual(CHAT_FLOOR);              // чат — всегда
      if (area >= TRANSCRIPT_FLOOR + 12 + CHAT_FLOOR) expect(side).toBeGreaterThanOrEqual(TRANSCRIPT_FLOOR);
      const split = sideSplit();                                                 // и разделителем — не уже
      expect(area - Number(split.getAttribute("aria-valuemax")) - 12).toBeGreaterThanOrEqual(CHAT_FLOOR);
      expect(Number(split.getAttribute("aria-valuemin"))).toBeGreaterThanOrEqual(Math.min(TRANSCRIPT_FLOOR, area - CHAT_FLOOR - 12));
    }
  });

  test("I2: колонка уступает первой: сначала до 120, потом чат с 280 до 160", () => {
    expect(chatReserve(1000)).toBe(CHAT_MIN + 12);
    expect(1000 - chatReserve(1000)).toBeGreaterThan(TRANSCRIPT_FLOOR);
    expect(400 - chatReserve(400)).toBe(TRANSCRIPT_FLOOR);                        // колонка — на своём минимуме
    expect(400 - TRANSCRIPT_FLOOR - 12).toBeLessThan(CHAT_MIN);                    // уже уступает чат
    expect(chatReserve(255)).toBe(CHAT_FLOOR + 12);                                // 300 px: чат — на минимуме
  });

  test("I2: ширина по умолчанию (CSS) — по тому же правилу: чату ≥ 160 + промежуток, колонка прокручивается", () => {
    const rule = chatCss.slice(chatCss.indexOf(".chat-ws__body {"), chatCss.indexOf("}", chatCss.indexOf(".chat-ws__body {")));
    expect(rule).toContain("max(0px, min(clamp(120px, 30%, 480px), calc(100% - 172px)))");
    expect(rule).toContain("minmax(160px, 1fr)");
    expect(chatCss).toMatch(/\.chat-ws__transcript \{[^}]*overflow-x: auto/);
    expect(chatCss).toContain(".chat-ws__transcript > .chat-ws__feed { min-width: 108px; }");
  });

  test("плотность ленты — по ширине колонки чата: широкое окно, узкий чат — реакции без подписей", () => {
    sideLayout({ px: 1600 });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const w = this.classList.contains("chat-ws__main") ? CHAT_COMPACT_PX - 80 : 1600;
      return { width: w, height: 600, top: 0, left: 0, right: w, bottom: 600, x: 0, y: 0, toJSON: () => ({}) };
    });
    render(<Host live={makeLive({ agent: agentInfo() })} wide />);
    load();
    const like = screen.getByRole("button", { name: "Полезно" });
    expect(like).toHaveTextContent(/^$/);                                            // только значок
    expect(like.querySelector("svg")).not.toBeNull();
    // Шапка сессии — по всей области: кнопка «Что я знаю» с подписью.
    expect(screen.getByRole("button", { name: "Что я знаю" })).toHaveTextContent("Что я знаю");
  });

  test("I4: до первого состояния — раскладка по последнему флагу участника; выключен — сразу прежняя, без чата", () => {
    localStorage.setItem(PARTICIPANT_KEY, "0");
    const { rerender } = render(<Host live={makeLive({ loaded: false })} />);
    expect(screen.queryByRole("log", { name: "Чат с ассистентом" })).toBeNull();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    rerender(<Host live={makeLive({ loaded: true })} />);                           // пришло: участника нет
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(localStorage.getItem(PARTICIPANT_KEY)).toBe("0");
    rerender(<Host live={makeLive({ loaded: true, agent: agentInfo() })} />);       // включили — запомнили
    expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toBeInTheDocument();
    expect(localStorage.getItem(PARTICIPANT_KEY)).toBe("1");
  });

  test("I4: флага нет или хранилище недоступно — до первого состояния чат", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("нет хранилища"); });
    render(<Host live={makeLive({ loaded: false })} />);
    expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toBeInTheDocument();
  });
});
