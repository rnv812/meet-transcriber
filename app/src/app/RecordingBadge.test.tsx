import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { RecordingBadge, RecordingWarnings } from "./RecordingBadge";
import * as api from "../lib/api";
import * as shell from "../lib/shell";
import type { AssistantInfo, LiveStatus, Snapshot } from "../lib/types";

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());
const snap = (extra: Partial<Snapshot>): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
});
/** Подсказка кнопки записи: «Идёт запись · мм:сс», состояние ассистента, пометки. */
const status = () => screen.getByRole("tooltip");
const stop = () => screen.getByRole("button", { name: "Остановить и сохранить" });
const startButton = () => screen.getByRole("button", { name: "Начать запись" });
/** Кнопка записи открывает меню вариантов; «Записать» — обычная запись (то, что раньше делал щелчок). */
const startPlain = async () => {
  await userEvent.click(startButton());
  await userEvent.click(await screen.findByRole("menuitem", { name: /^Записать/ }));
};

test("вне записи — главная кнопка-значок рейки «Начать запись» с подсказкой", () => {
  render(<RecordingBadge endpoint={ep} snapshot={snap({})} />);
  const start = screen.getByRole("button", { name: "Начать запись" });
  expect(start).toHaveClass("btn", "btn--primary", "btn--lg", "btn--icon");
  expect(start).toHaveTextContent("");
  expect(start.parentElement?.querySelector(".tooltip")).toHaveTextContent("Начать запись");
  expect(screen.queryByRole("button", { name: "Остановить и сохранить" })).toBeNull();
  // Меню вариантов открывает сама кнопка: отдельной узкой «ещё» под ней нет.
  expect(start).toHaveAttribute("aria-haspopup", "menu");
  expect(start).toHaveAttribute("aria-expanded", "false");
  expect(screen.getAllByRole("button")).toEqual([start]);
});

test("при записи: «Остановить и сохранить» (danger) с подсказкой «Идёт запись · мм:сс» — меню, первым пунктом — recordingCommand stop", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  render(<RecordingBadge endpoint={ep} snapshot={snap({ status: "recording", source: "manual", elapsed_s: 754 })} />);
  expect(stop()).toHaveClass("btn--danger", "btn--lg", "btn--icon");
  expect(stop()).toHaveAccessibleDescription("Идёт запись · 12:34");
  expect(stop()).toHaveAttribute("aria-haspopup", "menu");
  expect(screen.queryByRole("button", { name: "Начать запись" })).toBeNull();
  await userEvent.click(stop());
  expect(spy).not.toHaveBeenCalled();
  const menu = await screen.findByRole("menu", { name: "Действия с записью" });
  expect(stop()).toHaveAttribute("aria-expanded", "true");
  const first = screen.getAllByRole("menuitem")[0]!;
  expect(first).toHaveTextContent(/^Остановить и сохранить/);
  await waitFor(() => expect(first).toHaveFocus());
  expect(menu).toHaveTextContent("Остановить без сохранения");
  await userEvent.click(first);
  expect(spy).toHaveBeenCalledWith(ep, "stop");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("при записи Enter → Enter останавливает и сохраняет", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  render(<RecordingBadge endpoint={ep} snapshot={snap({ status: "recording", source: "manual", elapsed_s: 5 })} />);
  stop().focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(screen.getByRole("menuitem", { name: /^Остановить и сохранить/ })).toHaveFocus());
  await userEvent.keyboard("{Enter}");
  expect(spy).toHaveBeenCalledWith(ep, "stop");
});

test("автозапись помечена в подсказке", () => {
  render(<RecordingBadge endpoint={ep} snapshot={snap({ status: "recording", source: "auto", elapsed_s: 5 })} />);
  expect(status()).toHaveTextContent("Идёт запись · 00:05 · автозапись");
});

