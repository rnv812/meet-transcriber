/**
 * Панель «Спикеры встречи» сбоку карточки: кто сколько говорил, как звучит
 * (▶ у фраз), на кого из базы голосов похож — и назначение имён.
 *
 * Правки копятся в панели (`staging.ts`) и применяются одним набором — это
 * шаг истории встречи. Шаги отменяются и повторяются (Ctrl+Z / Ctrl+Shift+Z,
 * пока фокус в карточке и не в поле ввода), из истории можно вернуться к
 * любому состоянию. Отмена убирает и голоса, которые шаг запомнил в базе.
 *
 * «Это я — {владелец}» с флажком «Запомнить мой голос» запоминает ваш голос из
 * этой встречи образцом владельца (не человеком базы голосов). Флажок есть,
 * только если у встречи есть отпечаток вашего голоса с микрофона
 * (`owner_voice` обзора); отмена шага убирает и образец.
 */

import {
  useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent,
  type RefObject,
} from "react";
import {
  applySpeakers, getSpeakers, redoSpeakers, revertSpeakers, undoSpeakers, type Endpoint,
} from "../../../lib/api";
import { CircleHelp, Play, Plus, UserRound, X } from "lucide-react";
import { clock, errorText, plural } from "../../../lib/format";
import { isUnnamed } from "../../../lib/speakers";
import { speakerTones } from "../../../lib/tones";
import type { Job, SpeakerRow, SpeakersView } from "../../../lib/types";
import { Avatar } from "../../../ui/Avatar";
import { Button } from "../../../ui/Button";
import { HelpTip, TipLine } from "../../../ui/HelpTip";
import { IconButton } from "../../../ui/IconButton";
import { Tip } from "../../../ui/Tip";
import { PersonMark } from "../PersonMark";
import type { PersonColor } from "../Turns";
import { HistoryList, HistoryTools, useUndoKeys } from "./SpeakerHistory";
import { MicRemoved } from "./MicRemoved";
import { SplitView } from "./SplitView";
import { ThresholdBox } from "./ThresholdBox";
import {
  changeText, finalOf, preview, prune, rememberDefault, toOps, type Change, type Staged,
} from "./staging";
import "./speakers.css";
import { Icon } from "../../../ui/Icon";
import { BADGE_CLASS } from "../../../ui/badge";

/** Сколько секунд играет фраза по ▶. */
export const PHRASE_S = 6;
/** Сколько людей базы показывать в списке «Кто это?». */
const PICK_MAX = 8;

const NO_JOBS: Job[] = [];
const fold = (s: string) => s.trim().toLowerCase().replace(/ё/g, "е");
const pct = (x: number) => `${Math.round(x * 100)}%`;

type Pick = { label: string; mode: "assign" | "merge" };
type Option = { key: string; text: string; hint?: string; change: Change; person?: PersonColor };

