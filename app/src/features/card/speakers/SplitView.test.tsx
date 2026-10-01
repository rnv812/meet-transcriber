import { createRef } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../../lib/api";
import type { Job, SpeakerRow, SpeakersView, SplitPreview } from "../../../lib/types";
import { SpeakersPanel } from "./SpeakersPanel";
import { defaultChoices } from "./SplitView";

vi.mock("../../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../../lib/api")>()),
  getSpeakers: vi.fn(),
  prepareSplit: vi.fn(),
  previewSplit: vi.fn(),
  applySplit: vi.fn(),
  cancelJob: vi.fn(),
  thresholdPlan: vi.fn(),
  applyThreshold: vi.fn(),
}));

const ep = { base: "/api", token: null };
const row = (label: string, extra: Partial<SpeakerRow> = {}): SpeakerRow => ({
  label, name: /^Спикер/.test(label) ? null : label, seconds: 600, share: 0.4, turns: 40,
  samples: [], has_voice: true, suggestions: [], ...extra,
});
const view = (extra: Partial<SpeakersView> = {}): SpeakersView => ({
  owner: "Вы", history: [], pos: 0, voice_threshold: null, voice_threshold_default: 0.75,
  speakers: [row("Спикер 1"), row("Спикер 2", { turns: 1 }), row("Вы", { has_voice: false })], ...extra,
});
const people = [
  { name: "Анна Смирнова", color: "#4b6bd6", has_avatar: false },
  { name: "Борис Козлов", color: "#c0793a", has_avatar: false },
];
const group = (key: string, idx: number[], extra = {}) => ({
  key, idx, seconds: 300, share: 0.5, turns: idx.length, voiced: idx.length, suggestions: [], name: null,
  samples: [{ start: idx[0]! * 10, end: idx[0]! * 10 + 4, text: `Фраза ${key}` }], ...extra,
});
const preview = (extra: Partial<SplitPreview> = {}): SplitPreview => ({
  label: "Спикер 1", mode: "auto", fingerprint: "fp1", segments: 6, voiced: 5, unsure: null,
  groups: [
    group("g0", [0, 2, 4], { suggestions: [{ name: "Борис Козлов", score: 0.81 }], name: "Борис Козлов" }),
    group("g1", [1, 3, 5]),
  ],
  ...extra,
});
const job = (state: Job["state"], extra: Partial<Job> = {}): Job => ({
  id: "v1", kind: "speaker_split", folder: "C:/rec/r1", state, stage: "voices", label: "голоса реплик",
  done: 3, total: 12, note: null, result: null, error: null, ...extra,
});

function setup(jobs: Job[] = []) {
  const cardRef = createRef<HTMLElement>();
  const handlers = { onClose: vi.fn(), onPlay: vi.fn(), onShowTurns: vi.fn(), onChanged: vi.fn() };
  const ui = (j: Job[]) => (
    <section ref={cardRef}>
      <SpeakersPanel endpoint={ep} recordingId="r1" people={people} open focus={null} version={1}
        playable cardRef={cardRef} jobs={j} {...handlers} />
    </section>
  );
  const utils = render(ui(jobs));
  return { ...handlers, rerender: (j: Job[]) => utils.rerender(ui(j)) };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSpeakers).mockResolvedValue(view());
  vi.mocked(api.previewSplit).mockResolvedValue(preview());
  vi.mocked(api.applySplit).mockResolvedValue(view({ pos: 1 }));
});

test("имена групп по умолчанию: из базы, затем прежний спикер, затем новые", () => {
  const p = preview({ groups: [group("a", [0]), group("b", [1], { name: "Анна" }), group("c", [2]),
    group("d", [3], { name: "Анна" })], unsure: group("unsure", [4]) });
  expect(defaultChoices(p)).toEqual({
    a: { to: "keep", remember: false }, b: { to: "Анна", remember: false },
    c: { to: null, remember: false }, d: { to: null, remember: false }, unsure: { to: "keep", remember: false },
  });
});

