import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { CommandResult, LiveStatus, Snapshot } from "../../lib/types";
import { RecordingNow } from "./RecordingNow";

/** Страница «Идёт запись» в карточке записи (обычная запись, без ассистента). */

const ep = { base: "/api", token: null };
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });
const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: false, starting: false, stopping: false, folder: null, error: null, started_at: null, ...o,
});
const snap = (o: Partial<Snapshot> = {}): Snapshot => ({
  status: "recording", source: "manual", folder: "C:/rec/r1", elapsed_s: 767, levels: {},
  auto_record: { enabled: true, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, live: live(), ...o,
});
const reply = (s: Snapshot, action: string): CommandResult => ({ ...s, ok: true, action });

test("большой таймер мм:сс из elapsed_s, «Идёт запись · вручную», «Началась в чч:мм»", () => {
  render(<RecordingNow endpoint={ep} snapshot={snap()} startedAt="2026-10-07T21:05:00" autoTranscribe />);
  expect(screen.getByRole("timer", { name: "Время записи" })).toHaveTextContent("12:47");
  expect(screen.getByText("Идёт запись · вручную")).toBeInTheDocument();
  expect(screen.getByText(/Началась в 21:05\. После остановки встреча сразу расшифруется\./)).toBeInTheDocument();
});

test("часы идут сами между снимками", () => {
  vi.useFakeTimers();
  render(<RecordingNow endpoint={ep} snapshot={snap({ elapsed_s: 59 })} />);
  expect(screen.getByRole("timer")).toHaveTextContent("00:59");
  act(() => { vi.advanceTimersByTime(2000); });
  expect(screen.getByRole("timer")).toHaveTextContent("01:01");
});

test("автозапись — так и сказано; без авто-расшифровки — без обещания расшифровать", () => {
  render(<RecordingNow endpoint={ep} snapshot={snap({ source: "auto" })} startedAt="2026-10-07T09:30:00"
    autoTranscribe={false} />);
  expect(screen.getByText("Идёт запись · автозапись")).toBeInTheDocument();
  expect(screen.getByText(/Началась в 09:30\./)).not.toHaveTextContent("сразу расшифруется");
});

test("уровни микрофона и собеседников из snapshot.levels — доля без дБ", () => {
  const { rerender } = render(<RecordingNow endpoint={ep} snapshot={snap({ levels: { "mic.opus": 0.42, "sys.opus": 0.07 } })} />);
  const mic = screen.getByRole("meter", { name: "Микрофон" });
  const sys = screen.getByRole("meter", { name: "Собеседники" });
  expect(mic).toHaveAttribute("aria-valuenow", "42");
  expect(mic).toHaveAttribute("aria-valuemin", "0");
  expect(mic).toHaveAttribute("aria-valuemax", "100");
  expect(sys).toHaveAttribute("aria-valuenow", "7");
  expect(screen.queryByText(/дБ|dB/)).toBeNull();
  rerender(<RecordingNow endpoint={ep} snapshot={snap({ levels: { "mic.opus": 0.9 } })} />);
  expect(screen.getByRole("meter", { name: "Микрофон" })).toHaveAttribute("aria-valuenow", "90");
  expect(screen.getByRole("meter", { name: "Собеседники" })).toHaveAttribute("aria-valuenow", "0");
});

test("«Остановить и сохранить» — команда stop", async () => {
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(reply(snap({ status: "idle" }), "stop"));
  render(<RecordingNow endpoint={ep} snapshot={snap()} />);
  await userEvent.click(screen.getByRole("button", { name: "Остановить и сохранить" }));
  expect(command).toHaveBeenCalledWith(ep, "stop");
});

test("«Остановить без сохранения…»: тот же вопрос, фокус на «Отмена», «Удалить запись» — cancel", async () => {
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(reply(snap({ status: "idle" }), "cancel"));
  render(<RecordingNow endpoint={ep} snapshot={snap({ forget_gaps: ["Codex"] })} />);
  await userEvent.click(screen.getByRole("button", { name: "Остановить без сохранения…" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Остановить без сохранения?");
  expect(dialog).toHaveTextContent("История ассистента в Codex останется в самом Codex.");
  expect(within(dialog).getByRole("button", { name: "Отмена" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(command).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Остановить без сохранения…" }));
  await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Удалить запись" }));
  expect(command).toHaveBeenCalledWith(ep, "cancel");
});

test("ответы команд — сразу новый снимок: stop и attach (как у кнопки в рейке)", async () => {
  const stopped = reply(snap({ status: "idle" }), "stop");
  vi.spyOn(api, "recordingCommand").mockResolvedValue(stopped);
  const attached = { ok: true, ...live({ starting: true, attached: true }) };
  vi.spyOn(api, "liveAttach").mockResolvedValue(attached);
  const onSnapshot = vi.fn();
  const s = snap();
  render(<RecordingNow endpoint={ep} snapshot={s} onSnapshot={onSnapshot} />);
  await userEvent.click(screen.getByRole("button", { name: "Включить ассистента" }));
  expect(onSnapshot).toHaveBeenLastCalledWith({ ...s, live: live({ starting: true, attached: true }) });
  await userEvent.click(screen.getByRole("button", { name: "Остановить и сохранить" }));
  expect(onSnapshot).toHaveBeenLastCalledWith(stopped);
});

test("ошибка команды видна на странице", async () => {
  vi.spyOn(api, "recordingCommand").mockRejectedValue(new Error("резидент занят"));
  render(<RecordingNow endpoint={ep} snapshot={snap()} />);
  await userEvent.click(screen.getByRole("button", { name: "Остановить и сохранить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("резидент занят");
});

test("«Позвать ассистента» — существующий attach, пока ассистента нет", async () => {
  const attach = vi.spyOn(api, "liveAttach").mockResolvedValue({ ok: true, ...live({ starting: true, attached: true }) });
  render(<RecordingNow endpoint={ep} snapshot={snap()} />);
  const card = screen.getByRole("region", { name: "Позвать ассистента" });
  expect(card).toHaveTextContent("Запись не прервётся");
  await userEvent.click(within(card).getByRole("button", { name: "Включить ассистента" }));
  expect(attach).toHaveBeenCalledWith(ep);
});

test("attach не удался (ok:false) — причина видна", async () => {
  vi.spyOn(api, "liveAttach").mockResolvedValue({ ok: false, ...live({ error: "нет интерпретатора" }) });
  render(<RecordingNow endpoint={ep} snapshot={snap()} />);
  await userEvent.click(screen.getByRole("button", { name: "Включить ассистента" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("нет интерпретатора");
});

test("без модели «Включить ассистента» неактивна, причина рядом", () => {
  render(<RecordingNow endpoint={ep} snapshot={snap()} noModel="Подключите Claude Code, Codex или OpenCode в настройках" />);
  const button = screen.getByRole("button", { name: "Включить ассистента" });
  expect(button).toBeDisabled();
  expect(button).toHaveAccessibleDescription("Подключите Claude Code, Codex или OpenCode в настройках");
});

test("ассистент уже подключается — карточки нет, этап запуска в строке состояния", () => {
  render(<RecordingNow endpoint={ep}
    snapshot={snap({ live: live({ attached: true, starting: true, stage: "загружаю модель распознавания…" }) })} />);
  expect(screen.queryByRole("region", { name: "Позвать ассистента" })).toBeNull();
  expect(screen.getByText("Ассистент запускается: загружаю модель распознавания…")).toBeInTheDocument();
});

test("временная встреча: «не сохранится», «Закончить» спрашивает, «Сохранить как обычную встречу» — keep", async () => {
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(reply(snap(), "keep"));
  render(<RecordingNow endpoint={ep} snapshot={snap({ temporary: true, live: live({ attached: true, active: true }) })} />);
  expect(screen.getByText("Временная — не сохранится")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Остановить без сохранения…" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить как обычную встречу" }));
  expect(command).toHaveBeenCalledWith(ep, "keep");
  await userEvent.click(screen.getByRole("button", { name: "Закончить временную встречу" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Временная встреча закончится и будет удалена.");
  await userEvent.click(within(dialog).getByRole("button", { name: "Закончить" }));
  expect(command).toHaveBeenLastCalledWith(ep, "stop");
});

test("предупреждения — выноски: мало места и подмена устройства", () => {
  render(<RecordingNow endpoint={ep} snapshot={snap({
    disk_free_gb: 3,
    devices_fallback: [{ kind: "mic", name: "Jabra", device: "Realtek" }],
  })} />);
  expect(screen.getByRole("status", { name: "Мало места: 3 ГБ" })).toBeInTheDocument();
  expect(screen.getByRole("status", { name: "Микрофон «Jabra» не найден — запись с системного" }))
    .toHaveTextContent("Запись идёт с «Realtek»");
});

test("macOS без разрешения на системный звук: выноска role=status с подписанной кнопкой", async () => {
  const open = vi.spyOn(shell, "openScreenRecordingSettings").mockResolvedValue();
  const missing = { notice: "Звук собеседников не записывается — нет разрешения", permission: true };
  render(<RecordingNow endpoint={ep} snapshot={snap({ system_audio_missing: missing })} />);
  const note = screen.getByRole("status", { name: missing.notice });
  await userEvent.click(within(note).getByRole("button", { name: "Открыть настройки «Запись экрана»" }));
  expect(open).toHaveBeenCalledTimes(1);
  // Подпись дорожки собеседников говорит, что звука нет.
  expect(screen.getByText("не записывается")).toBeInTheDocument();
});

test("настройки не открылись — ошибка видна", async () => {
  vi.spyOn(shell, "openScreenRecordingSettings").mockRejectedValue(new Error("нет доступа"));
  const missing = { notice: "Звук собеседников не записывается", permission: true };
  render(<RecordingNow endpoint={ep} snapshot={snap({ system_audio_missing: missing })} />);
  await userEvent.click(screen.getByRole("button", { name: "Открыть настройки «Запись экрана»" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("нет доступа"));
});
