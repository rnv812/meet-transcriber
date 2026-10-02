/**
 * Шаг 4 «Модели»: каталог `/models` с размерами и скачивание задачей
 * резидента. Скачивать необязательно — выбранная модель и так скачается при
 * первой расшифровке; здесь — чтобы первая встреча не ждала гигабайты.
 */

import { useCallback, useEffect, useState } from "react";
import { type Endpoint, type Model, type ModelsState, canDownloadModel, downloadModel, getModels } from "../../lib/api";
import { errorText } from "../../lib/format";
import { jobActive, useTrackedJob } from "../../state/useTrackedJob";
import type { Job } from "../../lib/types";
import { Button } from "../../ui/Button";
import { gb } from "./gate";

const KIND: Record<string, string> = { asr: "распознавание", diarization: "спикеры", align: "выравнивание" };

function percent(job: Job): number | null {
  return job.total ? Math.round(((job.done ?? 0) / job.total) * 100) : null;
}

function ModelItem({ model, job, busy, canDownload, onDownload }: {
  model: Model; job: Job | null; busy: boolean; canDownload: boolean; onDownload: () => void;
}) {
  const mine = job && job.folder === model.id ? job : null;
  const pct = mine ? percent(mine) : null;
  return (
    <div role="group" aria-label={model.title} className="wizard__model">
      <div className="wizard__model-text">
        <span>
          {model.title}
          {model.recommended && <span className="tag tag--live">рекомендуется</span>}
        </span>
        <span className="wizard__hint">
          {KIND[model.kind] ?? model.kind} · {gb(model.size_gb)} ГБ
          {model.downloaded ? " · скачана" : ""}{model.blocked ? " · нужен токен Hugging Face" : ""}
        </span>
        {mine && jobActive(mine) && (
          <div className="wizard__meter"><div className="wizard__meter-fill" style={{ width: `${pct ?? 100}%` }} /></div>
        )}
        {mine?.state === "failed" && <span className="error">{mine.error}</span>}
      </div>
      {mine && jobActive(mine) ? (
        <span className="muted num">Качаю…{pct !== null ? ` ${pct}%` : ""}</span>
      ) : (
        <Button onClick={onDownload} disabled={busy || model.blocked || !canDownload || model.downloaded}>
          {model.downloaded ? "Скачана" : "Скачать"}
        </Button>
      )}
    </div>
  );
}

export function StepModels({ endpoint, onNext }: { endpoint: Endpoint; onNext: () => void }) {
  const [models, setModels] = useState<ModelsState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setModels(await getModels(endpoint));
      setError(null);
    } catch (cause) {
      setError(`Каталог моделей не загрузился: ${errorText(cause)}`);
    }
  }, [endpoint]);

  useEffect(() => { void load(); }, [load]);
  const [job, setJob] = useTrackedJob(endpoint, "download-model", load);

  const download = async (id: string) => {
    try {
      setJob(await downloadModel(endpoint, id));
    } catch (cause) {
      setError(errorText(cause));
    }
  };

  const busy = jobActive(job);
  return (
    <>
      <p className="muted">
        Модели можно скачать сейчас, чтобы первая расшифровка не ждала загрузки. Окно можно
        закрыть — скачивание продолжится.
      </p>
      {error && <p className="error">{error}</p>}
      {!models && !error && <p className="muted">Загружаю…</p>}
      {models && !models.can_download && (
        <p className="muted">Загрузчик моделей ещё не готов — модели скачаются при первой расшифровке.</p>
      )}
      {models && (
        <div className="wizard__models">
          {models.items.map((m) => (
            <ModelItem key={m.id} model={m} job={job} busy={busy} canDownload={canDownloadModel(models, m)}
              onDownload={() => void download(m.id)} />
          ))}
        </div>
      )}
      <div className="wizard__bar">
        <Button variant="primary" onClick={onNext}>Далее</Button>
      </div>
    </>
  );
}
