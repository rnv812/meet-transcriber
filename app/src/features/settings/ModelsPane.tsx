import { useCallback, useEffect, useState } from "react";
import {
  type Endpoint, type Model, type ModelsState, canDownloadModel, downloadModel, getModels, isGigaam, removeModel,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { inTauri, retryGigaamInstall } from "../../lib/shell";
import { jobActive, useTrackedJobs } from "../../state/useTrackedJob";
import { Check } from "lucide-react";
import { downloadDetail, jobFraction } from "../../lib/progress";
import type { Job } from "../../lib/types";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { Icon } from "../../ui/Icon";
import { Loading } from "../../ui/Loading";
import { ProgressBar } from "../../ui/ProgressBar";
import { PathText, Row } from "./Section";

const KIND: Record<string, string> = { asr: "распознавание", diarization: "разделение на спикеров", align: "время слов" };

/**
 * Почему «Скачать» сейчас недоступно — подсказкой на кнопке; null — доступно.
 * Другие загрузки не мешают: разные модели качаются одновременно.
 */
function downloadBlocked(model: Model, removing: boolean, canDownload: boolean): string | null {
  if (model.blocked) return "Нужен токен Hugging Face с принятыми условиями модели";
  if (!canDownload) return "Загрузчик моделей устанавливается вместе с движком";
  if (removing) return "Модель удаляется";
  return null;
}

/** Почему «Удалить…» сейчас недоступно; null — доступно. Мешает только загрузка этой же модели. */
function removeBlocked(downloading: boolean, removing: boolean): string | null {
  if (downloading) return "Модель скачивается — удалить её можно после загрузки";
  if (removing) return "Модель удаляется";
  return null;
}

function ModelRow({ model, job, removing, canDownload, usage, confirming, onDownload, onRemove,
  onConfirmRemove, onCancelRemove }: {
  model: Model; job: Job | null; removing: boolean; canDownload: boolean;
  /** Где модель выбрана в «Распознавании»; пусто — нигде. */
  usage: string[];
  confirming: boolean;
  onDownload: () => void; onRemove: () => void;
  onConfirmRemove: () => void; onCancelRemove: () => void;
}) {
  const selected = usage.length > 0;
  const mine = job && job.folder === model.id ? job : null;
  const loading = mine !== null && jobActive(mine);
  const blocked = downloadBlocked(model, removing, canDownload);
  const noRemove = removeBlocked(loading, removing);
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
        {selected && <span className="srow__hint model-row__usage">Выбрана в «Распознавании»: {usage.join("; ")}</span>}
      </div>
      <div className="srow__control">
        {selected && (
          <span className="model-row__chosen" title="Модель выбрана в разделе «Распознавание»">
            <Icon as={Check} size="sm" />Выбрана
          </span>
        )}
        {isGigaam(model) && model.downloaded ? (
          // GigaAM не обновляется: веса закреплены контрольной суммой.
          <span className="model-row__chosen model-row__chosen--muted">Скачана</span>
        ) : !loading && (
          <Button onClick={onDownload} disabled={blocked !== null} title={blocked ?? undefined}>
            {model.downloaded ? "Обновить" : "Скачать"}
          </Button>
        )}
        {model.removable && (
          <Button variant="danger" onClick={onRemove} disabled={noRemove !== null} title={noRemove ?? undefined}
            aria-label={`Удалить модель ${model.title}`}>
            Удалить…
          </Button>
        )}
      </div>
      {loading && mine && (
        <div className="model-row__progress">
          <ProgressBar value={jobFraction(mine)} stageKey={mine.id} size="sm"
            label={mine.state === "queued" ? "В очереди" : "Скачивается"}
            extrapolate={mine.state === "running"} cap={mine.cap ?? 0.99}
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

/**
 * Модели: загрузка, удаление, размеры. Выбирают модель в «Распознавании»;
 * здесь только видно, где она выбрана (`usage` — по черновику настроек).
 */
export function ModelsPane({ endpoint, usage = {} }: {
  endpoint: Endpoint;
  /** id модели → где она выбрана («видеокарта», «процессор — записи не на русском»). */
  usage?: Record<string, string[]>;
}) {
  const [models, setModels] = useState<ModelsState | null>(null);
  const [tried, setTried] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Какая модель сейчас удаляется (запрос идёт); null — никакая. */
  const [removing, setRemoving] = useState<string | null>(null);
  // Удаление — после подтверждения в строке модели: это гигабайты повторной загрузки.
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  const load = useCallback(async () => {
    try { setModels(await getModels(endpoint)); setError(null); }
    catch (e) { setModels(null); setError(`Каталог моделей не загрузился: ${errorText(e)}`); }
    finally { setTried(true); }
  }, [endpoint]);

  useEffect(() => { void load(); }, [load]);
  const [jobs, track] = useTrackedJobs(endpoint, "download-model", load);

  const download = async (id: string) => {
    try { track(await downloadModel(endpoint, id)); } catch (e) { setError(errorText(e)); }
  };
  const remove = async (id: string) => {
    setConfirmRemove(null);
    setRemoving(id);
    try {
      const result = await removeModel(endpoint, id);
      await load();
      if (!result.ok) setError(result.error ?? "Модель не удалена");
    } catch (e) { setError(errorText(e)); }
    finally { setRemoving(null); }
  };

  /** «Повторить» необязательную установку GigaAM: её делает оболочка (uv). */
  const retryGigaam = async () => {
    setRetrying(true);
    try { await retryGigaamInstall(); setError(null); }
    catch (e) { setError(`GigaAM не установилась: ${errorText(e)}`); }
    finally { setRetrying(false); await load(); }
  };

  if (!models) {
    return <>{error && <p className="error">{error}</p>}
      {tried ? <p className="muted">Нет данных.</p> : <Loading label="Загружаю каталог моделей…" />}</>;
  }
  const done = Object.values(jobs).filter((j) => j.state === "done").map((j) => j.result);
  return (
    <>
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
          key={m.id} model={m} job={jobs[m.id] ?? null} removing={removing === m.id}
          canDownload={canDownloadModel(models, m)}
          usage={m.kind === "asr" ? usage[m.id] ?? [] : []} confirming={confirmRemove === m.id}
          onDownload={() => void download(m.id)}
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
      {done.length > 0 && <p className="notice" role="status">Скачано: {done.join(", ")}</p>}
    </>
  );
}
