import { useCallback, useEffect, useState } from "react";
import { type Endpoint, type Model, type ModelsState, downloadModel, getJobs, getModels } from "../../lib/api";
import type { Job } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Row } from "./Section";

const KIND: Record<string, string> = { asr: "распознавание", diarization: "спикеры", align: "выравнивание" };
const active = (j: Job | null) => j?.state === "queued" || j?.state === "running";

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

export function ModelsPane({ endpoint, selectedModel, onSelect, token, onToken }: {
  endpoint: Endpoint;
  /** Модель распознавания из черновика (а не сохранённая). */
  selectedModel: string | null;
  onSelect: (id: string) => void;
  token: string;
  onToken: (v: string) => void;
}) {
  const [models, setModels] = useState<ModelsState | null>(null);
  const [tried, setTried] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setModels(await getModels(endpoint)); setError(null); }
    catch (e) { setModels(null); setError(`Каталог моделей не загрузился: ${String(e)}`); }
    finally { setTried(true); }
  }, [endpoint]);

  useEffect(() => { void load(); }, [load]);

  // Returning to the section while a task runs: pick it up so progress shows.
  useEffect(() => {
    let live = true;
    getJobs(endpoint).then((r) => {
      const mine = r.items.find((i) => i.kind === "download-model" && active(i));
      if (live && mine) setJob((cur) => cur ?? mine);
    }).catch(() => {});
    return () => { live = false; };
  }, [endpoint]);

  useEffect(() => {
    if (!job || !active(job)) return;
    let live = true;
    const timer = window.setInterval(async () => {
      const list = await getJobs(endpoint).catch(() => null);
      if (!live) return;
      const mine = list?.items.find((i) => i.id === job.id);
      if (mine) setJob(mine);
      if (mine && (mine.state === "done" || mine.state === "failed")) void load();
    }, 2000);
    return () => { live = false; window.clearInterval(timer); };
  }, [endpoint, job, load]);

  const download = async (id: string) => {
    try { setJob(await downloadModel(endpoint, id)); } catch (e) { setError(String(e)); }
  };

  if (!models) {
    return <>{error && <p className="error">{error}</p>}<p className="muted">{tried ? "Нет данных." : "Загружаю…"}</p></>;
  }
  const busy = active(job);
  return (
    <>
      {error && <p className="error">{error}</p>}
      {!models.can_download && (
        <p className="notice">
          Загрузчик моделей приходит вместе с движком. Установите его выше — после этого модели можно
          скачивать; выбранная модель распознавания и так скачается при первой расшифровке.
        </p>
      )}
      <Row
        label="Токен Hugging Face" htmlFor="hf-token"
        hint="нужен только модели спикеров: она за принятием условий на huggingface.co. Пусто — берётся переменная среды HF_TOKEN"
      >
        <input id="hf-token" type="text" placeholder={models.token ? "задан" : "не задан"} value={token} onChange={(e) => onToken(e.target.value)} />
      </Row>
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
