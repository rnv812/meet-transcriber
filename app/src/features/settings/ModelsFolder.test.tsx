import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModelsFolder } from "./ModelsFolder";
import { resetMoveForTests, startMove } from "./storageMove";
import * as shell from "../../lib/shell";

vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: vi.fn(() => true),
  pickFolder: vi.fn(),
  storageStatus: vi.fn(),
  storageMove: vi.fn(),
  storageCancel: vi.fn(async () => {}),
  modelsStorageCheck: vi.fn(),
  modelsStorageMove: vi.fn(),
  modelsStorageAbandon: vi.fn(async () => {}),
  modelsStorageReset: vi.fn(async () => {}),
  onStorageProgress: vi.fn(),
  onEngineProgress: vi.fn(),
}));

const DATA = "C:\\Users\\u\\AppData\\Local\\meet";
const SHARED = "C:\\Users\\u\\.cache\\huggingface\\hub";

const status = (over: Partial<shell.StorageStatus> = {}): shell.StorageStatus => ({
  root: null, home: DATA, default_home: DATA, missing: null, moving: false, cancellable: false,
  moving_kind: null, models_root: null, models_dir: `${DATA}\\models`, models_default: `${DATA}\\models`,
  models_missing: null, models_unreadable: false, models_interrupted: null, ...over,
});
const plan = (over: Partial<shell.StorageCheck> = {}): shell.StorageCheck => ({
  target: "F:\\Models", free_gb: 300.2, needs_gb: 4.6, engine_gb: 0, models_gb: 4.1, busy: null, error: null,
  ...over,
});

let progress: ((p: shell.StorageProgress) => void) | null = null;

beforeEach(() => {
  vi.clearAllMocks();
  resetMoveForTests();
  progress = null;
  vi.mocked(shell.inTauri).mockReturnValue(true);
  vi.mocked(shell.storageStatus).mockResolvedValue(status());
  vi.mocked(shell.onStorageProgress).mockImplementation(async (cb) => { progress = cb; return () => {}; });
  vi.mocked(shell.onEngineProgress).mockResolvedValue(() => {});
});

test("по умолчанию — рядом с движком, модели HF в общем кэше; можно перенести", async () => {
  render(<ModelsFolder cache={SHARED} />);
  expect(await screen.findByText(`${DATA}\\models`)).toBeInTheDocument();
  expect(screen.getByText(/в общем кэше Hugging Face/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Перенести…" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Вернуть в папку по умолчанию" })).toBeNull();
});