test("вне записи — «Начать запись» открывает меню вариантов, «Записать» вызывает recordingCommand start", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  render(<RecordingBadge endpoint={ep} snapshot={snap({ disk_free_gb: 3.2 })} />);
  await userEvent.click(startButton());
  expect(spy).not.toHaveBeenCalled();
  expect(await screen.findByRole("menu", { name: "Варианты записи" })).toBeInTheDocument();
  expect(startButton()).toHaveAttribute("aria-expanded", "true");
  const first = screen.getAllByRole("menuitem")[0]!;
  expect(first).toHaveTextContent(/^Записать/);
  await waitFor(() => expect(first).toHaveFocus());
  await userEvent.click(first);
  expect(spy).toHaveBeenCalledWith(ep, "start");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("вне записи Enter → Enter начинает обычную запись", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  render(<RecordingBadge endpoint={ep} snapshot={snap({})} />);
  startButton().focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(screen.getByRole("menuitem", { name: /^Записать/ })).toHaveFocus());
  await userEvent.keyboard("{Enter}");
  expect(spy).toHaveBeenCalledWith(ep, "start");
});

test("пункты меню — со значком, названием и пояснением", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await userEvent.click(startButton());
  await screen.findByRole("menu", { name: "Варианты записи" });
  for (const item of screen.getAllByRole("menuitem")) {
    expect(item.querySelector(".rec-menu__icon")).toHaveAttribute("aria-hidden", "true");
    expect(item.querySelector(".rec-menu__title")?.textContent).toBeTruthy();
    expect(item.querySelector(".rec-menu__note")?.textContent).toBeTruthy();
  }
});

test("стрелки ходят по доступным пунктам, Tab закрывает меню", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await userEvent.click(startButton());
  await screen.findByRole("menu", { name: "Варианты записи" });
  await waitFor(() => expect(api.getAssistant).toHaveBeenCalled());
  const items = screen.getAllByRole("menuitem");
  await waitFor(() => expect(items[0]).toHaveFocus());
  await userEvent.keyboard("{ArrowDown}");
  expect(items[1]).toHaveFocus();
  await userEvent.keyboard("{ArrowUp}{ArrowUp}");
  expect(items[items.length - 1]).toHaveFocus();
  await userEvent.keyboard("{Home}");
  expect(items[0]).toHaveFocus();
  await userEvent.keyboard("{Tab}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(startButton()).toHaveFocus();
});

test("щелчок снаружи закрывает меню, повторный щелчок по кнопке — тоже", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const spy = vi.spyOn(api, "recordingCommand");
  render(<><p>снаружи</p><RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} /></>);
  await userEvent.click(startButton());
  await screen.findByRole("menu");
  await userEvent.click(screen.getByText("снаружи"));
  expect(screen.queryByRole("menu")).toBeNull();
  await userEvent.click(startButton());
  await screen.findByRole("menu");
  await userEvent.click(startButton());
  expect(screen.queryByRole("menu")).toBeNull();
  expect(spy).not.toHaveBeenCalled();
});

test("мало места — кнопка-значок предупреждения с той же подсказкой", () => {
  render(<RecordingWarnings endpoint={ep} snapshot={snap({ disk_free_gb: 3.2 })} />);
  const warn = screen.getByRole("button", { name: "Мало места: 3.2 ГБ" });
  expect(warn.parentElement?.querySelector(".tooltip")).toHaveTextContent("Мало места: 3.2 ГБ");
});

test("места хватает, резидент не на связи — предупреждений нет", () => {
  const { container, rerender } = render(<RecordingWarnings endpoint={ep} snapshot={snap({})} />);
  expect(container).toBeEmptyDOMElement();
  rerender(<RecordingWarnings endpoint={ep} online={false} snapshot={snap({ disk_free_gb: 1 })} />);
  expect(container).toBeEmptyDOMElement();
});

