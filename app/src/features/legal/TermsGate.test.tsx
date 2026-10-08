import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import { acceptTerms, TERMS_SECTIONS, TERMS_VERSION, termsAccepted } from "../../lib/terms";
import type { Snapshot } from "../../lib/types";
import { TermsGate, TERMS_RECORDING } from "./TermsGate";
import { TermsText } from "./TermsText";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  recordingCommand: vi.fn(),
  liveStop: vi.fn(),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  closeWindow: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const getSettings = vi.mocked(api.getSettings);
const patchSettings = vi.mocked(api.patchSettings);

const notAccepted = () => getSettings.mockResolvedValue({ ui: { wizard_done: true, terms_accepted: "" } });

beforeEach(() => {
  getSettings.mockReset();
  patchSettings.mockReset();
  vi.mocked(api.recordingCommand).mockReset();
  vi.mocked(api.liveStop).mockReset();
  patchSettings.mockResolvedValue({ settings: { ui: { terms_accepted: TERMS_VERSION } }, restart_required: [] });
  vi.mocked(shell.closeWindow).mockClear();
});

const app = <button type="button">Окно Meet</button>;
const dialog = () => screen.findByRole("dialog", { name: "Прежде чем продолжить" });

test("условия не приняты — заслонка поверх окна; окно под ней отрисовано и недоступно", async () => {
  notAccepted();
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  const box = await dialog();
  expect(box).toHaveAttribute("aria-modal", "true");
  expect(box).toHaveClass("sheet");
  expect(box.closest(".backdrop")).toHaveClass("backdrop--modal");
  expect(screen.getByText("Окно Meet", { selector: "button" }).closest("[inert]")).not.toBeNull();
  for (const s of TERMS_SECTIONS) expect(within(box).getByRole("heading", { name: s.title })).toBeInTheDocument();
  expect(getSettings).toHaveBeenCalledWith(ep);
});

test("условия текущей версии уже приняты — заслонки нет", async () => {
  getSettings.mockResolvedValue({ ui: { terms_accepted: TERMS_VERSION } });
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  await waitFor(() => expect(getSettings).toHaveBeenCalled());
  await act(async () => {});
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Окно Meet" })).toBeInTheDocument();
});

test("принята старая версия текста — заслонка снова", async () => {
  getSettings.mockResolvedValue({ ui: { terms_accepted: "2020-01-01" } });
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  expect(await dialog()).toBeInTheDocument();
});

