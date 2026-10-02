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

const ENGINE_PROFILE = { cuda: "для видеокарты", cpu: "для процессора", mac: "для Apple Silicon" } as const;

/** Строк лога в памяти: uv многословен, хвоста хватает. */
const MAX_LINES = 400;
/** Как часто перечитывать состояние, пока идёт установка, начатая не этим шагом. */
export const ATTACHED_POLL_MS = 2000;
/** Место под CPU-версию, если оболочка его не прислала (`needs_cpu_gb`), — как `NEEDS_CPU_GB` в engine.rs. */
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
  /** Следим за установкой, начатой не здесь (окно закрывали, фоновое обновление). */
  const [attached, setAttached] = useState(false);
  const elsewhere = engine?.installing === true;

  const setPhase = (next: InstallPhase) => {
    phaseRef.current = next;
    setPhaseState(next);
    onPhase(next);
  };

  // Установка уже идёт — показываем её ход (события те же), а не «Установить».
  useEffect(() => {
    if (elsewhere && phaseRef.current === "idle") {
      setAttached(true);
      setPhase("running");
    }
    // setPhase — замыкание над onPhase; перезапуск эффекта по нему не нужен.
  }, [elsewhere]);

  // Конец чужой установки виден только по состоянию оболочки — опрашиваем.
  useEffect(() => {
    if (!attached) return;
    if (!elsewhere) {
      setAttached(false);
      if (engine?.installed) {
        setPhase("done");
      } else {
        setError("Установка не завершилась — подробности в журнале установки");
        setPhase("failed");
      }
      return;
    }
    const timer = setInterval(() => void refresh.current(), ATTACHED_POLL_MS);
    return () => clearInterval(timer);
  }, [attached, elsewhere, engine?.installed]);

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
  const cpuNeeds = engine.needs_cpu_gb ?? NEEDS_CPU_GB;
  const cpuShortfall = freeSpaceShortfall(cpuNeeds, engine.free_gb);
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
          Версия {engine.version}{engine.profile ? ` · ${ENGINE_PROFILE[engine.profile]}` : ""}
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
        <dt>Нужно места на диске</dt>
        <dd>около {gb(engine.needs_gb)} ГБ — PyTorch и библиотеки распознавания (модели скачиваются отдельно, на шаге «Модели»)</dd>
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
            Установить CPU-версию ({gb(cpuNeeds)} ГБ)
          </button>
        )}
      </div>
    </>
  );
}
