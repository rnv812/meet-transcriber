/**
 * Плавающая панель: главная поверхность приложения.
 *
 * Правила, из которых сделан этот экран:
 *
 * * панель никогда не перехватывает фокус — иначе она мешает встрече, ради
 *   которой существует (окно создаётся в Rust с focus: false);
 * * свёрнутая занимает минимум места («пилюля» с точкой состояния и временем);
 * * состояние приходит одним снимком, поэтому панель рисуется сразу, не
 *   дожидаясь, пока что-нибудь произойдёт;
 * * «дежурный не запущен» — это тоже экран, а не пустота с ошибкой в консоли.
 */

import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  fitWindowTo,
  hidePanel,
  inTauri,
  openEditor,
  openFolder,
  openSettings,
  startDragging,
  transcribeRecording,
} from "./api";
import "./panel.css";
import type { Snapshot } from "./types";
import { useResident } from "./useResident";

/** Секунды → «М:СС» или «Ч:ММ:СС» (как в трее). */
function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}

/**
 * Секунды записи, тикающие локально между снимками.
 *
 * Источник правды — резидент (`elapsed_s`), но снимок приходит раз в несколько
 * секунд, а секунды на панели должны бежать ровно. Поэтому каждый снимок
 * ставит новую точку отсчёта, а между снимками время идёт по локальным часам.
 */
