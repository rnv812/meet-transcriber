/**
 * Шаг 4 «Модели»: каталог `/models` с размерами и скачивание задачей
 * резидента. Скачивать необязательно — выбранная модель и так скачается при
 * первой расшифровке; здесь — чтобы первая встреча не ждала гигабайты.
 */

import { useCallback, useEffect, useState } from "react";
import { type Endpoint, type Model, type ModelsState, canDownloadModel, downloadModel, getModels } from "../../lib/api";
import { errorText } from "../../lib/format";
import { jobActive, useTrackedJobs } from "../../state/useTrackedJob";
import type { Job } from "../../lib/types";
import { downloadDetail } from "../../lib/progress";
import { Button } from "../../ui/Button";
import { JobProgress } from "../../ui/JobProgress";
import { Loading } from "../../ui/Loading";
import { gb } from "./gate";

const KIND: Record<string, string> = { asr: "распознавание", diarization: "спикеры", align: "выравнивание" };


function ModelItem({ model, job, canDownload, onDownload }: {
  model: Model; job: Job | null; canDownload: boolean; onDownload: () => void;
}) {
  const mine = job && job.folder === model.id ? job : null;
  return (
    <div role="group" aria-label={model.title} className="wizard__model">
      <div className="wizard__model-text">
        <span>
          {model.title}
          {model.recommended && <span className="badge badge--fresh badge--plain">рекомендуется</span>}
        </span>
        <span className="wizard__hint">
          {KIND[model.kind] ?? model.kind} · {gb(model.size_gb)} ГБ
          {model.downloaded ? " · скачана" : ""}{model.blocked ? " · нужен токен Hugging Face" : ""}
        </span>
        {mine && jobActive(mine) && (
          <JobProgress job={mine} size="sm" label={mine.state === "queued" ? "В очереди" : "Скачивается"}
            detail={downloadDetail(mine)} ariaLabel={`Загрузка модели ${model.title}`} />
        )}
        {mine?.state === "failed" && <span className="wizard__error">{mine.error}</span>}
      </div>
      {mine && jobActive(mine) ? null : (
        <Button onClick={onDownload} disabled={model.blocked || !canDownload || model.downloaded}>
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
  // Разные модели качаются одновременно — у каждой свой ход.
  const [jobs, track] = useTrackedJobs(endpoint, "download-model", load);

  const download = async (id: string) => {
    try {
      track(await downloadModel(endpoint, id));
    } catch (cause) {
      setError(errorText(cause));
    }
  };

  return (
    <>
      <p className="muted">
        Модели можно скачать сейчас, чтобы первая расшифровка не ждала загрузки. Окно можно
        закрыть — скачивание продолжится.
      </p>
      {error && <p className="wizard__error">{error}</p>}
      {!models && !error && <Loading label="Загружаю каталог моделей…" />}
      {models && !models.can_download && (
        <p className="muted">Загрузчик моделей ещё не готов — модели скачаются при первой расшифровке.</p>
      )}
      {models && (
        <div className="wizard__models">
          {models.items.map((m) => (
            <ModelItem key={m.id} model={m} job={jobs[m.id] ?? null} canDownload={canDownloadModel(models, m)}
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
