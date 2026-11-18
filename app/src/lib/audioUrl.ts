/**
 * Адрес звука для <audio> — один на всё окно: плеер карточки (и образцы в ней),
 * образец человека в «Голосах», примеры «Моего голоса».
 *
 * Токен — в query: <audio> не умеет ставить заголовки (сервер это поддерживает
 * намеренно, см. control.py).
 *
 * На macOS звук всегда просится в WebM (`format=webm`): Ogg/Opus WKWebView не
 * играет до macOS 15.4, а на 15.4+ играет с неверным медиа-временем — щелчок по
 * слову и точная остановка образца промахивались бы. Резидент перепаковывает
 * тот же Opus без перекодирования и кэширует рядом с дорожкой. На Windows
 * (WebView2) — Ogg как есть. Формат решается один раз по платформе окна, не
 * пробной загрузкой: `canPlayType` в WebKit ненадёжен, а таймаут загрузки ложно
 * срабатывает на больших файлах.
 */

import type { Endpoint } from "./api";
import { OS, type Os } from "./platform";

/**
 * `playback` — то, что играет карточка: обе стороны звонка, сведённые
 * резидентом (импорт — как есть); `them`/`you` — одна сторона для
 * переключателя плеера (собеседники; «Вы» — микрофон на шкале текста, с
 * применённым сдвигом); `sys`/`mic`/`source` — сырые дорожки (образцы голосов,
 * диагностика).
 */
export type AudioTrack = "playback" | "them" | "you" | "sys" | "mic" | "source";
/** В каком контейнере просить звук; null — как лежит у резидента. */
export type AudioFormat = "webm" | null;

export const audioFormat = (os: Os): AudioFormat => (os === "macos" ? "webm" : null);
/** Формат звука этого окна. */
export const AUDIO_FORMAT: AudioFormat = audioFormat(OS);

/**
 * Адрес дорожки записи. `span` — кусок [начало, конец] в секундах (фрагмент
 * `#t=`: образцы голосов); `format` — по умолчанию формат этого окна.
 */
export function audioUrl(ep: Endpoint, id: string, track: AudioTrack,
  { span, format = AUDIO_FORMAT }: { span?: readonly [number, number]; format?: AudioFormat } = {}): string {
  const q = new URLSearchParams({ track });
  if (format) q.set("format", format);
  if (ep.token) q.set("token", ep.token);
  const url = `${ep.base}/recordings/${encodeURIComponent(id)}/audio?${q}`;
  return span ? `${url}#t=${span[0]},${span[1]}` : url;
}

/**
 * Почему звук по адресу не загрузился — текст ошибки резидента (перепаковка не
 * вышла, дорожки нет). Спрашивает один байт: <audio> текста ошибки не видит.
 * null — звук отдаётся (не сумел сам плеер) или резидент не ответил.
 */
export async function audioProblem(url: string): Promise<string | null> {
  let r: Response;
  try {
    r = await fetch(url.split("#")[0]!, { headers: { Range: "bytes=0-0" } });
  } catch {
    return null;
  }
  if (r.ok) return null;
  try {
    const body: unknown = await r.json();
    const error = (body as { error?: unknown } | null)?.error;
    if (typeof error === "string" && error) return error;
  } catch {
    // не JSON — ниже код ответа
  }
  return `ошибка ${r.status}`;
}
