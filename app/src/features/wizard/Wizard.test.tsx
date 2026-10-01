import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Wizard } from "./Wizard";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { EngineStatus } from "../../lib/shell";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  resolveEndpoint: vi.fn(),
  getState: vi.fn(),
  getHfStatus: vi.fn(),
  setHfToken: vi.fn(),
  getModels: vi.fn(),
  downloadModel: vi.fn(),
  getJobs: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  setAutoRecord: vi.fn(),
}));
const events = vi.hoisted(() => ({
  progress: null as ((p: { step: number; of: number; line: string }) => void) | null,
  failed: null as ((f: { step: number; tail: string }) => void) | null,
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: () => true,
  installEngine: vi.fn(),
  onEngineProgress: vi.fn(async (cb: typeof events.progress) => { events.progress = cb; return () => {}; }),
  onEngineFailed: vi.fn(async (cb: typeof events.failed) => { events.failed = cb; return () => {}; }),
  openUrl: vi.fn(async () => {}),
  autostartAvailable: vi.fn(async () => true),
  setAutostart: vi.fn(async () => {}),
}));

const ep = { base: "http://127.0.0.1:5000", token: "t" };
const MODEL_URL = "https://huggingface.co/pyannote/speaker-diarization-community-1";

const engine = (extra: Partial<EngineStatus> = {}): EngineStatus => ({
  installed: false, version: "0.1.0", env_dir: "C:\\Users\\me\\AppData\\Local\\meet\\engine\\0.1.0",
  profile: null, gpu: "NVIDIA GeForce RTX 5070 Ti", free_gb: 120.5, needs_gb: 5, ...extra,
});

type Props = Partial<Parameters<typeof Wizard>[0]>;
const show = (props: Props = {}) => {
  const onClose = vi.fn();
  const onRefreshEngine = vi.fn(async () => {});
  const view = render(<Wizard engine={engine()} endpoint={null} recording={false}
    onClose={onClose} onRefreshEngine={onRefreshEngine} {...props} />);
  return { onClose, onRefreshEngine, ...view };
};

beforeEach(() => {
  vi.clearAllMocks();
  events.progress = null;
  events.failed = null;
  vi.mocked(api.getHfStatus).mockResolvedValue({ configured: false, source: null, check: null });
  vi.mocked(api.getModels).mockResolvedValue({
    items: [
      { id: "large-v3", kind: "asr", title: "Whisper large-v3", note: "", size_gb: 3.1, recommended: true,
        downloaded: false, size_on_disk: 0, selected: true, blocked: false },
      { id: "pyannote/speaker-diarization-community-1", kind: "diarization", title: "Спикеры", note: "",
        size_gb: 0.1, gated: true, downloaded: false, size_on_disk: 0, selected: false, blocked: true },
    ],
    cache: "C:/hf", token: false, selected: "large-v3", can_download: true,
  });
  vi.mocked(api.getJobs).mockResolvedValue({ items: [] });
  vi.mocked(api.getDevices).mockResolvedValue({
    available: true, pinning: false, system: { name: "Динамики", rate: 48000 }, mic: { name: "Микрофон", rate: 48000 },
  });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: true, running: ["Telegram.exe", "zoom.exe"] });
  vi.mocked(api.getSettings).mockResolvedValue({ auto_record: { enabled: false, processes: ["zoom.exe"] } });
  vi.mocked(api.patchSettings).mockResolvedValue({ settings: {}, restart_required: [] });
  vi.mocked(api.setAutoRecord).mockResolvedValue({} as never);
});

test("шаг «Ваш компьютер»: видеокарта, профиль и оценка часовой встречи", async () => {
  show();
  expect(screen.getByRole("heading", { name: "Ваш компьютер" })).toBeInTheDocument();
  expect(screen.getByText("NVIDIA GeForce RTX 5070 Ti")).toBeInTheDocument();
  expect(screen.getByText(/для видеокарты/)).toBeInTheDocument();
  expect(screen.getByText("60 мин встречи ≈ 13 мин обработки")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByRole("heading", { name: "Установка движка" })).toBeInTheDocument();
});