test("резидента нет или настройки не прочитались — окно не заслоняется", async () => {
  const { rerender } = render(<TermsGate endpoint={null}>{app}</TermsGate>);
  expect(getSettings).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
  getSettings.mockRejectedValue(new Error("нет связи"));
  rerender(<TermsGate endpoint={ep}>{app}</TermsGate>);
  await waitFor(() => expect(getSettings).toHaveBeenCalled());
  await act(async () => {});
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("резидент появился позже — заслонка показывается", async () => {
  notAccepted();
  const { rerender } = render(<TermsGate endpoint={null}>{app}</TermsGate>);
  rerender(<TermsGate endpoint={ep}>{app}</TermsGate>);
  expect(await dialog()).toBeInTheDocument();
});

test("фокус сразу на флажке; «Продолжить» недоступна, пока флажок не отмечен", async () => {
  notAccepted();
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  const box = await dialog();
  const check = within(box).getByRole("checkbox", { name: "Я прочитал(а) и принимаю эти условия" });
  expect(check).toHaveClass("cb");
  await waitFor(() => expect(check).toHaveFocus());
  const go = within(box).getByRole("button", { name: "Продолжить" });
  expect(go).toBeDisabled();
  await userEvent.click(check);
  expect(go).toBeEnabled();
  await userEvent.click(check);
  expect(go).toBeDisabled();
});

test("«Продолжить» — PATCH с версией условий, заслонка уходит", async () => {
  notAccepted();
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  const box = await dialog();
  await userEvent.click(within(box).getByRole("checkbox"));
  await userEvent.click(within(box).getByRole("button", { name: "Продолжить" }));
  expect(patchSettings).toHaveBeenCalledWith(ep, { ui: { terms_accepted: TERMS_VERSION } });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByRole("button", { name: "Окно Meet" }).closest("[inert]")).toBeNull();
});

test("PATCH не прошёл — ошибка в окне, окно остаётся, можно повторить", async () => {
  notAccepted();
  patchSettings.mockRejectedValueOnce(new Error("резидент не ответил"));
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  const box = await dialog();
  await userEvent.click(within(box).getByRole("checkbox"));
  await userEvent.click(within(box).getByRole("button", { name: "Продолжить" }));
  const alert = await within(box).findByRole("alert");
  expect(alert).toHaveTextContent("резидент не ответил");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  const go = within(box).getByRole("button", { name: "Продолжить" });
  expect(go).toBeEnabled();
  await userEvent.click(go);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(patchSettings).toHaveBeenCalledTimes(2);
});

test("Esc и щелчок по затемнению окно не закрывают; Esc не уходит дальше", async () => {
  notAccepted();
  const outer = vi.fn();
  document.addEventListener("keydown", outer);
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  const box = await dialog();
  fireEvent.keyDown(within(box).getByRole("checkbox"), { key: "Escape" });
  fireEvent.keyDown(document.body, { key: "Escape" });
  fireEvent.mouseDown(box.closest(".backdrop")!);
  fireEvent.click(box.closest(".backdrop")!);
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(outer).not.toHaveBeenCalled();
  document.removeEventListener("keydown", outer);
});

test("Tab ходит по кругу внутри окна", async () => {
  notAccepted();
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  const box = await dialog();
  const check = within(box).getByRole("checkbox");
  await waitFor(() => expect(check).toHaveFocus());
  await userEvent.click(check);
  const go = within(box).getByRole("button", { name: "Продолжить" });
  go.focus();
  await userEvent.tab();
  expect(box.contains(document.activeElement)).toBe(true);
  const first = document.activeElement as HTMLElement;
  await userEvent.tab({ shift: true });
  expect(go).toHaveFocus();
  // Фокус ушёл наружу (щелчок мимо) — Tab возвращает его в окно.
  (first as HTMLElement).blur();
  document.body.focus();
  await userEvent.tab();
  expect(box.contains(document.activeElement)).toBe(true);
});

test("«Закрыть Meet» закрывает окно, условия не принимаются", async () => {
  notAccepted();
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  const box = await dialog();
  await userEvent.click(within(box).getByRole("button", { name: "Закрыть Meet" }));
  expect(shell.closeWindow).toHaveBeenCalled();
  expect(patchSettings).not.toHaveBeenCalled();
});

test("deferred — заслонка ждёт (мастер со своим шагом); после — перечитывает настройки", async () => {
  notAccepted();
  const { rerender } = render(<TermsGate endpoint={ep} deferred>{app}</TermsGate>);
  await act(async () => {});
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(getSettings).not.toHaveBeenCalled();
  rerender(<TermsGate endpoint={ep}>{app}</TermsGate>);
  expect(await dialog()).toBeInTheDocument();
});

test("условия приняты в другом месте окна (шаг мастера) — заслонка уходит", async () => {
  notAccepted();
  render(<TermsGate endpoint={ep}>{app}</TermsGate>);
  await dialog();
  await act(async () => { await acceptTerms(ep); });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("termsAccepted — только текущая версия", () => {
  expect(termsAccepted({ ui: { terms_accepted: TERMS_VERSION } })).toBe(true);
  expect(termsAccepted({ ui: { terms_accepted: "" } })).toBe(false);
  expect(termsAccepted({ ui: {} })).toBe(false);
  expect(termsAccepted({})).toBe(false);
  expect(termsAccepted(null)).toBe(false);
});

test("TermsText — три раздела с заголовками; уровень заголовка задаётся", () => {
  render(<TermsText headingLevel={4} />);
  for (const s of TERMS_SECTIONS) {
    const h = screen.getByRole("heading", { name: s.title, level: 4 });
    expect(h).toBeInTheDocument();
  }
  expect(screen.getByText(/Anthropic/)).toBeInTheDocument();
  expect(screen.getByText(/Apache 2\.0/)).toBeInTheDocument();
});

test("текст условий — в пределах 150–220 слов", () => {
  const words = TERMS_SECTIONS.flatMap((s) => [s.title, ...s.paragraphs]).join(" ").split(/\s+/)
    .filter((w) => /[\p{L}\d]/u.test(w));
  expect(words.length).toBeGreaterThanOrEqual(150);
  expect(words.length).toBeLessThanOrEqual(220);
});

// --- Идёт запись (M3): остановить можно, не принимая условий ---------------------------------

const snap = (extra: Partial<Snapshot>): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
});
const recLine = async () => within(await dialog()).findByRole("group", { name: TERMS_RECORDING });

test("записи нет — строки «Идёт запись» в заслонке нет", async () => {
  notAccepted();
  render(<TermsGate endpoint={ep} snapshot={snap({})}>{app}</TermsGate>);
  const box = await dialog();
  expect(within(box).queryByRole("group", { name: TERMS_RECORDING })).toBeNull();
  expect(within(box).queryByRole("button", { name: "Остановить и сохранить" })).toBeNull();
});

test("идёт запись — «Остановить и сохранить» в заслонке: та же команда, что у рейки; условия не приняты", async () => {
  notAccepted();
  const stopped = { ...snap({ status: "idle" }), ok: true, action: "stop" };
  vi.mocked(api.recordingCommand).mockResolvedValue(stopped);
  const onSnapshot = vi.fn();
  render(<TermsGate endpoint={ep} snapshot={snap({ status: "recording", source: "auto", elapsed_s: 90 })}
    onSnapshot={onSnapshot}>{app}</TermsGate>);
  const line = await recLine();
  expect(line).toHaveTextContent(TERMS_RECORDING);
  await userEvent.click(within(line).getByRole("button", { name: "Остановить и сохранить" }));
  expect(api.recordingCommand).toHaveBeenCalledWith(ep, "stop");
  await waitFor(() => expect(onSnapshot).toHaveBeenCalledWith(stopped));
  // Условия не приняты и не сохранялись — заслонка на месте.
  expect(patchSettings).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "Прежде чем продолжить" })).toBeInTheDocument();
});

