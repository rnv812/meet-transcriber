import { useCallback, useEffect, useState } from "react";
import {
  type Endpoint, type Model, type ModelsState, GIGAAM_PREFIX, canDownloadModel, downloadModel, getModels, isGigaam,
  removeModel,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { inTauri, retryGigaamInstall } from "../../lib/shell";
import { jobActive, useTrackedJob } from "../../state/useTrackedJob";
import { Check } from "lucide-react";
import { downloadDetail, jobFraction } from "../../lib/progress";
import type { Job } from "../../lib/types";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { Icon } from "../../ui/Icon";
import { Loading } from "../../ui/Loading";
import { ProgressBar } from "../../ui/ProgressBar";
import { HfTokenRow } from "./HfTokenRow";
import { PathText, Row } from "./Section";

const KIND: Record<string, string> = { asr: "распознавание", diarization: "разделение на спикеров", align: "время слов" };

/** Почему «Скачать» сейчас недоступно — подсказкой на кнопке; null — доступно. */
function downloadBlocked(model: Model, busy: boolean, canDownload: boolean): string | null {
  if (model.blocked) return "Нужен токен Hugging Face с принятыми условиями модели";
  if (!canDownload) return "Загрузчик моделей устанавливается вместе с движком";
  if (busy) return "Дождитесь окончания другой загрузки";
  return null;
}

function ModelRow({ model, job, busy, canDownload, selected, confirming, onDownload, onSelect, onRemove,
  onConfirmRemove, onCancelRemove }: {
  model: Model; job: Job | null; busy: boolean; canDownload: boolean; selected: boolean; confirming: boolean;
  onDownload: () => void; onSelect: () => void; onRemove: () => void;
  onConfirmRemove: () => void; onCancelRemove: () => void;
}) {
  const mine = job && job.folder === model.id ? job : null;
  const loading = mine !== null && jobActive(mine);
  const blocked = downloadBlocked(model, busy, canDownload);
  return (
    <div className="srow model-row" role="group" aria-label={model.title}>
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
        {model.kind === "asr" && (selected ? (
          <span className="model-row__chosen" title="Эта модель распознаёт речь по умолчанию">
            <Icon as={Check} size="sm" />Выбрана
          </span>
        ) : (
          <Button onClick={onSelect}>Выбрать</Button>
        ))}
        {isGigaam(model) && model.downloaded ? (
          // GigaAM не обновляется: веса закреплены контрольной суммой.
          <span className="model-row__chosen model-row__chosen--muted">Скачана</span>
        ) : !loading && (
          <Button onClick={onDownload} disabled={blocked !== null} title={blocked ?? undefined}>
            {model.downloaded ? "Обновить" : "Скачать"}
          </Button>
        )}
        {model.removable && (
          <Button variant="danger" onClick={onRemove} disabled={busy} aria-label={`Удалить модель ${model.title}`}>
            Удалить…
          </Button>
        )}
      </div>
      {loading && mine && (
        <div className="model-row__progress">
          <ProgressBar value={jobFraction(mine)} stageKey={mine.id} size="sm" label="Скачивается"
            detail={downloadDetail(mine) ?? "Окно можно закрыть — загрузка продолжится"}
            ariaLabel={`Загрузка модели ${model.title}`} />
        </div>
      )}
      {mine?.state === "failed" && <p className="error model-row__progress">{mine.error}</p>}
      {confirming && (
        <ConfirmDialog inline className="model-row__progress" title={`Удалить модель «${model.title}»?`}
          message={selected
            ? `Модель выбрана для распознавания — она скачается снова (${model.size_gb} ГБ) при следующей расшифровке.`
            : `Файлы модели (${model.size_gb} ГБ) будут удалены с диска; понадобится — скачайте снова.`}
          confirmLabel="Удалить" onConfirm={onConfirmRemove} onCancel={onCancelRemove} />
      )}
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
  // Удаление — после подтверждения в строке модели: это гигабайты повторной загрузки.
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

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

  /** «Повторить» необязательную установку GigaAM: её делает оболочка (uv). */
  const retryGigaam = async () => {
    setRetrying(true);
    try { await retryGigaamInstall(); setError(null); }
    catch (e) { setError(`GigaAM не установилась: ${errorText(e)}`); }
    finally { setRetrying(false); await load(); }
  };

  // Токен — до каталога: он нужен и тогда, когда каталог не загрузился.
  const token = <HfTokenRow endpoint={endpoint} onChanged={() => void load()} />;
  if (!models) {
    return <>{token}{error && <p className="error">{error}</p>}
      {tried ? <p className="muted">Нет данных.</p> : <Loading label="Загружаю каталог моделей…" />}</>;
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
      {models.gigaam_install_error && (
        <p className="notice" role="status">
          {models.gigaam_install_error}{" "}
          {inTauri() && (
            <Button onClick={() => void retryGigaam()} disabled={retrying}>
              {retrying ? "Устанавливаю…" : "Повторить"}
            </Button>
          )}
        </p>
      )}
      {!models.can_download && (
        <p className="notice">
          Загрузчик моделей устанавливается вместе с движком. После установки движка модели можно скачать
          здесь; выбранная модель распознавания скачается и при первой расшифровке.
        </p>
      )}
      {models.items.map((m) => (
        <ModelRow
          key={m.id} model={m} job={job} busy={busy} canDownload={canDownloadModel(models, m)}
          selected={isSelected(m)} confirming={confirmRemove === m.id}
          onDownload={() => void download(m.id)} onSelect={() => select(m)}
          onRemove={() => setConfirmRemove(m.id)}
          onConfirmRemove={() => void remove(m.id)} onCancelRemove={() => setConfirmRemove(null)}
        />
      ))}
      <Row label="Папка моделей" hint="Общая с библиотеками движка: скачанные модели не загружаются повторно">
        <span className="folder"><PathText path={models.cache} /></span>
      </Row>
      {models.gigaam_cache && (
        <Row label="Папка моделей GigaAM" hint="В папке данных приложения; после загрузки работает без сети">
          <span className="folder"><PathText path={models.gigaam_cache} /></span>
        </Row>
      )}
      {job?.state === "done" && <p className="notice" role="status">Скачано: {job.result}</p>}
    </>
  );
}
