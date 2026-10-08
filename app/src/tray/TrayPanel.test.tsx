import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "../lib/api";
import type { AssistantInfo, LiveStatus, Recording, Snapshot } from "../lib/types";
import { NO_PROVIDER, TrayPanel } from "./TrayPanel";

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());

const snap = (extra: Partial<Snapshot> = {}): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
} as Snapshot);
const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: false, starting: false, stopping: false, folder: null, error: null, started_at: null, ...o,
});
const rec = (o: Partial<Recording> = {}): Recording => ({
  id: "2026-10-05_14-05", path: "D:\\rec\\2026-10-05_14-05", started_at: "2026-10-05T14:05:00",
  duration_s: 2537, tracks: {}, has_transcript: false, has_voices: false, title: "Планёрка отдела",
  source: "record", ...o,
});
const provider = (p: string | null): AssistantInfo =>
  ({ provider: p, setting: "auto", available: {}, knowledge_dir: null, checking: false });

function panel(props: Partial<Parameters<typeof TrayPanel>[0]> = {}) {
  const onOpen = vi.fn();
  const onSnapshot = vi.fn();
  const view = render(
    <TrayPanel endpoint={ep} snapshot={snap()} snapshotAt={Date.now()} online recent={null}
      justStopped={false} assistant={null} onSnapshot={onSnapshot} onOpen={onOpen} {...props} />,
  );
  return { ...view, onOpen, onSnapshot };
}

test("простой: «Начать запись» — тот же /recording/start, что у кнопки в окне; ответ применяется", async () => {
  const started = { ...snap({ status: "recording", elapsed_s: 0 }), ok: true, action: "started" };
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue(started);
  const { onSnapshot } = panel();
  expect(screen.getByText("Запись не идёт")).toBeInTheDocument();
  // Главное действие панели — главная кнопка Aurora, остальные — тише.
  expect(screen.getByRole("button", { name: "Начать запись" })).toHaveClass("btn", "btn--primary");
  expect(screen.getByRole("button", { name: "С ассистентом" })).toHaveClass("btn--outline");
  expect(screen.getByRole("button", { name: "Временная встреча с ассистентом" })).toHaveClass("btn--ghost");
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(spy).toHaveBeenCalledWith(ep, "start");
  expect(onSnapshot).toHaveBeenCalledWith(started);
});

test("простой с автозаписью — так и сказано; «С ассистентом» запускает /live/start", async () => {
  const spy = vi.spyOn(api, "liveStart").mockResolvedValue({ ok: true, ...live({ starting: true }) });
  const { onSnapshot } = panel({
    snapshot: snap({ auto_record: { enabled: true, processes: [], grace_seconds: 0, state: null, mic: null, render: null } }),
  });
  expect(screen.getByText("Автозапись включена")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "С ассистентом" }));
  expect(spy).toHaveBeenCalledWith(ep);
  expect(onSnapshot.mock.calls[0]?.[0].live.starting).toBe(true);
});

