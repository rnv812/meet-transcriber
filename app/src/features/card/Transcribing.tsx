/**
 * Страница «Расшифровывается» во вкладке «Расшифровка» карточки (макет
 * MeetApp, «расшифровывается»): бейдж, карточка этапов «Запись · Распознавание ·
 * Спикеры · Анализ», строки будущего текста скелетом, «Окно можно закрыть…»,
 * отмена — как была (кнопку передаёт карточка).
 *
 * Этапы — из задачи резидента: её `stage` (convert/asr/align → распознавание,
 * diarize/voices/render → спикеры; обрезка, копирование и объединение — ещё
 * запись) и `done/total` этапа — процент и шкала идущего; без своей шкалы —
 * бегущая полоса. Общий ход — строкой под карточкой: «Этап 2 из 5 · 25 % ·
 * осталось ~8 мин» (`step/steps/fraction`, оценка — как у JobProgress).
 * Анализ встречи — отдельная задача после расшифровки: здесь только кто и
 * когда его сделает (подпись даёт карточка по настройкам).
 *
 * Строк текста по ходу распознавания резидент не присылает — их нет и здесь.
 */

import { Check } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { clock, plural } from "../../lib/format";
import { clamp01, etaText, jobElapsed, jobEta, jobFraction } from "../../lib/progress";
import { stageLabel } from "../../lib/status";
import type { Job } from "../../lib/types";
import { Icon } from "../../ui/Icon";
import { ETA_TICK_MS } from "../../ui/JobProgress";
import { ProgressBar } from "../../ui/ProgressBar";
import { Callout } from "./Callout";
import "./transcribing.css";

const TITLES = ["Запись", "Распознавание", "Спикеры", "Анализ"] as const;
/** Этап задачи → колонка карточки (неизвестный — распознавание). */
const COLUMN: Record<string, number> = {
  trim: 0, copy: 0, merge: 0,
  convert: 1, model: 1, asr: 1, align: 1,
  diarize: 2, voices: 2, render: 2,
};
const columnOf = (stage: string | null | undefined) => (stage ? COLUMN[stage] ?? 1 : 1);

/** Доля этапа 0…1 из `done/total` этапа; неизвестно — null. */
function stageFraction(job: Job | null): number | null {
  if (!job || !job.total || job.total <= 0 || typeof job.done !== "number") return null;
  return clamp01(job.done / job.total);
}

type Col = { title: string; state: "done" | "active" | "pending"; sub: string | null; bar?: ReactNode; pct?: string | null };

export function Transcribing({ job, queued = false, stage, label, durationS, tracks, analysis, actions }: {
  /** Задача расшифровки (идёт или в очереди); null — обработка без задачи (обрезка ожидания). */
  job: Job | null;
  queued?: boolean;
  /** Этап и подпись без задачи (RecStatus `running`). */
  stage?: string;
  label?: string;
  durationS: number | null | undefined;
  /** Сколько дорожек у записи. */
  tracks: number;
  /** Подпись колонки «Анализ»: кто и когда («Claude Code, после расшифровки»). */
  analysis: string;
  /** Отмена и прочие действия — как были. */
  actions?: ReactNode;
}) {
  const [now, setNow] = useState(() => Date.now());
  const running = !queued && job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setNow(Date.now()), ETA_TICK_MS);
    return () => window.clearInterval(t);
  }, [running]);

  const curStage = job?.stage ?? stage ?? null;
  // В очереди готова только запись; иначе — колонка идущего этапа.
  const active = queued ? null : columnOf(curStage);
  const stageName = job ? stageLabel(job) : label ?? "";
  const frac = queued ? null : stageFraction(job);
  const pct = frac === null ? null : `${Math.floor(frac * 100)} %`;
  const recordSub = [
    durationS ? clock(durationS) : null,
    tracks ? `${tracks} ${plural(tracks, "дорожка", "дорожки", "дорожек")}` : null,
  ].filter(Boolean).join(" · ") || null;
  const pendingSub = ["", queued ? "ждёт очереди" : "следом", "подписи появятся следом"];

  const cols: Col[] = TITLES.slice(0, 3).map((title, i) => {
    const done = active === null ? i === 0 : i < active;
    if (done) return { title, state: "done", sub: i === 0 ? recordSub : null };
    if (active === null || i > active) return { title, state: "pending", sub: pendingSub[i] ?? null };
    return {
      title, state: "active", pct,
      // Подпись этапа — когда она говорит больше названия колонки («Распознавание собеседников»).
      sub: stageName && stageName !== title ? stageName : null,
      bar: (
        <ProgressBar value={frac} size="sm" stageKey={`${job?.id ?? "trim"}:${curStage ?? ""}`}
          ariaLabel={stageName || title} className="transcribing__bar" />
      ),
    };
  });
  cols.push({ title: TITLES[3], state: "pending", sub: analysis || null });

  // Общий ход задачи: этап N из M, доля всей работы и оценка оставшегося.
  const overall = job && running && typeof job.fraction === "number" ? jobFraction(job) : null;
  const eta = job && running ? etaText(jobEta(job, jobFraction(job), jobElapsed(job, now))) : null;
  const total = [
    job?.step && job.steps && job.steps > 1 ? `Этап ${job.step} из ${job.steps}` : null,
    overall !== null ? `${Math.floor(overall * 100)} %` : null,
    eta,
  ].filter(Boolean).join(" · ");

  return (
    <section className="transcribing" aria-label="Ход расшифровки">
      <div className="transcribing__head">
        {queued ? <span className="badge">В очереди на расшифровку</span>
          : <span className="badge badge--info">Расшифровывается</span>}
      </div>
      <ol className="card transcribing__stages" aria-label="Этапы расшифровки">
        {cols.map((c) => (
          <li key={c.title} className={`transcribing__stage transcribing__stage--${c.state}`}
            aria-current={c.state === "active" ? "step" : undefined}>
            <span className="transcribing__title">
              {c.state === "done" && <Icon as={Check} size="sm" className="transcribing__check" />}
              <span>{c.title}</span>
              {c.pct && <span className="transcribing__pct num">{c.pct}</span>}
              <span className="sr-only">{c.state === "done" ? ", готово" : c.state === "active" ? ", идёт" : ", впереди"}</span>
            </span>
            {c.bar}
            {c.sub && <span className="transcribing__sub">{c.sub}</span>}
          </li>
        ))}
      </ol>
      {total && <p className="transcribing__total num">{total}</p>}
      {job?.warning && <Callout tone="warn" role="note">{job.warning}</Callout>}
      <div className="skel transcribing__skel" role="status" aria-busy="true" aria-label="Текст появится после распознавания">
        {[90, 70, 82].map((w) => <div key={w} className="sk" style={{ width: `${w}%` }} />)}
      </div>
      <p className="transcribing__hint">Окно можно закрыть: Meet доделает расшифровку в фоне и покажет уведомление.</p>
      {actions && <div className="transcribing__actions">{actions}</div>}
    </section>
  );
}