test("выбор папки — проверка, перенос с ходом, отменой и итогом", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("F:\\");
  vi.mocked(shell.modelsStorageCheck).mockResolvedValue(plan());
  let finish: (path: string) => void = () => {};
  vi.mocked(shell.modelsStorageMove).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(<ModelsFolder cache={SHARED} />);
  await userEvent.click(await screen.findByRole("button", { name: "Перенести…" }));
  expect(shell.pickFolder).toHaveBeenCalledWith(`${DATA}\\models`);
  expect(shell.modelsStorageCheck).toHaveBeenCalledWith("F:\\");
  expect(await screen.findByText("F:\\Models")).toBeInTheDocument();
  expect(screen.getByText(/Нужно около 4,6 ГБ, свободно 300,2 ГБ/)).toBeInTheDocument();
  expect(screen.getByText(/Движок не переносится/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
  // Оболочка уже разрешила подпапку: переносим в неё.
  expect(shell.modelsStorageMove).toHaveBeenCalledWith("F:\\Models");
  vi.mocked(shell.storageStatus).mockResolvedValue(status({ moving: true, moving_kind: "models", cancellable: true }));
  await waitFor(() => expect(progress).not.toBeNull());
  act(() => progress?.({ phase: "models", text: "Копирование моделей", done: 1024 ** 3, total: 4 * 1024 ** 3 }));
  expect(await screen.findByText("Копирование моделей")).toBeInTheDocument();
  expect(screen.getByText(/1 из 4 ГБ/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Перенести…" })).toBeNull();
  await userEvent.click(await screen.findByRole("button", { name: "Отменить" }, { timeout: 2000 }));
  expect(shell.storageCancel).toHaveBeenCalled();
  vi.mocked(shell.storageStatus).mockResolvedValue(status({ models_root: "F:\\Models", models_dir: "F:\\Models" }));
  await act(async () => finish("F:\\Models"));
  expect(await screen.findByText("Готово: модели — в F:\\Models")).toBeInTheDocument();
});

test("занято — перенести нельзя, причина видна", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("F:\\Models");
  vi.mocked(shell.modelsStorageCheck).mockResolvedValue(plan({ busy: "скачиваются модели" }));
  render(<ModelsFolder cache={SHARED} />);
  await userEvent.click(await screen.findByRole("button", { name: "Перенести…" }));
  expect(await screen.findByText(/Перенести сейчас нельзя: скачиваются модели/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Перенести" })).toBeDisabled();
});

test("отдельная папка — можно вернуть модели в папку по умолчанию", async () => {
  vi.mocked(shell.storageStatus).mockResolvedValue(status({ models_root: "F:\\Models", models_dir: "F:\\Models" }));
  vi.mocked(shell.modelsStorageCheck).mockResolvedValue(plan({ target: `${DATA}\\models` }));
  vi.mocked(shell.modelsStorageMove).mockResolvedValue(`${DATA}\\models`);
  render(<ModelsFolder cache="F:\\Models\\hf" />);
  expect(await screen.findByText("F:\\Models")).toBeInTheDocument();
  expect(screen.getByText(/в этой папке/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Вернуть в папку по умолчанию" }));
  expect(shell.modelsStorageCheck).toHaveBeenCalledWith(null);
  expect(await screen.findByText(/Вернуть модели в папку по умолчанию/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
  expect(shell.modelsStorageMove).toHaveBeenCalledWith(null);
});

test("прерванный перенос — «Продолжить» и «Отменить перенос»", async () => {
  vi.mocked(shell.storageStatus).mockResolvedValue(status({
    models_interrupted: { to: "F:\\Models", to_dir: "F:\\Models" },
  }));
  vi.mocked(shell.modelsStorageMove).mockResolvedValue("F:\\Models");
  render(<ModelsFolder cache={SHARED} />);
  expect(await screen.findByText(/прерван/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Перенести…" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Отменить перенос" }));
  expect(shell.modelsStorageAbandon).toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  expect(shell.modelsStorageMove).toHaveBeenCalledWith("F:\\Models");
});

test("диск с моделями отключён — сказано, и можно вернуть в папку по умолчанию", async () => {
  vi.mocked(shell.storageStatus).mockResolvedValue(status({
    models_root: "F:\\Models", models_dir: "F:\\Models", models_missing: "F:\\Models",
  }));
  render(<ModelsFolder cache="F:\\Models\\hf" />);
  expect(await screen.findByText(/Папка моделей недоступна/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Перенести…" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Вернуть в папку по умолчанию" }));
  expect(shell.modelsStorageReset).toHaveBeenCalled();
});

test("идёт перенос движка — кнопок переноса моделей нет, его хода тоже", async () => {
  vi.mocked(shell.storageMove).mockImplementation(() => new Promise(() => {}));
  render(<ModelsFolder cache={SHARED} />);
  await screen.findByRole("button", { name: "Перенести…" });
  act(() => { void startMove("E:\\Meet"); });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Перенести…" })).toBeNull());
  await waitFor(() => expect(progress).not.toBeNull());
  act(() => progress?.({ phase: "models", text: "Копирование моделей", done: 1, total: 2 }));
  expect(screen.queryByText("Копирование моделей")).toBeNull();
});

test("окно в браузере — только путь", async () => {
  vi.mocked(shell.inTauri).mockReturnValue(false);
  vi.mocked(shell.storageStatus).mockResolvedValue(null);
  render(<ModelsFolder cache={SHARED} />);
  expect(await screen.findByText(SHARED)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Перенести…" })).toBeNull();
});
