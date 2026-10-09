import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import * as api from "../lib/api";
import * as shell from "../lib/shell";
import type { AssistantInfo, LiveStatus, Snapshot } from "../lib/types";
import { RecordingBadge } from "./RecordingBadge";

vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  inTauri: vi.fn(() => false),
  invoke: vi.fn(),
}));

/** «Включить ассистента» / «Выключить ассистента» посреди обычной записи. */

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());
const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: false, starting: false, stopping: false, folder: null, error: null, started_at: null, ...o,
});
const recording = (l: LiveStatus = live()): Snapshot => ({
  status: "recording", source: "manual", folder: "D:/rec/f", elapsed_s: 754, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, live: l,
});
const assistant = (o: Partial<AssistantInfo> = {}): AssistantInfo => ({
  provider: "claude", setting: "auto", available: {}, knowledge_dir: null, checking: false, ...o,
});
/** Подсказка кнопки остановки: «Идёт запись · мм:сс», ассистент, этап его запуска. */
const status = () => {
  // Облачко ui/Tip — при наведении; тот же текст — описание кнопки (скрытый узел по aria-describedby).
  const id = stopButton().getAttribute("aria-describedby")?.split(" ").pop();
  const node = id ? document.getElementById(id) : null;
  if (!node) throw new Error("у кнопки записи нет подсказки-описания");
  return node;
};
const stopButton = () => screen.getByRole("button", { name: "Остановить и сохранить" });
/** Меню «Действия с записью» открывает сама кнопка остановки. */
const openMenu = () => userEvent.click(stopButton());
/** Ассистент во время записи — своя кнопка под кнопкой записи (0.5). */
const agentButton = () => screen.getByRole("button", { name: /^(Включить ассистента|Ассистент)$/ });
const openAgent = () => userEvent.click(agentButton());

test("0.5: во время записи своя кнопка «Включить ассистента» под кнопкой записи — ответ применяется сразу", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const attach = vi.spyOn(api, "liveAttach").mockResolvedValue(
    { ok: true, ...live({ starting: true, attached: true, folder: "D:/rec/f" }) });
  const stop = vi.spyOn(api, "recordingCommand");
  function Harness() {
    const [s, setS] = useState(recording());
    return <RecordingBadge endpoint={ep} snapshot={s} onSnapshot={setS} />;
  }
  render(<Harness />);
  expect(status()).toHaveTextContent("Идёт запись · 12:34");
  await openAgent();
  const item = await screen.findByRole("menuitem", { name: /^Рабочая встреча/ });
  await waitFor(() => expect(item).toBeEnabled());
  expect(item).toHaveTextContent("догонит начало встречи");
  await userEvent.click(item);
  expect(attach).toHaveBeenCalledWith(ep, "work");
  expect(stop).not.toHaveBeenCalled(); // запись не останавливали
  await waitFor(() => expect(status()).toHaveTextContent("Ассистент запускается…"));
  // Всё та же запись (не с 00:00); секунды могли уйти вперёд — на медленной машине тест идёт дольше.
  expect(status()).toHaveTextContent(/Идёт запись · 12:3[0-9]/);
  expect(screen.queryByRole("menu")).toBeNull();
});

test("«Включить ассистента» → «Личный» — подключает с профилем «Личный»", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const attach = vi.spyOn(api, "liveAttach").mockResolvedValue(
    { ok: true, ...live({ starting: true, attached: true, folder: "D:/rec/f" }) });
  render(<RecordingBadge endpoint={ep} snapshot={recording()} />);
  await openAgent();
  const item = await screen.findByRole("menuitem", { name: /^Личный/ });
  // Профили — пунктами группы «Включить ассистента» (подпись группы, а не в каждом пункте).
  expect(within(screen.getByRole("group", { name: "Включить ассистента" })).getAllByRole("menuitem")).toHaveLength(2);
  await waitFor(() => expect(item).toBeEnabled());
  expect(item).toHaveTextContent("без базы знаний");
  await userEvent.click(item);
  expect(attach).toHaveBeenCalledWith(ep, "personal");
});

test("без подключённой модели «Включить ассистента» неактивен, с подсказкой", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant({ provider: null }));
  const attach = vi.spyOn(api, "liveAttach");
  render(<RecordingBadge endpoint={ep} snapshot={recording()} />);
  await openAgent();
  const item = await screen.findByRole("menuitem", { name: /^Рабочая встреча/ });
  await waitFor(() => expect(item).toBeDisabled());
  expect(screen.getByRole("menu")).toHaveTextContent("Подключите Claude Code, Codex или OpenCode в настройках");
  await userEvent.click(item);
  expect(attach).not.toHaveBeenCalled();
});

