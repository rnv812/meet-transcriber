/**
 * ✦ «Улучшить расшифровку» (M8): ИИ находит неверно распознанные термины
 * («апи» → «API») и, по желанию, явные ошибки распознавания обычных слов, а
 * человек просматривает короткий список замен и применяет выбранное одним шагом
 * истории встречи.
 *
 * Здесь: состояние (`GET /recordings/{id}/improve`), тихая строка в карточке
 * (задача, готовое предложение, подсказка после GigaAM), окно со списком групп
 * «апи → API · 12» (флажок, места с окружением и ▶), итог над репликами с
 * «Отменить» (пока шаг последний, как у «Исправить…»).
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Sparkles } from "lucide-react";
import {
  ApiError, applyImprove, dismissImproveHint, getImprove, runImprove, undoSpeakers, type Endpoint,
} from "../../lib/api";
import { clock, errorText, plural } from "../../lib/format";
import type { ImproveGroup, ImproveState, Job } from "../../lib/types";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { improvedText } from "./speakers/staging";
import "./improve.css";

const norm = (p: string) => p.replace(/\\/g, "/").toLowerCase();
/** Сколько мест группы показывать раскрытыми. */
const PLACES = 5;
const placesWord = (n: number) => plural(n, "место", "места", "мест");

export type ImproveScope = "terms" | "all";

/** Задача улучшения этой записи, которая ждёт или идёт. */
export function improveJobOf(folder: string | null | undefined, jobs: Job[]): Job | null {
  if (!folder) return null;
  return jobs.find((j) => j.kind === "improve" && norm(j.folder) === norm(folder)
    && (j.state === "queued" || j.state === "running")) ?? null;
}

/** Почему «Улучшить расшифровку» сейчас недоступно; null — доступно. */
export function improveBlocked(noModel: boolean): string | null {
  return noModel ? "Подключите Claude Code или Codex в настройках" : null;
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
    addTerms: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const res = await applyImprove(endpoint, id, {
        groups: ids, extra, created_at: created, add_rules: addRules, add_terms: addTerms,
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
      onApply={(ids, extra, rules, terms) => void apply(ids, extra, rules, terms)} onRerun={blocked ? undefined : () => void rerun()}
      onClose={() => { setOpen(false); setError(null); }} />
  ) : null;
  const bar = notice ? (
    <div className="tsel" role="status" aria-live="polite">
      <span>{notice.text}</span>
      {notice.step && notice.step === head && (
        <button type="button" className="spk-link" disabled={busy} onClick={() => void undo()}>Отменить</button>
      )}
      <button type="button" className="spk-link tsel__close" aria-label="Скрыть" onClick={() => setNotice(null)}>×</button>
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
              onClick={() => onPlay(Math.max(0, s.start - 0.3), s.end + 0.5)}>▶ {clock(s.start)}</button>
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

/** Окно «Улучшить расшифровку»: ход задачи или короткий список замен. */
export function ImproveDialog({ state, busy, error, playable, onPlay, onApply, onRerun, onClose }: {
  state: ImproveState | null;
  busy: boolean;
  error: string | null;
  playable: boolean;
  onPlay: (start: number, until: number) => void;
  onApply: (ids: string[], extra: Record<string, number[]>, addRules: boolean, addTerms: boolean) => void;
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
  const title = useRef<HTMLHeadingElement>(null);
  // Повтор не удался — прежний свежий список всё ещё можно применить.
  const groups = state?.state === "ready" || state?.state === "failed" ? state.proposal?.groups ?? [] : [];
  const created = state?.proposal?.created_at;

  // Новое предложение — заново выбор по умолчанию.
  useEffect(() => {
    setOff(new Set()); setFixesOn(new Set()); setExtraOn(new Set()); setExpanded(new Set());
  }, [created]);
  useEffect(() => { title.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const termGroups = groups.filter((g) => g.kind === "term");
  const fixGroups = groups.filter((g) => g.kind === "fix");
  const chosen = chosenGroups(groups, scope, off, fixesOn);
  const extra = chosenExtra(termGroups, extraOn);
  const extraCount = Object.values(extra).reduce((n, x) => n + x.length, 0);
  const total = chosen.reduce((n, g) => n + g.count, 0) + extraCount;
  const fixChosen = fixGroups.filter((g) => fixesOn.has(g.id));
  const fixCount = fixChosen.reduce((n, g) => n + g.count, 0);
  /** Сколько мест группы будет заменено: отмеченные названные и отмеченные остальные. */
  const willReplace = (g: ImproveGroup) => (off.has(g.id) ? 0 : g.count) + (extra[g.id]?.length ?? 0);
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
        <span className="analysis-chip">
          <span className="analysis-chip__pulse" aria-hidden="true" />
          {state?.state === "queued" ? "Улучшение в очереди…" : "ИИ проверяет расшифровку…"}
        </span>
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
            return (
              <li key={g.id} className="improve__group">
                <div className="improve__row">
                  {expander(g.id, g.find)}
                  <label className="improve__check">
                    <input type="checkbox" checked={!off.has(g.id)} onChange={() => setOff((x) => toggle(x, g.id))} />
                    <span className="improve__pair">
                      <span className="improve__from">{g.find}</span> → <strong>{g.replace}</strong>
                    </span>
                    <span className="muted num" title={`Будет заменено: ${n} ${placesWord(n)}`}>· {n}</span>
                  </label>
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
                                  onClick={() => onPlay(Math.max(0, x.start - 0.3), x.end + 0.5)}>▶ {clock(x.start)}</button>
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
                      <label className="improve__check">
                        <input type="checkbox" checked={fixesOn.has(g.id)} onChange={() => setFixesOn((x) => toggle(x, g.id))} />
                        <span className="improve__pair"><span className="improve__from">{g.find}</span> → <strong>{g.replace}</strong></span>
                        <span className="muted num">· {g.count}</span>
                      </label>
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
          <button type="button" className="spk__close" aria-label="Закрыть" onClick={onClose}>×</button>
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
            {ready && total > 0 && (
              <span className="muted improve__total">Будет заменено: {total} {placesWord(total)}</span>
            )}
            <Button onClick={onClose}>{ready ? "Отмена" : "Закрыть"}</Button>
            {ready && (
              <Button variant="primary" disabled={busy || !total}
                onClick={() => onApply(chosen.map((g) => g.id), extra, rules, terms)}>Применить выбранное</Button>
            )}
          </span>
        </div>
      </div>
    </div>
  );
}
