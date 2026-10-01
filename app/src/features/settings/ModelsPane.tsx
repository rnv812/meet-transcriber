import { useCallback, useEffect, useState } from "react";
import { type Endpoint, type Model, type ModelsState, downloadModel, getModels } from "../../lib/api";
import { errorText } from "../../lib/format";
import { jobActive, useTrackedJob } from "../../state/useTrackedJob";
import { Button } from "../../ui/Button";
import { HfTokenRow } from "./HfTokenRow";
import { Row } from "./Section";

const KIND: Record<string, string> = { asr: "распознавание", diarization: "спикеры", align: "выравнивание" };

function ModelRow({ model, busy, canDownload, selected, onDownload, onSelect }: {
  model: Model; busy: boolean; canDownload: boolean; selected: boolean;
  onDownload: () => void; onSelect: () => void;
}) {
  return (
    <div className="srow">
      <div className="srow__text">
        <span className="srow__label">
          {model.title}
          {model.recommended && <span className="tag tag--live">рекомендуется</span>}
        </span>
        <span className="srow__hint">{model.note}</span>
        <span className="srow__hint">
          {KIND[model.kind] ?? model.kind} · {model.size_gb} ГБ · {model.id}
          {model.downloaded ? " · скачана" : ""}{model.blocked ? " · нужен токен" : ""}
        </span>
      </div>
      <div className="srow__control">
        {model.kind === "asr" && (
          <Button variant={selected ? "primary" : "default"} onClick={onSelect} disabled={selected}>
            {selected ? "выбрана" : "выбрать"}
          </Button>
        )}
        <Button onClick={onDownload} disabled={busy || model.blocked || !canDownload}>
          {model.downloaded ? "обновить" : "скачать"}
        </Button>
      </div>
    </div>
  );
}

export function ModelsPane({ endpoint, selectedModel, onSelect }: {
  endpoint: Endpoint;
  /** Модель распознавания из черновика (а не сохранённая). */
  selectedModel: string | null;
  onSelect: (id: string) => void;
}) {
  const [models, setModels] = useState<ModelsState | null>(null);
  const [tried, setTried] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setModels(await getModels(endpoint)); setError(null); }
    catch (e) { setModels(null); setError(`Каталог моделей не загрузился: ${errorText(e)}`); }
    finally { setTried(true); }
  }, [endpoint]);

  useEffect(() => { void load(); }, [load]);
  const [job, setJob] = useTrackedJob(endpoint, "download-model", load);

  const download = async (id: string) => {
    try { setJob(await downloadModel(endpoint, id)); } catch (e) { setError(errorText(e)); }
  };

  // Токен — до каталога: он нужен и тогда, когда каталог не загрузился.
  const token = <HfTokenRow endpoint={endpoint} onChanged={() => void load()} />;
  if (!models) {
    return <>{token}{error && <p className="error">{error}</p>}<p className="muted">{tried ? "Нет данных." : "Загружаю…"}</p></>;
  }
  const busy = jobActive(job);
  return (
    <>
      {token}
      {error && <p className="error">{error}</p>}
      {!models.can_download && (
        <p className="notice">
          Загрузчик моделей приходит вместе с движком. Установите его выше — после этого модели можно
          скачивать; выбранная модель распознавания и так скачается при первой расшифровке.
        </p>
      )}
      {models.items.map((m) => (
        <ModelRow
          key={m.id} model={m} busy={busy} canDownload={models.can_download}
          selected={selectedModel !== null ? m.id === selectedModel : m.selected}
          onDownload={() => void download(m.id)} onSelect={() => onSelect(m.id)}
        />
      ))}
      <Row label="Кэш моделей" hint="общий с библиотеками движка: уже скачанное не качается заново">
        <code className="path">{models.cache}</code>
      </Row>
      {busy && <p className="muted">Качаю {job?.folder}: это гигабайты. Окно можно закрыть — задача идёт в резиденте.</p>}
      {job?.state === "failed" && <p className="error">{job.error}</p>}
      {job?.state === "done" && <p className="notice">Скачано: {job.result}</p>}
    </>
  );
}
