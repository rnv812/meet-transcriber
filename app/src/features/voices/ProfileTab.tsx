/**
 * Вкладка «Профиль» человека (Голоса → человек): как он общается во встречах —
 * по его репликам, моделью (meet.profiles). Профили включаются в настройках;
 * выключены — вкладки нет.
 *
 * Каждое утверждение — с 1–3 ссылками «встреча · мм:сс»: щелчок открывает
 * встречу на этой реплике. «Мои заметки» сохраняются сами и обновлением
 * профиля не трогаются. ✦ «Подготовиться к разговору» и ✦ «Обсудить с
 * агентом» вставляют профиль во вкладку «Агент» последней общей встречи.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MoreHorizontal, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { deleteProfile, getProfile, makeProfile, saveProfileNotes, type Endpoint } from "../../lib/api";
import { dayLabel, errorText, plural } from "../../lib/format";
import { discussText, prepareText, SECTION_ORDER, SECTION_TITLES } from "../../lib/profileAgent";
import type { Job, Profile, ProfileView } from "../../lib/types";
import { Button } from "../../ui/Button";
import { PcmSection } from "./PcmSection";
import { RefChips } from "./RefChips";
import "./profile.css";

/** Пауза после последней правки заметок до сохранения. */
export const NOTES_SAVE_MS = 800;

/** Профиль человека с сервера; перечитывается, когда меняются задачи профилей. */
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
  const sig = useMemo(
    () => jobs.filter((j) => j.kind === "profile").map((j) => `${j.id}:${j.state}`).join(","), [jobs]);
  const last = useRef(sig);
  useEffect(() => {
    if (sig !== last.current) { last.current = sig; void reload(); }
  }, [sig, reload]);
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

/** «Мои заметки»: markdown как есть, сохраняется сам (пауза NOTES_SAVE_MS). */
export function ProfileNotes({ endpoint, name, initial }: { endpoint: Endpoint; name: string; initial: string }) {
  const [text, setText] = useState(initial);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    const value = pending.current;
    if (value === null) return;
    pending.current = null;
    setStatus("saving");
    try {
      await saveProfileNotes(endpoint, name, value);
      if (pending.current === null) setStatus("saved");
      setError(null);
    } catch (e) {
      pending.current ??= value;
      setStatus("error");
      setError(errorText(e));
    }
  }, [endpoint, name]);

  // Закрыли вкладку или карточку посреди паузы — несохранённое всё равно уходит.
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
    const value = pending.current;
    if (value !== null) void saveProfileNotes(endpoint, name, value).catch(() => {});
  }, [endpoint, name]);

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

