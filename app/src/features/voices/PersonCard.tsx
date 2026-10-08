import { Play } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import {
  audioUrl, deleteAvatar, deletePerson, getPerson, getSample, mergePerson, putAvatar, renamePerson, setPersonRole,
  type Endpoint,
} from "../../lib/api";
import { dayLabel, duration, errorText } from "../../lib/format";
import { ROLE_MAX, type Person, type PersonCard as PersonData } from "../../lib/types";
import { Button } from "../../ui/Button";
import { ConfirmDialog, useConfirm } from "../../ui/ConfirmDialog";
import { Select } from "../../ui/Select";
import { AvatarEditor, pastedImage } from "./AvatarEditor";

/** Пример в пустом поле «Кто это». */
export const ROLE_PLACEHOLDER = "Например: CTO Acme, заказчик";

/** «Кто это» как его сохранит резидент: переводы строк и лишние пробелы — один пробел, не длиннее 160. */
export const cleanRole = (text: string) => text.replace(/\s+/g, " ").trim().slice(0, ROLE_MAX);

type Props = {
  endpoint: Endpoint;
  person: Person;
  others: Person[];
  version?: number;
  onAvatar: () => void;
  onRenamed: (to: string) => void;
  onRemoved: (next: string | null) => void;
  /** «Кто это» сохранено — перечитать список людей (столбец таблицы, подсказки чипов). */
  onRoleSaved?: () => void;
  onOpenRecording: (id: string) => void;
  /**
   * Зачем открыта карточка из строки таблицы: переименовать (курсор в имени),
   * объединить (фокус на выборе человека) или удалить (сразу вопрос). Новый
   * `n` повторяет намерение для уже открытой карточки. Нет — карточка берёт
   * фокус сама (Ctrl+V для фотографии).
   */
  intent?: PersonIntent;
};

export type PersonIntent = { kind: "rename" | "merge" | "delete"; n: number };

const MAX_AVATAR = 10 * 1024 * 1024;

export function PersonCard({
  endpoint, person, others, version, onAvatar, onRenamed, onRemoved, onRoleSaved, onOpenRecording, intent,
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
  const nameInput = useRef<HTMLInputElement>(null);
  const mergeSelect = useRef<HTMLButtonElement>(null);
  /** «Кто это»: черновик в поле, последнее сохранённое и «Сохранено» после удачной записи. */
  const [role, setRole] = useState(person.role ?? "");
  const savedRole = useRef(person.role ?? "");
  const [roleSaved, setRoleSaved] = useState(false);
  const roleCancelled = useRef(false);
  const roleInput = useRef<HTMLInputElement>(null);
  const roleId = useId();
  // Список людей перечитан (роль поменяли в другом месте) — поле за ним, если его сейчас не правят.
  const listedRole = person.role ?? "";
  useEffect(() => {
    if (document.activeElement === roleInput.current) return;
    savedRole.current = listedRole;
    setRole(listedRole);
  }, [listedRole]);

  useEffect(() => {
    if (!intent) { root.current?.focus(); return; }
    if (intent.kind === "delete") { setConfirm("delete"); return; }
    const target = intent.kind === "merge" ? mergeSelect.current : nameInput.current;
    (target ?? root.current)?.focus();
    if (intent.kind === "rename") nameInput.current?.select();
  }, [intent]);

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
  /** Уход из поля «Кто это» (и Enter): сохранить, если изменилось; Esc — вернуть сохранённое. */
  const saveRole = () => {
    if (roleCancelled.current) { roleCancelled.current = false; setRole(savedRole.current); return; }
    const next = cleanRole(role);
    if (next === savedRole.current) { setRole(next); return; }
    void run(async () => {
      const saved = await setPersonRole(endpoint, name, next);
      savedRole.current = saved;
      setRole(saved);
      setRoleSaved(true);
      onRoleSaved?.();
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
        <div className="pcard__who">
          <input
            ref={nameInput}
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
          <Button variant="ghost" size="xs" icon={Play} className="pcard__play" onClick={() => void play()}>
            Прослушать образец
          </Button>
        </div>
      </div>

      {/* «Кто это»: роль или короткая заметка; её видит и ассистент на встречах с этим человеком. */}
      <div className="pcard__role">
        <div className="pcard__role-head">
          <label className="pcard__label" htmlFor={roleId}>Кто это</label>
          <span className="pcard__saved" role="status">{roleSaved ? "Сохранено" : ""}</span>
        </div>
        <input
          ref={roleInput}
          id={roleId}
          className="field field--md"
          placeholder={ROLE_PLACEHOLDER}
          maxLength={ROLE_MAX}
          value={role}
          onChange={(e) => { setRole(e.target.value); setRoleSaved(false); }}
          onBlur={saveRole}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
            if (e.key === "Escape") { e.stopPropagation(); roleCancelled.current = true; e.currentTarget.blur(); }
          }}
        />
      </div>
      {error && <div className="pcard__error" role="alert">{error}</div>}
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

      <section className="pcard__section" aria-label="Встречи">
        <h3 className="pcard__h">
          Встречи{data ? <span className="pcard__count num">{data.meetings.length}</span> : null}
        </h3>
        <ul className="pcard__meetings">
          {data?.meetings.map((m) => (
            <li key={m.recording}>
              <button type="button" className="pcard__meeting" onClick={() => onOpenRecording(m.recording)}>
                <span className="pcard__meeting-title">{m.title || m.recording}</span>
                <span className="pcard__meeting-meta num">
                  {m.started_at ? `${dayLabel(m.started_at)} · ` : ""}{duration(m.seconds)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <div className="pcard__actions">
        {others.length > 0 && (
          <Select<string>
            ref={mergeSelect}
            aria-label="Объединить с…"
            placeholder="Объединить с…"
            size="sm"
            width={184}
            value=""
            options={others.map((o) => ({ value: o.name, label: o.name }))}
            onChange={(to) => setConfirm({ merge: to })}
          />
        )}
        <Button variant="danger" onClick={() => setConfirm("delete")}>Удалить голос</Button>
      </div>
      {confirm && (
        <ConfirmDialog
          title={confirm === "delete" ? `Удалить голос «${name}»?` : `Объединить «${name}» с «${confirm.merge}»?`}
          message={confirm === "delete"
            ? `Образцы голоса будут удалены: в новых встречах «${name}» больше не будет узнаваться.`
            : `Образцы «${name}» перейдут к «${confirm.merge}», а «${name}» исчезнет из базы голосов.`}
          confirmLabel={confirm === "delete" ? "Удалить" : "Объединить"}
          onConfirm={doConfirmed} onCancel={() => setConfirm(null)} />
      )}
      {confirmNode}
    </div>
  );
}
