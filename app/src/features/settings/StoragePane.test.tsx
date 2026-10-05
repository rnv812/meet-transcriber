import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StoragePane } from "./StoragePane";
import { resetMoveForTests } from "./storageMove";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getStorage: vi.fn(),
  answerLeftovers: vi.fn(),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: vi.fn(() => true),
  pickFolder: vi.fn(),
  storageStatus: vi.fn(),
  storageCheck: vi.fn(),
  storageMove: vi.fn(),
  storageCancel: vi.fn(async () => {}),
  storageAbandon: vi.fn(async () => {}),
  onStorageProgress: vi.fn(),
  onEngineProgress: vi.fn(),
}));

const ep = { base: "/api", token: null };
const GB = 1024 ** 3;

const info = (over: Partial<api.StorageInfo> = {}): api.StorageInfo => ({
  root: null, custom: false, home: "C:\\Users\\u\\AppData\\Local\\meet",
  engine_dir: "C:\\Users\\u\\AppData\\Local\\meet\\engine", models_dir: "C:\\Users\\u\\AppData\\Local\\meet\\models",
  hf_cache: "C:\\Users\\u\\.cache\\huggingface\\hub", shared_cache: "C:\\Users\\u\\.cache\\huggingface\\hub",
  missing: null, models_bytes: 4 * GB, moving: false, busy: null, leftovers: null, ...over,
});
const status = (over: Partial<shell.StorageStatus> = {}): shell.StorageStatus => ({
  root: null, home: "C:\\Users\\u\\AppData\\Local\\meet", default_home: "C:\\Users\\u\\AppData\\Local\\meet",
  missing: null, moving: false, cancellable: false, ...over,
});
const plan = (over: Partial<shell.StorageCheck> = {}): shell.StorageCheck => ({
  target: "E:\\Meet", free_gb: 120.4, needs_gb: 12.6, engine_gb: 8, models_gb: 4.1, busy: null, error: null, ...over,
});

let progress: ((p: shell.StorageProgress) => void) | null = null;

beforeEach(() => {
  vi.clearAllMocks();
  resetMoveForTests();
  progress = null;
  vi.mocked(shell.inTauri).mockReturnValue(true);
  vi.mocked(api.getStorage).mockResolvedValue(info());
  vi.mocked(shell.storageStatus).mockResolvedValue(status());
  vi.mocked(shell.onStorageProgress).mockImplementation(async (cb) => { progress = cb; return () => {}; });
  vi.mocked(shell.onEngineProgress).mockResolvedValue(() => {});
});

