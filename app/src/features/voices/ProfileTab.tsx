/**
 * Вкладка «Профиль» человека (Голоса → человек): как он общается во встречах —
 * по его репликам, моделью (meet.profiles). Профили включаются в настройках;
 * выключены — вкладки нет.
 *
 * Каждое утверждение — с 1–3 ссылками «встреча · мм:сс»: щелчок открывает
 * встречу на этой реплике; реплику изменили после профиля — ссылка
 * неактивна («реплика изменилась»). У каждого утверждения есть «Скрыть»:
 * скрытое не показывается и после обновления профиля. «Мои заметки»
 * сохраняются сами (по очереди, без потерь при перерисовке) и обновлением
 * профиля не трогаются. ✦ «Подготовиться к разговору» и ✦ «Обсудить с
 * агентом» вставляют профиль во вкладку «Агент» последней общей встречи.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { MoreHorizontal, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import {
  deleteProfile, getProfile, hideProfileStatement, makeProfile, saveProfileNotes, type Endpoint,
} from "../../lib/api";
import { dayLabel, errorText, plural } from "../../lib/format";
import { discussText, grounded, prepareText, SECTION_ORDER, SECTION_TITLES } from "../../lib/profileAgent";
import type { Job, Profile, ProfileView } from "../../lib/types";
import { Button } from "../../ui/Button";
import { PcmSection } from "./PcmSection";
import { HideButton, RefChips, type OpenAt } from "./RefChips";
import "./profile.css";

/** Пауза после последней правки заметок до сохранения. */
export const NOTES_SAVE_MS = 800;
/** Задача профиля кончилась — перечитать не сразу: события задач идут пачкой. */
export const PROFILE_RELOAD_MS = 400;
/** Индекс реплик ещё считается — спросить снова через. */
export const INDEXING_POLL_MS = 2000;

const norm = (p: string) => p.replace(/\\/g, "/").toLowerCase();

/**
 * Профиль человека с сервера. Перечитывается, когда меняется задача профиля
 * именно этого человека (с паузой PROFILE_RELOAD_MS), и раз в
 * INDEXING_POLL_MS, пока резидент впервые считает реплики.
 */
export function useProfile(endpoint: Endpoint, name: string, jobs: Job[]) {
  const [view, setView] = useState<ProfileView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const reload = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const got = await getProfile(endpoint, name);
      if (mine === seq.current) { setView(got); setError(null); }
    } catch (e) {
      if (mine === seq.current) setError(errorText(e));
    }
  }, [endpoint, name]);
  useEffect(() => { void reload(); }, [reload]);
  const pid = view?.id ?? null;
  const sig = useMemo(() => (pid
    ? jobs.filter((j) => j.kind === "profile" && norm(j.folder).endsWith(`/${pid}.json`))
      .map((j) => `${j.id}:${j.state}`).join(",")
    : jobs.filter((j) => j.kind === "profile").map((j) => j.id).join(",")), [jobs, pid]);
  const last = useRef(sig);
  useEffect(() => {
    if (sig === last.current) return;
    last.current = sig;
    const timer = setTimeout(() => void reload(), PROFILE_RELOAD_MS);
    return () => clearTimeout(timer);
  }, [sig, reload]);
  const indexing = view?.indexing === true;
  useEffect(() => {
    if (!indexing) return;
    const timer = setTimeout(() => void reload(), INDEXING_POLL_MS);
    return () => clearTimeout(timer);
  }, [indexing, view, reload]);
  return { view, error, reload };
}

