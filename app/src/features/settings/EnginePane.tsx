import { useCallback, useEffect, useState } from "react";
import { type Endpoint, type EngineState, getEngine, getJobs, installEngine } from "../../lib/api";
import type { Job } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Row } from "./Section";

const active = (j: Job | null) => j?.state === "queued" || j?.state === "running";

export function EnginePane({ endpoint }: { endpoint: Endpoint }) {
  const [engine, setEngine] = useState<EngineState | null>(null);
  const [tried, setTried] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setEngine(await getEngine(endpoint)); setError(null); }
    catch (e) { setEngine(null); setError(`Движок не загрузился: ${String(e)}`); }
    finally { setTried(true); }
  }, [endpoint]);

  useEffect(() => { void load(); }, [load]);

  // Установка идёт минутами; опрос переживает любые обрывы.
  useEffect(() => {
    if (!job || !active(job)) return;
    const timer = window.setInterval(async () => {
      const jobs = await getJobs(endpoint).catch(() => null);
      const mine = jobs?.items.find((i) => i.id === job.id);
      if (mine) setJob(mine);
      if (mine && (mine.state === "done" || mine.state === "failed")) void load();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [endpoint, job, load]);

  const install = async () => {
    try { setJob(await installEngine(endpoint)); } catch (e) { setError(String(e)); }
  };

  if (!engine) {
    return <>{error && <p className="error">{error}</p>}<p className="muted">{tried ? "Нет данных." : "Загружаю…"}</p></>;
  }
  const busy = active(job);
  return (
    <>
      {error && <p className="error">{error}</p>}
      <Row label="Состояние" hint="без движка приложение пишет встречи, но не расшифровывает их">
        <span className={engine.installed ? "tag tag--live" : "tag"}>{engine.installed ? "установлен" : "не установлен"}</span>
      </Row>
      <Row label="Видеокарта" hint="без неё расшифровка идёт на процессоре и заметно дольше">
        <span className="tag">{engine.gpu.available ? (engine.gpu.name ?? "есть") : "не найдена"}</span>
      </Row>
      <Row label="ffmpeg" hint="нужен и для записи, и для конвертации дорожек">
        <span className={engine.ffmpeg ? "tag tag--live" : "tag"}>{engine.ffmpeg ? "есть" : "нет"}</span>
      </Row>
      <Row
        label="Что будет установлено"
        hint={`вариант ${engine.flavor === "cuda" ? "для видеокарты" : "для процессора"}, около ${engine.download_gb} ГБ загрузки`}
      >
        <span className="tags">
          {engine.components.map((c) => (
            <span key={c.module} className={c.installed ? "tag tag--live" : "tag"}>{c.title}</span>
          ))}
        </span>
      </Row>
      <Row label="Куда" hint="окружение, которым резидент запускает задачи"><code className="path">{engine.target}</code></Row>
      <div className="sbar">
        <Button variant="primary" onClick={() => void install()} disabled={busy}>
          {busy ? "Ставлю…" : engine.installed ? "Переустановить" : "Установить движок"}
        </Button>
        {busy && <span className="muted">это надолго: гигабайты и минуты</span>}
        {job?.state === "failed" && <span className="error">{job.error}</span>}
        {job?.state === "done" && <span className="notice">Готово</span>}
      </div>
      {job?.note && <p className="muted">{job.note}</p>}
    </>
  );
}