test("«Разделить…»: голоса готовы — предпросмотр, смена K, имена и применение одним шагом", async () => {
  vi.mocked(api.prepareSplit).mockResolvedValue({ label: "Спикер 1", segments: 6, voiced: 5, missing: 0,
    ready: true, fingerprint: "fp1" });
  const { onPlay, onChanged } = setup();
  const first = await screen.findByRole("region", { name: /^Спикер 1/ });
  // У спикера с одной репликой разделять нечего.
  expect(within(screen.getByRole("region", { name: /^Спикер 2/ })).queryByRole("button", { name: "Разделить…" })).toBeNull();
  await userEvent.click(within(first).getByRole("button", { name: "Разделить…" }));
  const wizard = screen.getByRole("region", { name: "Разделить спикера «Спикер 1»" });
  await userEvent.selectOptions(within(wizard).getAllByRole("combobox", { name: "Сколько голосов" })[0]!, "3");
  await userEvent.click(within(wizard).getByRole("button", { name: "Разделить по голосу" }));
  expect(api.previewSplit).toHaveBeenCalledWith(ep, "r1", { label: "Спикер 1", mode: "auto", k: 3 });
  const g1 = await within(wizard).findByRole("region", { name: "Голос 1" });
  expect(within(wizard).queryByText(/скорее всего, это один человек/)).toBeNull();
  expect(within(g1).getByRole("button", { name: /Кому отдать группу «Голос 1»: Борис Козлов/ })).toBeInTheDocument();
  await userEvent.click(within(g1).getByRole("button", { name: "Прослушать фразу с 00:00" }));
  expect(onPlay).toHaveBeenCalledWith(0, 4);
  await userEvent.click(within(g1).getByRole("checkbox", { name: /Запомнить голос/ }));
  const g2 = within(wizard).getByRole("region", { name: "Голос 2" });
  await userEvent.click(within(g2).getByRole("button", { name: /Кому отдать группу «Голос 2»/ }));
  await userEvent.click(within(g2).getByRole("option", { name: /Новый спикер без имени/ }));
  // Другое число голосов — новый предпросмотр без задачи.
  await userEvent.selectOptions(within(wizard).getByRole("combobox", { name: "Сколько голосов" }), "2");
  await waitFor(() => expect(api.previewSplit).toHaveBeenLastCalledWith(ep, "r1", { label: "Спикер 1", mode: "auto", k: 2 }));
  const again = await within(wizard).findByRole("region", { name: "Голос 2" });
  await userEvent.click(within(again).getByRole("button", { name: /Кому отдать группу «Голос 2»/ }));
  await userEvent.click(within(again).getByRole("option", { name: /Новый спикер без имени/ }));
  await userEvent.click(within(wizard).getByRole("button", { name: "Применить разделение" }));
  expect(api.applySplit).toHaveBeenCalledWith(ep, "r1", {
    label: "Спикер 1", mode: "auto", fingerprint: "fp1",
    groups: [{ idx: [0, 2, 4], to: "Борис Козлов", remember: false }, { idx: [1, 3, 5], to: null, remember: false }],
  });
  expect(await screen.findByText(/Спикер разделён/)).toBeInTheDocument();
  expect(onChanged).toHaveBeenCalled();
});

test("голоса не посчитаны: задача с прогрессом, готово — предпросмотр; по образцам — группа «Не уверен»", async () => {
  vi.mocked(api.prepareSplit).mockResolvedValue({ label: "Спикер 1", segments: 6, voiced: 5, missing: 5,
    ready: false, fingerprint: "fp1", job: job("queued") });
  vi.mocked(api.previewSplit).mockResolvedValue(preview({
    mode: "people",
    groups: [group("p0", [0, 2], { person: "Анна Смирнова", name: "Анна Смирнова" }),
      group("p1", [1, 3], { person: "Борис Козлов", name: "Борис Козлов" })],
    unsure: group("unsure", [4, 5]),
  }));
  const { rerender } = setup([job("queued")]);
  await userEvent.click(within(await screen.findByRole("region", { name: /^Спикер 1/ })).getByRole("button", { name: "Разделить…" }));
  const wizard = screen.getByRole("region", { name: "Разделить спикера «Спикер 1»" });
  await userEvent.click(within(wizard).getByRole("radio", { name: /По образцам из базы голосов/ }));
  expect(within(wizard).getByRole("button", { name: "Разделить по голосу" })).toBeDisabled();
  await userEvent.click(within(wizard).getByRole("checkbox", { name: "Анна Смирнова" }));
  await userEvent.click(within(wizard).getByRole("checkbox", { name: "Борис Козлов" }));
  await userEvent.click(within(wizard).getByRole("button", { name: "Разделить по голосу" }));
  expect(await within(wizard).findByText(/Задача в очереди/)).toBeInTheDocument();
  rerender([job("running", { done: 6 })]);
  expect(await within(wizard).findByText("Считаются голоса реплик: 6 из 12")).toBeInTheDocument();
  rerender([job("done", { done: 12 })]);
  expect(await within(wizard).findByRole("region", { name: "Не уверен" })).toBeInTheDocument();
  expect(api.previewSplit).toHaveBeenCalledWith(ep, "r1",
    { label: "Спикер 1", mode: "people", people: ["Анна Смирнова", "Борис Козлов"] });
  await userEvent.click(within(wizard).getByRole("button", { name: "Применить разделение" }));
  expect(api.applySplit).toHaveBeenCalledWith(ep, "r1", expect.objectContaining({
    mode: "people",
    groups: [{ idx: [0, 2], to: "Анна Смирнова", remember: false }, { idx: [1, 3], to: "Борис Козлов", remember: false },
      { idx: [4, 5], to: "Спикер 1", remember: false }],
  }));
});