test("запись: большой таймер тикает, название звонка, «авто», «Остановить» — /recording/stop", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
    panel({
      snapshot: snap({ status: "recording", source: "auto", elapsed_s: 754, title: "Google Meet — Планёрка" }),
      snapshotAt: Date.now(),
    });
    expect(screen.getByRole("timer")).toHaveTextContent("12:34");
    expect(screen.getByRole("timer")).toHaveClass("tp__timer");
    expect(screen.getByText("Google Meet — Планёрка")).toBeInTheDocument();
    // Остановка — красная кнопка Aurora; запись без сохранения — тише, но тоже «опасная».
    expect(screen.getByRole("button", { name: "Остановить" })).toHaveClass("btn", "btn--danger");
    expect(screen.getByText("авто")).toBeInTheDocument();
    expect(screen.queryByText("с ассистентом")).toBeNull();
    act(() => { vi.advanceTimersByTime(2000); });
    expect(screen.getByRole("timer")).toHaveTextContent("12:36");
    await userEvent.click(screen.getByRole("button", { name: "Остановить" }));
    expect(spy).toHaveBeenCalledWith(ep, "stop");
    // Паузы нет.
    expect(screen.queryByRole("button", { name: /пауз/i })).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("спрятанная панель таймер не крутит", () => {
  vi.useFakeTimers();
  try {
    panel({ snapshot: snap({ status: "recording", elapsed_s: 10 }), snapshotAt: Date.now(), visible: false });
    expect(screen.getByRole("timer")).toHaveTextContent("00:10");
    act(() => { vi.advanceTimersByTime(3000); });
    expect(screen.getByRole("timer")).toHaveTextContent("00:10");
  } finally {
    vi.useRealTimers();
  }
});

test("ассистент, включённый в запись, — метка «с ассистентом»; остановка — всё та же запись", async () => {
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  const stopLive = vi.spyOn(api, "liveStop");
  panel({ snapshot: snap({ status: "recording", elapsed_s: 5, live: live({ active: true, attached: true }) }) });
  expect(screen.getByText("с ассистентом")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Остановить" }));
  expect(spy).toHaveBeenCalledWith(ep, "stop");
  expect(stopLive).not.toHaveBeenCalled();
});

test("ассистент запускается посреди записи — метка об этом", () => {
  panel({ snapshot: snap({ status: "recording", live: live({ starting: true, attached: true }) }) });
  expect(screen.getByText("ассистент запускается…")).toBeInTheDocument();
});

test("запись с ассистентом с начала: таймер от started_at, остановка — /live/stop", async () => {
  const now = Date.now();
  const spy = vi.spyOn(api, "liveStop").mockResolvedValue({ ok: true, action: "stopping", ...live({ stopping: true }) });
  const { onSnapshot } = panel({ snapshot: snap({ live: live({ active: true, started_at: now / 1000 - 65 }) }) });
  expect(screen.getByRole("timer")).toHaveTextContent("01:05");
  expect(screen.getByText("с ассистентом")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Остановить" }));
  expect(spy).toHaveBeenCalledWith(ep);
  expect(onSnapshot.mock.calls[0]?.[0].live.stopping).toBe(true);
});

test("ассистент запускается — запись уже идёт, «Остановить» — /live/stop", async () => {
  const spy = vi.spyOn(api, "liveStop").mockResolvedValue({ ok: true, action: "stopping", ...live({ stopping: true }) });
  panel({ snapshot: snap({ live: live({ starting: true }) }) });
  expect(screen.getByText("Запись уже идёт — ассистент загружает модель.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Остановить" }));
  expect(spy).toHaveBeenCalledWith(ep);
});

test("ассистент дописывает запись — «Остановить» недоступна", () => {
  panel({ snapshot: snap({ live: live({ active: true, stopping: true }) }) });
  expect(screen.getByText("Сохраняю запись…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Остановить" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Остановить" })).toHaveClass("btn--danger");
});

test("только что остановлена: «Открыть запись» открывает окно на её карточке", async () => {
  const recent = rec();
  const { onOpen } = panel({ recent, justStopped: true });
  expect(screen.getByText("Запись сохранена")).toBeInTheDocument();
  expect(screen.getByText("42:17")).toBeInTheDocument();
  expect(screen.getByText("Планёрка отдела")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Открыть запись" })).toHaveClass("btn--primary");
  await userEvent.click(screen.getByRole("button", { name: "Открыть запись" }));
  expect(onOpen).toHaveBeenCalledWith({ recording: "2026-10-05_14-05" });
  expect(screen.getByRole("button", { name: "Начать новую запись" })).toHaveClass("btn--outline");
});

test("простой: последняя запись видна строкой и открывается щелчком", async () => {
  const { onOpen } = panel({ recent: rec({ title: null }) });
  const row = screen.getByRole("button", { name: /Открыть запись «Запись — .*14:05»/ });
  expect(row).toHaveTextContent("42 мин");
  await userEvent.click(row);
  expect(onOpen).toHaveBeenCalledWith({ recording: "2026-10-05_14-05" });
});

test("«Открыть Meet» — окно без записи", async () => {
  const { onOpen } = panel();
  await userEvent.click(screen.getByRole("button", { name: "Открыть Meet" }));
  expect(onOpen).toHaveBeenCalledWith({});
});

test("ошибка команды видна в панели и скрывается", async () => {
  vi.spyOn(api, "recordingCommand").mockRejectedValue(new Error("микрофон занят"));
  panel();
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("микрофон занят");
  await userEvent.click(screen.getByRole("button", { name: "Скрыть ошибку" }));
  expect(screen.queryByRole("alert")).toBeNull();
});

test("ассистент не запустился сразу (ok: false) — его ошибка", async () => {
  vi.spyOn(api, "liveStart").mockResolvedValue({ ok: false, ...live({ error: "нет интерпретатора" }) });
  panel();
  await userEvent.click(screen.getByRole("button", { name: "С ассистентом" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("нет интерпретатора");
});

test("нет провайдера (409) — подсказка и ссылка в раздел «Модели» настроек", async () => {
  vi.spyOn(api, "liveStart").mockRejectedValue(new api.ApiError(409, "no provider"));
  const { onOpen } = panel();
  await userEvent.click(screen.getByRole("button", { name: "С ассистентом" }));
  expect(await screen.findByText(new RegExp(NO_PROVIDER))).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "С ассистентом" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Открыть настройки" }));
  expect(onOpen).toHaveBeenCalledWith({ section: "models" });
});

test("провайдер не подключён заранее — «С ассистентом» недоступна сразу", () => {
  panel({ assistant: provider(null) });
  expect(screen.getByRole("button", { name: "С ассистентом" })).toBeDisabled();
  expect(screen.getByText(new RegExp(NO_PROVIDER))).toBeInTheDocument();
  // Тот же текст, что у кнопки записи окна: OpenCode тоже годится.
  expect(screen.getByText(/Подключите Claude Code, Codex или OpenCode в настройках/)).toBeInTheDocument();
});

test("резидента нет — так и сказано, кнопок записи нет, окно открыть можно", () => {
  panel({ online: false, snapshot: null });
  expect(screen.getByText("Служба записи не запущена")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Начать запись" })).toBeNull();
  expect(screen.getByRole("button", { name: "Открыть Meet" })).toBeInTheDocument();
});

test("подмена микрофона во время записи — предупреждение", () => {
  panel({ snapshot: snap({ status: "recording", devices_fallback: [{ kind: "mic", name: "USB", device: null }] }) });
  expect(screen.getByText("Микрофон «USB» не найден — запись с системного")).toBeInTheDocument();
});