test("macOS без разрешения на системный звук: предупреждение во время записи, по нажатию — настройки", async () => {
  const open = vi.spyOn(shell, "openScreenRecordingSettings").mockResolvedValue();
  const missing = { notice: "Системный звук не пишется — нет разрешения", permission: true };
  const { rerender } = render(<RecordingWarnings endpoint={ep}
    snapshot={snap({ system_audio_missing: missing })} />);
  expect(screen.queryByRole("button", { name: missing.notice })).toBeNull();
  rerender(<RecordingWarnings endpoint={ep}
    snapshot={snap({ status: "recording", source: "manual", system_audio_missing: missing })} />);
  const warn = screen.getByRole("button", { name: missing.notice });
  expect(warn).toHaveAccessibleDescription(/Открыть настройки/);
  await userEvent.click(warn);
  expect(open).toHaveBeenCalledTimes(1);
});

test("настройки не открылись — ошибка видна, её можно скрыть", async () => {
  vi.spyOn(shell, "openScreenRecordingSettings").mockRejectedValue(new Error("нет доступа"));
  const missing = { notice: "Системный звук не пишется — нет разрешения", permission: true };
  render(<RecordingWarnings endpoint={ep}
    snapshot={snap({ status: "recording", source: "manual", system_audio_missing: missing })} />);
  await userEvent.click(screen.getByRole("button", { name: missing.notice }));
  expect(await screen.findByRole("alert")).toHaveTextContent("нет доступа");
  await userEvent.click(screen.getByRole("button", { name: "Скрыть ошибку" }));
  expect(screen.queryByRole("alert")).toBeNull();
});

test("ошибка команды видна рядом с кнопкой", async () => {
  vi.spyOn(api, "recordingCommand").mockRejectedValue(new Error("микрофон занят"));
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={snap({})} />);
  await startPlain();
  expect(await screen.findByText("микрофон занят")).toBeInTheDocument();
});

test("ответ команды применяется сразу: «Идёт запись» без ожидания опроса", async () => {
  vi.spyOn(api, "recordingCommand").mockResolvedValue(
    { ...snap({ status: "recording", source: "manual", elapsed_s: 0 }), ok: true, action: "started" });
  function Harness() {
    const [s, setS] = useState(snap({}));
    return <RecordingBadge endpoint={ep} snapshot={s} onSnapshot={setS} />;
  }
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<Harness />);
  await startPlain();
  expect(await screen.findByRole("tooltip")).toHaveTextContent("Идёт запись · 00:00");
  expect(stop()).toBeInTheDocument();
});

test("таймер тикает каждую секунду между опросами", () => {
  vi.useFakeTimers();
  try {
    const at = Date.now();
    render(<RecordingBadge endpoint={ep} snapshotAt={at}
      snapshot={snap({ status: "recording", source: "manual", elapsed_s: 10 })} />);
    expect(status()).toHaveTextContent("Идёт запись · 00:10");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(status()).toHaveTextContent("Идёт запись · 00:11");
    act(() => { vi.advanceTimersByTime(2000); });
    expect(status()).toHaveTextContent("Идёт запись · 00:13");
  } finally {
    vi.useRealTimers();
  }
});

test("резидент не на связи — бейджа нет", () => {
  const { container } = render(<RecordingBadge endpoint={ep} online={false} snapshot={snap({})} />);
  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByRole("button")).toBeNull();
});

const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: false, starting: false, stopping: false, folder: null, error: null, started_at: null, ...o,
});
const assistant = (o: Partial<AssistantInfo> = {}): AssistantInfo => ({
  provider: "claude", setting: "auto", available: {}, knowledge_dir: null, checking: false, ...o,
});
const openMenu = async () => {
  await userEvent.click(startButton());
  return screen.findByRole("menuitem", { name: /С ассистентом · Рабочая встреча/ });
};

test("кнопка записи открывает меню: «С ассистентом» запускает живой режим, ответ применяется сразу", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const start = vi.spyOn(api, "liveStart").mockResolvedValue({ ok: true, ...live({ starting: true }) });
  function Harness() {
    const [s, setS] = useState(snap({ live: live() }));
    return <RecordingBadge endpoint={ep} snapshot={s} onSnapshot={setS} />;
  }
  render(<Harness />);
  expect(screen.getByRole("button", { name: "Начать запись" })).toBeInTheDocument();
  const item = await openMenu();
  await waitFor(() => expect(api.getAssistant).toHaveBeenCalledWith(ep));
  expect(item).toBeEnabled();
  await userEvent.click(item);
  expect(start).toHaveBeenCalledWith(ep, { profile: "work" });
  expect(await screen.findByText("Ассистент запускается…")).toBeInTheDocument();
  expect(screen.queryByRole("menu")).toBeNull();
});

