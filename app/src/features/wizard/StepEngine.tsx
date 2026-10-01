/**
 * Шаг 2 «Установка движка»: место на диске, установка оболочкой (uv) с ходом
 * по шагам из событий `engine-progress`, сбой — хвост лога из `engine-failed`.
 *
 * Свободное место меняется, пока человек читает шаг: состояние движка
 * перечитывается при входе на шаг, по «Проверить снова», когда окно снова
 * в фокусе и после установки (удачной или нет).
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
/** Место под CPU-версию — как `NEEDS_CPU_GB` в engine.rs (оболочка отдаёт только для профиля по видеокарте). */
export const NEEDS_CPU_GB = 3;

type Progress = { step: number; of: number; titles: Record<number, string>; lines: string[] };
const NO_PROGRESS: Progress = { step: 0, of: 4, titles: {}, lines: [] };

function advance(cur: Progress, p: EngineProgress): Progress {
  const titles = cur.titles[p.step] === undefined ? { ...cur.titles, [p.step]: p.line } : cur.titles;
  return { step: p.step, of: p.of, titles, lines: [...cur.lines, p.line].slice(-MAX_LINES) };
}

export type InstallPhase = "idle" | "running" | "done" | "failed";

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

export function StepEngine({ engine, profile, recording, onRefresh, onPhase, onNext }: {
  engine: EngineStatus | null | undefined;
  /** Профиль по видеокарте; владелец NVIDIA может выбрать и CPU-версию. */
  profile: Profile;
  /** Идёт запись: установка гасит резидент — не предлагаем. */
  recording: boolean;
  /** Перечитать состояние движка у оболочки (место, «установлен»). */
  onRefresh: () => Promise<void>;
  /** Ход установки — мастеру: пока она идёт, его не закрыть. */
  onPhase: (phase: InstallPhase) => void;
  onNext: () => void;
}) {
  const [phase, setPhaseState] = useState<InstallPhase>("idle");
  const [progress, setProgress] = useState<Progress>(NO_PROGRESS);
  const [failure, setFailure] = useState<EngineFailed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Profile>(profile);
  const [checking, setChecking] = useState(false);
  const ready = useRef<Promise<unknown>>(Promise.resolve());
  const refresh = useRef(onRefresh);
  refresh.current = onRefresh;
  const phaseRef = useRef(phase);

  const setPhase = (next: InstallPhase) => {
    phaseRef.current = next;
    setPhaseState(next);
    onPhase(next);
  };

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

  // Вход на шаг и возврат в окно (освобождали место в проводнике) — перечитать.
  useEffect(() => {
    void refresh.current();
    const onFocus = () => { if (phaseRef.current !== "running") void refresh.current(); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
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

  const run = async (fresh: boolean, target: Profile) => {
    setChosen(target);
    setPhase("running");
    setProgress(NO_PROGRESS);
    setFailure(null);
    setError(null);
    await ready.current;
    try {
      await installEngine(target, fresh);
      setPhase("done");
    } catch (cause) {
      setError(errorText(cause));
      setPhase("failed");
    }
    // И после успеха («установлен», профиль), и после сбоя (место, маркер).
    void refresh.current();
  };

  const recheck = async () => {
    setChecking(true);
    try { await refresh.current(); } finally { setChecking(false); }
  };

  const shortfall = freeSpaceShortfall(engine.needs_gb, engine.free_gb);
  const cpuShortfall = freeSpaceShortfall(NEEDS_CPU_GB, engine.free_gb);
  const drive = driveOf(engine.env_dir);
  const installed = phase === "done" || (phase === "idle" && engine.installed);
  // «Повторить» — тем же профилем, что и упавшая попытка.
  const retryShort = chosen === "cpu" ? cpuShortfall : shortfall;
  const offerCpu = profile === "cuda" && (phase === "idle" || phase === "failed");

  const recordingHint = recording && <p className="muted">Остановите запись, чтобы переустановить движок</p>;

  if (installed) {
    return (
      <>
        <p className="notice wizard__lead">Движок установлен</p>
        <p className="muted">
          Версия {engine.version}{engine.profile ? ` · ${engine.profile === "cuda" ? "для видеокарты" : "для процессора"}` : ""}
        </p>
        {recordingHint}
        <div className="wizard__bar">
          <Button variant="primary" onClick={onNext}>Далее</Button>
          {phase === "idle" && (
            <Button onClick={() => void run(true, engine.profile ?? profile)} disabled={recording}>
              Переустановить с нуля
            </Button>
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
          {/* Хвост лога говорит больше строки оболочки: показываем что-то одно. */}
          {failure ? (
            <>
              <p className="error">
                Шаг {failure.step} не удался{progress.titles[failure.step] ? `: ${progress.titles[failure.step]}` : ""}
              </p>
              <pre className="log">{failure.tail}</pre>
            </>
          ) : error && <p className="error">{error}</p>}
        </div>
      )}
      {shortfall !== null && phase !== "running" && (
        <div className="wizard__space">
          <span className="error">Освободите {gb(shortfall)} ГБ на диске{drive ? ` ${drive}` : ""}</span>
          <Button onClick={() => void recheck()} disabled={checking}>
            {checking ? "Проверяю…" : "Проверить снова"}
          </Button>
        </div>
      )}
      {recordingHint}
      <div className="wizard__bar">
        {phase === "idle" && (
          <Button variant="primary" onClick={() => void run(false, profile)} disabled={recording || shortfall !== null}>
            Установить
          </Button>
        )}
        {phase === "running" && <span className="muted">Это займёт несколько минут: скачиваются гигабайты</span>}
        {phase === "failed" && (
          <>
            <Button variant="primary" onClick={() => void run(false, chosen)} disabled={recording || retryShort !== null}>
              Повторить
            </Button>
            <Button onClick={() => void run(true, chosen)} disabled={recording}>Переустановить с нуля</Button>
          </>
        )}
        {offerCpu && (
          <button type="button" className="wizard__link" onClick={() => void run(false, "cpu")}
            disabled={recording || cpuShortfall !== null}
            title="Расшифровка на процессоре — медленнее, зато движок меньше">
            Установить CPU-версию ({NEEDS_CPU_GB} ГБ)
          </button>
        )}
      </div>
    </>
  );
}