test("включён — «· ассистент» в подсказке и «Выключить ассистента» (запись продолжается)", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const detach = vi.spyOn(api, "liveDetach").mockResolvedValue(
    { ok: true, action: "stopping", ...live({ active: true, stopping: true, attached: true }) });
  function Harness() {
    const [s, setS] = useState(recording(live({ active: true, attached: true, folder: "D:/rec/f" })));
    return <RecordingBadge endpoint={ep} snapshot={s} onSnapshot={setS} />;
  }
  render(<Harness />);
  expect(status()).toHaveTextContent(/Идёт запись · 12:3[0-9] · ассистент/);
  await openAgent();
  const item = await screen.findByRole("menuitem", { name: /Выключить ассистента/ });
  expect(item).toHaveTextContent("запись продолжится");
  await userEvent.click(item);
  expect(detach).toHaveBeenCalledWith(ep);
  await waitFor(() => expect(status()).toHaveTextContent("Ассистент выключается…"));
  expect(stopButton()).toBeEnabled(); // запись можно остановить
});

test("0.5: окно ассистента скрыли ✕ — «Показать окно ассистента» в меню кнопки ассистента (только в приложении)", async () => {
  vi.mocked(shell.inTauri).mockReturnValue(true);
  vi.mocked(shell.invoke).mockResolvedValue(undefined);
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={recording(live({ active: true, attached: true, folder: "D:/rec/f" }))}
    onSnapshot={() => {}} />);
  await openAgent();
  await userEvent.click(await screen.findByRole("menuitem", { name: /^Показать окно ассистента/ }));
  expect(shell.invoke).toHaveBeenCalledWith("live_panel_show");
  expect(screen.queryByRole("menu")).toBeNull();
  vi.mocked(shell.inTauri).mockReturnValue(false);
});

test("вне приложения (браузер) пункта «Показать окно ассистента» нет", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={recording(live({ active: true, attached: true, folder: "D:/rec/f" }))} />);
  await openAgent();
  await screen.findByRole("menuitem", { name: /Выключить ассистента/ });
  expect(screen.queryByRole("menuitem", { name: /Показать окно ассистента/ })).toBeNull();
});

test("отказ резидента виден рядом с кнопкой", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  vi.spyOn(api, "liveAttach").mockRejectedValue(new Error("Ассистент ещё запускается или останавливается"));
  render(<RecordingBadge endpoint={ep} snapshot={recording()} />);
  await openAgent();
  const item = await screen.findByRole("menuitem", { name: /^Рабочая встреча/ });
  await waitFor(() => expect(item).toBeEnabled());
  await userEvent.click(item);
  expect(await screen.findByRole("alert")).toHaveTextContent("Ассистент ещё запускается");
});

test("«Остановить и сохранить» во время записи с ассистентом — остановка самой записи", async () => {
  const stop = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  const liveStop = vi.spyOn(api, "liveStop");
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={recording(live({ active: true, attached: true }))} />);
  await openMenu();
  // 0.5: в меню «Стоп» — только остановка; ассистент — своей кнопкой.
  expect(screen.getAllByRole("menuitem").map((i) => i.querySelector(".rec-menu__title")?.textContent)).toEqual([
    "Остановить и сохранить", "Остановить без сохранения…",
  ]);
  await userEvent.click(screen.getByRole("menuitem", { name: /^Остановить и сохранить/ }));
  expect(stop).toHaveBeenCalledWith(ep, "stop");
  expect(liveStop).not.toHaveBeenCalled();
});

test("подключается: этап старта виден в подсказке вместо общего «запускается»", () => {
  const { rerender } = render(<RecordingBadge endpoint={ep}
    snapshot={recording(live({ starting: true, attached: true, stage: "подключаюсь к записи…" }))} />);
  expect(status()).toHaveTextContent("Ассистент запускается: подключаюсь к записи…");
  // Отвод уже читается, модель ещё грузится: ассистент ещё не слушает.
  rerender(<RecordingBadge endpoint={ep} snapshot={recording(live({
    active: true, ready: false, attached: true, stage: "загружаю модель распознавания…" }))} />);
  expect(status()).toHaveTextContent("Ассистент запускается: загружаю модель распознавания…");
  expect(status()).not.toHaveTextContent("· ассистент");
  rerender(<RecordingBadge endpoint={ep}
    snapshot={recording(live({ active: true, ready: true, attached: true }))} />);
  expect(status()).toHaveTextContent("· ассистент");
  expect(status()).not.toHaveTextContent(/Ассистент запускается/);
});