/** «обновлено сегодня 14:05», «обновлено 2 окт 14:05». */
function updatedLabel(at: number): string {
  const label = dayLabel(new Date(at * 1000).toISOString());
  return `обновлено ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}

function byMeetings(n: number): string {
  return `по ${n} ${plural(n, "встрече", "встречам", "встречам")}`;
}

function turnsIn(turns: number, meetings: number): string {
  return `${turns} ${plural(turns, "реплика", "реплики", "реплик")} в ${meetings} ${plural(meetings, "встрече", "встречах", "встречах")}`;
}

export type NotesControl = { cancel: () => void };

/**
 * «Мои заметки»: markdown как есть, сохраняется сам (пауза NOTES_SAVE_MS) и по
 * очереди — более позднее сохранение не обгонит раннее. `control.cancel()` —
 * забыть несохранённое (профиль удаляют: заметки не должны вернуться).
 */
export function ProfileNotes({ endpoint, name, initial, control }: {
  endpoint: Endpoint; name: string; initial: string; control?: MutableRefObject<NotesControl | null>;
}) {
  const [text, setText] = useState(initial);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const chain = useRef<Promise<void>>(Promise.resolve());
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const send = useCallback((value: string) => {
    chain.current = chain.current.then(async () => {
      if (alive.current) setStatus("saving");
      try {
        await saveProfileNotes(endpoint, name, value);
        if (!alive.current) return;
        if (pending.current === null) setStatus("saved");
        setError(null);
      } catch (e) {
        if (!alive.current) return;
        pending.current ??= value;
        setStatus("error");
        setError(errorText(e));
      }
    });
    return chain.current;
  }, [endpoint, name]);

  const flush = useCallback(() => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    const value = pending.current;
    if (value === null) return chain.current;
    pending.current = null;
    return send(value);
  }, [send]);

  useEffect(() => {
    if (!control) return;
    control.current = {
      cancel: () => {
        if (timer.current) { clearTimeout(timer.current); timer.current = null; }
        pending.current = null;
      },
    };
    return () => { control.current = null; };
  }, [control]);

  // Закрыли вкладку или карточку посреди паузы — несохранённое всё равно уходит (в очередь).
  useEffect(() => () => { void flush(); }, [flush]);

  const change = (value: string) => {
    setText(value);
    pending.current = value;
    setStatus("idle");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), NOTES_SAVE_MS);
  };

  return (
    <section className="profile__notes" aria-labelledby="profile-notes-h">
      <div className="profile__notes-head">
        <h4 id="profile-notes-h" className="profile__h">Мои заметки</h4>
        <span className="profile__saved" role="status">
          {status === "saving" ? "Сохраняю…" : status === "saved" ? "Сохранено" : status === "error" ? "Не сохранено" : ""}
        </span>
      </div>
      <textarea
        className="profile__textarea"
        aria-label="Мои заметки"
        placeholder="Ваши наблюдения и договорённости — обновление профиля их не меняет. Можно писать в Markdown."
        value={text}
        onChange={(e) => change(e.target.value)}
        onBlur={() => void flush()}
        rows={4}
      />
      {error && <div className="profile__err" role="alert">{error}</div>}
    </section>
  );
}

function Statements({ profile, keyName, onOpenAt, onHide }: {
  profile: Profile; keyName: (typeof SECTION_ORDER)[number]; onOpenAt: OpenAt; onHide: (text: string) => void;
}) {
  const items = profile.sections[keyName] ?? [];
  if (!items.length) return null;
  return (
    <section className={`profile__card profile__card--${keyName}`} aria-label={SECTION_TITLES[keyName]}>
      <h4 className="profile__h">{SECTION_TITLES[keyName]}</h4>
      <ul className="profile__list">
        {items.map((s, n) => (
          <li key={n} className="profile__item">
            <div className="profile__line">
              <p className="profile__text">{s.text}</p>
              <HideButton onHide={() => onHide(s.text)} />
            </div>
            <RefChips profile={profile} refs={s.refs} onOpenAt={onOpenAt} />
          </li>
        ))}
      </ul>
    </section>
  );
}

const FOOT = "Профиль хранится только на этом компьютере. Это описание стиля общения по репликам, а не оценка личности.";

export function ProfileTab({
  endpoint, name, view, reload, onOpenAt, onAskAgent,
}: {
  endpoint: Endpoint;
  name: string;
  view: ProfileView;
  reload: () => Promise<void>;
  /** Открыть встречу на реплике (номер сегмента транскрипта, начало реплики). */
  onOpenAt: OpenAt;
  /** Вставить текст во вкладку «Агент» встречи (null — последней в библиотеке). */
  onAskAgent: (recording: string | null, text: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const notes = useRef<NotesControl | null>(null);
  const profile = view.profile ?? null;
  const running = view.state === "queued" || view.state === "running";
  const stats = view.stats ?? { turns: 0, meetings: 0 };
  const pcmOn = view.pcm_enabled !== false;

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); await reload(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  const make = () => act(() => makeProfile(endpoint, name));
  const remove = () => {
    setConfirm(false);
    setMenu(false);
    notes.current?.cancel(); // несохранённые заметки после удаления не возвращаются
    void act(() => deleteProfile(endpoint, name));
  };
  const hide = (text: string) => void act(() => hideProfileStatement(endpoint, name, text, true));
  const unhideAll = () => void act(() => hideProfileStatement(endpoint, name, null, false));
  // Общей встречи нет — последняя в библиотеке (без «материалов этой встречи» в просьбе).
  const shared = view.latest_meeting ?? null;
  const target = shared ?? view.latest_any ?? null;

  const status = running ? (
    <div className="profile__status" role="status">
      <span className="profile__pulse" aria-hidden="true" />
      {view.state === "queued" ? "Профиль в очереди…" : profile ? "Профиль обновляется…" : "Профиль составляется…"}
    </div>
  ) : view.state === "failed" ? (
    <div className="profile__status profile__status--err" role="status">
      <span title={view.error || undefined}>
        {view.error?.startsWith("Не удалось составить профиль с опорой") ? view.error : "Не удалось составить профиль"}
      </span>
      <Button onClick={() => void make()} disabled={busy}>Повторить</Button>
    </div>
  ) : profile?.review && profile.review.checked === false ? (
    <div className="profile__status profile__status--warn" role="status">
      <span title={profile.review.error || undefined}>
        Проверка утверждений не завершена — показано то, что прошло фильтр
      </span>
      <Button onClick={() => void make()} disabled={busy}>Повторить</Button>
    </div>
  ) : null;

  let main;
  if (profile) {
    const sections = SECTION_ORDER.filter((k) => (profile.sections[k] ?? []).length > 0);
    main = (
      <>
        {profile.summary && grounded(profile) && (
          <div className="profile__summary">
            <div className="profile__line">
              <p className="profile__text"><span className="profile__label">Коротко:</span> {profile.summary}</p>
              <HideButton onHide={() => hide(profile.summary)} />
            </div>
            <RefChips profile={profile} refs={profile.summary_refs} onOpenAt={onOpenAt} />
          </div>
        )}
        <div className="profile__agent">
          <Button onClick={() => onAskAgent(target, prepareText(name, profile, pcmOn, shared !== null))}
            title={shared ? "Профиль и заготовка просьбы — во вкладку «Агент» последней общей встречи"
              : "Общих встреч нет — во вкладку «Агент» последней встречи"}>
            <Sparkles size={14} strokeWidth={1.75} aria-hidden="true" />Подготовиться к разговору: {name}
          </Button>
          <Button onClick={() => onAskAgent(target, discussText(name, profile, pcmOn))}
            title="Профиль со ссылками на реплики — во вкладку «Агент» последней общей встречи">
            <Sparkles size={14} strokeWidth={1.75} aria-hidden="true" />Обсудить с агентом
          </Button>
        </div>
        <div className="profile__cards">
          {sections.map((k) => <Statements key={k} profile={profile} keyName={k} onOpenAt={onOpenAt} onHide={hide} />)}
          {!sections.length && <p className="muted">Все утверждения скрыты или их ссылки ведут на удалённые встречи.</p>}
        </div>
        {pcmOn && <PcmSection profile={profile} note={view.pcm_note} onOpenAt={onOpenAt} onHide={hide} />}
      </>
    );
  } else if (view.indexing) {
    main = (
      <div className="profile__empty" role="status">
        <h4 className="profile__empty-title">Подсчитываю реплики…</h4>
        <p className="muted">Первый раз это занимает до минуты на большой библиотеке встреч.</p>
      </div>
    );
  } else if (view.level === "none") {
    main = (
      <div className="profile__empty">
        <h4 className="profile__empty-title">Недостаточно данных</h4>
        <p>{(view.note ?? "").replace(/^Недостаточно данных:\s*/, "")}</p>
        <p className="muted">Профиль можно будет составить, когда у человека станет больше реплик во встречах.</p>
      </div>
    );
  } else {
    main = (
      <div className="profile__empty profile__empty--intro">
        <h4 className="profile__empty-title">Профиль общения</h4>
        <p>
          Агент опишет, как человек общается во встречах: стиль, что для него важно, как лучше строить разговор
          и чего избегать. Каждое наблюдение — со ссылками на реплики.
        </p>
        <p className="muted">
          {`Есть ${turnsIn(stats.turns, stats.meetings)}.`}
          {view.level === "reduced" ? " Реплик пока немного — профиль будет сокращённым." : ""}
        </p>
        <Button variant="primary" onClick={() => void make()} disabled={busy || running}>
          <Sparkles size={14} strokeWidth={1.75} aria-hidden="true" />Составить профиль
        </Button>
      </div>
    );
  }

  // Порядок дочерних элементов постоянный: «Мои заметки» не пересоздаются,
  // когда профиль появляется или исчезает (иначе терялись бы последние буквы).
  return (
    <div className="profile">
      {profile ? (
        <div className="profile__bar">
          <span className="profile__meta">{byMeetings(profile.meetings)} · {updatedLabel(profile.updated_at)}</span>
          <span className="profile__tools">
            <Button onClick={() => void make()} disabled={busy || running}
              title={view.self ? "Ваш профиль обновляется только вручную" : undefined}>
              <RefreshCw size={14} strokeWidth={1.75} aria-hidden="true" />Обновить профиль
            </Button>
            <span className="menu-wrap">
              <button type="button" className="icon-btn" aria-label="Ещё действия с профилем" aria-haspopup="menu"
                aria-expanded={menu} onClick={() => setMenu((m) => !m)}>
                <MoreHorizontal size={16} strokeWidth={1.75} aria-hidden="true" />
              </button>
              {menu && (
                <div className="menu profile__menu" role="menu">
                  <button type="button" role="menuitem" className="danger" onClick={() => { setMenu(false); setConfirm(true); }}>
                    <Trash2 size={14} strokeWidth={1.75} aria-hidden="true" />Удалить профиль…
                  </button>
                </div>
              )}
            </span>
          </span>
        </div>
      ) : null}
      {confirm ? (
        <div className="confirm" role="alertdialog" aria-label="Удалить профиль">
          <span>Удалить профиль и ваши заметки о человеке «{name}»?</span>
          <Button variant="danger" onClick={remove}>Удалить</Button>
          <Button onClick={() => setConfirm(false)} autoFocus>Отмена</Button>
        </div>
      ) : null}
      {status}
      {error ? <div className="profile__err" role="alert">{error}</div> : null}
      {profile && !running && view.has_new && view.state !== "failed" ? (
        <p className="profile__hint">Есть новые реплики — профиль можно обновить.</p>
      ) : null}
      {profile?.reduced ? (
        <p className="profile__hint">Сокращённый профиль: пока {turnsIn(profile.turns, profile.meetings)}.</p>
      ) : null}
      {profile && view.self ? <p className="profile__hint">Это вы: ваш профиль обновляется только вручную.</p> : null}
      {view.hidden ? (
        <p className="profile__hint">
          Скрыто утверждений: {view.hidden} ·{" "}
          <button type="button" className="profile__link" onClick={unhideAll}>Показать снова</button>
        </p>
      ) : null}
      {main}
      <ProfileNotes key={name} endpoint={endpoint} name={name} initial={view.notes ?? ""} control={notes} />
      <p className="profile__foot">{FOOT}</p>
    </div>
  );
}
