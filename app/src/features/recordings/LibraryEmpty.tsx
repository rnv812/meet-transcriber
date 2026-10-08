/**
 * Пустая библиотека (макет MeetApp, EMPTY): полное сияние Aurora — одно из
 * немногих мест с ним (пустые состояния и мастер). Вид сияния
 * (`ui.aurora_style`) и дрейф (`ui.motion`) задают атрибуты на <html>
 * (theme/appearance), блок — `.aurora--live`, чтобы дрейф был.
 *
 * Текст на сиянии всегда светлый: блок — в тёмной теме (`data-theme="dark"`,
 * как в макете), а палитра повторяется на нём (`data-aurora`): иначе тёмная
 * тема блока вернула бы акцент и сияние палитры по умолчанию.
 *
 * «Записей пока нет», пояснение, «Начать запись» (та же команда, что у кнопки в
 * рейке), «Импортировать файл» (тот же выбор файла, что у зоны импорта) и
 * стеклянная плашка «Автозапись включена: Zoom, …» из `auto_record.processes`
 * (имена программ — из каталога «Программы звонков») или «Автозапись
 * выключена».
 */

import { useEffect, useState } from "react";

import { type Endpoint, importFile, recordingCommand } from "../../lib/api";
import { errorText } from "../../lib/format";
import { OS } from "../../lib/platform";
import { inTauri, pickMedia } from "../../lib/shell";
import type { AutoRecord, Snapshot } from "../../lib/types";
import { Button } from "../../ui/Button";
import { callPrograms } from "../settings/CallPrograms";
import { BROWSER_HINT } from "./ImportZone";
import "./library-empty.css";

const lower = (s: string) => s.toLowerCase();

/** Программы автозаписи по-человечески: «Zoom, Microsoft Teams, Custom, браузеры» (без повторов). */
export function autoRecordText(auto: AutoRecord): string {
  if (!auto.enabled) return "Автозапись выключена";
  const catalog = callPrograms(OS);
  const names: string[] = [];
  for (const exe of auto.processes) {
    const known = catalog.find((p) => p.exes.some((e) => lower(e) === lower(exe)));
    const name = known?.title ?? exe.replace(/\.exe$/i, "");
    if (name && !names.includes(name)) names.push(name);
  }
  if (auto.browsers?.length) names.push("браузеры");
  return names.length ? `Автозапись включена: ${names.join(", ")}` : "Автозапись включена";
}

/** Палитра сияния с <html> (её ставит useAppearance) — и её смена. */
function useRootPalette(): string | undefined {
  const read = () => (typeof document === "undefined" ? undefined : document.documentElement.dataset.aurora);
  const [palette, setPalette] = useState(read);
  useEffect(() => {
    const root = document.documentElement;
    const watch = new MutationObserver(() => setPalette(root.dataset.aurora));
    watch.observe(root, { attributes: true, attributeFilter: ["data-aurora"] });
    return () => watch.disconnect();
  }, []);
  return palette;
}

export function LibraryEmpty({ endpoint, snapshot, onSnapshot, onImported }: {
  endpoint: Endpoint | null;
  snapshot: Snapshot | null;
  /** Ответ команды записи — новый снимок резидента (не ждать опроса). */
  onSnapshot?: (s: Snapshot) => void;
  onImported?: () => void;
}) {
  const palette = useRootPalette();
  const [error, setError] = useState<string | null>(null);
  const auto = snapshot?.auto_record;
  const recording = snapshot?.status === "recording" || !!snapshot?.live?.active || !!snapshot?.live?.starting;

  const start = async () => {
    if (!endpoint) return;
    setError(null);
    try {
      const result = await recordingCommand(endpoint, "start");
      onSnapshot?.(result);
    } catch (e) {
      setError(errorText(e));
    }
  };
  const importOne = async () => {
    if (!endpoint) return;
    setError(null);
    if (!inTauri()) {
      setError(BROWSER_HINT);
      return;
    }
    const path = await pickMedia();
    if (!path) return;
    try {
      await importFile(endpoint, path);
    } catch (e) {
      setError(`${path.split(/[\\/]/).pop() || path}: ${errorText(e)}`);
    }
    onImported?.();
  };

  return (
    <div className="empty aurora aurora--live lib-empty" data-theme="dark" data-aurora={palette}>
      <h2 className="lib-empty__title">Записей пока нет</h2>
      <p className="lib-empty__text">
        {auto?.enabled
          ? "Meet начнёт запись сам, когда начнётся звонок. Можно начать и вручную или перетащить сюда готовый файл."
          : "Нажмите «Начать запись» или перетащите файл"}
      </p>
      <div className="lib-empty__actions">
        <Button variant="primary" size="lg" disabled={!endpoint || recording} onClick={start}>
          <i className="lib-empty__dot" aria-hidden="true" />Начать запись
        </Button>
        <Button size="lg" className="lib-empty__import" disabled={!endpoint} onClick={importOne}>
          Импортировать файл
        </Button>
      </div>
      {error && <p className="lib-empty__error" role="alert">{error}</p>}
      {auto && (
        <span className="glass lib-empty__auto">
          <i className={`lib-empty__auto-dot${auto.enabled ? " lib-empty__auto-dot--on" : ""}`} aria-hidden="true" />
          {autoRecordText(auto)}
        </span>
      )}
    </div>
  );
}