test("профиль выбирается тем же щелчком: два пункта, «Личный» запускает с ним", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const start = vi.spyOn(api, "liveStart").mockResolvedValue({ ok: true, ...live({ starting: true }) });
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  const first = await openMenu();
  const items = screen.getAllByRole("menuitem");
  expect(items.map((i) => i.textContent)).toEqual([
    expect.stringMatching(/^Записать/),
    expect.stringContaining("С ассистентом · Рабочая встреча"),
    expect.stringContaining("С ассистентом · Личный"),
    expect.stringContaining("Временная встреча"),
  ]);
  expect(items[2]).toHaveTextContent("без базы знаний");
  expect(first).toBe(items[1]);
  await waitFor(() => expect(items[0]).toHaveFocus());
  await userEvent.click(screen.getByRole("menuitem", { name: /Личный/ }));
  expect(start).toHaveBeenCalledWith(ep, { profile: "personal" });
});

test("профиль по умолчанию из настроек — первым и с пометкой", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue({ ...assistant(), profile: "personal" });
  const start = vi.spyOn(api, "liveStart").mockResolvedValue({ ok: true, ...live({ starting: true }) });
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await openMenu();
  await waitFor(() => expect(screen.getAllByRole("menuitem")[1])
    .toHaveTextContent("С ассистентом · Личный (по умолчанию)"));
  const items = screen.getAllByRole("menuitem");
  expect(items[0]).toHaveTextContent(/^Записать/);
  expect(items[2]).toHaveTextContent("С ассистентом · Рабочая встреча");
  expect(items[2]).not.toHaveTextContent("по умолчанию");
  await userEvent.click(items[1]!);
  expect(start).toHaveBeenCalledWith(ep, { profile: "personal" });
});

test("без провайдера неактивны оба пункта профиля и временная встреча; «Записать» доступна", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant({ provider: null }));
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await openMenu();
  const agentItems = () => screen.getAllByRole("menuitem", { name: /С ассистентом|Временная встреча/ });
  expect(agentItems()).toHaveLength(3);
  await waitFor(() => expect(agentItems().every((i) => (i as HTMLButtonElement).disabled)).toBe(true));
  expect(screen.getByRole("menuitem", { name: /^Записать/ })).toBeEnabled();
  expect(screen.getByRole("menuitem", { name: /Личный/ }))
    .toHaveAccessibleDescription("Подключите Claude Code, Codex или OpenCode в настройках");
});

test("без провайдера «С ассистентом» неактивен, с подсказкой", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant({ provider: null }));
  const start = vi.spyOn(api, "liveStart");
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  const item = await openMenu();
  await waitFor(() => expect(item).toBeDisabled());
  expect(screen.getByRole("menu")).toHaveTextContent("Подключите Claude Code, Codex или OpenCode в настройках");
  await userEvent.click(item);
  expect(start).not.toHaveBeenCalled();
});

test("провайдер ещё проверяется — пункт доступен", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant({ provider: null, checking: true }));
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  const item = await openMenu();
  await waitFor(() => expect(api.getAssistant).toHaveBeenCalled());
  expect(item).toBeEnabled();
});

test("отказ живого режима (409) — текст ошибки рядом с кнопкой", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  vi.spyOn(api, "liveStart").mockRejectedValue(
    new api.ApiError(409, "Подключите Claude Code, Codex или OpenCode в настройках"));
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await userEvent.click(await openMenu());
  expect(await screen.findByRole("alert")).toHaveTextContent("Подключите Claude Code, Codex или OpenCode в настройках");
});

test("меню открывается и из контекстного меню кнопки записи", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await userEvent.pointer({ keys: "[MouseRight]", target: startButton() });
  expect(await screen.findByRole("menu", { name: "Варианты записи" })).toBeInTheDocument();
  expect(startButton()).toHaveAttribute("aria-expanded", "true");
});

