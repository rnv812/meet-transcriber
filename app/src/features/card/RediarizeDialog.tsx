/**
 * «Переразделить на спикеров…»: заново только разделение на спикеров (и
 * узнавание голосов по базе) — без повторного распознавания речи. Текст
 * остаётся прежним, реплики раздаются новым спикерам.
 *
 * Параметры → задача резидента (минуты на час записи) → предпросмотр
 * (спикеры, доли, фразы с ▶, сколько реплик сменят спикера) → «Применить» —
 * шаг истории встречи: его отменяет «Отменить» в панели «Спикеры».
 */

import { Play, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  applyRediarized, cancelJob, discardRediarized, getRediarized, rediarize, type Endpoint,
} from "../../lib/api";
import { clock, errorText, plural } from "../../lib/format";
import type { Job, RediarizeParams, RediarizePreview } from "../../lib/types";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { JobProgress } from "../../ui/JobProgress";
import { ProgressBar } from "../../ui/ProgressBar";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Icon } from "../../ui/Icon";
import { IconButton } from "../../ui/IconButton";
import "./speakers/speakers.css";
import "./rediarize.css";

const PHRASE_S = 6;
const pct = (x: number) => `${Math.round(x * 100)}%`;
const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

/** Задача переразделения этой записи (ждёт или идёт). */
export function rediarizeJobOf(folder: string, jobs: Job[]): Job | null {
  return jobs.find((j) => j.kind === "rediarize" && norm(j.folder) === norm(folder)
    && (j.state === "queued" || j.state === "running")) ?? null;
}

type Count = "auto" | "exact" | "range";
type Phase = { kind: "form" } | { kind: "working"; jobId: string } | { kind: "preview"; preview: RediarizePreview };

