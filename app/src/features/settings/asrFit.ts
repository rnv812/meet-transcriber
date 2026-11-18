/**
 * «Распознавание» (0.6.2): подбор под этот компьютер и строка «Как сейчас».
 *
 * Машина — по `/engine`: Mac (`flavor: "mac"`), видеокарта NVIDIA, годная
 * для распознавания (`cuda_ok` — то же правило, что у `asr.resolve_device`),
 * или процессор. Подбор:
 *
 * - без годной NVIDIA — GigaAM на процессоре (`asr.cpu_backend`);
 * - NVIDIA с CUDA — GigaAM для русского на видеокарте (`asr.backend`),
 *   записи не на русском — Whisper на видеокарте;
 * - Mac — GigaAM (видеоядра Apple берёт сам движок), записи не на русском —
 *   MLX Whisper: отдельной настройки нет, движок берёт его сам, если модель
 *   скачана (macOS 14+), — поэтому в подборе это только скачивание.
 *
 * Whisper при GigaAM распознаёт только записи не на русском: модель «только
 * русский» по каталогу (`language: "ru"`) меняется на многоязычную — на
 * видеокарте самую крупную, на процессоре самую лёгкую. Своя модель вне
 * каталога остаётся как есть. Устройство меняется на «Авто», только если
 * нынешнее не даёт подобранную строку. Язык речи, модель GigaAM и прочее не
 * трогаются.
 */

import { type EngineState, type ModelsState, GIGAAM_PREFIX } from "../../lib/api";
import {
  type AsrDevice, DEFAULT_GIGAAM, backendOf, deviceInEffect, gigaamTitle, gpuState, modelTitle,
} from "./AsrChoice";
import type { FitDownload, FitPlan, NowPart, Raw } from "./Section";

export type Machine = "cuda" | "cpu" | "mac";

export function machineOf(engine: EngineState): Machine {
  if (engine.flavor === "mac") return "mac";
  return (gpuState(engine)?.ok ?? false) ? "cuda" : "cpu";
}

/** Многоязычный Whisper, если каталог без языков (старый резидент) или не загрузился. */
const FALLBACK_WHISPER: Record<AsrDevice, string> = {
  cuda: "Systran/faster-whisper-large-v3",
  cpu: "Systran/faster-whisper-medium",
};
const DEVICE_TITLE: Record<string, string> = { auto: "Авто", cuda: "Видеокарта", cpu: "Процессор" };
const WHERE: Record<AsrDevice, string> = { cuda: "на видеокарте", cpu: "на процессоре" };
const engineTitle = (backend: string) => (backend === "gigaam" ? "GigaAM" : "Whisper");

/** Многоязычный Whisper каталога: на видеокарте — самый крупный, на процессоре — самый лёгкий. */
function multilingual(models: ModelsState | null, row: AsrDevice): string {
  const list = (models?.items ?? [])
    .filter((m) => m.kind === "asr" && m.backend === "faster-whisper" && m.language && m.language !== "ru")
    .sort((a, b) => a.size_gb - b.size_gb);
  const pick = row === "cuda" ? list.at(-1) : list[0];
  return pick?.id ?? FALLBACK_WHISPER[row];
}

function machineTitle(machine: Machine, engine: EngineState): string {
  if (machine === "mac") {
    return "Mac с Apple Silicon: русскую речь распознаёт GigaAM, остальные языки — MLX Whisper на видеоядрах Apple";
  }
  if (machine === "cuda") {
    const name = engine.gpu.name ? ` (${engine.gpu.name})` : "";
    return `Компьютер с видеокартой NVIDIA${name}: русскую речь распознаёт GigaAM, остальные языки — Whisper на видеокарте`;
  }
  const gpu = gpuState(engine);
  if (engine.gpu.available && gpu?.reason) {
    return `Видеокарта не годится для распознавания (${gpu.reason}): GigaAM на процессоре, остальные языки — Whisper`;
  }
  return "Компьютер без видеокарты NVIDIA: GigaAM на процессоре, остальные языки — Whisper";
}

/** Подбор: значения `asr` для черновика и как они выглядят списком. */
export type AsrFit = { plan: FitPlan; values: Record<string, unknown> };