test("Esc закрывает меню", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await openMenu();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("при живом режиме: «Идёт запись · мм:сс · ассистент», «Остановить и сохранить» сразу вызывает liveStop (меню нет)", async () => {
  const liveStop = vi.spyOn(api, "liveStop").mockResolvedValue({ ok: true, action: "stopping", ...live({ stopping: true }) });
  const rec = vi.spyOn(api, "recordingCommand");
  render(<RecordingBadge endpoint={ep}
    snapshot={snap({ live: live({ active: true, folder: "C:/r/x", started_at: Date.now() / 1000 - 65 }) })} />);
  expect(status()).toHaveTextContent(/^Идёт запись · 01:0\d · ассистент$/);
  expect(screen.queryByRole("button", { name: "Начать запись" })).toBeNull();
  expect(stop()).not.toHaveAttribute("aria-haspopup");
  await userEvent.click(stop());
  expect(screen.queryByRole("menu")).toBeNull();
  expect(liveStop).toHaveBeenCalledWith(ep);
  expect(rec).not.toHaveBeenCalled();
});

test("ассистент запускается: подпись и «Отменить запуск»", async () => {
  const cancel = vi.spyOn(api, "liveStop").mockResolvedValue({ ok: true, action: "cancelled", ...live() });
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ starting: true }) })} />);
  expect(status()).toHaveTextContent("Ассистент запускается…");
  const button = screen.getByRole("button", { name: "Отменить запуск" });
  expect(button).toHaveClass("btn--danger");
  await userEvent.click(button);
  expect(cancel).toHaveBeenCalledWith(ep);
});

test("ассистент дописывает запись: «Останавливаю…», кнопка остановки неактивна", () => {
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ stopping: true, folder: "C:/r/x" }) })} />);
  expect(status()).toHaveTextContent("Останавливаю…");
  expect(stop()).toBeDisabled();
});

test("запуск не удался (ok:false) — ошибка видна", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  vi.spyOn(api, "liveStart").mockResolvedValue(
    { ok: false, ...live({ error: "Не удалось запустить ассистента: нет python" }) });
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await userEvent.click(await openMenu());
  expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось запустить ассистента: нет python");
});

test("старая ошибка живого режима из первого снимка не висит в простое", () => {
  render(<RecordingBadge endpoint={ep}
    snapshot={snap({ live: live({ error: "Ассистент завершился (код 1)" }) })} />);
  expect(screen.queryByRole("alert")).toBeNull();
});

test("новая ошибка живого режима видна, гаснет сама через несколько секунд", () => {
  vi.useFakeTimers();
  try {
    const { rerender } = render(<RecordingBadge endpoint={ep}
      snapshot={snap({ live: live({ active: true, started_at: Date.now() / 1000 }) })} />);
    expect(screen.queryByRole("alert")).toBeNull();
    rerender(<RecordingBadge endpoint={ep}
      snapshot={snap({ live: live({ error: "Ассистент завершился (код 1)" }) })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Ассистент завершился (код 1)");
    // Следующий снимок с той же ошибкой — не новая ошибка.
    act(() => { vi.advanceTimersByTime(5000); });
    rerender(<RecordingBadge endpoint={ep}
      snapshot={snap({ live: live({ error: "Ассистент завершился (код 1)" }) })} />);
    act(() => { vi.advanceTimersByTime(1500); });
    expect(screen.queryByRole("alert")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("ошибку живого режима можно скрыть; при запуске она не видна", async () => {
  const { rerender } = render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  rerender(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ error: "упал" }) })} />);
  expect(screen.getByRole("alert")).toHaveTextContent("упал");
  await userEvent.click(screen.getByRole("button", { name: "Скрыть ошибку" }));
  expect(screen.queryByRole("alert")).toBeNull();
  rerender(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ starting: true }) })} />);
  rerender(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ starting: true, error: "снова" }) })} />);
  expect(screen.queryByRole("alert")).toBeNull();
});