test("по умолчанию — папка данных и общий кэш Hugging Face", async () => {
  render(<StoragePane endpoint={ep} />);
  expect(await screen.findByText("C:\\Users\\u\\AppData\\Local\\meet")).toBeInTheDocument();
  expect(screen.getByText(/общем кэше Hugging Face/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Вернуть на системный диск" })).toBeNull();
});

test("выбор папки — сколько нужно и сколько свободно, затем «Перенести»", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\");
  vi.mocked(shell.storageCheck).mockResolvedValue(plan());
  render(<StoragePane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Выбрать папку…" }));
  expect(shell.storageCheck).toHaveBeenCalledWith("E:\\");
  expect(await screen.findByText("E:\\Meet")).toBeInTheDocument();
  expect(screen.getByText(/Нужно около 12,6 ГБ/)).toBeInTheDocument();
  expect(screen.getByText(/свободно 120,4 ГБ/)).toBeInTheDocument();
  expect(screen.getByText(/Записи не переносятся/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Перенести" })).toBeEnabled();
});

test("идёт запись — перенести нельзя, причина видна", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\Meet");
  vi.mocked(shell.storageCheck).mockResolvedValue(plan({ busy: "идёт запись" }));
  render(<StoragePane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Выбрать папку…" }));
  expect(await screen.findByText(/Перенести сейчас нельзя: идёт запись/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Перенести" })).toBeDisabled();
});

test("места не хватает — отказ до начала", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\Meet");
  vi.mocked(shell.storageCheck).mockResolvedValue(plan({ free_gb: 3, error: "Недостаточно места: нужно 12,6 ГБ, свободно 3 ГБ" }));
  render(<StoragePane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Выбрать папку…" }));
  expect(await screen.findByText("Недостаточно места: нужно 12,6 ГБ, свободно 3 ГБ")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Перенести" })).toBeDisabled();
});

test("негодная папка — понятный отказ", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\Photos");
  vi.mocked(shell.storageCheck).mockRejectedValue("Папка E:\\Photos\\Meet не пуста — выберите пустую папку");
  render(<StoragePane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Выбрать папку…" }));
  expect(await screen.findByText(/не пуста — выберите пустую папку/)).toBeInTheDocument();
});

test("перенос: ход, «Отменить», итог", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\");
  vi.mocked(shell.storageCheck).mockResolvedValue(plan());
  let finish: (path: string) => void = () => {};
  vi.mocked(shell.storageMove).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  vi.mocked(shell.storageStatus).mockResolvedValue(status({ cancellable: true }));
  render(<StoragePane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Выбрать папку…" }));
  await userEvent.click(await screen.findByRole("button", { name: "Перенести" }));
  expect(shell.storageMove).toHaveBeenCalledWith("E:\\Meet");
  await waitFor(() => expect(progress).not.toBeNull());
  act(() => progress!({ phase: "models", text: "Копирование моделей", done: 2 * GB, total: 4 * GB }));
  expect(await screen.findByText("Копирование моделей")).toBeInTheDocument();
  expect(screen.getByText(/2 из 4 ГБ/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Отменить" }));
  expect(shell.storageCancel).toHaveBeenCalled();
  vi.mocked(api.getStorage).mockResolvedValue(info({ root: "E:\\Meet", custom: true, home: "E:\\Meet" }));
  await act(async () => finish("E:\\Meet"));
  expect(await screen.findByText(/Готово: движок и модели — в E:\\Meet/)).toBeInTheDocument();
});

test("сбой переноса — ошибка, всё на прежнем месте", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\");
  vi.mocked(shell.storageCheck).mockResolvedValue(plan());
  vi.mocked(shell.storageMove).mockRejectedValue("Перенос отменён — движок и модели остались на прежнем месте");
  render(<StoragePane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Выбрать папку…" }));
  await userEvent.click(await screen.findByRole("button", { name: "Перенести" }));
  expect(await screen.findByText(/остались на прежнем месте/)).toBeInTheDocument();
});

test("в своей папке — «Вернуть на системный диск» проверяет папку по умолчанию", async () => {
  vi.mocked(api.getStorage).mockResolvedValue(info({ root: "E:\\Meet", custom: true, home: "E:\\Meet",
    hf_cache: "E:\\Meet\\models\\hf" }));
  vi.mocked(shell.storageStatus).mockResolvedValue(status({ root: "E:\\Meet", home: "E:\\Meet" }));
  vi.mocked(shell.storageCheck).mockResolvedValue(plan({ target: "C:\\Users\\u\\AppData\\Local\\meet" }));
  render(<StoragePane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Вернуть на системный диск" }));
  expect(shell.storageCheck).toHaveBeenCalledWith("C:\\Users\\u\\AppData\\Local\\meet");
});

test("после переезда — вопрос об остатках в общем кэше, по умолчанию «Удалить»", async () => {
  vi.mocked(api.getStorage).mockResolvedValue(info({ root: "E:\\Meet", custom: true, home: "E:\\Meet",
    leftovers: { cache: "C:\\Users\\u\\.cache\\huggingface\\hub", bytes: Math.round(3.4 * GB),
      repos: [{ id: "Systran/faster-whisper-medium", bytes: Math.round(1.5 * GB) },
        { id: "pyannote/speaker-diarization-community-1", bytes: Math.round(1.9 * GB) }] } }));
  vi.mocked(api.answerLeftovers).mockResolvedValue({ ok: true, removed: ["Systran/faster-whisper-medium"] });
  render(<StoragePane endpoint={ep} />);
  expect(await screen.findByText(/Модели Meet остались и в общем кэше/)).toBeInTheDocument();
  expect(screen.getByText(/3,4 ГБ/)).toBeInTheDocument();
  expect(screen.getByText(/могут пользоваться другие программы/)).toBeInTheDocument();
  const remove = screen.getByRole("button", { name: "Удалить из общего кэша" });
  expect(remove).toHaveClass("btn--primary");
  vi.mocked(api.getStorage).mockResolvedValue(info({ root: "E:\\Meet", custom: true, home: "E:\\Meet" }));
  await userEvent.click(remove);
  expect(api.answerLeftovers).toHaveBeenCalledWith(ep, true);
  await waitFor(() => expect(screen.queryByText(/Модели Meet остались/)).toBeNull());
});

test("«Оставить» — модели в общем кэше не трогаются", async () => {
  vi.mocked(api.getStorage).mockResolvedValue(info({ custom: true, root: "E:\\Meet", home: "E:\\Meet",
    leftovers: { cache: "C:\\hf", bytes: GB, repos: [{ id: "a/b", bytes: GB }] } }));
  vi.mocked(api.answerLeftovers).mockResolvedValue({ ok: true, removed: [] });
  render(<StoragePane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Оставить" }));
  expect(api.answerLeftovers).toHaveBeenCalledWith(ep, false);
});

test("вне приложения переносить нельзя — только сведения", async () => {
  vi.mocked(shell.inTauri).mockReturnValue(false);
  vi.mocked(shell.storageStatus).mockResolvedValue(null);
  render(<StoragePane endpoint={ep} />);
  expect(await screen.findByText("C:\\Users\\u\\AppData\\Local\\meet")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Выбрать папку…" })).toBeNull();
});


test("прерванный перенос в настройках — «Продолжить» и «Отменить перенос»", async () => {
  vi.mocked(shell.storageStatus).mockResolvedValue(status({ interrupted: "E:\\Meet" }));
  vi.mocked(shell.storageMove).mockReturnValue(new Promise(() => {}));
  render(<StoragePane endpoint={ep} />);
  expect(await screen.findByText(/прерван — всё работает из прежней папки/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Выбрать папку…" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Отменить перенос" }));
  expect(shell.storageAbandon).toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  expect(shell.storageMove).toHaveBeenCalledWith("E:\\Meet");
});

test("явно выбранная папка данных — это системный диск, без «Вернуть»", async () => {
  vi.mocked(api.getStorage).mockResolvedValue(info({ root: "C:\\Users\\u\\AppData\\Local\\meet\\",
    custom: true, hf_cache: "C:\\Users\\u\\AppData\\Local\\meet\\models\\hf" }));
  render(<StoragePane endpoint={ep} />);
  expect(await screen.findByText(/Системный диск: движок и все модели Meet/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Вернуть на системный диск" })).toBeNull();
});

test("продолжение — своя надпись и слова о загрузке пакетов", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\Meet");
  vi.mocked(shell.storageCheck).mockResolvedValue(plan({ resume: true }));
  render(<StoragePane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Выбрать папку…" }));
  expect(await screen.findByRole("button", { name: "Продолжить перенос" })).toBeEnabled();
  expect(screen.getByText(/скачаются один раз в эту папку — нужен интернет/)).toBeInTheDocument();
});
