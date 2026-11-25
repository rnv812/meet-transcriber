/**
 * «Распознавание» на Mac (0.6.2): одна выбранная модель на все встречи, без
 * деления по языку встречи. Ключи прежние — строка процессора:
 * `asr.cpu_backend` + `asr.gigaam_model` (GigaAM) или `asr.cpu_model`
 * (Whisper: модель каталога, модель MLX или своя — id / папка). Так
 * обновившийся видит выбранной свою прежнюю основную модель.
 *
 * Чем модель пойдёт на этом Mac (`macRoute`) — как у резидента
 * (`asr._choose_mac`): GigaAM — видеоядра (MPS, `/engine` `mac.mps`); Whisper
 * с чекпоинтом MLX (`mlx` у модели каталога, модель MLX каталога, своя папка
 * MLX), MLX доступен (macOS 14+, установлен) и чекпоинт скачан — MLX на
 * видеоядрах; иначе faster-whisper на процессоре (видеоядер он не умеет).
 */

import { type EngineState, type MacEngine, type Model, type ModelsState, GIGAAM_PREFIX, isGigaam } from "../../lib/api";
import { IS_MAC } from "../../lib/platform";
import { DEFAULT_GIGAAM, backendOf } from "./AsrChoice";
import { gbText, type Raw } from "./Section";

/** Показывать ли раздел по-маковски: окно на Mac или резидент с движком для Apple Silicon. */
export const macView = (engine: EngineState | null): boolean => IS_MAC || engine?.flavor === "mac";

/** Выбранная модель на Mac: «gigaam/имя» или id / папка Whisper. */
export function macModelOf(asr: Raw[string] | undefined): string {
  if (backendOf(asr, "cpu") === "gigaam") return GIGAAM_PREFIX + String(asr?.gigaam_model ?? DEFAULT_GIGAAM);
  return String(asr?.cpu_model ?? "");
}

/** Значения `asr` для выбранной модели. */
export function macModelValues(id: string): Record<string, unknown> {
  return id.startsWith(GIGAAM_PREFIX)
    ? { cpu_backend: "gigaam", gigaam_model: id.slice(GIGAAM_PREFIX.length) }
    : { cpu_backend: "faster-whisper", cpu_model: id };
}

/** Модели для выбора: GigaAM, Whisper, модели только MLX; чекпоинты MLX (`mlx_of`) — не отдельные модели. */
export function macChoices(models: ModelsState | null): Model[] {
  const asr = (models?.items ?? []).filter((m) => m.kind === "asr" && !m.mlx_of);
  const rank = (m: Model) => (isGigaam(m) ? 0 : m.backend === "mlx-whisper" ? 2 : 1);
  return [...asr].sort((a, b) => rank(a) - rank(b));
}

/**
 * Чекпоинт MLX модели по каталогу: модель MLX — она сама, модель
 * faster-whisper — её `mlx`. null — чекпоинта нет; undefined — модели нет в
 * каталоге (своя: резидент сам посмотрит, MLX ли это).
 */
export function mlxCheckpoint(models: ModelsState | null, id: string): Model | null | undefined {
  const m = models?.items.find((x) => x.id === id);
  if (!m) return undefined;
  if (m.backend === "mlx-whisper") return m;
  return m.mlx ? models?.items.find((x) => x.id === m.mlx) ?? null : null;
}

/** Где пойдёт модель: `mlx` — MLX на видеоядрах, `mps` — видеоядра (torch), `cpu` — процессор, `off` — недоступна. */
export type RouteKind = "mlx" | "mps" | "cpu" | "off";

/** Что поправить, чтобы модель пошла на видеоядрах. */
export type RouteAction = { kind: "download"; model: Model } | { kind: "install" } | null;

export type MacRoute = {
  kind: RouteKind;
  /** Коротко — справа в списке моделей и в строке «Сейчас настроено». */
  short: string;
  /** Полностью — под списком: через что пойдёт и почему. */
  text: string;
  action: RouteAction;
};

/** MLX доступен: старый резидент без `mac` — считаем, что да (как до 0.6.2). */
const mlxReady = (mac: MacEngine | undefined) => !mac || (mac.mlx_supported && mac.mlx_installed);

/** Через что пойдёт модель `id` на этом Mac. */
export function macRoute(id: string, engine: EngineState | null, models: ModelsState | null): MacRoute {
  const mac = engine?.mac;
  if (id.startsWith(GIGAAM_PREFIX)) {
    if (mac && !mac.mps) {
      return { kind: "cpu", short: "процессор", action: null,
        text: `Процессор: видеоядра недоступны — ${mac.mps_reason ?? "причина неизвестна"}` };
    }
    return { kind: "mps", short: "видеоядра (MPS)", action: null,
      text: "Видеоядра Apple (MPS); если операция там не прошла — шаг повторяется на процессоре" };
  }
  const checkpoint = mlxCheckpoint(models, id);
  if (checkpoint === undefined) {
    return { kind: "cpu", short: "MLX или процессор", action: null,
      text: id ? "Своя модель: чекпоинт MLX — на видеоядрах (MLX), модель faster-whisper — на процессоре"
        : "Модель не выбрана" };
  }
  if (checkpoint === null) {
    return { kind: "cpu", short: "процессор", action: null,
      text: "Процессор (faster-whisper): у этой модели нет чекпоинта MLX, а faster-whisper видеоядер не умеет" };
  }
  const only = checkpoint.id === id;
  if (!mlxReady(mac)) {
    const reason = mac?.mlx_reason ?? "MLX Whisper недоступен";
    const install: RouteAction = mac?.mlx_supported ? { kind: "install" } : null;
    if (only) return { kind: "off", short: `недоступна: ${reason}`, action: install,
      text: `Недоступна: ${reason}. Пока выбрана — распознаёт Whisper medium на процессоре` };
    return { kind: "cpu", short: "процессор", action: install,
      text: `Процессор (faster-whisper): MLX недоступен — ${reason}` };
  }
  if (!checkpoint.downloaded) {
    const size = gbText(checkpoint.size_gb);
    return only
      ? { kind: "cpu", short: "MLX, видеоядра — после загрузки", action: { kind: "download", model: checkpoint },
        text: `MLX на видеоядрах — после загрузки модели (${size}); до тех пор — Whisper medium на процессоре` }
      : { kind: "cpu", short: "процессор; MLX — после загрузки чекпоинта", action: { kind: "download", model: checkpoint },
        text: `Процессор (faster-whisper), пока не скачан чекпоинт MLX (${size}); с ним — MLX на видеоядрах` };
  }
  return { kind: "mlx", short: "MLX, видеоядра", action: null, text: "MLX на видеоядрах Apple" };
}

/** Пояснение справа в списке моделей: где пойдёт и скачана ли. */
export function macOptionDetail(m: Model, engine: EngineState | null, models: ModelsState | null): string {
  const route = macRoute(m.id, engine, models);
  const disk = m.downloaded ? "скачана" : `${gbText(m.size_gb)}, не скачана`;
  return `${route.short} · ${disk}`;
}
