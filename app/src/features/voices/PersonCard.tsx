import { Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  audioUrl, deleteAvatar, deletePerson, getPerson, getSample, mergePerson, putAvatar, renamePerson,
  type Endpoint,
} from "../../lib/api";
import { dayLabel, duration, errorText } from "../../lib/format";
import type { Job, Person, PersonCard as PersonData } from "../../lib/types";
import { Button } from "../../ui/Button";
import { ConfirmDialog, useConfirm } from "../../ui/ConfirmDialog";
import { AvatarEditor, pastedImage } from "./AvatarEditor";
import { ProfileTab, useProfile } from "./ProfileTab";
import type { OpenAt } from "./RefChips";

type Props = {
  endpoint: Endpoint;
  person: Person;
  others: Person[];
  version?: number;
  onAvatar: () => void;
  onRenamed: (to: string) => void;
  onRemoved: (next: string | null) => void;
  onOpenRecording: (id: string) => void;
  /** Задачи резидента: профиль перечитывается, когда его задача кончилась. */
  jobs?: Job[];
  /** Открыть встречу на реплике (ссылка утверждения профиля). */
  onOpenAt?: OpenAt;
  /** Текст во вкладку «Агент» встречи (null — последней в библиотеке). */
  onAskAgent?: (recording: string | null, text: string) => void;
  /** Открыта вкладка «Профиль»: панель человека шире. */
  onWide?: (wide: boolean) => void;
};

const NO_JOBS: Job[] = [];
type PersonTab = "voice" | "profile";

const MAX_AVATAR = 10 * 1024 * 1024;

