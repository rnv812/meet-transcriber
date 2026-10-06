/**
 * ✦ «Улучшить расшифровку» (M8): ИИ находит неверно распознанные термины
 * («апи» → «API») и, по желанию, явные ошибки распознавания обычных слов, а
 * человек просматривает короткий список замен и применяет выбранное одним шагом
 * истории встречи.
 *
 * Здесь: состояние (`GET /recordings/{id}/improve`), тихая строка в карточке
 * (задача, готовое предложение, подсказка после GigaAM), окно со списком групп
 * «апи → API · 12» (флажок, места с окружением и ▶; «как правильно» можно
 * вписать своё), итог над репликами с «Отменить» (пока шаг последний, как у
 * «Исправить…»).
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Pencil, Play, Sparkles, X } from "lucide-react";
import {
  ApiError, applyImprove, dismissImproveHint, getImprove, runImprove, undoSpeakers, type Endpoint,
} from "../../lib/api";
import { clock, errorText, plural } from "../../lib/format";
import type { ImproveGroup, ImproveState, Job } from "../../lib/types";
import { isModelProgress } from "../../lib/progress";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { JobProgress } from "../../ui/JobProgress";
import { improvedText } from "./speakers/staging";
import "./improve.css";
import { Icon } from "../../ui/Icon";

const norm = (p: string) => p.replace(/\\/g, "/").toLowerCase();
/** Сколько мест группы показывать раскрытыми. */
const PLACES = 5;
const placesWord = (n: number) => plural(n, "место", "места", "мест");
/** Сколько символов можно вписать в «как правильно» (как TEXT_MAX резидента). */
const TARGET_MAX = 200;

/** «Как правильно» из поля: NFC, пробелы по краям сняты, внутри — схлопнуты (как у резидента). */
export const cleanTarget = (value: string) => value.normalize("NFC").replace(/\s+/g, " ").trim();
/** Управляющие, невидимые (нулевой ширины, смена направления) и одиночные суррогаты — как у резидента. */
const HIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/gu;
/** Почему вписанное (уже через cleanTarget) не годится; null — годится. */
export function targetProblem(text: string): string | null {
  if (!text.replace(HIDDEN, "").trim()) return "Впишите, как правильно, — или Esc, чтобы оставить предложенное";
  if (text.length > TARGET_MAX) return `Слишком длинно: не больше ${TARGET_MAX} символов`;
  if (text.replace(HIDDEN, "") !== text) return "Невидимые или управляющие знаки — впишите обычным текстом";
  return null;
}
/** Почему «Применить выбранное» недоступно, пока в поле правки негодное. */
const APPLY_WAITS = "Сначала исправьте, как правильно, или отмените правку (Esc)";

export type ImproveScope = "terms" | "all";

/** Задача улучшения этой записи, которая ждёт или идёт. */
export function improveJobOf(folder: string | null | undefined, jobs: Job[]): Job | null {
  if (!folder) return null;
  return jobs.find((j) => j.kind === "improve" && norm(j.folder) === norm(folder)
    && (j.state === "queued" || j.state === "running")) ?? null;
}

/** Почему «Улучшить расшифровку» сейчас недоступно; null — доступно. */
export function improveBlocked(noModel: boolean): string | null {
  return noModel ? "Подключите Claude Code, Codex или OpenCode в настройках" : null;
}

/** Выбранные группы: термины с флажком и, в режиме «Термины и явные ошибки», — отмеченные исправления. */
export function chosenGroups(groups: ImproveGroup[], scope: ImproveScope, off: ReadonlySet<string>,
  fixesOn: ReadonlySet<string>): ImproveGroup[] {
  return groups.filter((g) => (g.kind === "term" ? !off.has(g.id) : scope === "all" && fixesOn.has(g.id)));
}

/** Ключ отмеченного места вне названных моделью фраз: группа и номер в `more`. */
const extraKey = (group: string, k: number) => `${group}:${k}`;