function useElapsed(base: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  const [anchor, setAnchor] = useState<{ base: number; at: number } | null>(null);
  const stopped = base === null;
  useEffect(() => {
    setAnchor(base === null ? null : { base, at: Date.now() });
  }, [base]);
  useEffect(() => {
    if (stopped) return;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [stopped]);
  if (!anchor) return 0;
  return anchor.base + (now - anchor.at) / 1000;
}

type Mood = "idle" | "call" | "recording" | "offline";

function moodOf(snapshot: Snapshot | null, connected: boolean): Mood {
  if (!connected || !snapshot) return "offline";
  if (snapshot.status === "recording") return "recording";
  // Машина детектора говорит про звонок, а не про нашу запись: звонок идёт, а
  // записи нет — значит автозапись выключена или человек её остановил.
  if (snapshot.auto_record.state === "recording") return "call";
  return "idle";
}

const MOOD_TITLE: Record<Mood, string> = {
  idle: "Жду встречу",
  call: "Звонок идёт",
  recording: "Идёт запись",
  offline: "Дежурный не запущен",
};

function Meter({ label, value }: { label: string; value: number }) {
  const percent = Math.min(100, Math.round(value * 140)); // 0.7 уже «громко»
  return (
    <div className="meter" title={`${label}: ${value.toFixed(2)}`}>
      <span className="meter__label">{label}</span>
      <span className="meter__track">
        <span className="meter__fill" style={{ width: `${percent}%` }} />
      </span>
    </div>
  );
}

function Signal({ label, value }: { label: string; value: boolean | null }) {
  const state = value === null ? "unknown" : value ? "yes" : "no";
  const text = value === null ? "неизвестно" : value ? "да" : "нет";
  return (
    <span className={`signal signal--${state}`}>
      {label}: {text}
    </span>
  );
}

/** Имя папки из пути: id записи для API. */
function folderId(path: string): string {
  const parts = path.split(/[\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

const JOB_STATE_TEXT: Record<string, string> = {
  queued: "в очереди",
  running: "расшифровываю",
  done: "готово",
  failed: "не вышло",
  cancelled: "отменено",
};

function App() {
  const { snapshot, progress, job, finished, log, connected, error, command, launch, endpoint } =
    useResident();
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  // Сбой самой оболочки (перетаскивание, окно настроек) — показываем там же,
  // где ошибки резидента: молча проглоченная ошибка в панели без консоли
  // означает «ничего не происходит и непонятно почему».
  const [uiError, setUiError] = useState<string | null>(null);
  const mood = moodOf(snapshot, connected);
  const recording = mood === "recording";

  const elapsed = useElapsed(recording ? (snapshot?.elapsed_s ?? 0) : null);

  // Расшифровка или только что законченная запись — панель разворачивается
  // сама: это ровно те моменты, когда человек ждёт от неё следующего шага.
  useEffect(() => {
    if (progress) setExpanded(true);
  }, [progress]);
  useEffect(() => {
    if (finished) setExpanded(true);
  }, [finished]);

  const startTranscribe = async () => {
    if (!endpoint || !finished) return;
    setBusy(true);
    setUiError(null);
    try {
      await transcribeRecording(endpoint, folderId(finished));
    } catch (cause) {
      setUiError(String(cause));
    } finally {
      setBusy(false);
    }
  };

  // Задача этой записи, а не какая-нибудь из очереди.
  const ownJob =
    job && finished && folderId(job.folder) === folderId(finished) ? job : null;
  const jobRunning = ownJob?.state === "queued" || ownJob?.state === "running";

  // Окно подгоняется под панель: иначе прозрачный остаток окна и обрезанная его
  // краями тень видны как прямоугольник вокруг панели, а невидимая часть окна
  // молча съедает клики по тому, что под ней.
  useEffect(() => {
    const panel = document.querySelector<HTMLElement>(".panel");
    if (!panel) return;
    const observer = new ResizeObserver(() => void fitWindowTo(panel));
    observer.observe(panel);
    void fitWindowTo(panel);
    return () => observer.disconnect();
  }, []);

  const levels = snapshot?.levels ?? {};
  const auto = snapshot?.auto_record;

  return (
    <div className={`panel panel--${mood} ${expanded ? "panel--open" : ""}`}>
      {/*
        Зона перетаскивания — вся шапка целиком, включая промежутки между
        кнопками: цель курсора проверяется здесь, а не разметкой.

        IMPORTANT: только явный вызов startDragging(), без
        data-tauri-drag-region. Атрибут Tauri обрабатывает сам, и вместе с
        вызовом на один mousedown приходило два запроса на перетаскивание —
        окно не двигалось вовсе.
      */}
      <div
        className="panel__grip"
        onMouseDown={(event) => {
          const onButton = (event.target as HTMLElement).closest("button");
          if (onButton || event.button !== 0 || event.detail !== 1) return;
          startDragging().catch((cause) => setUiError(String(cause)));
        }}
      >
        <div
          className="panel__head"
          onDoubleClick={() => setExpanded((value) => !value)}
          title="Потяните, чтобы переместить"
        >
          <span className={`dot dot--${mood}`} />
          <span className="panel__title">{MOOD_TITLE[mood]}</span>
          {recording && <span className="panel__timer num">{formatElapsed(elapsed)}</span>}
          {snapshot?.source === "auto" && recording && (
            <span className="badge">авто</span>
          )}
        </div>
        <button
          className={`icon-btn chevron ${expanded ? "chevron--open" : ""}`}
          onClick={() => setExpanded((value) => !value)}
          title={expanded ? "Свернуть" : "Развернуть"}
          aria-label={expanded ? "Свернуть" : "Развернуть"}
          aria-expanded={expanded}
        >
          ›
        </button>
        <button
          className="icon-btn"
          onClick={() => openSettings().catch((cause) => setUiError(String(cause)))}
          title="Настройки"
          aria-label="Настройки"
        >
          ⚙
        </button>
        <button
          className="icon-btn"
          onClick={() => hidePanel().catch((cause) => setUiError(String(cause)))}
          title="Спрятать панель (вернуть — из трея)"
          aria-label="Спрятать панель"
        >
          ✕
        </button>
      </div>

      {expanded && (
        <div className="panel__body">
          {mood === "offline" ? (
            <div className="block">
              <p className="muted">
                Панель не нашла дежурного. Он держит запись и отвечает на команды —
                без него панель только смотрит.
              </p>
              <p className="muted">
                Если иконка записи в трее есть, а панель его не видит — дежурный
                запущен старой версией, без control API: его нужно перезапустить.
                Прервать этим идущую запись нельзя — сначала остановите её.
              </p>
              {inTauri() && (
                <button className="btn btn--primary" onClick={() => void launch()}>
                  Запустить дежурного
                </button>
              )}
            </div>
          ) : (
            <>
              {recording && (
                <div className="block">
                  <div className="meters">
                    <Meter label="встреча" value={levels["sys.opus"] ?? 0} />
                    <Meter label="микрофон" value={levels["mic.opus"] ?? 0} />
                  </div>
                  <p className="path" title={snapshot?.folder ?? ""}>
                    {snapshot?.folder ?? "папка ещё не создана"}
                  </p>
                </div>
              )}

              {finished && !recording && (
                <div className="block">
                  <div className="progress__row">
                    <span>Запись сохранена</span>
                    {ownJob && (
                      <span className="muted">
                        {JOB_STATE_TEXT[ownJob.state] ?? ownJob.state}
                      </span>
                    )}
                  </div>
                  <p className="path" title={finished}>
                    {finished}
                  </p>
                  {ownJob?.state === "running" && ownJob.label && (
                    <>
                      <div className="progress__row">
                        <span>{ownJob.label}</span>
                        {ownJob.note && <span className="muted">{ownJob.note}</span>}
                      </div>
                      <span className="progress__track">
                        <span
                          className={`progress__fill ${
                            ownJob.total ? "" : "progress__fill--pulse"
                          }`}
                          style={
                            ownJob.total
                              ? {
                                  width: `${((ownJob.done ?? 0) / ownJob.total) * 100}%`,
                                }
                              : undefined
                          }
                        />
                      </span>
                    </>
                  )}
                  {ownJob?.state === "failed" && (
                    <p className="error">{ownJob.error ?? "расшифровка не удалась"}</p>
                  )}
                  <div className="actions">
                    <button
                      className="btn btn--primary"
                      onClick={() => void startTranscribe()}
                      disabled={busy || jobRunning}
                    >
                      {jobRunning ? "Расшифровываю…" : "Расшифровать"}
                    </button>
                    <button
                      className="btn"
                      onClick={() =>
                        openEditor(folderId(finished)).catch((c) => setUiError(String(c)))
                      }
                    >
                      Открыть в редакторе
                    </button>
                    <button
                      className="btn"
                      onClick={() => openFolder(finished).catch((c) => setUiError(String(c)))}
                    >
                      Папка
                    </button>
                  </div>
                </div>
              )}

              {progress && (
                <div className="block">
                  <div className="progress__row">
                    <span>{progress.label}</span>
                    {progress.note && <span className="muted">{progress.note}</span>}
                  </div>
                  <span className="progress__track">
                    <span
                      className={`progress__fill ${
                        progress.total ? "" : "progress__fill--pulse"
                      }`}
                      style={
                        progress.total
                          ? { width: `${((progress.done ?? 0) / progress.total) * 100}%` }
                          : undefined
                      }
                    />
                  </span>
                </div>
              )}

              {auto && !recording && (
                <div className="block">
                  <p className="muted">
                    Автозапись {auto.enabled ? "включена" : "выключена"}
                    {auto.processes.length > 0 && `: ${auto.processes.join(", ")}`}
                  </p>
                  <div className="signals">
                    <Signal label="микрофон" value={auto.mic} />
                    <Signal label="звук" value={auto.render} />
                  </div>
                </div>
              )}

              <div className="actions">
                {recording ? (
                  <>
                    <button
                      className="btn btn--primary"
                      onClick={() => void command("stop")}
                    >
                      Остановить
                    </button>
                    <button
                      className="btn btn--danger"
                      onClick={() => void command("cancel")}
                      title="Удалить папку записи"
                    >
                      Отменить
                    </button>
                    {snapshot?.source === "auto" && (
                      <button
                        className="btn"
                        onClick={() => void command("adopt")}
                        title="Автостоп больше не тронет эту запись"
                      >
                        Взять под свою руку
                      </button>
                    )}
                  </>
                ) : (
                  <button
                    className="btn btn--primary"
                    onClick={() => void command("start")}
                  >
                    Начать запись
                  </button>
                )}
              </div>

              {log.length > 0 && (
                <details className="journal">
                  <summary>Журнал дежурного</summary>
                  <ol>
                    {log.slice(-8).map((line, index) => (
                      <li key={`${index}-${line}`}>{line}</li>
                    ))}
                  </ol>
                </details>
              )}
            </>
          )}

          {(error || uiError) && <p className="error">{error ?? uiError}</p>}
        </div>
      )}
    </div>
  );
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