test("задача счёта голосов не удалась — ошибка и возврат к выбору способа", async () => {
  vi.mocked(api.prepareSplit).mockResolvedValue({ label: "Спикер 1", segments: 6, voiced: 5, missing: 5,
    ready: false, fingerprint: "fp1", job: job("running") });
  const { rerender } = setup([job("running")]);
  await userEvent.click(within(await screen.findByRole("region", { name: /^Спикер 1/ })).getByRole("button", { name: "Разделить…" }));
  await userEvent.click(screen.getByRole("button", { name: "Разделить по голосу" }));
  await screen.findByText(/Считаются голоса реплик/);
  rerender([job("failed", { error: "движок расшифровки не установлен" })]);
  expect(await screen.findByRole("alert")).toHaveTextContent("движок расшифровки не установлен");
  expect(screen.getByRole("button", { name: "Разделить по голосу" })).toBeInTheDocument();
});

test("похожие голоса групп — предупреждение, что это, скорее всего, один человек", async () => {
  vi.mocked(api.prepareSplit).mockResolvedValue({ label: "Спикер 1", segments: 6, voiced: 5, missing: 0,
    ready: true, fingerprint: "fp1" });
  vi.mocked(api.previewSplit).mockResolvedValue(preview({ similar: 0.93 }));
  setup();
  await userEvent.click(within(await screen.findByRole("region", { name: /^Спикер 1/ })).getByRole("button", { name: "Разделить…" }));
  await userEvent.click(screen.getByRole("button", { name: "Разделить по голосу" }));
  expect(await screen.findByText(/Голоса групп очень похожи \(93%\): скорее всего, это один человек/)).toBeInTheDocument();
});

test("порог узнавания: предпросмотр изменений по ползунку и применение", async () => {
  vi.mocked(api.thresholdPlan).mockImplementation(async (_ep, _id, value) => ({
    value, rows: [],
    changes: value < 0.75 ? [{ label: "Спикер 1", auto: true, best: "Анна Смирнова", score: 0.71, to: "Анна Смирнова" }] : [],
  }));
  vi.mocked(api.applyThreshold).mockResolvedValue(view({ voice_threshold: 0.7, pos: 1,
    step: { id: "t", at: "2026-09-30T17:00:00", enrolled: [], created_people: [], ops: [{ type: "threshold", value: 0.7 },
      { type: "rename", label: "Спикер 1", from: "Спикер 1", to: "Анна Смирнова" }] } }));
  const { onChanged } = setup();
  await screen.findByRole("region", { name: /^Спикер 1/ });
  await userEvent.click(screen.getByRole("button", { name: /Порог узнавания голоса: 75% \(общий\)/ }));
  const slider = screen.getByRole("slider", { name: "Порог узнавания голоса, %" });
  expect(await screen.findByText("Имена спикеров не изменятся.")).toBeInTheDocument();
  // fireEvent: у ползунка нет набора с клавиатуры в jsdom.
  const { fireEvent } = await import("@testing-library/react");
  fireEvent.change(slider, { target: { value: "70" } });
  expect(await screen.findByText(/Спикер 1 → Анна Смирнова/)).toBeInTheDocument();
  expect(screen.getByText(/похож на Анна Смирнова: 71%/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Применить" }));
  expect(api.applyThreshold).toHaveBeenCalledWith(ep, "r1", 0.7);
  expect(await screen.findByText("Имена пересчитаны с новым порогом.")).toBeInTheDocument();
  expect(onChanged).toHaveBeenCalled();
});