/** Подбор под машину по черновику `asr`: только отличия от черновика. */
export function asrFit(engine: EngineState, asr: Raw[string] | undefined, models: ModelsState | null): AsrFit {
  const machine = machineOf(engine);
  const row: AsrDevice = machine === "cuda" ? "cuda" : "cpu";
  const a = asr ?? {};
  const values: Record<string, unknown> = {};
  const changes: string[] = [];
  const want = (key: string, value: unknown, text: string) => {
    values[key] = value;
    changes.push(text);
  };

  const device = String(a.device ?? "auto");
  if (row === "cuda" ? device === "cpu" : device === "cuda") {
    want("device", "auto", `Устройство: ${DEVICE_TITLE[device] ?? device} → Авто`);
  }
  const backend = backendOf(a, row);
  if (backend !== "gigaam") {
    want(row === "cuda" ? "backend" : "cpu_backend", "gigaam", `Движок ${WHERE[row]}: ${engineTitle(backend)} → GigaAM`);
  }
  const modelKey = row === "cuda" ? "model" : "cpu_model";
  const whisper = String(a[modelKey] ?? "");
  let whisperAfter = whisper;
  if (models?.items.find((m) => m.id === whisper)?.language === "ru") {
    whisperAfter = multilingual(models, row);
    if (whisperAfter !== whisper) {
      want(modelKey, whisperAfter, `Whisper ${WHERE[row]} для записей не на русском: `
        + `${modelTitle(models, whisper)} → ${modelTitle(models, whisperAfter)}`);
    }
  }

  // Что понадобится скачать под подобранные значения.
  const needs = [{ id: GIGAAM_PREFIX + String(a.gigaam_model ?? DEFAULT_GIGAAM), why: "русская речь" }];
  if (machine === "mac") {
    const mlx = models?.items.find((m) => m.backend === "mlx-whisper");
    if (mlx) needs.push({ id: mlx.id, why: "записи не на русском, macOS 14 и новее" });
  } else {
    needs.push({ id: whisperAfter, why: "записи не на русском" });
  }
  const downloads: FitDownload[] | null = models === null ? null : needs.flatMap(({ id, why }) => {
    const m = models.items.find((x) => x.id === id);
    return m && !m.downloaded ? [{ id, title: m.title, size_gb: m.size_gb, why }] : [];
  });
  return { plan: { title: machineTitle(machine, engine), changes, downloads }, values };
}

/** Язык речи словами. */
function languageText(raw: unknown): string {
  const lang = String(raw ?? "ru").trim().toLowerCase() || "ru";
  if (lang === "ru") return "речь на русском";
  if (lang === "auto") return "язык определяется по записи";
  return `язык речи ${lang}`;
}

/**
 * «Как сейчас» по сохранённым настройкам: устройство, которое распознаёт,
 * движок и модель его строки, запасной Whisper для записей не на русском и
 * язык. Ссылки на движок и модели — внутри строки этого устройства.
 */
export function asrNow(saved: Raw, engine: EngineState | null, models: ModelsState | null): NowPart[] {
  const a = saved.asr ?? {};
  const gpu = gpuState(engine);
  const effect = deviceInEffect(String(a.device ?? "auto"), gpu);
  const mac = engine?.flavor === "mac";
  const parts: NowPart[] = [{
    text: mac ? "Mac" : effect === "cuda" ? `видеокарта${gpu?.name ? ` (${gpu.name})` : ""}`
      : effect === "cpu" ? "процессор" : "видеокарта или процессор",
    to: "Устройство",
  }];
  if (effect) {
    const scope = `[data-device="${effect}"]`;
    const whisper = String(a[effect === "cuda" ? "model" : "cpu_model"] ?? "");
    if (backendOf(a, effect) === "gigaam") {
      parts.push({ text: gigaamTitle(models, String(a.gigaam_model ?? DEFAULT_GIGAAM)), to: "Движок", scope });
      const mlx = mac && models?.items.some((m) => m.backend === "mlx-whisper" && m.downloaded);
      parts.push({ text: `не на русском — ${mlx ? "MLX Whisper" : modelTitle(models, whisper)}`, to: "Модель GigaAM", scope });
    } else {
      parts.push({ text: modelTitle(models, whisper), to: "Модель Whisper", scope });
    }
  }
  parts.push({ text: languageText(a.language), to: "Язык речи" });
  return parts;
}
