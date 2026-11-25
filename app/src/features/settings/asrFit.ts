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
 * - Mac (`macFit`, 0.6.2) — одна модель на все встречи: язык речи русский
 *   или «auto» — GigaAM на видеоядрах (MPS); другой язык — многоязычный
 *   Whisper с чекпоинтом MLX (macOS 14+), иначе самый лёгкий многоязычный на
 *   процессоре. Скачать — модель и её чекпоинт MLX.
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
import { macModelOf, macModelValues, macRoute, mlxCheckpoint } from "./macRoute";
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

/** Модель на Mac словами: GigaAM — по имени, Whisper — по каталогу. */
export function macModelTitle(models: ModelsState | null, id: string): string {
  return id.startsWith(GIGAAM_PREFIX) ? gigaamTitle(models, id.slice(GIGAAM_PREFIX.length)) : modelTitle(models, id) || "не выбрана";
}

/** Многоязычный Whisper для Mac: с чекпоинтом MLX — самый крупный; без MLX — самый лёгкий. */
function macWhisper(models: ModelsState | null, mlx: boolean): string {
  const list = (models?.items ?? [])
    .filter((m) => m.kind === "asr" && m.backend === "faster-whisper" && m.language && m.language !== "ru")
    .sort((a, b) => a.size_gb - b.size_gb);
  const withMlx = list.filter((m) => m.mlx);
  const pick = mlx && withMlx.length > 0 ? withMlx.at(-1) : list[0];
  return pick?.id ?? FALLBACK_WHISPER.cpu;
}

/** Подбор на Mac: одна модель на все встречи — по языку речи из настроек (см. модуль). */
function macFit(engine: EngineState, a: Raw[string], models: ModelsState | null): AsrFit {
  const mac = engine.mac;
  const mlx = !mac || mac.mlx_supported;
  const values: Record<string, unknown> = {};
  const changes: string[] = [];
  const current = macModelOf(a);
  const lang = String(a.language ?? "ru").trim().toLowerCase() || "ru";
  const russian = lang === "ru" || lang === "auto";
  const currentRu = current.startsWith(GIGAAM_PREFIX) || models?.items.find((m) => m.id === current)?.language === "ru";
  let target = current;
  if (russian && !current.startsWith(GIGAAM_PREFIX)) target = GIGAAM_PREFIX + String(a.gigaam_model ?? DEFAULT_GIGAAM);
  else if (!russian && currentRu) target = macWhisper(models, mlx);
  if (target !== current) {
    // GigaAM — та, что уже в черновике (`gigaam_model` не трогаем): меняется только движок.
    Object.assign(values, target.startsWith(GIGAAM_PREFIX) ? { cpu_backend: "gigaam" } : macModelValues(target));
    changes.push(`Модель распознавания: ${macModelTitle(models, current)} → ${macModelTitle(models, target)}`);
  }
  const needs = [target];
  const checkpoint = mlxCheckpoint(models, target);
  if (checkpoint && checkpoint.id !== target && mlx) needs.push(checkpoint.id);
  const downloads: FitDownload[] | null = models === null ? null : needs.flatMap((id) => {
    const m = models.items.find((x) => x.id === id);
    const why = m?.mlx_of ? "чекпоинт MLX — на видеоядрах" : "модель распознавания";
    return m && !m.downloaded ? [{ id, title: m.title, size_gb: m.size_gb, why }] : [];
  });
  const route = target.startsWith(GIGAAM_PREFIX)
    ? (mac && !mac.mps ? "GigaAM на процессоре (видеоядра недоступны)" : "GigaAM на видеоядрах (MPS)")
    : mlx && checkpoint ? "Whisper с чекпоинтом MLX на видеоядрах" : "Whisper на процессоре";
  let title = `Mac с Apple Silicon: одна модель на все встречи — ${route}`;
  if (!russian) title += `, язык речи ${lang}`;
  if (mac?.mlx_supported && !mac.mlx_installed && !target.startsWith(GIGAAM_PREFIX)) {
    title += ". MLX Whisper ещё не установлен — «Установить» в строке «MLX Whisper»";
  }
  return { plan: { title, changes, downloads }, values };
}

/** Подбор под машину по черновику `asr`: только отличия от черновика. */
export function asrFit(engine: EngineState, asr: Raw[string] | undefined, models: ModelsState | null): AsrFit {
  const machine = machineOf(engine);
  if (machine === "mac") return macFit(engine, asr ?? {}, models);
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
  const needs = [
    { id: GIGAAM_PREFIX + String(a.gigaam_model ?? DEFAULT_GIGAAM), why: "русская речь" },
    { id: whisperAfter, why: "записи не на русском" },
  ];
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
export function asrNow(saved: Raw, engine: EngineState | null, models: ModelsState | null, mac = false): NowPart[] {
  const a = saved.asr ?? {};
  if (mac) {
    // Mac: модель и через что она пойдёт сейчас (по сведениям резидента), язык.
    const id = macModelOf(a);
    return [
      { text: "Mac", to: "Видеоядра Apple" },
      { text: macModelTitle(models, id), to: "Модель распознавания" },
      { text: macRoute(id, engine, models).short, to: "Модель распознавания" },
      { text: languageText(a.language), to: "Язык речи" },
    ];
  }
  const gpu = gpuState(engine);
  const effect = deviceInEffect(String(a.device ?? "auto"), gpu);
  const parts: NowPart[] = [{
    text: effect === "cuda" ? `видеокарта${gpu?.name ? ` (${gpu.name})` : ""}`
      : effect === "cpu" ? "процессор" : "видеокарта или процессор",
    to: "Устройство",
  }];
  if (effect) {
    const scope = `[data-device="${effect}"]`;
    const whisper = String(a[effect === "cuda" ? "model" : "cpu_model"] ?? "");
    if (backendOf(a, effect) === "gigaam") {
      parts.push({ text: gigaamTitle(models, String(a.gigaam_model ?? DEFAULT_GIGAAM)), to: "Движок", scope });
      parts.push({ text: `не на русском — ${modelTitle(models, whisper)}`, to: "Модель GigaAM", scope });
    } else {
      parts.push({ text: modelTitle(models, whisper), to: "Модель Whisper", scope });
    }
  }
  parts.push({ text: languageText(a.language), to: "Язык речи" });
  return parts;
}