export function RediarizeDialog({
  endpoint, id, folder, jobs, ready, twoTrack, playable, onPlay, onClose, onApplied,
}: {
  endpoint: Endpoint;
  id: string;
  folder: string;
  jobs: Job[];
  /** Результат уже посчитан (rediarize.json) — сразу предпросмотр. */
  ready: boolean;
  /** Запись звонка: собеседники на своей дорожке, владелец — на микрофоне. */
  twoTrack: boolean;
  playable: boolean;
  onPlay: (start: number, until: number) => void;
  onClose: () => void;
  onApplied: () => void;
}) {
  const running = rediarizeJobOf(folder, jobs);
  const [phase, setPhase] = useState<Phase>(running ? { kind: "working", jobId: running.id } : { kind: "form" });
  /** «Отказаться» / «Другие параметры» выбрасывают посчитанное (это минуты работы) — сначала вопрос. */
  const [askDiscard, setAskDiscard] = useState<"again" | "close" | null>(null);
  const [count, setCount] = useState<Count>("auto");
  const [exact, setExact] = useState(3);
  const [low, setLow] = useState(2);
  const [high, setHigh] = useState(6);
  const [sensitivity, setSensitivity] = useState(50);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.querySelector<HTMLElement>("h3")?.focus(); }, []);

  const loadPreview = useCallback(async () => {
    try {
      setPhase({ kind: "preview", preview: await getRediarized(endpoint, id) });
    } catch (e) {
      setError(errorText(e));
      setPhase({ kind: "form" });
    }
  }, [endpoint, id]);
  // Только при открытии: результат уже посчитан — сразу предпросмотр.
  useEffect(() => { if (ready && !running) void loadPreview(); }, []);

  const job = phase.kind === "working" ? jobs.find((j) => j.id === phase.jobId) ?? null : null;
  useEffect(() => {
    if (phase.kind !== "working" || !job) return;
    if (job.state === "done") void loadPreview();
    else if (job.state === "failed" || job.state === "cancelled") {
      setError(job.state === "cancelled" ? "Переразделение отменено" : job.error || "Переразделение не удалось");
      setPhase({ kind: "form" });
    }
  }, [job?.state]); // остальное читается в момент смены состояния

  const params = (): RediarizeParams => {
    const out: RediarizeParams = {};
    if (count === "exact") out.num_speakers = exact;
    if (count === "range") { out.min_speakers = low; out.max_speakers = high; }
    if (sensitivity !== 50) out.sensitivity = sensitivity / 100;
    return out;
  };
  const invalid = count === "range" && low > high;

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const { job: queued } = await rediarize(endpoint, id, params());
      setPhase({ kind: "working", jobId: queued.id });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    if (phase.kind === "working") await cancelJob(endpoint, phase.jobId).catch(() => {});
  };
  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      await applyRediarized(endpoint, id);
      onApplied();
      onClose();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const discard = async (again: boolean) => {
    setBusy(true);
    try {
      await discardRediarized(endpoint, id);
      onApplied();
      if (again) setPhase({ kind: "form" });
      else onClose();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  // Окно модальное: Esc закрывает его, где бы ни был фокус, а Tab ходит по
  // кругу внутри окна и не уходит в карточку под ним.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex='0']")];
      if (!items.length) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      const at = document.activeElement;
      const inside = at instanceof HTMLElement && box.current.contains(at);
      if (!inside || (e.shiftKey && at === first) || (!e.shiftKey && at === last)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const who = twoTrack ? "собеседников" : "участников";

  // Лист Aurora по центру на затемнении — поверх всего окна (в body): у предков
  // карточки может быть свой слой, а тосты остаются выше (--z-toast > --z-overlay).
  return createPortal(
    <div className="backdrop backdrop--modal confirm-layer" role="presentation"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="sheet redia" role="dialog" aria-modal="true" aria-labelledby="redia-title" ref={box}>
        <div className="redia__head">
          <h3 id="redia-title" tabIndex={-1}>Переразделить на спикеров</h3>
          <IconButton icon={X} label="Закрыть" onClick={onClose} />
        </div>
        <p className="redia__lead">
          Заново определяется, кто говорит, — по звуку записи. Текст расшифровки не меняется и повторно не распознаётся.
        </p>
        {error && <div className="redia__error" role="alert">{error}</div>}

        {phase.kind === "form" && (
          <div className="redia__form">
            <fieldset className="redia__count">
              <legend>
                {twoTrack ? "Сколько собеседников было на звонке (без вас)" : "Сколько человек говорило"}
              </legend>
              <label className="check-row">
                <input type="radio" className="rd" name="redia-count" checked={count === "auto"} onChange={() => setCount("auto")} />
                Определить автоматически</label>
              <label className="check-row">
                <input type="radio" className="rd" name="redia-count" checked={count === "exact"} onChange={() => setCount("exact")} />
                Точно:
                <input type="number" min={1} max={20} className="field field--sm num redia__n" aria-label={`Число ${who}`} value={exact}
                  disabled={count !== "exact"} onChange={(e) => setExact(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
              </label>
              <label className="check-row">
                <input type="radio" className="rd" name="redia-count" checked={count === "range"} onChange={() => setCount("range")} />
                От
                <input type="number" min={1} max={20} className="field field--sm num redia__n" aria-label={`Наименьшее число ${who}`} value={low}
                  disabled={count !== "range"} onChange={(e) => setLow(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
                до
                <input type="number" min={1} max={20} className="field field--sm num redia__n" aria-label={`Наибольшее число ${who}`} value={high}
                  disabled={count !== "range"} onChange={(e) => setHigh(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
              </label>
              {invalid && <span className="redia__error">Наименьшее число больше наибольшего</span>}
            </fieldset>
            <div className="redia__sens">
              <label htmlFor="redia-sens">Чувствительность разделения</label>
              <HelpTip label="Что такое чувствительность разделения" title="Чувствительность разделения">
                <TipLine>Насколько отличаться должны голоса, чтобы считаться разными людьми.</TipLine>
                <TipLine>Выше — разные люди с похожими голосами разделяются лучше, но один человек чаще оказывается двумя спикерами. Ниже — наоборот.</TipLine>
                <TipLine>Середина — как при обычной расшифровке. Если знаете, сколько было {who}, надёжнее указать их число.</TipLine>
              </HelpTip>
              <div className="redia__slider">
                <span className="muted">ниже</span>
                <input id="redia-sens" type="range" min={0} max={100} step={5} value={sensitivity}
                  aria-valuetext={sensitivity === 50 ? "как при расшифровке" : `${sensitivity}%`}
                  onChange={(e) => setSensitivity(Number(e.target.value))} />
                <span className="muted">выше</span>
              </div>
              <span className="muted redia__hint">{sensitivity === 50 ? "Как при расшифровке" : `${sensitivity}%`}</span>
            </div>
            <p className="muted redia__hint">Займёт несколько минут на час записи. Окно можно закрыть: результат сохранится до вашего решения.</p>
            <div className="redia__actions">
              <Button variant="ghost" size="md" onClick={onClose}>Отмена</Button>
              <Button variant="primary" size="md" disabled={busy || invalid} onClick={() => void start()}>Запустить</Button>
            </div>
          </div>
        )}

        {phase.kind === "working" && (
          <div className="redia__work" role="status" aria-live="polite">
            {job && job.state === "running" ? <JobProgress job={job} />
              : <ProgressBar value={null} label="В очереди" />}
            <div className="redia__actions">
              <Button variant="ghost" size="md" onClick={onClose}>Скрыть</Button>
              <Button size="md" onClick={() => void cancel()}>Отменить</Button>
            </div>
          </div>
        )}

        {phase.kind === "preview" && (
          <div className="redia__preview">
            {phase.preview.stale ? (
              <div className="redia__warn" role="status">
                Расшифровку изменили после расчёта — запустите переразделение заново.
              </div>
            ) : (
              <p className="redia__summary">
                Было спикеров: {phase.preview.before}, станет: {phase.preview.speakers.length}.
                Сменят спикера {phase.preview.changed} из {phase.preview.segments}{" "}
                {plural(phase.preview.segments, "фразы", "фраз", "фраз")}
                {phase.preview.cut ? `, разделятся по словам: ${phase.preview.cut}` : ""}.
                {phase.preview.kept?.length ? ` Сохранены имена: ${phase.preview.kept.join(", ")}.` : ""}
              </p>
            )}
            <ul className="redia__list" aria-label="Спикеры после переразделения">
              {phase.preview.speakers.map((s) => (
                <li key={s.label} className="spk-row">
                  <div className="spk-row__name">{s.label}</div>
                  <div className="spk-row__stats muted num">
                    {pct(s.share)} времени · {s.turns} {plural(s.turns, "реплика", "реплики", "реплик")}
                  </div>
                  <div className="spk-row__bar" aria-hidden="true"><span style={{ width: pct(s.share) }} /></div>
                  <ul className="spk-row__phrases" aria-label={`Фразы: ${s.label}`}>
                    {s.samples.map((p) => (
                      <li key={p.start} className="spk-phrase">
                        <button type="button" className="spk-phrase__play" disabled={!playable}
                          aria-label={`Прослушать фразу с ${clock(p.start)}`}
                          onClick={() => onPlay(p.start, Math.min(p.end, p.start + PHRASE_S))}><Icon as={Play} size="sm" /></button>
                        <span className="spk-phrase__time num muted">{clock(p.start)}</span>
                        <span className="spk-phrase__text">{p.text}</span>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
            <p className="muted redia__hint">
              Имена переходят к новым спикерам по сходству голоса; правки отдельных реплик сохраняются, если голос их спикера узнан. Изменение можно отменить в панели «Спикеры».
            </p>
            {askDiscard && (
              <ConfirmDialog inline title={askDiscard === "again" ? "Посчитать с другими параметрами?" : "Отказаться от результата?"}
                message="Посчитанное разделение будет удалено — чтобы получить его снова, расчёт придётся повторить."
                confirmLabel={askDiscard === "again" ? "Удалить и задать параметры" : "Отказаться"}
                onCancel={() => setAskDiscard(null)}
                onConfirm={() => { const again = askDiscard === "again"; setAskDiscard(null); void discard(again); }} />
            )}
            {/* Кнопки остаются на месте: после «Отмена» фокус вернётся на них. */}
            <div className="redia__actions">
              <Button variant="ghost" size="md" onClick={() => setAskDiscard("again")} disabled={busy}>Другие параметры…</Button>
              <Button size="md" onClick={() => setAskDiscard("close")} disabled={busy}>Отказаться…</Button>
              <Button variant="primary" size="md" onClick={() => void apply()}
                disabled={busy || phase.preview.stale}>Применить</Button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
