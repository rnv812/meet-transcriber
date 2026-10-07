/**
 * Запись образца голоса в один щелчок (v037): строка «Запишите образец своего
 * голоса» в окне ассистента и карточке записи сразу открывает окно записи с
 * текстом для чтения — тот же рекордер, что в мастере и настройках.
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import type { OwnerVoiceStatus, OwnerVoiceTake } from "../../lib/types";
import {
  NUDGE_HIDDEN_KEY, OwnerVoiceDialog, OwnerVoiceNudge, nudgeHidden, resetNudge, wantsOwnerSample,
} from "./OwnerVoiceDialog";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getOwnerVoice: vi.fn(),
  recordOwnerVoice: vi.fn(),
}));

const ep = { base: "/api", token: null };
const status = (extra: Partial<OwnerVoiceStatus> = {}): OwnerVoiceStatus => ({
  samples: [], take: null, ready: true, reason: null, recording: false, seconds: 25, ...extra,
});
const take = (state: OwnerVoiceTake["state"], extra: Partial<OwnerVoiceTake> = {}): OwnerVoiceTake => ({
  state, device: null, seconds: 25, started_at: 1, error: null, sample_id: null, job: "j1", ...extra,
});
const LEAD = "Микрофон не делится на голоса.";

beforeEach(() => {
  vi.clearAllMocks();
  resetNudge();
});

test("предлагать запись — только когда микрофон делится, а образца нет", () => {
  expect(wantsOwnerSample({ split: true, owner_profile: false })).toBe(true);
  expect(wantsOwnerSample({ split: true, owner_profile: true })).toBe(false);
  expect(wantsOwnerSample({ split: false, owner_profile: false })).toBe(false);
  expect(wantsOwnerSample(null)).toBe(false);
  expect(wantsOwnerSample(undefined)).toBe(false);
});

test("один щелчок: строка открывает окно записи с текстом для чтения, запись — с микрофона из настроек", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status());
  vi.mocked(api.recordOwnerVoice).mockResolvedValue(status({ take: take("recording") }));
  render(<OwnerVoiceNudge endpoint={ep} lead={LEAD} />);
  const note = screen.getByRole("note", { name: "Образец голоса" });
  expect(note).toHaveTextContent("Микрофон не делится на голоса. Запишите образец своего голоса — "
    + "прочитайте вслух короткий текст (25 с)");
  await userEvent.click(within(note).getByRole("button", { name: "Записать образец" }));
  const dialog = screen.getByRole("dialog", { name: "Мой голос" });
  expect(within(dialog).getByText(/Утро выдалось тихим/)).toBeInTheDocument();
  await userEvent.click(await within(dialog).findByRole("button", { name: "Начать запись" }));
  expect(api.recordOwnerVoice).toHaveBeenCalledWith(ep, null);
  expect(await within(dialog).findByText(/Читайте вслух/)).toBeInTheDocument();
});

test("идёт запись встречи: текст виден, кнопка ждёт конца встречи и сама становится доступной", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status({ recording: true }))
    .mockResolvedValue(status());
  render(<OwnerVoiceDialog endpoint={ep} onClose={() => {}} pollMs={5} readyPollMs={5} />);
  const dialog = screen.getByRole("dialog", { name: "Мой голос" });
  expect(await within(dialog).findByText(/Идёт запись встречи — запишите образец после неё/)).toBeInTheDocument();
  expect(within(dialog).getByText(/Утро выдалось тихим/)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Начать запись" })).toBeDisabled();
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "Начать запись" })).toBeEnabled());
});

test("записали образец — строка уходит; Esc закрывает окно", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValueOnce(status())
    .mockResolvedValue(status({ take: take("done", { sample_id: "s1" }) }));
  vi.mocked(api.recordOwnerVoice).mockResolvedValue(status({ take: take("analyzing") }));
  render(<OwnerVoiceNudge endpoint={ep} lead={LEAD} pollMs={5} readyPollMs={5} />);
  await userEvent.click(screen.getByRole("button", { name: "Записать образец" }));
  await userEvent.click(await screen.findByRole("button", { name: "Начать запись" }));
  expect(await screen.findByText("Голос записан.")).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("note", { name: "Образец голоса" })).toBeNull();
});

test("прежняя попытка, которую резидент помнит, — не повод прятать строку", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status({ take: take("done", { sample_id: "old" }) }));
  render(<OwnerVoiceNudge endpoint={ep} lead={LEAD} />);
  await userEvent.click(screen.getByRole("button", { name: "Записать образец" }));
  await screen.findByText(/Утро выдалось тихим/);
  await userEvent.click(screen.getByRole("button", { name: "Готово" }));
  expect(screen.getByRole("note", { name: "Образец голоса" })).toBeInTheDocument();
});

test("«Скрыть» — до перезапуска окна, ничего не блокирует", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status());
  const { unmount } = render(<OwnerVoiceNudge endpoint={ep} lead={LEAD} />);
  await userEvent.click(screen.getByRole("button", { name: "Скрыть до перезапуска" }));
  expect(screen.queryByRole("note")).toBeNull();
  expect(window.sessionStorage.getItem(NUDGE_HIDDEN_KEY)).toBe("1");
  expect(nudgeHidden()).toBe(true);
  unmount();
  render(<OwnerVoiceNudge endpoint={ep} lead={LEAD} />);
  expect(screen.queryByRole("note")).toBeNull();
  expect(api.getOwnerVoice).not.toHaveBeenCalled();
});

test("окно закрывается щелчком мимо него; фокус возвращается на кнопку", async () => {
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status());
  render(<OwnerVoiceNudge endpoint={ep} lead={LEAD} />);
  const open = screen.getByRole("button", { name: "Записать образец" });
  await userEvent.click(open);
  const dialog = screen.getByRole("dialog", { name: "Мой голос" });
  await act(async () => {
    await userEvent.pointer({ keys: "[MouseLeft]", target: dialog.parentElement! });
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(open).toHaveFocus();
});