test("без видеокарты — профиль процессора и его оценка", () => {
  show({ engine: engine({ gpu: null }) });
  expect(screen.getByText(/Видеокарта NVIDIA не найдена/)).toBeInTheDocument();
  expect(screen.getByText("60 мин встречи ≈ 76 мин обработки")).toBeInTheDocument();
});

test("мало места — «Установить» неактивна, текст «Освободите N ГБ на диске C:»", () => {
  show({ start: "engine", engine: engine({ free_gb: 2.3, needs_gb: 5 }) });
  expect(screen.getByRole("button", { name: "Установить" })).toBeDisabled();
  expect(screen.getByText("Освободите 2,7 ГБ на диске C:")).toBeInTheDocument();
});

test("свободное место не узнать — установку не блокируем", () => {
  show({ start: "engine", engine: engine({ free_gb: null }) });
  expect(screen.getByRole("button", { name: "Установить" })).toBeEnabled();
});

test("идёт запись — установка недоступна с подсказкой", () => {
  show({ start: "engine", recording: true });
  expect(screen.getByRole("button", { name: "Установить" })).toBeDisabled();
  expect(screen.getByText("Остановите запись, чтобы переустановить движок")).toBeInTheDocument();
});

test("установка: прогресс по шагам, строки лога; профиль — по видеокарте", async () => {
  let finish: () => void = () => {};
  vi.mocked(shell.installEngine).mockImplementation(() => new Promise<void>((r) => { finish = r; }));
  const { onRefreshEngine } = show({ start: "engine" });
  await waitFor(() => expect(events.progress).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Установить" }));
  expect(shell.installEngine).toHaveBeenCalledWith("cuda", false);
  act(() => {
    events.progress!({ step: 1, of: 4, line: "Окружение Python" });
    events.progress!({ step: 1, of: 4, line: "Using CPython 3.12" });
    events.progress!({ step: 2, of: 4, line: "PyTorch для видеокарты" });
  });
  expect(screen.getByText("Шаг 2 из 4")).toBeInTheDocument();
  const steps = screen.getByRole("list", { name: "Шаги установки" });
  expect(within(steps).getByText("Окружение Python")).toBeInTheDocument();
  expect(within(steps).getByText("PyTorch для видеокарты")).toBeInTheDocument();
  expect(screen.getByText(/Using CPython 3.12/)).toBeInTheDocument();
  const before = onRefreshEngine.mock.calls.length;
  await act(async () => finish());
  expect(await screen.findByText("Движок установлен")).toBeInTheDocument();
  expect(onRefreshEngine.mock.calls.length).toBeGreaterThan(before);
});

test("engine-failed: хвост лога и «Повторить», «Переустановить с нуля»", async () => {
  let fail: (e: unknown) => void = () => {};
  vi.mocked(shell.installEngine).mockImplementation(() => new Promise<void>((_, rej) => { fail = rej; }));
  show({ start: "engine" });
  await waitFor(() => expect(events.failed).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Установить" }));
  act(() => {
    events.progress!({ step: 3, of: 4, line: "Библиотеки распознавания" });
    events.failed!({ step: 3, tail: "error: Failed to download torch\nConnection reset" });
  });
  await act(async () => fail("Шаг 3 не удался: Библиотеки распознавания"));
  expect(await screen.findByText(/Failed to download torch/)).toBeInTheDocument();
  expect(screen.getByText(/Connection reset/)).toBeInTheDocument();
  // Сообщение оболочки не повторяется рядом с хвостом лога.
  expect(screen.getAllByText(/Шаг 3 не удался/)).toHaveLength(1);
  vi.mocked(shell.installEngine).mockResolvedValue(undefined);
  await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(shell.installEngine).toHaveBeenLastCalledWith("cuda", false);
  expect(await screen.findByText("Движок установлен")).toBeInTheDocument();
});

test("отказ без события (установка уже идёт) — текст ошибки и «Повторить»", async () => {
  vi.mocked(shell.installEngine).mockRejectedValue("Установка уже идёт");
  show({ start: "engine" });
  await waitFor(() => expect(events.failed).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Установить" }));
  expect(await screen.findByText("Установка уже идёт")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Переустановить с нуля" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Переустановить с нуля" }));
  expect(shell.installEngine).toHaveBeenLastCalledWith("cuda", true);
});

test("движок уже стоит — «Далее» и переустановка с нуля", async () => {
  show({ start: "engine", engine: engine({ installed: true, profile: "cuda" }), endpoint: ep });
  expect(screen.getByText("Движок установлен")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Переустановить с нуля" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByRole("heading", { name: "Hugging Face" })).toBeInTheDocument();
});

test("после установки ждём сервис: «Запускаю сервис…», затем Hugging Face", async () => {
  let found: (e: typeof ep) => void = () => {};
  vi.mocked(api.resolveEndpoint).mockImplementation(() => new Promise((r) => { found = r; }));
  vi.mocked(api.getState).mockResolvedValue({} as never);
  show({ start: "engine", engine: engine({ installed: true, profile: "cuda" }) });
  await userEvent.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByText("Запускаю сервис…")).toBeInTheDocument();
  await act(async () => found(ep));
  expect(await screen.findByRole("heading", { name: "Hugging Face" })).toBeInTheDocument();
  await waitFor(() => expect(api.getHfStatus).toHaveBeenCalledWith(ep));
});

test("Hugging Face: ссылки открываются через оболочку", async () => {
  show({ start: "hf", endpoint: ep });
  await userEvent.click(await screen.findByRole("button", { name: "Открыть страницу модели" }));
  expect(shell.openUrl).toHaveBeenCalledWith(MODEL_URL);
  await userEvent.click(screen.getByRole("button", { name: "Открыть настройки токенов" }));
  expect(shell.openUrl).toHaveBeenCalledWith("https://huggingface.co/settings/tokens");
  expect(screen.getByText(/Read access to contents of all public gated repos/)).toBeInTheDocument();
});

test("токен с непринятыми условиями — текст про «Agree and access repository» и страница модели", async () => {
  vi.mocked(api.setHfToken).mockResolvedValue({
    ok: false, reason: "terms_not_accepted", message: "Условия модели не приняты — откройте страницу модели",
  });
  show({ start: "hf", endpoint: ep });
  await userEvent.type(await screen.findByLabelText("Токен"), "hf_abc");
  await userEvent.click(screen.getByRole("button", { name: "Проверить" }));
  expect(api.setHfToken).toHaveBeenCalledWith(ep, "hf_abc");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Условия модели не приняты — нажмите «Agree and access repository»");
  vi.mocked(shell.openUrl).mockClear();
  await userEvent.click(within(alert).getByRole("button", { name: "Открыть страницу модели" }));
  expect(shell.openUrl).toHaveBeenCalledWith(MODEL_URL);
  expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
});

test.each([
  ["invalid_token", "Неверный токен"],
  ["network", "Нет связи с huggingface.co"],
] as const)("проверка токена: %s — «%s»", async (reason, text) => {
  vi.mocked(api.setHfToken).mockResolvedValue({ ok: false, reason, message: "Нет связи с huggingface.co" });
  show({ start: "hf", endpoint: ep });
  await userEvent.type(await screen.findByLabelText("Токен"), "hf_x");
  await userEvent.click(screen.getByRole("button", { name: "Проверить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(text);
});

test("верный токен — «Доступ есть», поле очищено, «Далее» к моделям", async () => {
  vi.mocked(api.setHfToken).mockResolvedValue({ ok: true, reason: "ok", message: "Доступ есть" });
  show({ start: "hf", endpoint: ep });
  const input = await screen.findByLabelText("Токен");
  await userEvent.type(input, "hf_ok");
  await userEvent.click(screen.getByRole("button", { name: "Проверить" }));
  expect(await screen.findByText("Доступ есть")).toBeInTheDocument();
  expect(input).toHaveValue("");
  await userEvent.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByRole("heading", { name: "Модели" })).toBeInTheDocument();
});

test("«Пропустить» на шаге Hugging Face ведёт к моделям, мастер не закрывается", async () => {
  const { onClose } = show({ start: "hf", endpoint: ep });
  const step = await screen.findByRole("region", { name: "Hugging Face" });
  expect(within(step).getByText("Без токена расшифровка будет без разделения на спикеров")).toBeInTheDocument();
  await userEvent.click(within(step).getByRole("button", { name: "Пропустить" }));
  expect(screen.getByRole("heading", { name: "Модели" })).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});

test("модели: размеры и «Скачать» — задача с прогрессом", async () => {
  vi.mocked(api.downloadModel).mockResolvedValue({
    id: "j1", kind: "download-model", folder: "large-v3", state: "running", stage: null, label: "Качаю",
    done: 1, total: 4, note: null, result: null, error: null,
  });
  show({ start: "models", endpoint: ep });
  expect(await screen.findByText("Whisper large-v3")).toBeInTheDocument();
  expect(screen.getByText(/3,1 ГБ/)).toBeInTheDocument();
  const row = screen.getByRole("group", { name: "Whisper large-v3" });
  await userEvent.click(within(row).getByRole("button", { name: "Скачать" }));
  expect(api.downloadModel).toHaveBeenCalledWith(ep, "large-v3");
  expect(await within(row).findByText(/25%/)).toBeInTheDocument();
  // Модель спикеров за токеном — без него не качается.
  const gated = screen.getByRole("group", { name: "Спикеры" });
  expect(within(gated).getByRole("button", { name: "Скачать" })).toBeDisabled();
});

test("запись: устройства, автозапись и программы звонков", async () => {
  show({ start: "devices", endpoint: ep });
  expect(await screen.findByText(/Динамики/)).toBeInTheDocument();
  expect(screen.getByText(/Микрофон/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("switch", { name: /Поднимать запись/ }));
  expect(api.setAutoRecord).toHaveBeenCalledWith(ep, true);
  await userEvent.click(await screen.findByRole("checkbox", { name: "Telegram.exe" }));
  expect(api.patchSettings).toHaveBeenCalledWith(ep, { auto_record: { processes: ["zoom.exe", "Telegram.exe"] } });
});

test("«Готово»: про трей; автозапуск по умолчанию включён и применяется при выходе", async () => {
  const { onClose } = show({ start: "done", endpoint: ep });
  expect(screen.getByText("Приложение живёт в трее. Клик по иконке — окно, правый клик — запись")).toBeInTheDocument();
  const toggle = await screen.findByRole("switch", { name: "Запускать вместе с Windows" });
  expect(toggle).toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Готово" }));
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(shell.setAutostart).toHaveBeenCalledWith(true);
});

test("«Готово»: оболочка без автозапуска — переключателя нет", async () => {
  vi.mocked(shell.autostartAvailable).mockResolvedValue(false);
  const { onClose } = show({ start: "done", endpoint: ep });
  await waitFor(() => expect(shell.autostartAvailable).toHaveBeenCalled());
  expect(screen.queryByRole("switch", { name: "Запускать вместе с Windows" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Готово" }));
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(shell.setAutostart).not.toHaveBeenCalled();
});

test("«Пропустить» мастер доступен на каждом шаге и закрывает его", async () => {
  for (const start of ["hardware", "engine", "hf", "models", "devices"] as const) {
    const { onClose, unmount } = show({ start, endpoint: ep });
    await userEvent.click(screen.getByRole("button", { name: "Пропустить мастер" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();
  }
});

test("без оболочки (браузер) — шаг движка пропускается «Далее»", async () => {
  show({ start: "engine", engine: null, endpoint: ep });
  expect(screen.getByText("Установка движка доступна только в приложении.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByRole("heading", { name: "Hugging Face" })).toBeInTheDocument();
});

test("сервис не поднялся за 90 попыток раз в секунду — сообщение и «Подождать ещё»", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(api.resolveEndpoint).mockRejectedValue(new api.NoResidentError("нет"));
    show({ start: "hf" });
    expect(screen.getByText("Запускаю сервис…")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(89 * 1000); });
    expect(api.resolveEndpoint).toHaveBeenCalledTimes(90);
    expect(screen.getByText("Сервис записи не запустился за 90 секунд.")).toBeInTheDocument();
    vi.mocked(api.resolveEndpoint).mockResolvedValue(ep);
    vi.mocked(api.getState).mockResolvedValue({} as never);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Подождать ещё" })); });
    expect(screen.getByRole("heading", { name: "Hugging Face" })).toBeInTheDocument();
    expect(api.getHfStatus).toHaveBeenCalledWith(ep);
  } finally {
    vi.useRealTimers();
  }
});

test("шаг движка при входе перечитывает состояние: место могли освободить", () => {
  const { onRefreshEngine } = show({ start: "engine" });
  expect(onRefreshEngine).toHaveBeenCalledTimes(1);
});

test("«Проверить снова» у нехватки места; место освободили — «Установить» активна", async () => {
  const { onRefreshEngine, rerender, onClose } = show({ start: "engine", engine: engine({ free_gb: 2.3, needs_gb: 5 }) });
  onRefreshEngine.mockClear();
  await userEvent.click(screen.getByRole("button", { name: "Проверить снова" }));
  expect(onRefreshEngine).toHaveBeenCalledTimes(1);
  rerender(<Wizard start="engine" engine={engine({ free_gb: 40, needs_gb: 5 })} endpoint={null} recording={false}
    onClose={onClose} onRefreshEngine={onRefreshEngine} />);
  expect(screen.getByRole("button", { name: "Установить" })).toBeEnabled();
  expect(screen.queryByText(/Освободите/)).toBeNull();
});

test("окно снова в фокусе на шаге движка — состояние перечитывается", () => {
  const { onRefreshEngine } = show({ start: "engine" });
  onRefreshEngine.mockClear();
  act(() => { window.dispatchEvent(new Event("focus")); });
  expect(onRefreshEngine).toHaveBeenCalledTimes(1);
});

test("неудачная установка — состояние перечитывается (место, маркер)", async () => {
  vi.mocked(shell.installEngine).mockRejectedValue("Недостаточно места: нужно 5 ГБ, свободно 2,3 ГБ");
  const { onRefreshEngine } = show({ start: "engine" });
  await waitFor(() => expect(events.failed).not.toBeNull());
  onRefreshEngine.mockClear();
  await userEvent.click(screen.getByRole("button", { name: "Установить" }));
  expect(await screen.findByText("Недостаточно места: нужно 5 ГБ, свободно 2,3 ГБ")).toBeInTheDocument();
  expect(onRefreshEngine).toHaveBeenCalled();
});

test("видеокарта есть, места на CUDA мало — «Установить CPU-версию (3 ГБ)»", async () => {
  vi.mocked(shell.installEngine).mockResolvedValue(undefined);
  show({ start: "engine", engine: engine({ free_gb: 4, needs_gb: 5 }) });
  await waitFor(() => expect(events.progress).not.toBeNull());
  expect(screen.getByRole("button", { name: "Установить" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Установить CPU-версию (3 ГБ)" }));
  expect(shell.installEngine).toHaveBeenCalledWith("cpu", false);
  expect(await screen.findByText("Движок установлен")).toBeInTheDocument();
});

test("CPU-версия — запасной вариант и при достатке места; без видеокарты её нет", () => {
  const { unmount } = show({ start: "engine" });
  expect(screen.getByRole("button", { name: "Установить CPU-версию (3 ГБ)" })).toBeEnabled();
  unmount();
  show({ start: "engine", engine: engine({ gpu: null, needs_gb: 3 }) });
  expect(screen.queryByRole("button", { name: "Установить CPU-версию (3 ГБ)" })).toBeNull();
});

test("CPU-версия тоже не влезает — неактивна", () => {
  show({ start: "engine", engine: engine({ free_gb: 1.5, needs_gb: 5 }) });
  expect(screen.getByRole("button", { name: "Установить CPU-версию (3 ГБ)" })).toBeDisabled();
});

test("идёт установка — «Пропустить мастер» неактивна с подсказкой", async () => {
  vi.mocked(shell.installEngine).mockImplementation(() => new Promise<void>(() => {}));
  show({ start: "engine" });
  await waitFor(() => expect(events.progress).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Установить" }));
  const skip = screen.getByRole("button", { name: "Пропустить мастер" });
  expect(skip).toBeDisabled();
  expect(skip).toHaveAttribute("title", "Дождитесь окончания установки");
});