test("временная встреча — «Закончить временную встречу» с тем же вопросом, что в рейке", async () => {
  notAccepted();
  vi.mocked(api.recordingCommand).mockResolvedValue({ ...snap({}), ok: true, action: "stop" });
  render(<TermsGate endpoint={ep} snapshot={snap({ status: "recording", temporary: true })}>{app}</TermsGate>);
  const line = await recLine();
  await userEvent.click(within(line).getByRole("button", { name: "Закончить временную встречу" }));
  const ask = within(line).getByRole("alertdialog", { name: "Временная встреча закончится и будет удалена." });
  await userEvent.click(within(ask).getByRole("button", { name: "Продолжить" }));
  expect(api.recordingCommand).not.toHaveBeenCalled();
  await userEvent.click(within(line).getByRole("button", { name: "Закончить временную встречу" }));
  await userEvent.click(within(line).getByRole("button", { name: "Закончить" }));
  expect(api.recordingCommand).toHaveBeenCalledWith(ep, "stop");
});

test("запись с ассистентом без обычной — остановка через /live/stop; ошибка — в строке", async () => {
  notAccepted();
  const live = { active: true, starting: false, stopping: false, folder: "C:/rec/1", error: null, started_at: 1 };
  vi.mocked(api.liveStop).mockRejectedValueOnce(new Error("нет связи"))
    .mockResolvedValueOnce({ ok: true, action: "stop", ...live, active: false, stopping: true });
  const onSnapshot = vi.fn();
  render(<TermsGate endpoint={ep} snapshot={snap({ live })} onSnapshot={onSnapshot}>{app}</TermsGate>);
  const line = await recLine();
  const stop = within(line).getByRole("button", { name: "Остановить и сохранить" });
  await userEvent.click(stop);
  expect(await within(line).findByRole("alert")).toHaveTextContent("Не удалось остановить запись: нет связи");
  await userEvent.click(stop);
  await waitFor(() => expect(onSnapshot).toHaveBeenCalled());
  expect(onSnapshot.mock.calls[0]![0].live).toMatchObject({ active: false, stopping: true });
  expect(api.recordingCommand).not.toHaveBeenCalled();
});