export function SpeakersPanel({
  endpoint, recordingId, people, tones, avatarVersion, open, focus, version, playable, cardRef, jobs = NO_JOBS,
  onClose, onPlay, onShowTurns, onChanged, removedAsk = 0,
}: {
  /** Растёт с каждым «Показать» убранные повторы из карточки: список «Убрано с микрофона» раскрыт. */
  removedAsk?: number;
  endpoint: Endpoint;
  recordingId: string;
  /** Задачи резидента: счёт голосов для «Разделить спикера». */
  jobs?: Job[];
  people: PersonColor[];
  /** Цвета спикеров встречи (lib/tones) — те же, что в шапке и ленте; нет — по порядку строк панели. */
  tones?: ReadonlyMap<string, string>;
  avatarVersion?: Record<string, number>;
  open: boolean;
  /** Строка, к которой перейти (клик по участнику); `n` растёт с каждой просьбой. */
  focus: { label: string; n: number } | null;
  /** Меняется вместе с расшифровкой: перечитать спикеров. */
  version: unknown;
  playable: boolean;
  /** Карточка: Ctrl+Z / Ctrl+Shift+Z работают, пока фокус в ней. */
  cardRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onPlay: (start: number, until: number) => void;
  /** «Показать все реплики» этого спикера в расшифровке. */
  onShowTurns: (label: string) => void;
  /** Правки применены, отменены или повторены: перечитать запись и базу голосов. */
  onChanged: () => void;
}) {
  const [view, setView] = useState<SpeakersView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inflight = useRef(false);
  const [staged, setStaged] = useState<Staged>({});
  const [remember, setRemember] = useState<Record<string, boolean>>({});
  /** «Запомнить мой голос» — один на панель: все строки «Это я» — один голос. null — по умолчанию. */
  const [ownerChoice, setRememberOwner] = useState<boolean | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  /** «Разделить спикера…»: чья строка разделяется (панель показывает мастер вместо списка). */
  const [split, setSplit] = useState<string | null>(null);
  const rows = useRef<Record<string, HTMLElement | null>>({});
  const current = useRef({ endpoint, recordingId });
  current.current = { endpoint, recordingId };

  const show = useCallback((next: SpeakersView) => {
    setView(next);
    const labels = next.speakers.map((r) => r.label);
    setStaged((s) => prune(s, labels));
  }, []);

  const load = useCallback(async () => {
    try {
      const next = await getSpeakers(endpoint, recordingId);
      if (current.current.endpoint !== endpoint || current.current.recordingId !== recordingId) return;
      show(next);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [endpoint, recordingId, show]);

  useEffect(() => { void load(); }, [load, version]);

  const order = useMemo(() => view?.speakers.map((r) => r.label) ?? [], [view]);
  const rowTones = useMemo(() => tones ?? speakerTones(order, people), [tones, order, people]);
  const owner = view?.owner ?? "Вы";
  const history = view?.history ?? [];
  const pos = view?.pos ?? 0;
  /** Голос владельца у встречи — только кандидат: образец его не узнал. */
  const candidate = !!view?.owner_voice_candidate;
  // Кандидат не запоминается молча: флажок выключен, пока вы не скажете «это я».
  const rememberOwner = ownerChoice ?? !candidate;
  /**
   * Где предложить «Запомнить мой голос»: строка стала «Это я», и у встречи есть отпечаток вашего голоса.
   * Голос-кандидат — ещё и на самой строке владельца: подтвердить, что это вы.
   */
  const isMe = (row: SpeakerRow) => {
    if (!view?.owner_voice) return false;
    if (row.label === owner) return candidate && !staged[row.label];
    return !!staged[row.label] && finalOf(row.label, staged) === owner;
  };
  const ownerOnly = candidate && rememberOwner && !!view?.speakers.some((r) => r.label === owner && isMe(r));
  const pending = Object.keys(staged).length > 0 || ownerOnly;

  const run = useCallback(async (call: () => Promise<SpeakersView>, done?: string) => {
    if (inflight.current) return;
    inflight.current = true;
    setBusy(true);
    setError(null);
    setWarning(null);
    setNotice(null);
    try {
      const next = await call();
      show(next);
      if (next.voices_error) setWarning(next.voices_error);
      if (done) setNotice(done);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }, [show, onChanged]);

  const canUndo = pos > 0 && !busy;
  const canRedo = pos < history.length && !busy;
  const undo = useCallback(() => {
    if (pos > 0) void run(() => undoSpeakers(endpoint, recordingId), "Шаг отменён.");
  }, [pos, run, endpoint, recordingId]);
  const redo = useCallback(() => {
    if (pos < history.length) void run(() => redoSpeakers(endpoint, recordingId), "Шаг повторён.");
  }, [pos, history.length, run, endpoint, recordingId]);
  useUndoKeys(open, cardRef, undo, redo);

  // Открыли — фокус на заголовок панели (Esc сразу закрывает её); закрыли —
  // фокус туда, откуда открывали (участник, «Спикеры (N)», подпись реплики).
  const aside = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (open) {
      const was = document.activeElement;
      if (was instanceof HTMLElement && !aside.current?.contains(was)) opener.current = was;
      heading.current?.focus();
      return;
    }
    const now = document.activeElement;
    const lost = !now || now === document.body || aside.current?.contains(now);
    if (lost && opener.current?.isConnected) opener.current.focus();
    opener.current = null;
  }, [open]);

  // Клик по участнику: прокрутить к его строке и поставить на неё фокус.
  useEffect(() => {
    if (!open || !focus || !view) return;
    const el = rows.current[focus.label];
    el?.scrollIntoView?.({ block: "nearest" });
    el?.focus({ preventScroll: true });
  }, [open, focus, view]);

  const stage = (label: string, change: Change | null) => {
    setStaged((s) => {
      const next = { ...s };
      const noop = change === null
        || (change.kind === "rename" && change.to === label)
        || (change.kind === "reset" && isUnnamed(label));
      if (noop) delete next[label];
      else next[label] = change;
      return next;
    });
    setRemember((r) => {
      const next = { ...r };
      delete next[label];
      return next;
    });
    setPick(null);
    setNotice(null);
  };

  const rememberOf = (row: SpeakerRow) => remember[row.label] ?? rememberDefault(row, staged, owner);
  const canRemember = (row: SpeakerRow) => {
    // Голос владельца в базу людей не пишется: для него — «Запомнить мой голос».
    if (!row.has_voice || row.owner_voice_only || !staged[row.label]) return false;
    const final = finalOf(row.label, staged);
    // Ваш голос — не человек базы голосов: для «Это я» свой флажок (ниже).
    return final !== null && !isUnnamed(final) && final !== owner;
  };
  const apply = () => {
    if (!view || !pending) return;
    const flags: Record<string, boolean> = {};
    for (const row of view.speakers) if (staged[row.label] && canRemember(row)) flags[row.label] = rememberOf(row);
    const owned = rememberOwner && view.speakers.some(isMe);
    void run(async () => {
      const ops = toOps(order, staged);
      // Голос-кандидат запоминается только с явным подтверждением (флажок у вопроса «это точно вы?»).
      const next = owned && candidate
        ? await applySpeakers(endpoint, recordingId, ops, flags, true, true)
        : await applySpeakers(endpoint, recordingId, ops, flags, owned);
      setStaged({});
      setRemember({});
      setRememberOwner(null);
      return next;
    }, owned ? "Изменения применены, ваш голос запомнен. Итоги не пересчитываются автоматически."
      : "Изменения применены. Итоги не пересчитываются автоматически.");
  };
  const discard = () => { setStaged({}); setRemember({}); setRememberOwner(null); setPick(null); };
  const splitDone = (next: SpeakersView) => {
    setSplit(null);
    show(next);
    setError(null);
    setWarning(next.voices_error ?? null);
    setNotice("Спикер разделён. Итоги не пересчитываются автоматически.");
    onChanged();
  };
  const thresholdDone = (next: SpeakersView) => {
    show(next);
    setError(null);
    setWarning(null);
    const renamed = next.step?.ops.some((op) => op.type !== "threshold") ?? false;
    setNotice(renamed ? "Имена пересчитаны с новым порогом." : "Порог сохранён для этой встречи, имена не изменились.");
    onChanged();
  };
  const revert = (stepId: string | null) =>
    void run(() => revertSpeakers(endpoint, recordingId, stepId), "Состояние восстановлено.");

  const onPanelKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    e.preventDefault();
    if (pick) setPick(null);
    else onClose();
  };

  return (
    // Правая выдвижная панель Aurora (стекло) поверх карточки; не модальная: на
    // широкой карточке расшифровка остаётся видна и доступна рядом.
    <aside className="drawer spk" role="dialog" aria-modal="false" aria-label="Спикеры встречи" hidden={!open}
      ref={aside} onKeyDown={onPanelKey}>
      <div className="spk__head">
        <h3 className="spk__title" ref={heading} tabIndex={-1}>Спикеры встречи</h3>
        <HistoryTools canUndo={canUndo} canRedo={canRedo} onUndo={undo} onRedo={redo} />
        <IconButton icon={X} label="Закрыть панель спикеров" tooltip="Закрыть (Esc)" onClick={onClose} />
      </div>
      {error && <div className="spk__msg spk__err" role="alert">{error}</div>}
      {warning && <div className="spk__msg spk__warn" role="status">{warning}</div>}
      {notice && <div className="spk__msg muted" role="status">{notice}</div>}
      <div className="spk__body">
        {split && view && (
          <SplitView key={split} endpoint={endpoint} recordingId={recordingId} label={split}
            speakers={order} people={people} owner={owner} avatarVersion={avatarVersion} jobs={jobs}
            playable={playable} onPlay={onPlay} onDone={splitDone} onBack={() => setSplit(null)} />
        )}
        {!split && <>
        {!view && !error && <div className="muted spk__msg">Загрузка…</div>}
        {view && view.speakers.length === 0 && <div className="muted spk__msg">В расшифровке нет спикеров</div>}
        {view?.speakers.map((row) => (
          <SpeakerRowView key={row.label} row={row} rows={view.speakers} staged={staged} owner={owner}
            people={people} tone={rowTones.get(row.label)} avatarVersion={avatarVersion} endpoint={endpoint} playable={playable}
            focused={focus?.label === row.label}
            pick={pick?.label === row.label ? pick.mode : null}
            remember={canRemember(row) ? rememberOf(row) : null}
            rememberOwner={isMe(row) ? rememberOwner : null} ownerCandidate={candidate}
            onRememberOwner={setRememberOwner}
            refEl={(el) => { rows.current[row.label] = el; }}
            onPick={(mode) => setPick((p) => (p?.label === row.label && p.mode === mode ? null : { label: row.label, mode }))}
            onStage={(c) => stage(row.label, c)}
            onRemember={(v) => setRemember((r) => ({ ...r, [row.label]: v }))}
            onPlay={onPlay} onShowTurns={onShowTurns} onSplit={() => { setPick(null); setNotice(null); setSplit(row.label); }} />
        ))}
        {view?.mic_removed && view.mic_removed.length > 0 && (
          <MicRemoved items={view.mic_removed} playable={playable} onPlay={onPlay} ask={removedAsk} />
        )}
        {view && view.speakers.some((r) => r.has_voice) && (
          <ThresholdBox endpoint={endpoint} recordingId={recordingId} own={view.voice_threshold ?? null}
            fallback={view.voice_threshold_default ?? 0.75} busy={busy || pending} onApplied={thresholdDone} />
        )}
        {view && <HistoryList history={history} pos={pos} busy={busy} trimmed={!!view.trimmed} onRevert={revert} />}
        </>}
      </div>
      {pending && !split && (
        <div className="spk__foot">
          <div className="spk__preview" aria-live="polite">
            {Object.keys(staged).length ? preview(order, staged) : "Будет запомнен ваш голос из этой встречи"}
          </div>
          <div className="spk__note muted">Итоги не пересчитываются автоматически</div>
          <div className="spk__actions">
            <Button variant="ghost" size="md" onClick={discard} disabled={busy}>Сбросить</Button>
            <Button variant="primary" size="md" onClick={apply} disabled={busy}>Применить</Button>
          </div>
        </div>
      )}
    </aside>
  );
}

