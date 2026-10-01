/**
 * «Разделить спикера…» в панели «Спикеры»: диаризация слила двух людей в
 * одного — развести его реплики по голосу без перерасшифровки.
 *
 * 1. Способ: автоматически на K голосов или по образцам выбранных людей из
 *    базы голосов.
 * 2. Голоса реплик считает задача резидента (один раз: дальше кэш).
 * 3. Предпросмотр: группы с долей времени, фразами (▶) и похожими людьми;
 *    каждой группе — имя, «Спикер N» или оставить за прежним спикером.
 * 4. «Применить» — один шаг истории встречи (его отменяет «Отменить»).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  applySplit, cancelJob, prepareSplit, previewSplit, type Endpoint,
} from "../../../lib/api";
import { clock, errorText, plural } from "../../../lib/format";
import { isUnnamed } from "../../../lib/speakers";
import type { Job, SpeakersView, SplitGroup, SplitPreview } from "../../../lib/types";
import { Avatar } from "../../../ui/Avatar";
import { Button } from "../../../ui/Button";
import { HelpTip, TipLine } from "../../../ui/HelpTip";
import type { PersonColor } from "../Turns";
import { TargetPicker, type Target } from "./TargetPicker";

const PHRASE_S = 6;
const KS = [2, 3, 4, 5];
/** Сходство голосов групп, выше которого это, скорее всего, один человек (замер на реальной встрече: свои 0,91–0,94, чужие 0,02–0,08). */
const SAME_VOICE = 0.8;
const pct = (x: number) => `${Math.round(x * 100)}%`;

/** Куда группа: `keep` — остаётся за прежним спикером, иначе имя или null — новый «Спикер N». */
type Choice = { to: Target | "keep"; remember: boolean };

/** Имена групп по умолчанию: предложенное базой, первая без имени — прежний спикер, остальные — новые. */
export function defaultChoices(preview: SplitPreview): Record<string, Choice> {
  const out: Record<string, Choice> = {};
  const used = new Set<string>();
  let kept = false;
  for (const g of preview.groups) {
    if (g.name && !used.has(g.name)) {
      used.add(g.name);
      out[g.key] = { to: g.name, remember: false };
    } else if (!kept) {
      kept = true;
      out[g.key] = { to: "keep", remember: false };
    } else {
      out[g.key] = { to: null, remember: false };
    }
  }
  if (preview.unsure) out[preview.unsure.key] = { to: "keep", remember: false };
  return out;
}

type Phase =
  | { kind: "setup" }
  | { kind: "working"; job: Job | null }
  | { kind: "preview"; preview: SplitPreview };

