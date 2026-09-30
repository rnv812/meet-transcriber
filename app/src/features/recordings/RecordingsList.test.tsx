import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingsList } from "./RecordingsList";
import type { Job, Recording } from "../../lib/types";

const ep = { base: "/api", token: null };
const rec = (id: string, extra: Partial<Recording> = {}): Recording => ({
  id, path: `C:/rec/${id}`, started_at: "2026-09-30T10:00:00", duration_s: 1800, tracks: { sys: "s.wav" },
  has_transcript: false, has_voices: false, title: null, source: "record", ...extra,
});
const job = (folder: string, extra: Partial<Job>): Job => ({
  id: "j1", kind: "transcribe", folder, state: "running", stage: "asr", label: null,
  done: 1, total: 2, note: null, result: null, error: null, ...extra,
});

const items = [
  rec("a", { has_transcript: true, title: "Планёрка" }),
  rec("b"),
  rec("c"),
];
const jobs = [
  job("C:/rec/b", {}),
  job("C:/rec/c", { state: "failed", error: "нет памяти" }),
];
const library = { items, jobs, loading: false, error: null, refresh: async () => {} };
const resident = { status: "online" as const, endpoint: ep, snapshot: null, lastEvent: null, libraryTick: 0 };

function setup(props: Partial<Parameters<typeof RecordingsList>[0]> = {}) {
  const onSelect = vi.fn();
  const onQ = vi.fn();
  render(
    <RecordingsList selected="a" onSelect={onSelect} library={library} resident={resident} q="" onQ={onQ} {...props} />,
  );
  return { onSelect, onQ };
}

test("бейджи статусов: у готовой нет, у идущей процент, у упавшей ошибка", () => {
  setup();
  const opts = screen.getAllByRole("option");
  expect(opts).toHaveLength(3);
  expect(opts[0]!).toHaveTextContent("Планёрка");
  expect(opts[0]!).not.toHaveTextContent(/Ошибка|Распознавание|очереди/);
  expect(opts[0]!).toHaveAttribute("aria-selected", "true");
  expect(opts[1]!).toHaveTextContent("Распознавание 50%");
  expect(opts[1]!).toHaveAttribute("aria-selected", "false");
  expect(opts[2]!).toHaveTextContent("Ошибка");
});

test("клик по элементу вызывает onSelect(id)", async () => {
  const { onSelect } = setup();
  await userEvent.click(screen.getAllByRole("option")[1]!);
  expect(onSelect).toHaveBeenCalledWith("b");
});

test("ввод в поиск передаётся наверх", async () => {
  const { onQ } = setup();
  await userEvent.type(screen.getByRole("searchbox"), "а");
  expect(onQ).toHaveBeenLastCalledWith("а");
});

test("зона импорта: выбрать файл в браузере — сообщение, без пути", async () => {
  setup();
  expect(screen.getByText(/Перетащите аудио или видео сюда/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "выбрать файл" }));
  expect(await screen.findByText("Импорт — из приложения или перетаскиванием в окно приложения")).toBeInTheDocument();
});