function SpeakerRowView({
  row, rows, staged, owner, people, tone, avatarVersion, endpoint, playable, focused, pick, remember, rememberOwner,
  ownerCandidate = false, refEl,
  onPick, onStage, onRemember, onRememberOwner, onPlay, onShowTurns, onSplit,
}: {
  row: SpeakerRow;
  rows: SpeakerRow[];
  staged: Staged;
  owner: string;
  people: PersonColor[];
  /** Цвет спикера (кольцо знака и полоса доли); нет — серый. */
  tone?: string;
  avatarVersion?: Record<string, number>;
  endpoint: Endpoint;
  playable: boolean;
  focused: boolean;
  pick: Pick["mode"] | null;
  /** null — флажка нет (нечего запоминать). */
  remember: boolean | null;
  /** «Запомнить мой голос» у строки «Это я»; null — флажка нет. */
  rememberOwner: boolean | null;
  /** Голос владельца встречи — только кандидат (образец его не узнал): спросить честно. */
  ownerCandidate?: boolean;
  refEl: (el: HTMLElement | null) => void;
  onPick: (mode: Pick["mode"]) => void;
  onStage: (c: Change | null) => void;
  onRemember: (v: boolean) => void;
  onRememberOwner: (v: boolean) => void;
  onPlay: (start: number, until: number) => void;
  onShowTurns: (label: string) => void;
  /** «Разделить…»: под этим спикером оказались разные люди. */
  onSplit: () => void;
}) {
  const person = people.find((p) => p.name === row.label);
  const change = staged[row.label];
  const titleId = useId();
  return (
    <section ref={refEl} tabIndex={-1} aria-labelledby={titleId} style={{ "--person": tone } as CSSProperties}
      className={`spk-row${focused ? " spk-row--focus" : ""}${change ? " spk-row--staged" : ""}`}>
      <div className="spk-row__head">
        <PersonMark name={row.label} tone={tone} hasAvatar={!!person?.has_avatar}
          version={avatarVersion?.[row.label]} size={28} endpoint={endpoint} />
        <div className="spk-row__who">
          <div className="spk-row__name" id={titleId}>
            <span className={isUnnamed(row.label) ? "spk-row__label--unnamed" : ""}>{row.label}</span>
            {row.room && trackBadge(row.track)}
            {change && <span className="spk-row__change">{changeText(row.label, staged).slice(row.label.length + 1)}</span>}
          </div>
          <div className="spk-row__stats muted num">
            {pct(row.share)} времени · {row.turns} {plural(row.turns, "реплика", "реплики", "реплик")}
          </div>
        </div>
        <Button size="sm" aria-expanded={pick === "assign"} onClick={() => onPick("assign")}>Назначить…</Button>
      </div>
      <div className="spk-row__bar" aria-hidden="true">
        <span style={{ width: pct(row.share) }} />
      </div>

      {row.samples.length > 0 && (
        <ul className="spk-row__phrases" aria-label="Фразы спикера">
          {row.samples.map((s) => (
            <li key={s.start} className="spk-phrase">
              <button type="button" className="spk-phrase__play" disabled={!playable}
                aria-label={`Прослушать фразу с ${clock(s.start)}`}
                onClick={() => onPlay(s.start, Math.min(s.end, s.start + PHRASE_S))}><Icon as={Play} size="sm" /></button>
              <span className="spk-phrase__time num muted">{clock(s.start)}</span>
              <span className="spk-phrase__text">{s.text}</span>
            </li>
          ))}
        </ul>
      )}
      <Button variant="ghost" size="sm" className="spk-row__turns" onClick={() => onShowTurns(row.label)}>
        Показать все реплики
      </Button>

      {row.suggestions.length > 0 && (
        <div className="spk-row__sugs" role="group" aria-label="Похожие голоса из базы">
          <span className="muted">Похож на:</span>
          {row.suggestions.map((s) => {
            const p = people.find((x) => x.name === s.name);
            const same = finalOf(row.label, staged) === s.name;
            return (
              <Button key={s.name} variant="tonal" size="sm" className="spk-sug"
                aria-pressed={same} aria-label={`Это ${s.name}, сходство ${pct(s.score)}`}
                onClick={() => onStage(same ? null : { kind: "rename", to: s.name })}>
                <Avatar name={s.name} color={p?.color} hasAvatar={p?.has_avatar} version={avatarVersion?.[s.name]}
                  size={18} endpoint={endpoint} />
                <span>{s.name}</span>
                <span className="spk-sug__score num">{pct(s.score)}</span>
              </Button>
            );
          })}
        </div>
      )}

      {(rows.length > 1 || row.turns > 1 || change) && (
        <div className="ctl-row spk-row__actions">
          {rows.length > 1 && (
            <Button variant="ghost" size="sm" aria-expanded={pick === "merge"} onClick={() => onPick("merge")}>
              Объединить с…
            </Button>
          )}
          {row.turns > 1 && !change && (
            <Tip content="Под этим спикером оказались разные люди">
              <Button variant="ghost" size="sm" onClick={onSplit}>Разделить…</Button>
            </Tip>
          )}
          {change && <Button variant="ghost" size="sm" onClick={() => onStage(null)}>Убрать правку</Button>}
        </div>
      )}

      {pick === "assign" && (
        <AssignPicker row={row} owner={owner} people={people} suggestions={row.suggestions.map((s) => s.name)}
          avatarVersion={avatarVersion} endpoint={endpoint} onStage={onStage} />
      )}
      {pick === "merge" && (
        <div className="spk-pick" role="group" aria-label={`Объединить «${row.label}» с другим спикером`}>
          <div className="spk-pick__title">
            Тот же человек, что и…
            <HelpTip label="Что значит «Объединить»" title="Объединить спикеров">
              <TipLine>Бывает, что разделение на спикеров приняло одного человека за двоих.</TipLine>
              <TipLine>Объединение переносит все реплики этой строки к выбранному спикеру: дальше это один участник.</TipLine>
              <TipLine>Изменение применяется вместе с остальными по кнопке «Применить» и отменяется кнопкой «Отменить».</TipLine>
            </HelpTip>
          </div>
          {rows.filter((r) => r.label !== row.label).map((r) => (
            <button key={r.label} type="button" className="spk-opt" onClick={() => onStage({ kind: "merge", into: r.label })}>
              <Avatar name={r.label} size={18} endpoint={endpoint} />
              <span>{r.label}</span>
              <span className="muted num">{pct(r.share)}</span>
            </button>
          ))}
        </div>
      )}

      {remember !== null && (
        <label className="spk-row__remember">
          <input type="checkbox" className="cb" checked={remember} onChange={(e) => onRemember(e.target.checked)} />
          Запомнить голос
          <HelpTip label="Что значит «Запомнить голос»" title="Запомнить голос">
            <TipLine>Голос этого спикера будет записан под новым именем и убран у прежнего.</TipLine>
            <TipLine>На следующих встречах человек будет узнан автоматически.</TipLine>
            <TipLine>Отмена изменения убирает и запомненный голос.</TipLine>
          </HelpTip>
        </label>
      )}
      {rememberOwner !== null && ownerCandidate && (
        <div className="spk-row__ask">Голос не совпал с образцом — это точно вы?</div>
      )}
      {rememberOwner !== null && (
        <label className="spk-row__remember">
          <input type="checkbox" className="cb" checked={rememberOwner}
            onChange={(e) => onRememberOwner(e.target.checked)} />
          Запомнить мой голос
          <HelpTip label="Что значит «Запомнить мой голос»" title="Запомнить мой голос">
            <TipLine>Отпечаток вашего голоса из этой встречи поможет отличать вас от людей рядом, которых слышит ваш микрофон.</TipLine>
            <TipLine>Хранится только отпечаток голоса — набор чисел, без звука.</TipLine>
            <TipLine>Отмена изменения убирает и запомненный голос.</TipLine>
          </HelpTip>
        </label>
      )}
    </section>
  );
}