test("подключённый ассистент упал — ошибка видна, хотя запись идёт, и не гаснет сама", () => {
  vi.useFakeTimers();
  try {
    const { rerender } = render(<RecordingBadge endpoint={ep}
      snapshot={recording(live({ starting: true, attached: true }))} />);
    rerender(<RecordingBadge endpoint={ep} snapshot={recording(live({
      error: "Ассистент не запустился за 120 с: этап «загружаю модель распознавания» не закончился",
      ended_by: "crash" }))} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Ассистент не запустился за 120 с");
    act(() => { vi.advanceTimersByTime(30000); });
    expect(screen.getByRole("alert")).toHaveTextContent("Ассистент не запустился");
  } finally {
    vi.useRealTimers();
  }
});

test("та же ошибка при новом сбое — снова видна; конец записи убирает её", async () => {
  const fail = (at: number) => recording(live({
    error: "Ассистент упал — запись продолжается: сбой", error_at: at, ended_by: "crash" }));
  const { rerender } = render(<RecordingBadge endpoint={ep} snapshot={recording(live())} />);
  rerender(<RecordingBadge endpoint={ep} snapshot={fail(100)} />);
  expect(screen.getByRole("alert")).toHaveTextContent("запись продолжается: сбой");
  await userEvent.click(screen.getByRole("button", { name: "Скрыть ошибку" }));
  expect(screen.queryByRole("alert")).toBeNull();
  // Снова включили и снова упал с тем же текстом (окно не видело промежуточного null).
  rerender(<RecordingBadge endpoint={ep} snapshot={fail(200)} />);
  expect(screen.getByRole("alert")).toHaveTextContent("запись продолжается: сбой");
  // Запись остановили — уведомление про её ассистента больше не висит.
  rerender(<RecordingBadge endpoint={ep} snapshot={{ ...fail(200), status: "idle" }} />);
  expect(screen.queryByRole("alert")).toBeNull();
});

test("ошибка хвоста ассистента прошлой записи не всплывает над новой", () => {
  const { rerender } = render(<RecordingBadge endpoint={ep} snapshot={recording(live())} />);
  // Хвост прошлой записи убит по дедлайну, когда уже идёт другая (D:/rec/f).
  const late = recording(live({
    error: "Ассистент не успел сохранить сводку — процесс убит, запись не затронута",
    error_at: 300, error_folder: "D:\\rec\\old", ended_by: "recording" }));
  rerender(<RecordingBadge endpoint={ep} snapshot={late} />);
  expect(screen.queryByRole("alert")).toBeNull();
  // И после её конца — тоже: это было не про неё.
  rerender(<RecordingBadge endpoint={ep} snapshot={{ ...late, status: "idle", folder: null }} />);
  expect(screen.queryByRole("alert")).toBeNull();
});

test("ошибка ассистента своей записи видна (папка та же, разделители — любые)", () => {
  const { rerender } = render(<RecordingBadge endpoint={ep} snapshot={recording(live())} />);
  rerender(<RecordingBadge endpoint={ep} snapshot={recording(live({
    error: "Ассистент упал — запись продолжается: сбой", error_at: 400,
    error_folder: "D:\\rec\\f", ended_by: "crash" }))} />);
  expect(screen.getByRole("alert")).toHaveTextContent("запись продолжается: сбой");
});

test("0.5: кнопка ассистента — только во время записи; включён — «Ассистент», выключен — «Включить ассистента»", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const idle = { ...recording(), status: "idle" as const, folder: null };
  const { rerender } = render(<RecordingBadge endpoint={ep} snapshot={idle} />);
  expect(screen.queryByRole("button", { name: /ассистент/i })).toBeNull();
  rerender(<RecordingBadge endpoint={ep} snapshot={recording()} />);
  expect(agentButton()).toHaveAccessibleName("Включить ассистента");
  expect(agentButton()).toHaveAttribute("aria-haspopup", "menu");
  rerender(<RecordingBadge endpoint={ep} snapshot={recording(live({ active: true, attached: true, folder: "D:/rec/f" }))} />);
  expect(agentButton()).toHaveAccessibleName("Ассистент");
  expect(agentButton()).toHaveClass("is-on");
  // Меню «Стоп» про ассистента ничего не знает.
  await openMenu();
  expect(screen.queryByRole("menuitem", { name: /ассистент/i })).toBeNull();
});
