import { useCallback, useEffect, useState } from "react";
import {
  type Endpoint, type Model, type ModelsState, GIGAAM_PREFIX, canDownloadModel, downloadModel, getModels, isGigaam,
  removeModel,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { jobActive, useTrackedJob } from "../../state/useTrackedJob";
import { Button } from "../../ui/Button";
import { HfTokenRow } from "./HfTokenRow";
import { PathText, Row } from "./Section";

const KIND: Record<string, string> = { asr: "распознавание", diarization: "разделение на спикеров", align: "время слов" };

function ModelRow({ model, busy, canDownload, selected, onDownload, onSelect, onRemove }: {
  model: Model; busy: boolean; canDownload: boolean; selected: boolean;
  onDownload: () => void; onSelect: () => void; onRemove: () => void;
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
          {model.downloaded ? " · скачана" : ""}{model.blocked ? " · нужен токен Hugging Face" : ""}
          {!model.downloaded && model.removable ? " · загрузка не завершена" : ""}
        </span>
      </div>
      <div className="srow__control">
        {model.kind === "asr" && (
          <Button variant={selected ? "primary" : "default"} onClick={onSelect} disabled={selected}>
            {selected ? "Выбрана" : "Выбрать"}
          </Button>
        )}
        {isGigaam(model) && model.downloaded ? (
          // GigaAM не обновляется: веса закреплены контрольной суммой.
          <Button disabled>Скачана</Button>
        ) : (
          <Button onClick={onDownload} disabled={busy || model.blocked || !canDownload}>
            {model.downloaded ? "Обновить" : "Скачать"}
          </Button>
        )}
        {model.removable && (
          <Button onClick={onRemove} disabled={busy} aria-label={`Удалить модель ${model.title}`}>Удалить</Button>
        )}
      </div>
    </div>
  );
}

export function ModelsPane({ endpoint, selectedModel, selectedGigaam = null, onSelect, onSelectGigaam }: {
  endpoint: Endpoint;
  /** Модель Whisper из черновика (а не сохранённая). */
  selectedModel: string | null;
  /** Модель GigaAM из черновика (`asr.gigaam_model`, без префикса). */
  selectedGigaam?: string | null;
  onSelect: (id: string) => void;
  /** Выбрана модель GigaAM: имя без префикса («v3_e2e_rnnt»). */
  onSelectGigaam?: (name: string) => void;
}) {
  const [models, setModels] = useState<ModelsState | null>(null);
  const [tried, setTried] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  // Удаление выбранной модели — после подтверждения: она скачается снова при
  // следующей расшифровке (сотни мегабайт без предупреждения).
  const [confirmRemove, setConfirmRemove] = useState<Model | null>(null);

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
  const remove = async (id: string) => {
    setConfirmRemove(null);
    setRemoving(true);
    try {
      const result = await removeModel(endpoint, id);
      await load();
      if (!result.ok) setError(result.error ?? "Модель не удалена");
    } catch (e) { setError(errorText(e)); }
    finally { setRemoving(false); }
  };

  // Токен — до каталога: он нужен и тогда, когда каталог не загрузился.
  const token = <HfTokenRow endpoint={endpoint} onChanged={() => void load()} />;
  if (!models) {
    return <>{token}{error && <p className="error">{error}</p>}<p className="muted">{tried ? "Нет данных." : "Загружаю…"}</p></>;
  }
  const downloading = jobActive(job);
  const busy = downloading || removing;
  const isSelected = (m: Model) => {
    if (isGigaam(m)) return selectedGigaam !== null ? m.id === GIGAAM_PREFIX + selectedGigaam : m.selected;
    return selectedModel !== null ? m.id === selectedModel : m.selected;
  };
  const select = (m: Model) => {
    if (isGigaam(m)) onSelectGigaam?.(m.id.slice(GIGAAM_PREFIX.length));
    else onSelect(m.id);
  };
  return (
    <>
      {token}
      {error && <p className="error">{error}</p>}
      {!models.can_download && (
        <p className="notice">
          Загрузчик моделей устанавливается вместе с движком. После установки движка модели можно скачать
          здесь; выбранная модель распознавания скачается и при первой расшифровке.
        </p>
      )}
      {models.items.map((m) => (
        <ModelRow
          key={m.id} model={m} busy={busy} canDownload={canDownloadModel(models, m)}
          selected={isSelected(m)}
          onDownload={() => void download(m.id)} onSelect={() => select(m)}
          onRemove={() => (isSelected(m) ? setConfirmRemove(m) : void remove(m.id))}
        />
      ))}
      {confirmRemove && (
        <div className="notice" role="alertdialog" aria-label="Удалить выбранную модель">
          <span>
            Модель «{confirmRemove.title}» используется по умолчанию — она скачается снова при следующей
            расшифровке. Удалить?
          </span>{" "}
          <Button variant="danger" onClick={() => void remove(confirmRemove.id)}>Удалить</Button>{" "}
          <Button autoFocus onClick={() => setConfirmRemove(null)}>Отмена</Button>
        </div>
      )}
      <Row label="Папка моделей" hint="Общая с библиотеками движка: скачанные модели не загружаются повторно">
        <span className="folder"><PathText path={models.cache} /></span>
      </Row>
      {models.gigaam_cache && (
        <Row label="Папка моделей GigaAM" hint="В папке данных приложения; после загрузки работает без сети">
          <span className="folder"><PathText path={models.gigaam_cache} /></span>
        </Row>
      )}
      {downloading && <p className="muted">Скачивается {job?.folder}. Окно можно закрыть — загрузка продолжится в службе записи.</p>}
      {job?.state === "failed" && <p className="error">{job.error}</p>}
      {job?.state === "done" && <p className="notice">Скачано: {job.result}</p>}
    </>
  );
}
