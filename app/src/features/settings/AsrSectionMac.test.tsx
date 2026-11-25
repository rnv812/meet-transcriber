/**
 * «Распознавание» на Mac (0.6.2): одна модель на все встречи, без деления по
 * языку встречи; у каждой модели видно, через что она пойдёт на этом Mac
 * («MLX, видеоядра», «видеоядра (MPS)», «процессор»), рядом — «Скачать»
 * чекпоинта MLX или «Установить» MLX с причиной. Видеоядра для torch — всегда,
 * переключателя нет. Данные выдуманные.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import { AsrSection } from "./AsrSection";
import { SETTINGS_INDEX, searchSettings } from "./settingsIndex";
import type { Raw } from "./Section";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getEngine: vi.fn(),
  getModels: vi.fn(),
  getJobs: vi.fn(),
  downloadModel: vi.fn(),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: vi.fn(() => true),
  retryMlxInstall: vi.fn(),
}));

const ep = { base: "/api", token: null };
const STOCK = "Systran/faster-whisper-large-v3";
const STOCK_MLX = "mlx-community/whisper-large-v3-mlx";
const TURBO = "mlx-community/whisper-large-v3-turbo";

const item = (over: Partial<api.Model>): api.Model => ({
  id: "x", kind: "asr", title: "x", note: "", size_gb: 1, downloaded: false, size_on_disk: 0,
  selected: false, blocked: false, ...over,
});

const catalogue = (over: { turbo?: boolean; stockMlx?: boolean } = {}): api.ModelsState => ({
  items: [
    item({ id: "bzikst/faster-whisper-large-v3-russian", backend: "faster-whisper", title: "Whisper large-v3 — русский fine-tune", size_gb: 3.1, language: "ru" }),
    item({ id: STOCK, backend: "faster-whisper", title: "Whisper large-v3 — стоковая", size_gb: 3.1, language: "multi", mlx: STOCK_MLX }),
    item({ id: "gigaam/v3_e2e_rnnt", backend: "gigaam", title: "GigaAM v3 — русский", size_gb: 0.45, language: "ru", downloaded: true }),
    item({ id: "gigaam/v3_e2e_ctc", backend: "gigaam", title: "GigaAM v3 CTC — русский", size_gb: 0.44, language: "ru" }),
    item({ id: TURBO, backend: "mlx-whisper", title: "Whisper large-v3 turbo — MLX (Mac)", size_gb: 1.6, language: "multi", downloaded: Boolean(over.turbo) }),
    item({ id: STOCK_MLX, backend: "mlx-whisper", title: "Whisper large-v3 — чекпоинт MLX (Mac)", size_gb: 3.1, language: "multi", mlx_of: STOCK, downloaded: Boolean(over.stockMlx) }),
  ],
  cache: "/hf", token: true, selected: null, can_download: true, can_download_gigaam: true, gigaam_cache: "/gigaam",
});

const mac = (over: Partial<api.MacEngine> = {}): api.MacEngine => ({
  macos: "14.4.1", mps: true, mps_reason: null, mlx_supported: true, mlx_installed: true, mlx_reason: null, ...over,
});

const engine = (m: api.MacEngine): api.EngineState => ({
  installed: true, missing: [], components: [], gpu: { available: false, name: null }, flavor: "mac", download_gb: 2,
  python: "/env/bin/python", target: "/env", ffmpeg: true, cuda_ok: false, cuda_reason: "видеокарта NVIDIA не найдена", mac: m,
});

let saved: Raw[] = [];

function Harness({ asr }: { asr: Record<string, unknown> }) {
  const start: Raw = { asr: { language: "ru", align: true, ...asr }, recording: { auto_transcribe: true } };
  const [draft, setDraft] = useState<Raw>(start);
  saved.push(draft);
  return (
    <AsrSection draft={draft} saved={start} endpoint={ep} onOpenEngine={() => {}}
      set={(group, key, value) => setDraft((d) => ({ ...d, [group]: { ...d[group], [key]: value } }))} />
  );
}

async function open(asr: Record<string, unknown>, m = mac(), models = catalogue()) {
  vi.mocked(api.getEngine).mockResolvedValue(engine(m));
  vi.mocked(api.getModels).mockResolvedValue(models);
  render(<Harness asr={asr} />);
  await screen.findByRole("group", { name: "Как сейчас настроено" });
  await waitFor(() => expect(screen.queryByText("Узнаю, что это за компьютер…")).toBeNull());
  await screen.findByRole("combobox", { name: "Модель распознавания" });
}

const model = () => screen.getByRole("combobox", { name: "Модель распознавания" });
const route = () => screen.getByRole("status", { name: "Как пойдёт модель" });
const lastAsr = () => saved.at(-1)?.asr;

beforeEach(() => {
  vi.clearAllMocks();
  saved = [];
  vi.mocked(api.getJobs).mockResolvedValue({ items: [] });
});

test("прежняя основная модель (GigaAM) — выбрана; видеоядра без переключателя; строки видеокарты нет", async () => {
  await open({});
  expect(model()).toHaveTextContent("GigaAM v3 — русский");
  expect(route()).toHaveTextContent("Видеоядра Apple (MPS)");
  expect(screen.getByText("работают · macOS 14.4.1")).toBeInTheDocument();
  expect(screen.getByText("установлен")).toBeInTheDocument();
  expect(screen.queryByRole("radiogroup", { name: "Устройство" })).toBeNull();
  expect(screen.queryByRole("group", { name: "Видеокарта" })).toBeNull();
  expect(screen.queryByText(/записи не на русском/i)).toBeNull();
  expect(screen.getByRole("group", { name: "Как сейчас настроено" }))
    .toHaveTextContent("Mac · GigaAM v3 — русский · видеоядра (MPS) · речь на русском");
});

test("в списке у каждой модели — через что она пойдёт; чекпоинты MLX — не отдельные модели", async () => {
  await open({}, mac(), catalogue({ turbo: true }));
  await userEvent.click(model());
  expect(screen.getByRole("option", { name: /^GigaAM v3 CTC/ })).toHaveTextContent("видеоядра (MPS) · 0,44 ГБ, не скачана");
  expect(screen.getByRole("option", { name: /^Whisper large-v3 — русский fine-tune/ })).toHaveTextContent("процессор · 3,1 ГБ, не скачана");
  expect(screen.getByRole("option", { name: /^Whisper large-v3 — стоковая/ }))
    .toHaveTextContent("процессор; MLX — после загрузки чекпоинта");
  expect(screen.getByRole("option", { name: /^Whisper large-v3 turbo/ })).toHaveTextContent("MLX, видеоядра · скачана");
  expect(screen.queryByRole("option", { name: /чекпоинт MLX/ })).toBeNull();
});

test("Whisper с чекпоинтом MLX: «Скачать» чекпоинт; после загрузки — MLX на видеоядрах", async () => {
  vi.mocked(api.downloadModel).mockResolvedValue({ id: "j1", kind: "download-model", folder: STOCK_MLX, state: "queued" } as never);
  await open({ cpu_backend: "faster-whisper", cpu_model: STOCK, language: "en" });
  expect(route()).toHaveTextContent("Процессор (faster-whisper), пока не скачан чекпоинт MLX (3,1 ГБ); с ним — MLX на видеоядрах");
  await userEvent.click(within(route()).getByRole("button", { name: "Скачать" }));
  expect(api.downloadModel).toHaveBeenCalledWith(ep, STOCK_MLX);
  expect(await screen.findByText(/Скачивается/)).toBeInTheDocument();
});

test("выбор модели пишет прежние ключи строки процессора", async () => {
  await open({});
  await userEvent.click(model());
  await userEvent.click(screen.getByRole("option", { name: /^Whisper large-v3 — стоковая/ }));
  expect(lastAsr()).toMatchObject({ cpu_backend: "faster-whisper", cpu_model: STOCK });
  await userEvent.click(model());
  await userEvent.click(screen.getByRole("option", { name: /^GigaAM v3 CTC/ }));
  expect(lastAsr()).toMatchObject({ cpu_backend: "gigaam", gigaam_model: "v3_e2e_ctc" });
});

test("macOS 13: MLX недоступен с причиной, без «Установить»; модель только MLX — недоступна", async () => {
  await open({ cpu_backend: "faster-whisper", cpu_model: STOCK },
    mac({ macos: "13.6", mlx_supported: false, mlx_installed: false, mlx_reason: "нужна macOS 14 или новее" }));
  expect(screen.getByText("недоступен: нужна macOS 14 или новее")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Установить" })).toBeNull();
  expect(route()).toHaveTextContent("Процессор (faster-whisper): MLX недоступен — нужна macOS 14 или новее");
  await userEvent.click(model());
  const turbo = screen.getByRole("option", { name: /^Whisper large-v3 turbo/ });
  expect(turbo).toHaveTextContent("недоступна: нужна macOS 14 или новее");
  expect(turbo).toHaveAttribute("aria-disabled", "true");
});

test("macOS 14 без MLX: «Установить» ставит его и перечитывает состояние", async () => {
  vi.mocked(shell.retryMlxInstall).mockResolvedValue();
  await open({}, mac({ mlx_installed: false, mlx_reason: "MLX Whisper не установлен в движке" }));
  expect(screen.getByText("недоступен: MLX Whisper не установлен в движке")).toBeInTheDocument();
  vi.mocked(api.getEngine).mockResolvedValue(engine(mac()));
  await userEvent.click(screen.getByRole("button", { name: "Установить" }));
  expect(shell.retryMlxInstall).toHaveBeenCalled();
  expect(await screen.findByText("установлен")).toBeInTheDocument();
});

test("видеоядра недоступны — причина; GigaAM пойдёт на процессоре", async () => {
  await open({}, mac({ mps: false, mps_reason: "PyTorch не установлен в движке" }));
  expect(screen.getByText("недоступны: PyTorch не установлен в движке")).toBeInTheDocument();
  expect(route()).toHaveTextContent("Процессор: видеоядра недоступны — PyTorch не установлен в движке");
});

test("GigaAM, а язык речи в настройках не русский — сказано, что распознаёт Whisper", async () => {
  await open({ language: "en", cpu_model: STOCK });
  expect(screen.getByText(/Язык речи «en», а GigaAM понимает только русский/)).toHaveTextContent("Whisper large-v3 — стоковая");
});

test("подбор при языке en: многоязычный Whisper с чекпоинтом MLX и обе загрузки", async () => {
  await open({ language: "en" });
  await userEvent.click(screen.getByRole("button", { name: "Подобрать под этот компьютер" }));
  expect(screen.getByRole("list", { name: "Что изменится" }))
    .toHaveTextContent("Модель распознавания: GigaAM v3 — русский → Whisper large-v3 — стоковая");
  const downloads = screen.getByRole("list", { name: "Что скачать" });
  expect(downloads).toHaveTextContent("Whisper large-v3 — стоковая — 3,1 ГБ");
  expect(downloads).toHaveTextContent("Whisper large-v3 — чекпоинт MLX (Mac) — 3,1 ГБ (чекпоинт MLX — на видеоядрах)");
  expect(lastAsr()).toMatchObject({ cpu_backend: "faster-whisper", cpu_model: STOCK });
});

test("поиск: каждая подпись раздела на Mac есть в индексе; находятся «MLX», «MPS», «видеоядра»", async () => {
  await open({});
  const labels = [...document.querySelectorAll(".srow__label")].map((el) => (el.textContent ?? "").trim());
  expect(labels).toEqual(expect.arrayContaining(["Видеоядра Apple", "MLX Whisper", "Модель распознавания", "Язык речи"]));
  const missing = labels.filter((l) => !SETTINGS_INDEX.some((e) => e.section === "asr" && e.label === l));
  expect(missing).toEqual([]);
  // Строк Windows на Mac в индексе нет — поиск не ведёт к тому, чего не видно.
  expect(SETTINGS_INDEX.some((e) => e.section === "asr" && e.label === "Устройство")).toBe(false);
  const found = (q: string) => searchSettings(q).map((e) => e.label);
  expect(found("mlx")).toEqual(expect.arrayContaining(["MLX Whisper", "Модель распознавания"]));
  expect(found("MPS")).toContain("Видеоядра Apple");
  expect(found("видеоядра")).toEqual(expect.arrayContaining(["Видеоядра Apple", "MLX Whisper"]));
});
