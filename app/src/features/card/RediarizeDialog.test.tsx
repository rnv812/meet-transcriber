import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import type { Job, RediarizePreview } from "../../lib/types";
import { RediarizeDialog } from "./RediarizeDialog";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  rediarize: vi.fn(),
  getRediarized: vi.fn(),
  applyRediarized: vi.fn(),
  discardRediarized: vi.fn(),
  cancelJob: vi.fn(),
}));

const ep = { base: "/api", token: null };
const job = (state: Job["state"], stage: string | null = null): Job => ({
  id: "rd1", kind: "rediarize", folder: "C:/rec/r1", state, stage, label: null, done: null, total: null,
  note: null, result: null, error: state === "failed" ? "нет доступа к модели" : null,
});
const preview: RediarizePreview = {
  created_at: "2026-09-30T18:00:00", params: { num_speakers: 3 }, stale: false, before: 2, changed: 14, cut: 3,
  segments: 120,
  speakers: [
    { label: "Анна Смирнова", seconds: 600, share: 0.5, turns: 20, samples: [{ start: 12, end: 20, text: "Склад готов." }] },
    { label: "Спикер 1", seconds: 300, share: 0.25, turns: 9, samples: [] },
    { label: "Вы", seconds: 300, share: 0.25, turns: 12, samples: [] },
  ],
};

function setup(props: Partial<Parameters<typeof RediarizeDialog>[0]> = {}) {
  const handlers = { onPlay: vi.fn(), onClose: vi.fn(), onApplied: vi.fn() };
  const ui = (p: Partial<Parameters<typeof RediarizeDialog>[0]>) => (
    <RediarizeDialog endpoint={ep} id="r1" folder="C:/rec/r1" jobs={[]} ready={false} twoTrack playable
      {...handlers} {...props} {...p} />
  );
  const utils = render(ui({}));
  return { ...handlers, rerender: (p: Partial<Parameters<typeof RediarizeDialog>[0]>) => utils.rerender(ui(p)) };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.rediarize).mockResolvedValue({ job: job("queued") });
  vi.mocked(api.getRediarized).mockResolvedValue(preview);
  vi.mocked(api.applyRediarized).mockResolvedValue({ owner: "Вы", history: [], pos: 1, speakers: [] });
  vi.mocked(api.discardRediarized).mockResolvedValue({ ok: true });
});

test("параметры: точное число собеседников и чувствительность → задача, прогресс, предпросмотр, применение", async () => {
  const { rerender, onPlay, onApplied, onClose } = setup();
  const dialog = screen.getByRole("dialog", { name: "Переразделить на спикеров" });
  expect(within(dialog).getByText("Сколько собеседников было на звонке (без вас)")).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("radio", { name: /Точно/ }));
  const n = within(dialog).getByRole("spinbutton", { name: "Число собеседников" });
  fireEvent.change(n, { target: { value: "5" } });
  fireEvent.change(within(dialog).getByRole("slider"), { target: { value: "70" } });
  expect(within(dialog).getByLabelText("Что такое чувствительность разделения")).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "Запустить" }));
  expect(api.rediarize).toHaveBeenCalledWith(ep, "r1", { num_speakers: 5, sensitivity: 0.7 });
  expect(await within(dialog).findByText("В очереди…")).toBeInTheDocument();
  rerender({ jobs: [job("running", "diarize")] });
  expect(await within(dialog).findByText("Разделение на спикеров…")).toBeInTheDocument();
  rerender({ jobs: [job("done", "render")] });
  expect(await within(dialog).findByText(/Было спикеров: 2, станет: 3\. Сменят спикера 14 из 120 фраз, разделятся по словам: 3/))
    .toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "Прослушать фразу с 00:12" }));
  expect(onPlay).toHaveBeenCalledWith(12, 18);
  await userEvent.click(within(dialog).getByRole("button", { name: "Применить" }));
  expect(api.applyRediarized).toHaveBeenCalledWith(ep, "r1");
  expect(onApplied).toHaveBeenCalled();
  expect(onClose).toHaveBeenCalled();
});

test("готовый результат открывается сразу; «Отказаться» удаляет его", async () => {
  const { onClose } = setup({ ready: true });
  expect(await screen.findByText(/Было спикеров: 2, станет: 3/)).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(onClose).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole("button", { name: "Отказаться" }));
  expect(api.discardRediarized).toHaveBeenCalledWith(ep, "r1");
  expect(onClose).toHaveBeenCalled();
});

test("устаревший результат применить нельзя; диапазон проверяется; ошибка задачи видна", async () => {
  vi.mocked(api.getRediarized).mockResolvedValue({ ...preview, stale: true });
  const { rerender } = setup({ ready: true, twoTrack: false });
  expect(await screen.findByText(/Расшифровку изменили после расчёта/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Применить" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Другие параметры" }));
  expect(screen.getByText("Сколько человек говорило")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("radio", { name: /^От/ }));
  fireEvent.change(screen.getByRole("spinbutton", { name: "Наименьшее число участников" }), { target: { value: "7" } });
  expect(screen.getByText("Наименьшее число больше наибольшего")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Запустить" })).toBeDisabled();
  fireEvent.change(screen.getByRole("spinbutton", { name: "Наименьшее число участников" }), { target: { value: "3" } });
  await userEvent.click(screen.getByRole("button", { name: "Запустить" }));
  expect(api.rediarize).toHaveBeenCalledWith(ep, "r1", { min_speakers: 3, max_speakers: 6 });
  rerender({ jobs: [job("failed")] });
  expect(await screen.findByRole("alert")).toHaveTextContent("нет доступа к модели");
});
