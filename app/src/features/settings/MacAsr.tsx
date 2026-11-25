/**
 * «Распознавание» на Mac (0.6.2): что умеет этот Mac (видеоядра для torch и
 * MLX Whisper, «Установить» MLX) и одна модель распознавания на все встречи —
 * у каждой модели в списке видно, через что она пойдёт («MLX, видеоядра»,
 * «видеоядра (MPS)», «процессор»), под списком — полностью и с кнопкой
 * «Скачать» чекпоинта MLX. Правила — `macRoute.ts`; Windows этого не видит.
 */

import { useCallback, useId, useState } from "react";
import {
  type EngineState, type Endpoint, type ModelsState, downloadModel, GIGAAM_PREFIX,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { inTauri, retryMlxInstall } from "../../lib/shell";
import { jobActive, useTrackedJobs } from "../../state/useTrackedJob";
import { Button } from "../../ui/Button";
import { Select } from "../../ui/Select";
import { modelTitle } from "./AsrChoice";
import { fieldClass } from "./fields";
import { macChoices, macModelOf, macModelValues, macOptionDetail, macRoute } from "./macRoute";
import { Row, type Raw, type SetFn } from "./Section";

const OTHER = "\u0000other";

/** «Установить» MLX Whisper: необязательный шаг установки движка (оболочка, uv). */
function InstallMlx({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!inTauri()) return null;
  const run = async () => {
    setBusy(true);
    setError(null);
    try { await retryMlxInstall(); } catch (e) { setError(`MLX Whisper не установился: ${errorText(e)}`); }
    finally { setBusy(false); onDone(); }
  };
  return (
    <>
      <Button onClick={() => void run()} disabled={busy}>{busy ? "Устанавливаю…" : "Установить"}</Button>
      {error && <span className="error asr-route__error">{error}</span>}
    </>
  );
}

/** Что умеет этот Mac: видеоядра (MPS) и MLX Whisper — по сведениям резидента. */
export function MacAccel({ engine, onEngineChanged }: {
  engine: EngineState | null;
  /** Перечитать `/engine` (после установки MLX). */
  onEngineChanged: () => void;
}) {
  const mac = engine?.mac;
  const version = mac?.macos ? ` · macOS ${mac.macos}` : "";
  return (
    <>
      <Row label="Видеоядра Apple"
        hint="GigaAM, разделение на спикеров, время слов и голоса считаются на видеоядрах (MPS). Если операция там не прошла, шаг повторяется на процессоре — это страховка, не настройка">
        {!mac ? <span className="badge badge--plain">{engine ? "сведений нет" : "проверяю…"}</span>
          : mac.mps ? <span className="badge badge--fresh">работают{version}</span>
            : <span className="badge badge--stale">недоступны: {mac.mps_reason}</span>}
      </Row>
      <Row label="MLX Whisper"
        hint="Модели Whisper с чекпоинтом MLX идут на видеоядрах; без MLX — faster-whisper на процессоре. Нужна macOS 14 или новее">
        {!mac ? <span className="badge badge--plain">{engine ? "сведений нет" : "проверяю…"}</span>
          : mac.mlx_supported && mac.mlx_installed ? <span className="badge badge--fresh">установлен</span>
            : (
              <span className="asr-route">
                <span className="badge badge--stale">недоступен: {mac.mlx_reason}</span>
                {mac.mlx_supported && <InstallMlx onDone={onEngineChanged} />}
              </span>
            )}
      </Row>
    </>
  );
}

/**
 * Модель распознавания на Mac: список моделей каталога (у каждой — через что
 * пойдёт и скачана ли) и «Другая…» (id или папка); под списком — через что
 * пойдёт выбранная и что сделать, чтобы она шла на видеоядрах.
 */
export function MacModel({ draft, set, engine, models, endpoint, onModelsChanged, onEngineChanged }: {
  draft: Raw; set: SetFn; engine: EngineState | null; models: ModelsState | null; endpoint: Endpoint;
  onModelsChanged: () => void; onEngineChanged: () => void;
}) {
  const uid = useId();
  const asr = draft.asr ?? {};
  const value = macModelOf(asr);
  const choices = macChoices(models);
  const known = choices.some((m) => m.id === value);
  const [other, setOther] = useState(false);
  const showOther = choices.length === 0 || other || !known;
  const [error, setError] = useState<string | null>(null);
  const settled = useCallback(() => onModelsChanged(), [onModelsChanged]);
  const [jobs, track] = useTrackedJobs(endpoint, "download-model", settled);
  const route = macRoute(value, engine, models);
  const choose = (id: string) => {
    for (const [k, x] of Object.entries(macModelValues(id))) set("asr", k, x);
  };
  const download = async (id: string) => {
    setError(null);
    try { track(await downloadModel(endpoint, id)); } catch (e) { setError(errorText(e)); }
  };
  const action = route.action;
  const job = action?.kind === "download" ? jobs[action.model.id] : undefined;
  const loading = jobActive(job);
  const gigaam = value.startsWith(GIGAAM_PREFIX);
  const language = String(asr.language ?? "ru").trim().toLowerCase() || "ru";
  return (
    <>
      <Row label="Модель распознавания" htmlFor={`${uid}-model`} stack
        hint="Одна модель на все встречи. GigaAM — только русский, быстрее всех; Whisper понимает и другие языки и учитывает термины">
        <div className="asr-model">
          {choices.length > 0 && (
            <Select id={`${uid}-model`} size="sm" width="100%" value={showOther ? OTHER : value}
              options={[
                ...choices.map((m) => {
                  const r = macRoute(m.id, engine, models);
                  return { value: m.id, label: m.title, detail: macOptionDetail(m, engine, models), disabled: r.kind === "off" && m.id !== value };
                }),
                { value: OTHER, label: "Другая…", detail: "id Whisper или папка" },
              ]}
              onChange={(v) => {
                if (v === OTHER) { setOther(true); return; }
                setOther(false);
                choose(v);
              }} />
          )}
          {showOther && (
            <input type="text" className={fieldClass({ wide: true, mono: true })} spellCheck={false}
              id={choices.length === 0 ? `${uid}-model` : undefined}
              aria-label={choices.length === 0 ? undefined : "Модель распознавания: id модели или папка"}
              placeholder="id модели Whisper на Hugging Face или путь к папке (faster-whisper или MLX)"
              value={gigaam ? "" : value} onChange={(e) => choose(e.target.value)} />
          )}
          <p className="asr-route" role="status" aria-label="Как пойдёт модель">
            <span>{route.text}</span>
            {action?.kind === "download" && !loading && (
              <Button onClick={() => void download(action.model.id)}>Скачать</Button>
            )}
            {loading && <span className="muted">Скачивается «{modelTitle(models, action?.kind === "download" ? action.model.id : "")}»…</span>}
            {action?.kind === "install" && <InstallMlx onDone={onEngineChanged} />}
          </p>
          {job?.state === "failed" && <p className="error">{job.error}</p>}
          {error && <p className="error">{error}</p>}
          {gigaam && language !== "ru" && language !== "auto" && (
            <p className="notice" role="status">
              Язык речи «{language}», а GigaAM понимает только русский: пока так, встречи распознаёт Whisper —{" "}
              {modelTitle(models, String(asr.cpu_model ?? ""))}. Выберите модель Whisper или язык ru.
            </p>
          )}
        </div>
      </Row>
    </>
  );
}
