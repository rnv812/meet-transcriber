/**
 * Шаг 2 «Установка движка»: место на диске, установка оболочкой (uv) с ходом
 * по шагам из событий `engine-progress`, сбой — хвост лога из `engine-failed`.
 *
 * IMPORTANT: подписка на события — до вызова установки (`ready`): первая
 * строка шага — его название, пропустить её нельзя.
 */

import { useEffect, useRef, useState } from "react";
import type { Profile } from "../../lib/estimate";
import { errorText } from "../../lib/format";
import {
  type EngineFailed, type EngineProgress, type EngineStatus,
  installEngine, onEngineFailed, onEngineProgress,
} from "../../lib/shell";
import { Button } from "../../ui/Button";
import { driveOf, freeSpaceShortfall, gb } from "./gate";

/** Строк лога в памяти: uv многословен, хвоста хватает. */
const MAX_LINES = 400;

type Progress = { step: number; of: number; titles: Record<number, string>; lines: string[] };
const NO_PROGRESS: Progress = { step: 0, of: 4, titles: {}, lines: [] };

function advance(cur: Progress, p: EngineProgress): Progress {
  const titles = cur.titles[p.step] === undefined ? { ...cur.titles, [p.step]: p.line } : cur.titles;
  return { step: p.step, of: p.of, titles, lines: [...cur.lines, p.line].slice(-MAX_LINES) };
}

type Phase = "idle" | "running" | "done" | "failed";

function InstallProgress({ progress }: { progress: Progress }) {
  const steps = Array.from({ length: progress.of }, (_, i) => i + 1);
  return (
    <div className="wizard__install">
      <p className="muted">{progress.step > 0 ? `Шаг ${progress.step} из ${progress.of}` : "Начинаю…"}</p>
      <ol className="wizard__progress" aria-label="Шаги установки">
        {steps.map((n) => (
          <li key={n} className={n < progress.step ? "is-done" : n === progress.step ? "is-current" : undefined}>
            {progress.titles[n] ?? `Шаг ${n}`}
          </li>
        ))}
      </ol>
      {progress.lines.length > 0 && (
        <details className="wizard__log">
          <summary>Подробности</summary>
          <pre className="log">{progress.lines.join("\n")}</pre>
        </details>
      )}
    </div>
  );
}

export function StepEngine({ engine, profile, recording, onInstalled, onNext }: {
  engine: EngineStatus | null | undefined;
  profile: Profile;
  /** Идёт запись: установка гасит резидент — не предлагаем. */
  recording: boolean;
  onInstalled: () => void;
  onNext: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<Progress>(NO_PROGRESS);
  const [failure, setFailure] = useState<EngineFailed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ready = useRef<Promise<unknown>>(Promise.resolve());

  useEffect(() => {
    let offs: Array<() => void> = [];
    let gone = false;
    ready.current = Promise.all([
      onEngineProgress((p) => setProgress((cur) => advance(cur, p))),
      onEngineFailed((f) => setFailure(f)),
    ]).then((list) => {
      if (gone) list.forEach((off) => off());
      else offs = list;
    }).catch((cause) => console.warn("engine events:", cause));
    return () => {
      gone = true;
      offs.forEach((off) => off());
    };
  }, []);

  if (engine === undefined) return <p className="muted">Проверяю движок…</p>;
  if (engine === null) {
    // Браузер (dev): оболочки нет, резидент запущен руками — дальше можно.
    return (
      <>
        <p className="muted">Установка движка доступна только в приложении.</p>
        <div className="wizard__bar"><Button variant="primary" onClick={onNext}>Далее</Button></div>
      </>
    );
  }

  const run = async (fresh: boolean) => {
    setPhase("running");
    setProgress(NO_PROGRESS);
    setFailure(null);
    setError(null);
    await ready.current;
    try {
      await installEngine(profile, fresh);
      setPhase("done");
      onInstalled();
    } catch (cause) {
      setError(errorText(cause));
      setPhase("failed");
    }
  };

  const shortfall = freeSpaceShortfall(engine.needs_gb, engine.free_gb);
  const drive = driveOf(engine.env_dir);
  const installed = phase === "done" || (phase === "idle" && engine.installed);
  const blocked = recording || shortfall !== null;

  const hints = (
    <>
      {shortfall !== null && !installed && (
        <p className="error">Освободите {gb(shortfall)} ГБ на диске{drive ? ` ${drive}` : ""}</p>
      )}
      {recording && <p className="muted">Остановите запись, чтобы переустановить движок</p>}
    </>
  );

  if (installed) {
    return (
      <>
        <p className="notice wizard__lead">Движок установлен</p>
        <p className="muted">
          Версия {engine.version}{engine.profile ? ` · ${engine.profile === "cuda" ? "для видеокарты" : "для процессора"}` : ""}
        </p>
        {hints}
        <div className="wizard__bar">
          <Button variant="primary" onClick={onNext}>Далее</Button>
          {phase === "idle" && (
            <Button onClick={() => void run(true)} disabled={recording}>Переустановить с нуля</Button>
          )}
        </div>
      </>
    );
  }

  return (
    <>
      <dl className="wizard__facts">
        <dt>Объём</dt>
        <dd>около {gb(engine.needs_gb)} ГБ — PyTorch и модели распознавания</dd>
        <dt>Свободно</dt>
        <dd>{engine.free_gb === null ? "неизвестно" : `${gb(engine.free_gb)} ГБ${drive ? ` на диске ${drive}` : ""}`}</dd>
      </dl>
      {phase === "running" && <InstallProgress progress={progress} />}
      {phase === "failed" && (
        <div className="wizard__failed">
          {error && <p className="error">{error}</p>}
          {failure && (
            <>
              <p className="muted">Последние строки журнала (шаг {failure.step}):</p>
              <pre className="log">{failure.tail}</pre>
            </>
          )}
          {progress.lines.length > 0 && <InstallProgress progress={progress} />}
        </div>
      )}
      {hints}
      <div className="wizard__bar">
        {phase === "idle" && (
          <Button variant="primary" onClick={() => void run(false)} disabled={blocked}>Установить</Button>
        )}
        {phase === "running" && <span className="muted">Это займёт несколько минут: скачиваются гигабайты</span>}
        {phase === "failed" && (
          <>
            <Button variant="primary" onClick={() => void run(false)} disabled={blocked}>Повторить</Button>
            <Button onClick={() => void run(true)} disabled={recording}>Переустановить с нуля</Button>
          </>
        )}
      </div>
    </>
  );
}