function Statements({ profile, keyName, onOpenAt }: {
  profile: Profile; keyName: (typeof SECTION_ORDER)[number]; onOpenAt: (id: string, segment: number) => void;
}) {
  const items = profile.sections[keyName] ?? [];
  if (!items.length) return null;
  return (
    <section className={`profile__card profile__card--${keyName}`} aria-label={SECTION_TITLES[keyName]}>
      <h4 className="profile__h">{SECTION_TITLES[keyName]}</h4>
      <ul className="profile__list">
        {items.map((s, n) => (
          <li key={n} className="profile__item">
            <p className="profile__text">{s.text}</p>
            <RefChips profile={profile} refs={s.refs} onOpenAt={onOpenAt} />
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ProfileTab({
  endpoint, name, view, reload, onOpenAt, onAskAgent,
}: {
  endpoint: Endpoint;
  name: string;
  view: ProfileView;
  reload: () => Promise<void>;
  /** Открыть встречу на реплике (номер сегмента транскрипта). */
  onOpenAt: (recording: string, segment: number) => void;
  /** Вставить текст во вкладку «Агент» встречи (null — последней в библиотеке). */
  onAskAgent: (recording: string | null, text: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const profile = view.profile ?? null;
  const running = view.state === "queued" || view.state === "running";
  const stats = view.stats ?? { turns: 0, meetings: 0 };

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); await reload(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  const make = () => act(() => makeProfile(endpoint, name));
  const remove = () => { setConfirm(false); setMenu(false); void act(() => deleteProfile(endpoint, name)); };
  const meeting = view.latest_meeting ?? null;

  const status = running ? (
    <div className="profile__status" role="status">
      <span className="profile__pulse" aria-hidden="true" />
      {view.state === "queued" ? "Профиль в очереди…" : profile ? "Профиль обновляется…" : "Профиль составляется…"}
    </div>
  ) : view.state === "failed" ? (
    <div className="profile__status profile__status--err" role="status">
      <span title={view.error || undefined}>Не удалось составить профиль</span>
      <Button onClick={() => void make()} disabled={busy}>Повторить</Button>
    </div>
  ) : null;

  if (!profile) {
    return (
      <div className="profile">
        {status}
        {error && <div className="profile__err" role="alert">{error}</div>}
        {view.level === "none" ? (
          <div className="profile__empty">
            <h4 className="profile__empty-title">Недостаточно данных</h4>
            <p>{(view.note ?? "").replace(/^Недостаточно данных:\s*/, "")}</p>
            <p className="muted">Профиль можно будет составить, когда у человека станет больше реплик во встречах.</p>
          </div>
        ) : (
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
        )}
        <ProfileNotes key={name} endpoint={endpoint} name={name} initial={view.notes ?? ""} />
        <p className="profile__foot">Профиль хранится только на этом компьютере. Это описание стиля общения по репликам, а не оценка личности.</p>
      </div>
    );
  }

  const sections = SECTION_ORDER.filter((k) => (profile.sections[k] ?? []).length > 0);
  return (
    <div className="profile">
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
      {confirm && (
        <div className="confirm" role="alertdialog" aria-label="Удалить профиль">
          <span>Удалить профиль и ваши заметки о человеке «{name}»?</span>
          <Button variant="danger" onClick={remove}>Удалить</Button>
          <Button onClick={() => setConfirm(false)} autoFocus>Отмена</Button>
        </div>
      )}
      {status}
      {error && <div className="profile__err" role="alert">{error}</div>}
      {!running && view.has_new && view.state !== "failed" && (
        <p className="profile__hint">Есть новые реплики — профиль можно обновить.</p>
      )}
      {profile.reduced && (
        <p className="profile__hint">Сокращённый профиль: пока {turnsIn(profile.turns, profile.meetings)}.</p>
      )}
      {view.self && <p className="profile__hint">Это вы: ваш профиль обновляется только вручную.</p>}

      {profile.summary && (
        <p className="profile__summary"><span className="profile__label">Коротко:</span> {profile.summary}</p>
      )}
      <div className="profile__agent">
        <Button onClick={() => onAskAgent(meeting, prepareText(name, profile, view.pcm_enabled !== false))}
          title={`Подготовиться к разговору: ${name}. Профиль и заготовка просьбы — во вкладку «Агент» последней общей встречи`}>
          <Sparkles size={14} strokeWidth={1.75} aria-hidden="true" />Подготовиться к разговору
        </Button>
        <Button onClick={() => onAskAgent(meeting, discussText(name, profile, view.pcm_enabled !== false))}
          title="Профиль со ссылками на реплики — во вкладку «Агент» последней общей встречи">
          <Sparkles size={14} strokeWidth={1.75} aria-hidden="true" />Обсудить с агентом
        </Button>
      </div>

      <div className="profile__cards">
        {sections.map((k) => <Statements key={k} profile={profile} keyName={k} onOpenAt={onOpenAt} />)}
        {!sections.length && <p className="muted">Модель не нашла наблюдений со ссылками на реплики.</p>}
      </div>
      {view.pcm_enabled !== false && (
        <PcmSection profile={profile} note={view.pcm_note} onOpenAt={onOpenAt} />
      )}
      <ProfileNotes key={name} endpoint={endpoint} name={name} initial={view.notes ?? ""} />
      <p className="profile__foot">
        Профиль хранится только на этом компьютере. Это описание стиля общения по репликам, а не оценка личности.
      </p>
    </div>
  );
}