export function PersonCard({
  endpoint, person, others, version, onAvatar, onRenamed, onRemoved, onOpenRecording, jobs = NO_JOBS, onOpenAt,
  onAskAgent, onWide,
}: Props) {
  const name = person.name;
  const [data, setData] = useState<PersonData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState(name);
  const [confirm, setConfirm] = useState<null | "delete" | { merge: string }>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const stopAt = useRef<number | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const cancelled = useRef(false);
  const profile = useProfile(endpoint, name, jobs);
  const profilesOn = profile.view?.enabled === true;
  const [tab, setTab] = useState<PersonTab>("voice");
  const shown: PersonTab = profilesOn ? tab : "voice";
  useEffect(() => { onWide?.(shown === "profile"); }, [shown, onWide]);

  useEffect(() => root.current?.focus(), []);

  useEffect(() => {
    let live = true;
    getPerson(endpoint, name).then((d) => live && setData(d), (e) => live && setError(errorText(e)));
    return () => { live = false; };
  }, [endpoint, name]);

  async function run(fn: () => Promise<void>) {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    }
  }

  const upload = (blob: Blob) => run(async () => {
    if (blob.size > MAX_AVATAR) throw new Error("Файл больше 10 МБ");
    await putAvatar(endpoint, name, blob);
    onAvatar();
  });
  const [confirmNode, ask] = useConfirm();
  const reset = async () => {
    const ok = await ask({ title: "Убрать фотографию?", confirmLabel: "Убрать",
      message: `Вместо фотографии «${name}» снова будут показаны инициалы.` });
    if (!ok) return;
    await run(async () => {
      await deleteAvatar(endpoint, name);
      onAvatar();
    });
  };
  const rename = () => {
    if (cancelled.current) { cancelled.current = false; setDraft(name); return; }
    const to = draft.trim();
    if (!to || to === name) { setDraft(name); return; }
    void run(async () => {
      await renamePerson(endpoint, name, to);
      onRenamed(to);
    });
  };
  const play = () => run(async () => {
    const s = await getSample(endpoint, name);
    const a = audio.current;
    if (!a) return;
    stopAt.current = s.end;
    a.src = `${audioUrl(endpoint, s.recording, s.track as "sys" | "mic" | "source")}#t=${s.start},${s.end}`;
    a.load?.();
    void a.play?.()?.catch?.(() => {});
  });
  const doConfirmed = () => {
    const c = confirm;
    setConfirm(null);
    if (!c) return;
    void run(async () => {
      if (c === "delete") {
        await deletePerson(endpoint, name);
        onRemoved(null);
      } else {
        await mergePerson(endpoint, name, c.merge);
        onRemoved(c.merge);
      }
    });
  };

  return (
    <div
      className="pcard"
      ref={root}
      tabIndex={0}
      onPaste={(e) => {
        const f = pastedImage(e);
        if (f) { e.preventDefault(); void upload(f); }
      }}
    >
      <div className="pcard__head">
        <AvatarEditor
          endpoint={endpoint}
          person={person}
          hasAvatar={person.has_avatar}
          version={version}
          onUpload={(b) => void upload(b)}
          onReset={() => void reset()}
          onError={setError}
        />
        <input
          className="pcard__name"
          aria-label="Имя"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={rename}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") { cancelled.current = true; e.currentTarget.blur(); }
          }}
        />
      </div>
      {error && <div className="card__error" role="alert">{error}</div>}

      {profilesOn && (
        <div className="ptabs" role="tablist" aria-label="О человеке">
          {(["voice", "profile"] as const).map((t) => (
            <button key={t} type="button" role="tab" className="ptabs__tab" aria-selected={shown === t}
              id={`ptab-${t}`} aria-controls="ptab-panel"
              tabIndex={shown === t ? 0 : -1} onClick={() => setTab(t)}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); setTab(t === "voice" ? "profile" : "voice"); }
              }}>
              {t === "voice" ? "Голос" : "Профиль"}
            </button>
          ))}
        </div>
      )}
      <div className="ptabs__panel" {...(profilesOn
        ? { role: "tabpanel", id: "ptab-panel", "aria-labelledby": `ptab-${shown}` } : {})}>
      {shown === "profile" && profile.view ? (
        <ProfileTab endpoint={endpoint} name={name} view={profile.view} reload={profile.reload}
          onOpenAt={(m, i, t) => onOpenAt?.(m, i, t, name)} onAskAgent={(m, text) => onAskAgent?.(m, text)} />
      ) : (<>
      <div className="pcard__row">
        <Button icon={Play} onClick={() => void play()}>Прослушать образец</Button>
      </div>
      <audio
        ref={audio}
        className="pcard__audio"
        onTimeUpdate={(e) => {
          const a = e.currentTarget;
          if (stopAt.current !== null && a.currentTime >= stopAt.current) {
            a.pause();
            stopAt.current = null;
          }
        }}
      />

      <h3 className="pcard__h">Встречи</h3>
      <ul className="pcard__meetings">
        {data?.meetings.map((m) => (
          <li key={m.recording}>
            <button type="button" className="pcard__meeting" onClick={() => onOpenRecording(m.recording)}>
              <span>{m.title || m.recording}</span>
              <span className="muted">
                {m.started_at ? `${dayLabel(m.started_at)} · ` : ""}{duration(m.seconds)}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <div className="pcard__row">
        {others.length > 0 && (
          <select
            aria-label="Объединить с…"
            className="pcard__select"
            value=""
            onChange={(e) => e.target.value && setConfirm({ merge: e.target.value })}
          >
            <option value="">Объединить с…</option>
            {others.map((o) => <option key={o.name} value={o.name}>{o.name}</option>)}
          </select>
        )}
        <Button variant="danger" onClick={() => setConfirm("delete")}>Удалить голос</Button>
      </div>
      {confirm && (
        <ConfirmDialog
          title={confirm === "delete" ? `Удалить голос «${name}»?` : `Объединить «${name}» с «${confirm.merge}»?`}
          message={confirm === "delete"
            ? `Образцы голоса будут удалены: в новых встречах «${name}» больше не будет узнаваться.`
              + (profile.view?.profile ? " Профиль человека тоже удалится." : "")
            : `Образцы «${name}» перейдут к «${confirm.merge}», а «${name}» исчезнет из базы голосов`
              + (profile.view?.profile ? " вместе со своим профилем." : ".")}
          confirmLabel={confirm === "delete" ? "Удалить" : "Объединить"}
          onConfirm={doConfirmed} onCancel={() => setConfirm(null)} />
      )}
      {confirmNode}
      </>)}
      </div>
    </div>
  );
}