test("ошибку команды тоже можно скрыть", async () => {
  vi.spyOn(api, "recordingCommand").mockRejectedValue(new Error("микрофон занят"));
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={snap({})} />);
  await startPlain();
  await screen.findByText("микрофон занят");
  await userEvent.click(screen.getByRole("button", { name: "Скрыть ошибку" }));
  expect(screen.queryByRole("alert")).toBeNull();
});

test("меню закрывается, когда бейдж уходит из простоя", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const { rerender } = render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await openMenu();
  rerender(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ starting: true }) })} />);
  rerender(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  expect(screen.queryByRole("menu")).toBeNull();
  expect(startButton()).toHaveAttribute("aria-expanded", "false");
});

test("доступность меню: фокус на первый пункт («Записать»), Esc возвращает его на кнопку записи", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await openMenu();
  await waitFor(() => expect(screen.getByRole("menuitem", { name: /^Записать/ })).toHaveFocus());
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(startButton()).toHaveFocus();
});

test("неактивный «С ассистентом» связан с подсказкой; фокус — на «Записать»", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant({ provider: null }));
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  const item = await openMenu();
  await waitFor(() => expect(item).toBeDisabled());
  expect(item).toHaveAccessibleDescription("Подключите Claude Code, Codex или OpenCode в настройках");
  expect(screen.getByRole("menuitem", { name: /^Записать/ })).toHaveFocus();
});

test("время начала живого режима неизвестно — без таймера", () => {
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ active: true, folder: "C:/r/x" }) })} />);
  expect(status()).toHaveTextContent(/^Идёт запись · ассистент$/);
});

test("выбранный микрофон не найден — предупреждение в рейке; с какого пишем — в подсказке", () => {
  render(<RecordingWarnings endpoint={ep} snapshot={snap({
    status: "recording", source: "manual", elapsed_s: 5,
    devices_fallback: [
      { kind: "mic", name: "USB-микрофон", device: "Микрофон" },
      { kind: "output", name: "Наушники", device: "Колонки" },
    ],
  })} />);
  expect(screen.getByRole("button", { name: "Микрофон «USB-микрофон» не найден — запись с системного" }))
    .toHaveAccessibleDescription(/Запись идёт с «Микрофон»/);
  expect(screen.getByRole("button", { name: "Устройство вывода «Наушники» не найдено — запись с системного" }))
    .toHaveAccessibleDescription(/Запись идёт с «Колонки»/);
});

test("подмена устройства у ассистента тоже видна; вне записи — нет", () => {
  const fallback = [{ kind: "mic" as const, name: "USB-микрофон", device: "Микрофон" }];
  const live: LiveStatus = { active: true, starting: false, stopping: false, folder: "f", error: null, started_at: null };
  const { rerender } = render(<RecordingWarnings endpoint={ep} snapshot={snap({ live, devices_fallback: fallback })} />);
  expect(screen.getByRole("button", { name: /Микрофон «USB-микрофон» не найден/ })).toBeInTheDocument();
  rerender(<RecordingWarnings endpoint={ep} snapshot={snap({ devices_fallback: fallback })} />);
  expect(screen.queryByRole("button", { name: /не найден/ })).toBeNull();
});

test("запуск с ассистентом: этап виден; запись уже идёт, пока грузится модель", () => {
  const { rerender } = render(<RecordingBadge endpoint={ep}
    snapshot={snap({ live: live({ starting: true, stage: "открываю микрофон и звук…" }) })} />);
  expect(status()).toHaveTextContent("Ассистент запускается: открываю микрофон и звук…");
  rerender(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({
    active: true, ready: false, stage: "загружаю модель распознавания…",
    started_at: Date.now() / 1000 - 5 }) })} />);
  expect(status()).toHaveTextContent(/^Идёт запись · 00:0\d/);
  expect(status()).not.toHaveTextContent("· ассистент");
  expect(status()).toHaveTextContent("Ассистент запускается: загружаю модель распознавания…");
  expect(stop()).toBeEnabled();
});