export function SplitView({
  endpoint, recordingId, label, speakers, people, owner, avatarVersion, jobs, playable,
  onPlay, onDone, onBack,
}: {
  endpoint: Endpoint;
  recordingId: string;
  label: string;
  /** Спикеры встречи — варианты имени для групп. */
  speakers: string[];
  people: PersonColor[];
  owner: string;
  avatarVersion?: Record<string, number>;
  jobs: Job[];
  playable: boolean;
  onPlay: (start: number, until: number) => void;
  onDone: (view: SpeakersView) => void;
  onBack: () => void;
}) {
  const [mode, setMode] = useState<"auto" | "people">("auto");
  const [k, setK] = useState(2);
  const [chosen, setChosen] = useState<string[]>([]);
  const [phase, setPhase] = useState<Phase>({ kind: "setup" });
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [picking, setPicking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);

  const request = useCallback(() => (mode === "auto"
    ? { label, mode: "auto" as const, k }
    : { label, mode: "people" as const, people: chosen }), [label, mode, k, chosen]);

  const showPreview = useCallback(async (kk?: number) => {
    const req = mode === "auto" ? { label, mode: "auto" as const, k: kk ?? k } : request();
    const preview = await previewSplit(endpoint, recordingId, req);
    setChoices(defaultChoices(preview));
    setPicking(null);
    setPhase({ kind: "preview", preview });
  }, [endpoint, recordingId, label, mode, k, request]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const state = await prepareSplit(endpoint, recordingId, label);
      if (state.ready) await showPreview();
      else setPhase({ kind: "working", job: state.job ?? null });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  // Задача счёта голосов: прогресс из общего списка задач, готово — предпросмотр.
  const job = phase.kind === "working" && phase.job
    ? jobs.find((j) => j.id === phase.job!.id) ?? phase.job : null;
  const jobState = job?.state;
  useEffect(() => {
    if (phase.kind !== "working" || !job) return;
    if (jobState === "done") {
      void showPreview().catch((e) => { setError(errorText(e)); setPhase({ kind: "setup" }); });
    } else if (jobState === "failed" || jobState === "cancelled") {
      setError(job.error || "Голоса реплик не посчитаны");
      setPhase({ kind: "setup" });
    }
  }, [jobState]); // остальное читается в момент смены состояния задачи

  const cancel = async () => {
    if (job) await cancelJob(endpoint, job.id).catch(() => {});
    setPhase({ kind: "setup" });
  };

  const apply = async (preview: SplitPreview) => {
    setBusy(true);
    setError(null);
    try {
      const all = [...preview.groups, ...(preview.unsure ? [preview.unsure] : [])];
      const groups = all.map((g) => {
        const c = choices[g.key] ?? { to: "keep", remember: false };
        const to = c.to === "keep" ? label : c.to;
        return { idx: g.idx, to, remember: c.remember && to !== null && !isUnnamed(to) };
      });
      const view = await applySplit(endpoint, recordingId,
        { label, mode: preview.mode, fingerprint: preview.fingerprint, groups });
      onDone(view);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const setChoice = (key: string, c: Partial<Choice>) =>
    setChoices((cur) => ({ ...cur, [key]: { ...(cur[key] ?? { to: "keep", remember: false }), ...c } }));

  return (
    <section className="spk-split" aria-label={`Разделить спикера «${label}»`}>
      <div className="spk-split__head">
        <button type="button" className="spk-link" onClick={onBack}>← К списку спикеров</button>
        <h4 className="spk-split__title" ref={heading} tabIndex={-1}>Разделить «{label}» по голосу</h4>
        <p className="muted spk-split__lead">
          Если под одним спикером оказались разные люди, их реплики можно развести по голосу без повторной расшифровки.
        </p>
      </div>
      {error && <div className="card__error spk__msg" role="alert">{error}</div>}

      {phase.kind === "setup" && (
        <div className="spk-split__setup">
          <fieldset className="spk-split__modes">
            <legend className="sr-only">Способ разделения</legend>
            <label className="spk-split__mode">
              <input type="radio" name="split-mode" checked={mode === "auto"} onChange={() => setMode("auto")} />
              <span>Автоматически на</span>
              <select aria-label="Сколько голосов" value={k} disabled={mode !== "auto"}
                onChange={(e) => setK(Number(e.target.value))}>
                {KS.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              <span>{plural(k, "голос", "голоса", "голосов")}</span>
              <HelpTip label="Как работает автоматическое разделение" title="Автоматически на K голосов">
                <TipLine>Реплики спикера группируются по сходству голоса: получится столько групп, сколько вы укажете.</TipLine>
                <TipLine>Короткие реплики (меньше секунды) попадают в группу соседних реплик.</TipLine>
                <TipLine>Перед применением можно прослушать фразы каждой группы и назвать их.</TipLine>
              </HelpTip>
            </label>
            <label className="spk-split__mode">
              <input type="radio" name="split-mode" checked={mode === "people"} onChange={() => setMode("people")} />
              <span>По образцам из базы голосов</span>
              <HelpTip label="Как работает разделение по образцам" title="По образцам из базы">
                <TipLine>Выберите людей, которые говорили под этим спикером: каждая реплика достанется тому, на чей голос она больше похожа.</TipLine>
                <TipLine>Реплики, похожие на нескольких людей или ни на кого, попадут в группу «Не уверен» — их можно проверить вручную.</TipLine>
              </HelpTip>
            </label>
          </fieldset>
          {mode === "people" && (
            <PeopleChoice people={people} chosen={chosen} endpoint={endpoint} avatarVersion={avatarVersion}
              onChange={setChosen} />
          )}
          <div className="spk-split__actions">
            <Button onClick={onBack}>Отмена</Button>
            <Button variant="primary" disabled={busy || (mode === "people" && chosen.length < 2)} onClick={() => void start()}>
              Разделить по голосу
            </Button>
          </div>
          {mode === "people" && chosen.length < 2 && <div className="muted spk-split__hint">Выберите хотя бы двух людей</div>}
        </div>
      )}

      {phase.kind === "working" && (
        <div className="spk-split__work" role="status" aria-live="polite">
          <div>Считаются голоса реплик{job?.total ? `: ${job.done ?? 0} из ${job.total}` : "…"}</div>
          <div className="progress">
            <div className="progress__bar" style={{ width: job?.total ? `${Math.round(((job.done ?? 0) / job.total) * 100)}%` : "100%" }} />
          </div>
          <div className="muted spk-split__hint">
            {job?.state === "queued" ? "Задача в очереди. " : ""}
            Это делается один раз: следующие разделения этой встречи будут мгновенными.
          </div>
          <div className="spk-split__actions"><Button onClick={() => void cancel()}>Отменить</Button></div>
        </div>
      )}

      {phase.kind === "preview" && (
        <div className="spk-split__preview">
          {phase.preview.mode === "auto" && (
            <label className="spk-split__k">
              Голосов:
              <select aria-label="Сколько голосов" value={k} disabled={busy}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  setK(n);
                  setBusy(true);
                  void showPreview(n).catch((x) => setError(errorText(x))).finally(() => setBusy(false));
                }}>
                {KS.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          )}
          <div className="muted spk-split__hint">
            {phase.preview.segments} {plural(phase.preview.segments, "реплика", "реплики", "реплик")}, голос посчитан у {phase.preview.voiced}.
            Прослушайте фразы и выберите, кому отдать каждую группу.
          </div>
          {phase.preview.mode === "auto" && (phase.preview.similar ?? 0) >= SAME_VOICE && (
            <div className="spk__warn spk-split__hint" role="status">
              Голоса групп очень похожи ({pct(phase.preview.similar ?? 0)}): скорее всего, это один человек.
              Прослушайте фразы, прежде чем применять разделение.
            </div>
          )}
          {phase.preview.groups.map((g, n) => (
            <GroupCard key={g.key} group={g} title={g.person ? `${g.person}` : `Голос ${n + 1}`} label={label}
              choice={choices[g.key] ?? { to: "keep", remember: false }} picking={picking === g.key}
              speakers={speakers} people={people} owner={owner} endpoint={endpoint} avatarVersion={avatarVersion}
              playable={playable} onPlay={onPlay} onPick={() => setPicking((p) => (p === g.key ? null : g.key))}
              onChoice={(c) => { setChoice(g.key, c); setPicking(null); }} />
          ))}
          {phase.preview.unsure && (
            <GroupCard group={phase.preview.unsure} title="Не уверен" label={label} unsure
              choice={choices[phase.preview.unsure.key] ?? { to: "keep", remember: false }}
              picking={picking === phase.preview.unsure.key}
              speakers={speakers} people={people} owner={owner} endpoint={endpoint} avatarVersion={avatarVersion}
              playable={playable} onPlay={onPlay}
              onPick={() => setPicking((p) => (p === "unsure" ? null : "unsure"))}
              onChoice={(c) => { setChoice("unsure", c); setPicking(null); }} />
          )}
          <div className="muted spk-split__hint">Итоги не пересчитываются автоматически. Изменение можно отменить.</div>
          <div className="spk-split__actions">
            <Button onClick={() => setPhase({ kind: "setup" })} disabled={busy}>Назад</Button>
            <Button variant="primary" disabled={busy} onClick={() => void apply(phase.preview)}>Применить разделение</Button>
          </div>
        </div>
      )}
    </section>
  );
}

function choiceText(c: Choice, label: string): string {
  if (c.to === "keep") return `Оставить за «${label}»`;
  if (c.to === null) return "Новый спикер без имени";
  return c.to;
}

function GroupCard({
  group, title, label, unsure = false, choice, picking, speakers, people, owner, endpoint, avatarVersion,
  playable, onPlay, onPick, onChoice,
}: {
  group: SplitGroup;
  title: string;
  label: string;
  unsure?: boolean;
  choice: Choice;
  picking: boolean;
  speakers: string[];
  people: PersonColor[];
  owner: string;
  endpoint: Endpoint;
  avatarVersion?: Record<string, number>;
  playable: boolean;
  onPlay: (start: number, until: number) => void;
  onPick: () => void;
  onChoice: (c: Partial<Choice>) => void;
}) {
  const named = choice.to !== "keep" && choice.to !== null && !isUnnamed(choice.to);
  return (
    <section className={`spk-row spk-split-group${unsure ? " spk-split-group--unsure" : ""}`} aria-label={title}>
      <div className="spk-row__who">
        <div className="spk-row__name">{title}</div>
        <div className="spk-row__stats muted num">
          {clock(group.seconds)} · {pct(group.share)} времени · {group.turns} {plural(group.turns, "реплика", "реплики", "реплик")}
        </div>
        <div className="spk-row__bar" aria-hidden="true"><span style={{ width: pct(group.share) }} /></div>
      </div>
      {unsure && (
        <div className="muted spk-split__hint">
          Голос этих реплик не похож уверенно ни на одного из выбранных людей. По умолчанию они остаются за «{label}» — проверьте их вручную.
        </div>
      )}
      {group.samples.length > 0 && (
        <ul className="spk-row__phrases" aria-label="Фразы группы">
          {group.samples.map((s) => (
            <li key={s.start} className="spk-phrase">
              <button type="button" className="spk-phrase__play" disabled={!playable}
                aria-label={`Прослушать фразу с ${clock(s.start)}`}
                onClick={() => onPlay(s.start, Math.min(s.end, s.start + PHRASE_S))}>▶</button>
              <span className="spk-phrase__time num muted">{clock(s.start)}</span>
              <span className="spk-phrase__text">{s.text}</span>
            </li>
          ))}
        </ul>
      )}
      {group.suggestions.length > 0 && (
        <div className="spk-row__sugs" role="group" aria-label="Похожие голоса из базы">
          <span className="muted">Похож на:</span>
          {group.suggestions.map((s) => {
            const p = people.find((x) => x.name === s.name);
            const on = choice.to === s.name;
            return (
              <button key={s.name} type="button" className={`spk-sug${on ? " spk-sug--on" : ""}`} aria-pressed={on}
                aria-label={`Это ${s.name}, сходство ${pct(s.score)}`} onClick={() => onChoice({ to: on ? "keep" : s.name })}>
                <Avatar name={s.name} color={p?.color} hasAvatar={p?.has_avatar} version={avatarVersion?.[s.name]}
                  size={18} endpoint={endpoint} />
                <span>{s.name}</span>
                <span className="spk-sug__score num">{pct(s.score)}</span>
              </button>
            );
          })}
        </div>
      )}
      <div className="spk-row__actions">
        <span className="muted">Кому:</span>
        <button type="button" className="spk-btn" aria-expanded={picking} onClick={onPick}
          aria-label={`Кому отдать группу «${title}»: ${choiceText(choice, label)}`}>
          {choiceText(choice, label)} ▾
        </button>
      </div>
      {picking && (
        <div className="spk-split__pick">
          <button type="button" className="spk-opt" onClick={() => onChoice({ to: "keep", remember: false })}>
            <span className="spk-opt__icon" aria-hidden="true">=</span>
            <span>Оставить за «{label}»</span>
          </button>
          <TargetPicker label={`Кому отдать группу «${title}»`} speakers={speakers} current={label} people={people}
            owner={owner} endpoint={endpoint} avatarVersion={avatarVersion} onPick={(to) => onChoice({ to })} />
        </div>
      )}
      {named && (
        <label className="spk-row__remember">
          <input type="checkbox" checked={choice.remember} onChange={(e) => onChoice({ remember: e.target.checked })} />
          Запомнить голос
          <HelpTip label="Что значит «Запомнить голос»" title="Запомнить голос">
            <TipLine>Голос этой группы будет сохранён в базе голосов под выбранным именем.</TipLine>
            <TipLine>На следующих встречах человек будет узнан автоматически. Отмена изменения убирает и голос.</TipLine>
          </HelpTip>
        </label>
      )}
    </section>
  );
}

function PeopleChoice({ people, chosen, endpoint, avatarVersion, onChange }: {
  people: PersonColor[];
  chosen: string[];
  endpoint: Endpoint;
  avatarVersion?: Record<string, number>;
  onChange: (names: string[]) => void;
}) {
  const [q, setQ] = useState("");
  const fold = (s: string) => s.trim().toLowerCase().replace(/ё/g, "е");
  const shown = people.filter((p) => !q || fold(p.name).includes(fold(q)));
  if (!people.length) return <div className="muted spk-split__hint">В базе голосов пока никого нет.</div>;
  return (
    <div className="spk-split__people" role="group" aria-label="Кто говорил под этим спикером">
      <input className="spk-pick__input" type="search" placeholder="Поиск по базе голосов" aria-label="Поиск по базе голосов"
        value={q} onChange={(e) => setQ(e.target.value)} />
      <ul className="spk-split__plist">
        {shown.map((p) => (
          <li key={p.name}>
            <label className="spk-opt">
              <input type="checkbox" checked={chosen.includes(p.name)}
                onChange={(e) => onChange(e.target.checked ? [...chosen, p.name] : chosen.filter((n) => n !== p.name))} />
              <Avatar name={p.name} color={p.color} hasAvatar={p.has_avatar} version={avatarVersion?.[p.name]}
                size={18} endpoint={endpoint} />
              <span>{p.name}</span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}