/** Где звучит спикер записи звонка: у микрофона, но не владелец, — человек рядом с ним в комнате. */
function trackBadge(track: SpeakerRow["track"]) {
  if (track === "mic") {
    return (
      <Tip content="Говорил в ваш микрофон: человек рядом с вами">
        <span className={`${BADGE_CLASS.plain} spk-badge`}>в комнате</span>
      </Tip>
    );
  }
  if (track === "mixed") {
    return (
      <Tip content="Слышен и в звонке, и в вашем микрофоне">
        <span className={`${BADGE_CLASS.plain} spk-badge`}>в звонке и в комнате</span>
      </Tip>
    );
  }
  return null;
}

/** «Кто это?»: поиск по базе голосов, новый человек, «Это я» и «Неизвестный». */
function AssignPicker({ row, owner, people, suggestions, avatarVersion, endpoint, onStage }: {
  row: SpeakerRow;
  owner: string;
  people: PersonColor[];
  suggestions: string[];
  avatarVersion?: Record<string, number>;
  endpoint: Endpoint;
  onStage: (c: Change) => void;
}) {
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const q = fold(text);
  const typed = text.trim();

  const options: Option[] = [];
  const ranked = [...people].sort((a, b) => {
    const sa = suggestions.indexOf(a.name), sb = suggestions.indexOf(b.name);
    return (sa < 0 ? 99 : sa) - (sb < 0 ? 99 : sb) || a.name.localeCompare(b.name, "ru");
  });
  const matches = ranked.filter((p) => !q || fold(p.name).split(/\s+/).some((w) => w.startsWith(q)) || fold(p.name).startsWith(q));
  for (const p of matches.slice(0, PICK_MAX)) {
    options.push({ key: `p:${p.name}`, text: p.name, hint: "в базе голосов", change: { kind: "rename", to: p.name }, person: p });
  }
  if (typed && !people.some((p) => fold(p.name) === q) && fold(owner) !== q) {
    options.push({ key: "new", text: `Новый человек «${typed}»`, change: { kind: "rename", to: typed } });
  }
  if (row.label !== owner && (!q || fold(owner).startsWith(q) || "это я".startsWith(q))) {
    options.push({ key: "me", text: `Это я — ${owner}`, change: { kind: "rename", to: owner } });
  }
  if (!q || "неизвестный".startsWith(q)) {
    options.push({ key: "unknown", text: "Неизвестный", hint: "снять имя", change: { kind: "reset" } });
  }
  const at = Math.min(active, Math.max(0, options.length - 1));

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((at + step + options.length) % Math.max(1, options.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const o = options[at];
      if (o) onStage(o.change);
    }
  };

  return (
    <div className="spk-pick">
      <input className="field field--sm spk-pick__input" role="combobox" aria-label={`Кто это: ${row.label}`} autoFocus
        aria-expanded="true" aria-controls={listId} aria-autocomplete="list"
        aria-activedescendant={options[at] ? `${listId}-${at}` : undefined}
        placeholder="Имя или поиск по базе голосов" value={text}
        onChange={(e) => { setText(e.target.value); setActive(0); }} onKeyDown={onKey} />
      <ul className="spk-pick__list" role="listbox" id={listId} aria-label="Варианты">
        {options.map((o, i) => (
          // Мышью — щелчок по варианту, с клавиатуры — стрелки и Enter в поле (aria-activedescendant).
          <li key={o.key} id={`${listId}-${i}`} role="option" aria-selected={i === at}
            className={`spk-opt${i === at ? " spk-opt--active" : ""}`}
            onMouseDown={(e) => e.preventDefault()} onClick={() => onStage(o.change)}>
            {o.person ? (
              <Avatar name={o.person.name} color={o.person.color} hasAvatar={o.person.has_avatar}
                version={avatarVersion?.[o.person.name]} size={18} endpoint={endpoint} />
            ) : (
              <span className="spk-opt__icon" aria-hidden="true">
                <Icon as={o.key === "new" ? Plus : o.key === "me" ? UserRound : CircleHelp} size="sm" />
              </span>
            )}
            <span>{o.text}</span>
            {o.hint && <span className="muted spk-opt__hint">{o.hint}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
