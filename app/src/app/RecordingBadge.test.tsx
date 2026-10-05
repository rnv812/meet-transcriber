import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { RecordingBadge } from "./RecordingBadge";
import * as api from "../lib/api";
import type { AssistantInfo, LiveStatus, Snapshot } from "../lib/types";

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());
const snap = (extra: Partial<Snapshot>): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
});

test("при записи: REC, таймер, Стоп вызывает recordingCommand stop", async () => {
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  render(<RecordingBadge endpoint={ep} snapshot={snap({ status: "recording", source: "manual", elapsed_s: 754 })} />);
  expect(screen.getByText(/REC/)).toHaveTextContent("12:34");
  await userEvent.click(screen.getByRole("button", { name: /Стоп/ }));
  expect(spy).toHaveBeenCalledWith(ep, "stop");
});

test("автозапись помечена «авто»", () => {
  render(<RecordingBadge endpoint={ep} snapshot={snap({ status: "recording", source: "auto", elapsed_s: 5 })} />);
  expect(screen.getByText("авто")).toBeInTheDocument();
});

test("вне записи — Начать запись; мало места — предупреждение", async () => {
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  render(<RecordingBadge endpoint={ep} snapshot={snap({ disk_free_gb: 3.2 })} />);
  expect(screen.getByText("Мало места: 3.2 ГБ")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(spy).toHaveBeenCalledWith(ep, "start");
});

test("ошибка команды видна рядом с кнопкой", async () => {
  vi.spyOn(api, "recordingCommand").mockRejectedValue(new Error("микрофон занят"));
  render(<RecordingBadge endpoint={ep} snapshot={snap({})} />);
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(await screen.findByText("микрофон занят")).toBeInTheDocument();
});

test("ответ команды применяется сразу: REC без ожидания опроса", async () => {
  vi.spyOn(api, "recordingCommand").mockResolvedValue(
    { ...snap({ status: "recording", source: "manual", elapsed_s: 0 }), ok: true, action: "started" });
  function Harness() {
    const [s, setS] = useState(snap({}));
    return <RecordingBadge endpoint={ep} snapshot={s} onSnapshot={setS} />;
  }
  render(<Harness />);
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(await screen.findByText(/REC/)).toHaveTextContent("00:00");
  expect(screen.getByRole("button", { name: "Стоп" })).toBeInTheDocument();
});

test("таймер тикает каждую секунду между опросами", () => {
  vi.useFakeTimers();
  try {
    const at = Date.now();
    render(<RecordingBadge endpoint={ep} snapshotAt={at}
      snapshot={snap({ status: "recording", source: "manual", elapsed_s: 10 })} />);
    expect(screen.getByText(/REC/)).toHaveTextContent("00:10");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(screen.getByText(/REC/)).toHaveTextContent("00:11");
    act(() => { vi.advanceTimersByTime(2000); });
    expect(screen.getByText(/REC/)).toHaveTextContent("00:13");
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
  await userEvent.click(screen.getByRole("button", { name: "Другие варианты записи" }));
  return screen.findByRole("menuitem", { name: /С ассистентом/ });
};

test("«▾» открывает меню: «С ассистентом» запускает живой режим, ответ применяется сразу", async () => {
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
  expect(start).toHaveBeenCalledWith(ep);
  expect(await screen.findByText("Ассистент запускается…")).toBeInTheDocument();
  expect(screen.queryByRole("menu")).toBeNull();
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

test("Esc закрывает меню", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  await openMenu();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("при живом режиме: «● REC · ассистент», Стоп вызывает liveStop", async () => {
  const stop = vi.spyOn(api, "liveStop").mockResolvedValue({ ok: true, action: "stopping", ...live({ stopping: true }) });
  const rec = vi.spyOn(api, "recordingCommand");
  render(<RecordingBadge endpoint={ep}
    snapshot={snap({ live: live({ active: true, folder: "C:/r/x", started_at: Date.now() / 1000 - 65 }) })} />);
  expect(screen.getByText(/REC/)).toHaveTextContent(/● REC 01:0\d · ассистент/);
  expect(screen.queryByRole("button", { name: "Начать запись" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Стоп" }));
  expect(stop).toHaveBeenCalledWith(ep);
  expect(rec).not.toHaveBeenCalled();
});

test("ассистент запускается: подпись и Стоп (отмена запуска)", async () => {
  const stop = vi.spyOn(api, "liveStop").mockResolvedValue({ ok: true, action: "cancelled", ...live() });
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ starting: true }) })} />);
  expect(screen.getByText("Ассистент запускается…")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Стоп" }));
  expect(stop).toHaveBeenCalledWith(ep);
});

test("ассистент дописывает запись: «Останавливаю…», Стоп неактивен", () => {
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ stopping: true, folder: "C:/r/x" }) })} />);
  expect(screen.getByText("Останавливаю…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Стоп" })).toBeDisabled();
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
  render(<RecordingBadge endpoint={ep} snapshot={snap({})} />);
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
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
  expect(screen.getByRole("button", { name: "Другие варианты записи" })).toHaveAttribute("aria-expanded", "false");
});

test("доступность меню: фокус на первый пункт, Esc возвращает его на «▾»", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  const item = await openMenu();
  await waitFor(() => expect(item).toHaveFocus());
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(screen.getByRole("button", { name: "Другие варианты записи" })).toHaveFocus();
});

test("неактивный «С ассистентом» связан с подсказкой; фокус остаётся на «▾»", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant({ provider: null }));
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live() })} />);
  const item = await openMenu();
  await waitFor(() => expect(item).toBeDisabled());
  expect(item).toHaveAccessibleDescription("Подключите Claude Code, Codex или OpenCode в настройках");
  expect(screen.getByRole("button", { name: "Другие варианты записи" })).toHaveFocus();
});

test("время начала живого режима неизвестно — без таймера", () => {
  render(<RecordingBadge endpoint={ep} snapshot={snap({ live: live({ active: true, folder: "C:/r/x" }) })} />);
  expect(screen.getByText(/REC/)).toHaveTextContent(/^● REC · ассистент$/);
});

test("выбранный микрофон не найден — предупреждение у записи", () => {
  render(<RecordingBadge endpoint={ep} snapshot={snap({
    status: "recording", source: "manual", elapsed_s: 5,
    devices_fallback: [
      { kind: "mic", name: "USB-микрофон", device: "Микрофон" },
      { kind: "output", name: "Наушники", device: "Колонки" },
    ],
  })} />);
  expect(screen.getByText("Микрофон «USB-микрофон» не найден — запись с системного")).toBeInTheDocument();
  expect(screen.getByText("Устройство вывода «Наушники» не найдено — запись с системного")).toBeInTheDocument();
});

test("подмена устройства у ассистента тоже видна; вне записи — нет", () => {
  const fallback = [{ kind: "mic" as const, name: "USB-микрофон", device: "Микрофон" }];
  const live: LiveStatus = { active: true, starting: false, stopping: false, folder: "f", error: null, started_at: null };
  const { rerender } = render(<RecordingBadge endpoint={ep} snapshot={snap({ live, devices_fallback: fallback })} />);
  expect(screen.getByText(/Микрофон «USB-микрофон» не найден/)).toBeInTheDocument();
  rerender(<RecordingBadge endpoint={ep} snapshot={snap({ devices_fallback: fallback })} />);
  expect(screen.queryByText(/не найден/)).toBeNull();
});