/** Отмеченные места вне названных фраз → {группа: [номера]} для запроса. */
export function chosenExtra(groups: ImproveGroup[], on: ReadonlySet<string>): Record<string, number[]> {
  const out: Record<string, number[]> = {};
  for (const g of groups) {
    const idx = (g.more ?? []).map((_, k) => k).filter((k) => on.has(extraKey(g.id, k)));
    if (idx.length) out[g.id] = idx;
  }
  return out;
}

type Notice = { text: string; step?: string; undone?: boolean };

/**
 * Улучшение расшифровки в карточке: состояние, запуск, окно и итог. Состояние
 * перечитывается при смене задачи в очереди и при перечитывании записи
 * (`version`): правка текста делает предложение устаревшим — резидент его
 * выбрасывает.
 */
export function useImprove({ endpoint, id, folder, jobs, version, head, noModel, playable, onPlay, onChanged }: {
  endpoint: Endpoint;
  id: string;
  folder: string | null;
  jobs: Job[];
  version: unknown;
  /** Последний применённый шаг истории встречи (`edit_head`). */
  head?: string | null;
  noModel: boolean;
  playable: boolean;
  onPlay: (start: number, until: number) => void;
  /** Применено или отменено: перечитать запись. */
  onChanged: () => void;
}) {
  const [state, setState] = useState<ImproveState | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const job = improveJobOf(folder, jobs);
  const jobSig = folder
    ? jobs.filter((j) => j.kind === "improve" && norm(j.folder) === norm(folder)).map((j) => `${j.id}:${j.state}`).join(",")
    : "";
  const current = useRef({ endpoint, id });
  current.current = { endpoint, id };

  const reload = useCallback(async () => {
    try {
      const got = await getImprove(endpoint, id);
      if (current.current.endpoint === endpoint && current.current.id === id) setState(got);
    } catch (e) {
      // Старый резидент без улучшения (404) — просто «нет».
      if (e instanceof ApiError && e.status === 404) setState({ state: "none" });
    }
  }, [endpoint, id]);

  useEffect(() => { setState(null); setOpen(false); setNotice(null); setError(null); }, [endpoint, id]);
  useEffect(() => { void reload(); }, [reload, jobSig, version]);

  const shown: ImproveState | null = job
    ? { state: job.state === "running" ? "running" : "queued", job, hint: false }
    : state;

  const start = useCallback(async () => {
    setError(null);
    setOpen(true);
    const now = job ? "busy" : state?.state;
    if (now === "busy" || now === "ready") return;
    setBusy(true);
    try {
      await runImprove(endpoint, id);
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [endpoint, id, job, state, reload]);

  const rerun = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      await runImprove(endpoint, id);
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [endpoint, id, reload]);

  const dismiss = useCallback(async () => {
    setState((s) => (s ? { ...s, hint: false } : s));
    try { await dismissImproveHint(endpoint, id); } catch { /* подсказка — не повод для ошибки */ }
  }, [endpoint, id]);

  const created = state?.proposal?.created_at;
  const apply = useCallback(async (ids: string[], extra: Record<string, number[]>, addRules: boolean,
    addTerms: boolean, targets?: Record<string, string>) => {
    setBusy(true);
    setError(null);
    try {
      const res = await applyImprove(endpoint, id, {
        groups: ids, extra, created_at: created, add_rules: addRules, add_terms: addTerms,
        ...(targets && Object.keys(targets).length ? { targets } : {}),
      });
      const terms = res.groups.filter((g) => g.kind === "term").length;
      const parts = [`${improvedText(res.changed, terms)}. Итоги не пересчитываются автоматически.`];
      if (res.rules?.error) parts.push(res.rules.error);
      else if (res.rules?.added.length) {
        parts.push(`Правил для будущих встреч: ${res.rules.added.length}.`);
      }
      if (res.terms?.error) parts.push(res.terms.error);
      else if (res.terms?.added.length) parts.push(`Добавлено в термины: ${res.terms.added.join(", ")}.`);
      setNotice({ text: parts.join(" "), step: res.step?.id });
      setOpen(false);
      onChanged();
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [endpoint, id, created, onChanged, reload]);

  const undo = useCallback(async () => {
    if (!notice?.step) return;
    setBusy(true);
    try {
      await undoSpeakers(endpoint, id, notice.step);
      setNotice({ text: "Улучшение отменено" });
      onChanged();
    } catch (e) {
      setNotice({ text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }, [endpoint, id, notice, onChanged]);

  const blocked = improveBlocked(noModel);

  const status = (
    <ImproveStatus state={shown} busy={busy} onOpen={() => void start()} onRetry={blocked ? undefined : () => void start()}
      onDismiss={() => void dismiss()} />
  );
  const dialog = open ? (
    <ImproveDialog state={shown} busy={busy} error={error} playable={playable} onPlay={onPlay}
      onApply={(ids, extra, rules, terms, targets) => void apply(ids, extra, rules, terms, targets)} onRerun={blocked ? undefined : () => void rerun()}
      onClose={() => { setOpen(false); setError(null); }} />
  ) : null;
  const bar = notice ? (
    <div className="tsel" role="status" aria-live="polite">
      <span>{notice.text}</span>
      {notice.step && notice.step === head && (
        <button type="button" className="spk-link" disabled={busy} onClick={() => void undo()}>Отменить</button>
      )}
      <button type="button" className="spk-link tsel__close" aria-label="Скрыть" onClick={() => setNotice(null)}><Icon as={X} size="sm" /></button>
    </div>
  ) : null;
  return { state: shown, start, blocked, status, dialog, bar };
}

/** Тихая строка под действиями карточки; нечего сказать — ничего. */
export function ImproveStatus({ state, busy, onOpen, onRetry, onDismiss }: {
  state: ImproveState | null;
  busy: boolean;
  onOpen: () => void;
  /** Повторить; нет — без кнопки (модель не подключена). */
  onRetry?: () => void;
  onDismiss: () => void;
}) {
  if (!state) return null;
  switch (state.state) {
    case "queued":
    case "running":
      if (state.state === "running" && state.job && isModelProgress(state.job)) {
        return <div className="analysis-status" role="status"><JobProgress job={state.job} size="sm" /></div>;
      }
      return (
        <div className="analysis-status" role="status">
          <span className="analysis-chip" title={state.state === "queued" ? "Улучшение расшифровки ждёт в очереди" : "ИИ проверяет расшифровку"}>
            <span className="analysis-chip__pulse" aria-hidden="true" />
            {state.state === "queued" ? "Улучшение в очереди…" : "Улучшение расшифровки…"}
          </span>
        </div>
      );
    case "ready": {
      const groups = state.proposal?.groups ?? [];
      const terms = groups.filter((g) => g.kind === "term").length;
      const fixes = groups.length - terms;
      if (!groups.length) return null;
      return (
        <div className="analysis-status" role="status">
          <span className="muted">
            {terms
              ? `ИИ предлагает исправить ${terms} ${plural(terms, "термин", "термина", "терминов")}`
              : `ИИ предлагает ${fixes} ${plural(fixes, "исправление", "исправления", "исправлений")} распознавания`}
          </span>
          <button type="button" className="link-btn" onClick={onOpen} disabled={busy}>Просмотреть</button>
        </div>
      );
    }
    case "failed":
      return (
        <div className="analysis-status" role="status">
          <span className="muted" title={state.error || undefined}>Улучшение расшифровки не удалось</span>
          {state.proposal && state.proposal.groups.length > 0 && (
            <button type="button" className="link-btn" onClick={onOpen} disabled={busy}>Прежний список</button>
          )}
          {onRetry && <button type="button" className="link-btn" onClick={onRetry} disabled={busy}>Повторить</button>}
        </div>
      );
    default:
      if (!state.hint || !onRetry) return null;
      return (
        <div className="analysis-status improve-hint" role="status">
          <Sparkles size={13} strokeWidth={1.75} aria-hidden="true" className="improve-hint__icon" />
          <span className="muted">Похоже, в тексте есть термины латиницей — улучшить?</span>
          <button type="button" className="link-btn" onClick={onOpen} disabled={busy}>Улучшить</button>
          <button type="button" className="link-btn muted" onClick={onDismiss}>Не нужно</button>
        </div>
      );
  }
}

/** Места группы: время, окружение с выделенным словом, ▶. */
function Places({ group, playable, onPlay }: {
  group: ImproveGroup;
  playable: boolean;
  onPlay: (start: number, until: number) => void;
}) {
  return (
    <ul className="improve__places" aria-label={`Места: ${group.find}`}>
      {group.samples.slice(0, PLACES).map((s) => (
        <li key={`${s.segment}:${s.offset}`}>
          {playable ? (
            <button type="button" className="spk-link improve__play" title="Прослушать это место"
              aria-label={`Прослушать с ${clock(s.start)}`}
              onClick={() => onPlay(Math.max(0, s.start - 0.3), s.end + 0.5)}><Icon as={Play} size="sm" />{clock(s.start)}</button>
          ) : <span className="muted num">{clock(s.start)}</span>}
          <span className="improve__ctx">
            {s.before}<mark className="hit">{s.match}</mark>{s.after}
          </span>
        </li>
      ))}
      {group.count > Math.min(PLACES, group.samples.length) && (
        <li className="muted">и ещё {group.count - Math.min(PLACES, group.samples.length)}</li>
      )}
    </ul>
  );
}

/**
 * «Как правильно» группы: жирным, щелчок по нему или ✎ — правка на месте.
 * Enter или уход из поля принимают, Esc отменяет (окно при этом не
 * закрывается). Пустое не принимается: ошибка, поле остаётся, «Применить»
 * ждёт. Вписанное, равное предложенному, — не правка; равное исходному —
 * группа не заменяется.
 */
function Target({ group, value, onChange, onInvalid }: {
  group: ImproveGroup;
  /** Вписанное человеком; нет — предложенное ИИ. */
  value?: string;
  onChange: (value: string | undefined) => void;
  /** Поле сейчас с негодным текстом — применять нельзя. */
  onInvalid: (bad: boolean) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  /** Поле открыто: blur после Enter/Esc (поле уже убрано) второй раз не принимает. */
  const open = useRef(false);
  /** После Enter/Esc фокус — обратно на замену (а не в никуда: поле убрано). */
  const refocus = useRef(false);
  const button = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const invalid = useRef(onInvalid);
  invalid.current = onInvalid;
  // Строку убрали (сменили режим), пока в поле было негодное, — «Применить» не ждёт её.
  useEffect(() => () => invalid.current(false), []);
  useEffect(() => {
    if (!editing && refocus.current) { refocus.current = false; button.current?.focus(); }
  }, [editing]);
  const shown = value ?? group.replace;
  const edited = value !== undefined;
  const same = edited && value === cleanTarget(group.find);
  const errorId = `improve-target-${group.id}`;

  const begin = () => {
    open.current = true;
    setDraft(shown);
    setProblem(null);
    setEditing(true);
  };
  const close = () => {
    open.current = false;
    setEditing(false);
    setProblem(null);
    onInvalid(false);
  };
  /** Принять вписанное; негодное — ошибка, поле остаётся (→ false). */
  const commit = () => {
    if (!open.current) return true;
    const text = cleanTarget(draft);
    const bad = targetProblem(text);
    if (bad) {
      refocus.current = false; // поле остаётся — фокус никуда не переносится
      setProblem(bad);
      onInvalid(true);
      return false;
    }
    close();
    onChange(text === group.replace ? undefined : text);
    return true;
  };

  if (editing) {
    return (
      <span className="improve__to improve__to--edit">
        <input ref={input} className="improve__input" aria-label={`Как правильно: ${group.find}`} autoFocus value={draft}
          aria-invalid={problem ? true : undefined} aria-describedby={problem ? errorId : undefined}
          onFocus={(e) => e.currentTarget.select()}
          onChange={(e) => setDraft(e.target.value)}
          // Ушли из поля с негодным — фокус обратно в поле: ошибка рядом, «Применить» ждёт.
          onBlur={() => { if (!commit()) setTimeout(() => input.current?.focus(), 0); }}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); refocus.current = true; commit(); }
            // Esc — только отмена правки: окно по нему не закрывается.
            if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); refocus.current = true; close(); }
          }} />
        {problem && <span id={errorId} className="improve__err" role="alert">{problem}</span>}
      </span>
    );
  }
  return (
    <span className="improve__to">
      <button ref={button} type="button" className="improve__target" title="Изменить: вписать, как правильно" onClick={begin}>
        <strong>{shown}</strong>
      </button>
      <button type="button" className="improve__pen" aria-label={`Изменить замену: ${group.find}`}
        title="Изменить: вписать, как правильно" onClick={begin}><Icon as={Pencil} size="sm" /></button>
      {edited && (
        <span className="improve__edited" title={`Вписано вручную; ИИ предлагал «${group.replace}»`}>изменено</span>
      )}
      {edited && (
        <button type="button" className="spk-link improve__revert" onClick={() => onChange(undefined)}>вернуть предложенное</button>
      )}
      {same && <span className="muted improve__same">· как в тексте — не заменяется</span>}
    </span>
  );
}

/** Окно «Улучшить расшифровку»: ход задачи или короткий список замен. */
export function ImproveDialog({ state, busy, error, playable, onPlay, onApply, onRerun, onClose }: {
  state: ImproveState | null;
  busy: boolean;
  error: string | null;
  playable: boolean;
  onPlay: (start: number, until: number) => void;
  /** `targets` — {группа: вписанное человеком «как правильно»}; без правок — не передаётся. */
  onApply: (ids: string[], extra: Record<string, number[]>, addRules: boolean, addTerms: boolean,
    targets?: Record<string, string>) => void;
  /** «Проверить заново»; нет — модель не подключена. */
  onRerun?: () => void;
  onClose: () => void;
}) {
  const [scope, setScope] = useState<ImproveScope>("terms");
  /** Снятые флажки групп терминов (по умолчанию все отмечены). */
  const [off, setOff] = useState<ReadonlySet<string>>(new Set());
  /** Отмеченные исправления обычных слов — по умолчанию ни одного. */
  const [fixesOn, setFixesOn] = useState<ReadonlySet<string>>(new Set());
  /** Отмеченные места вне названных моделью фраз — по умолчанию ни одного. */
  const [extraOn, setExtraOn] = useState<ReadonlySet<string>>(new Set());
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [rules, setRules] = useState(false);
  const [terms, setTerms] = useState(false);
  /** Вписанное человеком «как правильно» по группам (только отличное от предложенного). */
  const [targets, setTargets] = useState<Readonly<Record<string, string>>>({});
  /** Группы, в поле которых сейчас негодный текст (пусто): применять нельзя. */
  const [invalid, setInvalid] = useState<ReadonlySet<string>>(new Set());
  const title = useRef<HTMLHeadingElement>(null);
  // Повтор не удался — прежний свежий список всё ещё можно применить.
  const groups = state?.state === "ready" || state?.state === "failed" ? state.proposal?.groups ?? [] : [];
  const created = state?.proposal?.created_at;

  // Новое предложение — заново выбор по умолчанию.
  useEffect(() => {
    setOff(new Set()); setFixesOn(new Set()); setExtraOn(new Set()); setExpanded(new Set());
    setTargets({}); setInvalid(new Set());
  }, [created]);
  useEffect(() => { title.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const termGroups = groups.filter((g) => g.kind === "term");
  const fixGroups = groups.filter((g) => g.kind === "fix");
  /** Вписали исходное — замена ничего не меняет: группа как снятая. */
  const keeps = (g: ImproveGroup) => targets[g.id] === cleanTarget(g.find);
  const chosen = chosenGroups(groups, scope, off, fixesOn).filter((g) => !keeps(g));
  const extra = chosenExtra(termGroups.filter((g) => !keeps(g)), extraOn);
  const extraCount = Object.values(extra).reduce((n, x) => n + x.length, 0);
  const total = chosen.reduce((n, g) => n + g.count, 0) + extraCount;
  const fixChosen = fixGroups.filter((g) => fixesOn.has(g.id));
  const fixCount = fixChosen.filter((g) => !keeps(g)).reduce((n, g) => n + g.count, 0);
  /** Вписанное — только у применяемых групп. */
  const sent = Object.fromEntries(Object.entries(targets).filter(([gid]) => chosen.some((g) => g.id === gid) || gid in extra));
  /** Сколько мест группы будет заменено: отмеченные названные и отмеченные остальные. */
  const willReplace = (g: ImproveGroup) => (keeps(g) ? 0 : (off.has(g.id) ? 0 : g.count) + (extra[g.id]?.length ?? 0));
  const targetOf = (g: ImproveGroup) => targets[g.id] ?? g.replace;
  const target = (g: ImproveGroup) => (
    <Target group={g} value={targets[g.id]}
      onChange={(v) => setTargets((t) => {
        const { [g.id]: _, ...rest } = t;
        return v === undefined ? rest : { ...rest, [g.id]: v };
      })}
      onInvalid={(bad) => setInvalid((x) => (x.has(g.id) === bad ? x : toggle(x, g.id)))} />
  );
  const toggle = (set: ReadonlySet<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  };
  const expander = (key: string, label: string) => (
    <button type="button" className="improve__exp" aria-expanded={expanded.has(key)}
      aria-label={`${expanded.has(key) ? "Скрыть" : "Показать"} места: ${label}`}
      onClick={() => setExpanded((x) => toggle(x, key))}>
      {expanded.has(key) ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
    </button>
  );

  const failure = state?.state === "failed" ? (
    <div className="card__error" role="alert" title={state.error || undefined}>
      Улучшение расшифровки не удалось{state.error ? `: ${state.error}` : ""}
      {groups.length > 0 && <span className="muted"> · ниже — прежний список</span>}
    </div>
  ) : null;
  let content: ReactNode;
  if (!state || state.state === "queued" || state.state === "running" || (state.state === "none" && busy)) {
    content = (
      <div className="improve__work" role="status">
        {state?.state === "running" && state.job && isModelProgress(state.job) ? <JobProgress job={state.job} size="sm" /> : (
          <span className="analysis-chip">
            <span className="analysis-chip__pulse" aria-hidden="true" />
            {state?.state === "queued" ? "Улучшение в очереди…" : "ИИ проверяет расшифровку…"}
          </span>
        )}
        <p className="muted improve__hint">Окно можно закрыть: когда список будет готов, ссылка на него появится в карточке.</p>
      </div>
    );
  } else if (state.state === "none") {
    // Ничего не идёт: запуск не удался или расшифровку изменили и список устарел.
    content = <p className="improve__empty">Списка замен нет — расшифровку изменили или проверку ещё не запускали.</p>;
  } else if (state.state === "failed" && !groups.length) {
    content = failure;
  } else if (!groups.length) {
    content = <p className="improve__empty">ИИ не нашёл неверно распознанных терминов.</p>;
  } else {
    content = (
      <>
        <fieldset className="improve__scope">
          <legend className="sr-only">Что исправлять</legend>
          <label><input type="radio" name="improve-scope" checked={scope === "terms"} onChange={() => setScope("terms")} />
            Только термины</label>
          <label><input type="radio" name="improve-scope" checked={scope === "all"} onChange={() => setScope("all")} />
            Термины и явные ошибки распознавания</label>
        </fieldset>
        {!termGroups.length && scope === "terms" && (
          <p className="muted improve__hint">Неверно распознанных терминов нет. Явные ошибки распознавания —
            в режиме «Термины и явные ошибки распознавания».</p>
        )}
        <ul className="improve__groups" aria-label="Предложенные замены">
          {termGroups.map((g) => {
            const more = g.more ?? [];
            const n = willReplace(g);
            const unmarked = more.length - (extra[g.id]?.length ?? 0);
            return (
              <li key={g.id} className="improve__group">
                <div className="improve__row">
                  {expander(g.id, g.find)}
                  {/* Флажок с «как распознано»; «как правильно» — рядом, своими кнопками (не внутри label). */}
                  <label className="improve__check improve__check--pair">
                    <input type="checkbox" aria-label={`${g.find} → ${targetOf(g)}`} checked={!off.has(g.id)}
                      onChange={() => setOff((x) => toggle(x, g.id))} />
                    <span className="improve__from">{g.find}</span>{" → "}
                  </label>
                  {target(g)}
                  <span className="muted num" title={`Будет заменено: ${n} ${placesWord(n)}`}>· {n}</span>
                  {unmarked > 0 && (
                    // Другие места термина видны и в свёрнутой строке: их стоит проверить.
                    <span className="muted improve__unmarked" title="ИИ их не отмечал — раскройте, чтобы проверить">
                      · ещё {unmarked} {placesWord(unmarked)}
                    </span>
                  )}
                </div>
                {expanded.has(g.id) && (
                  <>
                    <Places group={g} playable={playable} onPlay={onPlay} />
                    {more.length > 0 && (
                      <div className="improve__more">
                        <div className="muted improve__more-head">
                          Ещё {more.length} {placesWord(more.length)} — проверьте: ИИ их не отмечал
                        </div>
                        <ul className="improve__places" aria-label={`Ещё места: ${g.find}`}>
                          {more.map((x, k) => (
                            <li key={`${x.segment}:${x.offset}`}>
                              <input type="checkbox" aria-label={`Заменить и здесь: ${clock(x.start)}`}
                                checked={extraOn.has(extraKey(g.id, k))}
                                onChange={() => setExtraOn((on) => toggle(on, extraKey(g.id, k)))} />
                              {playable ? (
                                <button type="button" className="spk-link improve__play" title="Прослушать это место"
                                  aria-label={`Прослушать с ${clock(x.start)}`}
                                  onClick={() => onPlay(Math.max(0, x.start - 0.3), x.end + 0.5)}><Icon as={Play} size="sm" />{clock(x.start)}</button>
                              ) : <span className="muted num">{clock(x.start)}</span>}
                              <span className="improve__ctx">{x.before}<mark className="hit">{x.match}</mark>{x.after}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </>
                )}
              </li>
            );
          })}
          {scope === "all" && fixGroups.length > 0 && (
            <li className="improve__group">
              <div className="improve__row">
                {expander("fix", "Прочие исправления")}
                <label className="improve__check">
                  <input type="checkbox" checked={fixChosen.length === fixGroups.length}
                    ref={(el) => { if (el) el.indeterminate = fixChosen.length > 0 && fixChosen.length < fixGroups.length; }}
                    onChange={(e) => setFixesOn(e.target.checked ? new Set(fixGroups.map((g) => g.id)) : new Set())} />
                  <span className="improve__pair">Прочие исправления</span>
                  <span className="muted num">· {fixCount}</span>
                </label>
              </div>
              {expanded.has("fix") && (
                <ul className="improve__fixes">
                  {fixGroups.map((g) => (
                    <li key={g.id}>
                      <div className="improve__row">
                        <label className="improve__check improve__check--pair">
                          <input type="checkbox" aria-label={`${g.find} → ${targetOf(g)}`} checked={fixesOn.has(g.id)}
                            onChange={() => setFixesOn((x) => toggle(x, g.id))} />
                          <span className="improve__from">{g.find}</span>{" → "}
                        </label>
                        {target(g)}
                        <span className="muted num">· {keeps(g) ? 0 : g.count}</span>
                      </div>
                      <Places group={g} playable={playable} onPlay={onPlay} />
                    </li>
                  ))}
                </ul>
              )}
            </li>
          )}
        </ul>
        <div className="improve__opts">
          <label className="tfix__check">
            <input type="checkbox" checked={rules} onChange={(e) => setRules(e.target.checked)} />
            <span>Запомнить как правила для будущих встреч</span>
            <HelpTip label="Как работают правила для будущих встреч" title="Правила для будущих встреч">
              <TipLine>Каждая новая расшифровка сразу после распознавания заменит выбранные термины так же: целые
                слова, без учёта регистра, «ё» и «е» не различаются. Исправления обычных слов правилами не
                становятся: в другой встрече те же слова могут быть верными.</TipLine>
              <TipLine>Список правил — «Настройки → Распознавание», там их можно удалить.</TipLine>
            </HelpTip>
          </label>
          <label className="tfix__check">
            <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} />
            <span>Добавить в термины</span>
            <HelpTip label="Что такое термины распознавания" title="Термины распознавания">
              <TipLine>Правильные написания выбранных терминов (не исправлений обычных слов) попадут в список терминов распознавания («Настройки → Распознавание»):
                их реже путают в новых расшифровках, и ИИ при улучшении берёт написание оттуда.</TipLine>
            </HelpTip>
          </label>
        </div>
      </>
    );
  }

  const ready = (state?.state === "ready" || state?.state === "failed") && groups.length > 0;
  return (
    <div className="modal" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal__box improve" role="dialog" aria-modal="true" aria-labelledby="improve-title">
        <div className="redia__head">
          <h3 id="improve-title" tabIndex={-1} ref={title}>
            <Sparkles size={15} strokeWidth={1.75} aria-hidden="true" className="improve__star" />Улучшить расшифровку
          </h3>
          <button type="button" className="spk__close" aria-label="Закрыть" onClick={onClose}><Icon as={X} size="sm" /></button>
        </div>
        <p className="muted redia__lead">
          ИИ ищет неверно распознанные термины и предлагает замены. Меняются только эти слова: фразы не
          переписываются, спикеры не меняются. Применённое можно отменить в истории изменений.
        </p>
        {error && <div className="card__error" role="alert">{error}</div>}
        {state?.state === "failed" && groups.length > 0 && failure}
        {content}
        <div className="improve__foot">
          {(state?.state === "ready" || state?.state === "failed" || (state?.state === "none" && !busy)) && onRerun ? (
            <button type="button" className="link-btn" onClick={onRerun} disabled={busy}>Проверить заново</button>
          ) : <span />}
          <span className="improve__btns">
            {ready && invalid.size > 0 && <span id="improve-apply-why" className="improve__why">{APPLY_WAITS}</span>}
            {ready && total > 0 && invalid.size === 0 && (
              <span className="muted improve__total">Будет заменено: {total} {placesWord(total)}</span>
            )}
            <Button onClick={onClose}>{ready ? "Отмена" : "Закрыть"}</Button>
            {ready && (
              <Button variant="primary" disabled={busy || !total || invalid.size > 0}
                aria-describedby={invalid.size > 0 ? "improve-apply-why" : undefined}
                onClick={() => (Object.keys(sent).length
                  ? onApply(chosen.map((g) => g.id), extra, rules, terms, sent)
                  : onApply(chosen.map((g) => g.id), extra, rules, terms))}>Применить выбранное</Button>
            )}
          </span>
        </div>
      </div>
    </div>
  );
}
