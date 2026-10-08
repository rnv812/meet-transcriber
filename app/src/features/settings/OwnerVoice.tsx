/**
 * «Мой голос»: образец голоса владельца — по нему расшифровка отличает вас от
 * людей, которые сидят рядом и попадают в ваш микрофон.
 *
 * Запись ~25 с идёт в резиденте (`POST /owner-voice/record`): подпроцесс пишет
 * микрофон, задача строит отпечаток и проверяет качество. Окно опрашивает
 * `GET /owner-voice`, пока попытка идёт, и показывает итог словами.
 * Хранится только отпечаток голоса, звук удаляется сразу после разбора.
 *
 * Используется в мастере первого запуска (шаг «Ваш голос»), в настройках «Звук»
 * и в окне записи (OwnerVoiceDialog.tsx: из окна ассистента и карточки записи).
 *
 * «Найти по прошлым встречам» (`POST /owner-voice/derive`): задача ищет ваш
 * голос в последних звонках. Найденное — только предложение: карточка даёт
 * послушать три куска из разных встреч, образцом голос станет лишь после
 * «Да, это я» (`POST /owner-voice/suggestion`). Ничего не нашлось — причина словами.
 */

import { Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  answerOwnerSuggestion, audioUrl, cancelJob, deleteOwnerVoice, deriveOwnerVoice, type Endpoint, getOwnerVoice,
  recordOwnerVoice,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import type { OwnerVoiceSample, OwnerVoiceSampleRef, OwnerVoiceStatus } from "../../lib/types";
import { Button } from "../../ui/Button";
import { useConfirm } from "../../ui/ConfirmDialog";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { IconButton } from "../../ui/IconButton";
import { Tip } from "../../ui/Tip";
import { Row } from "./Section";
import "./ownv.css";

/** Как часто спрашивать резидент, пока идёт запись или разбор. */
export const POLL_MS = 1000;
/** Мастер: как часто перепроверять готовность (модель могла докачаться). */
export const READY_POLL_MS = 3000;

/** Нейтральный текст для чтения вслух: ~25 с в обычном темпе. */
export const READING_TEXT =
  "Утро выдалось тихим и прохладным. Над рекой медленно поднимался туман, а на другом берегу уже " +
  "виднелись крыши домов и старая водонапорная башня. По мосту проехал первый автобус, за ним — " +
  "несколько велосипедистов. В булочной на углу открыли окна, и по улице разнёсся запах свежего хлеба. " +
  "Город просыпался неспешно: кто-то поливал цветы на балконе, кто-то выгуливал собаку, а дворник " +
  "аккуратно сметал листья к обочине. День обещал быть ясным.";

export const PRIVACY_NOTE =
  "Хранится только отпечаток голоса — набор чисел. Сама запись удаляется сразу после обработки.";

/** Причина словами — отдельным предложением, с точкой. */
export const sentence = (s: string) => (/[.!?]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`);

/** «05.10» из «2026-10-05». */
export function shortDate(iso: string): string {
  const m = /^\d{4}-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[2]}.${m[1]}` : iso;
}

/** Строка образца в настройках: «записан 05.10 · USB-микрофон». */
export function sampleText(s: OwnerVoiceSample): string {
  const when = shortDate(s.date);
  if (s.source === "enroll") return [`записан ${when}`, s.device].filter(Boolean).join(" · ");
  if (s.source === "meeting") return `из встречи ${when}`;
  return `найден по прошлым встречам ${when}`;
}

const active = (status: OwnerVoiceStatus | null) =>
  status?.take?.state === "recording" || status?.take?.state === "analyzing" || !!status?.derive?.running;

/**
 * Состояние образца у резидента: загрузка, опрос во время записи, запись и
 * удаление. `watchReady` — перепроверять готовность, пока её нет (мастер:
 * модель разделения на спикеров могла ещё качаться); `watchBusy` — и пока идёт
 * запись встречи (окно записи открыто из окна ассистента: кнопка станет
 * доступна, как только встреча закончится).
 */
export function useOwnerVoice(endpoint: Endpoint, {
  pollMs = POLL_MS, readyPollMs = READY_POLL_MS, watchReady = false, watchBusy = false,
}: {
  pollMs?: number; readyPollMs?: number; watchReady?: boolean; watchBusy?: boolean;
} = {}) {
  const [status, setStatus] = useState<OwnerVoiceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const live = useRef(true);
  // StrictMode (dev) снимает и снова ставит эффекты: без `true` при монтаже ответы
  // резидента молча отбрасывались бы и статус так и остался бы «Проверяю образец голоса…».
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  /** Растёт при неудачном опросе: статус не изменился, а таймер нужно завести снова. */
  const [failures, setFailures] = useState(0);
  const reload = useCallback(async () => {
    try {
      const next = await getOwnerVoice(endpoint);
      if (live.current) { setStatus(next); setError(null); }
    } catch (e) {
      if (live.current) { setError(errorText(e)); setFailures((n) => n + 1); }
    }
  }, [endpoint]);

  useEffect(() => { void reload(); }, [reload]);

  const polling = active(status);
  const waiting = !!status && !polling
    && ((watchReady && !status.ready) || (watchBusy && !!status.recording));
  useEffect(() => {
    if (!polling && !waiting) return;
    const timer = setTimeout(() => void reload(), polling ? pollMs : readyPollMs);
    return () => clearTimeout(timer);
  }, [polling, waiting, status, failures, reload, pollMs, readyPollMs]);

  const record = async (device: string | null) => {
    setStarting(true);
    setError(null);
    try {
      const next = await recordOwnerVoice(endpoint, device);
      if (live.current) setStatus(next);
    } catch (e) {
      if (live.current) setError(errorText(e));
    } finally {
      if (live.current) setStarting(false);
    }
  };

  const remove = async (id: string) => {
    setError(null);
    try {
      const next = await deleteOwnerVoice(endpoint, id);
      if (live.current) setStatus(next);
    } catch (e) {
      if (live.current) setError(errorText(e));
    }
  };

  /** Идёт запрос поиска или ответа на найденный голос: кнопки не нажимаются дважды. */
  const [pending, setPending] = useState(false);
  /** Ответ резидента вместо статуса или текст отказа. */
  const call = async (request: () => Promise<OwnerVoiceStatus>) => {
    setError(null);
    setPending(true);
    try {
      const next = await request();
      if (live.current) setStatus(next);
    } catch (e) {
      if (live.current) setError(errorText(e));
    } finally {
      if (live.current) setPending(false);
    }
  };
  /** «Найти по прошлым встречам». */
  const derive = () => call(() => deriveOwnerVoice(endpoint));
  /** Найденный голос: `true` — «Да, это я», `false` — «Нет». */
  const answer = (accept: boolean) => call(() => answerOwnerSuggestion(endpoint, accept));
  /** «Остановить поиск»: снять задачу и перечитать статус. */
  const stopDerive = async () => {
    const job = status?.derive?.job;
    if (!job) return;
    setError(null);
    try {
      await cancelJob(endpoint, job);
    } catch (e) {
      if (live.current) setError(errorText(e));
    }
    await reload();
  };

  return {
    status, error, starting, pending, busy: starting || polling, record, remove, reload, derive, answer, stopDerive,
  };
}

export type OwnerVoice = ReturnType<typeof useOwnerVoice>;

/** Сколько секунд осталось читать: считаем от момента, когда увидели запись. */
function useCountdown(running: boolean, seconds: number): number {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    if (!running) { setLeft(seconds); return; }
    const start = Date.now();
    const tick = () => setLeft(Math.max(0, Math.ceil(seconds - (Date.now() - start) / 1000)));
    tick();
    const timer = setInterval(tick, 250);
    return () => clearInterval(timer);
  }, [running, seconds]);
  return left;
}

/** Текст для чтения, кнопка записи, ход и итог словами. */
export function OwnerVoiceRecorder({ voice, device }: { voice: OwnerVoice; device: string | null }) {
  const { status, error, starting, busy } = voice;
  const take = status?.take ?? null;
  const seconds = status?.seconds ?? 25;
  const left = useCountdown(take?.state === "recording", seconds);
  const blocked = status?.recording
    ? "Идёт запись встречи — запишите образец после неё."
    : status && !status.ready ? sentence(status.reason ?? "Записать образец сейчас нельзя") : null;
  const again = take?.state === "done" || take?.state === "failed";
  return (
    <div className="ownv">
      <p className="ownv__lead">Нажмите «Начать запись» и прочитайте вслух текст (~{seconds} с):</p>
      <blockquote className="ownv__text">{READING_TEXT}</blockquote>
      <div className="ownv__bar">
        <Button variant="primary" busy={starting} disabled={busy || !!blocked || !status}
          onClick={() => void voice.record(device)}>
          {again ? "Записать ещё раз" : "Начать запись"}
        </Button>
        <span className="ownv__state" aria-live="polite">
          {take?.state === "recording" && <span className="notice">Читайте вслух… осталось {left} с</span>}
          {take?.state === "analyzing" && <span className="muted">Обработка записи…</span>}
          {take?.state === "done" && <span className="notice">Голос записан.</span>}
        </span>
      </div>
      {take?.state === "recording" && (
        <span role="meter" aria-label="Запись образца" aria-valuemin={0} aria-valuemax={seconds}
          aria-valuenow={seconds - left} className="sound__level ownv__meter">
          <span className="sound__fill" style={{ width: `${Math.round(((seconds - left) / seconds) * 100)}%` }} />
        </span>
      )}
      {take?.state === "failed" && take.error && <p className="error" role="alert">{take.error}</p>}
      {blocked && <p className="muted">{blocked}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <p className="muted ownv__privacy">{PRIVACY_NOTE}</p>
    </div>
  );
}

/**
 * «Поиск 06.10: голос не предложен. Причина.» — если последний поиск ничего не
 * предложил (устаревшие причины резидент уже убрал). Пока поиск идёт или он
 * упал — не показываем: там своя строка.
 */
export function deriveNote(status: OwnerVoiceStatus | null): string | null {
  const d = status?.derive;
  if (!d || d.running || d.error || status?.suggestion || !d.last || d.last.status === "suggested"
    || !d.last.reason) return null;
  const when = d.last.date ? ` ${shortDate(d.last.date)}` : "";
  return `Поиск${when}: голос не предложен. ${sentence(d.last.reason)}`;
}

/**
 * Карточка найденного голоса: «Похоже, это ваш голос — послушайте: ▶ ▶ ▶ ·
 * Да, это я · Нет». Кусок играет дорожка микрофона той встречи.
 */
export function OwnerVoiceFound({ endpoint, voice }: { endpoint: Endpoint; voice: OwnerVoice }) {
  const audio = useRef<HTMLAudioElement>(null);
  const stopAt = useRef<number | null>(null);
  /** Какой пример играет: не открылся (запись удалили) — его ▶ гасим. */
  const playing = useRef<number | null>(null);
  const [dead, setDead] = useState<Set<number>>(() => new Set());
  const found = voice.status?.suggestion;
  if (!found) return null;
  const play = (s: OwnerVoiceSampleRef, i: number) => {
    const a = audio.current;
    if (!a) return;
    stopAt.current = s.end;
    playing.current = i;
    a.src = `${audioUrl(endpoint, s.recording, "mic")}#t=${s.start},${s.end}`;
    a.load?.();
    void a.play?.()?.catch?.(() => {});
  };
  const answering = voice.pending;
  return (
    <div className="ownv__found" role="group" aria-label="Найденный голос">
      {found.samples.length > 0
        ? <span>Похоже, это ваш голос — послушайте:</span>
        : <span>Похоже, это ваш голос, но записи с примерами уже удалены.</span>}
      {found.samples.map((s, i) => (dead.has(i)
        ? <IconButton key={`${s.recording}-${s.start}`} icon={Play} disabled
            label={`Пример ${i + 1} недоступен — запись удалена`} />
        : <IconButton key={`${s.recording}-${s.start}`} icon={Play}
            label={`Послушать пример ${i + 1} — встреча ${shortDate(s.recording)}`} onClick={() => play(s, i)} />
      ))}
      <span className="muted" aria-hidden="true">·</span>
      <Button variant="primary" disabled={answering} onClick={() => voice.answer(true)}>Да, это я</Button>
      <span className="muted" aria-hidden="true">·</span>
      <Button disabled={answering} onClick={() => voice.answer(false)}>Нет</Button>
      {found.conflict && (
        <span className="notice">Он не похож на ваш записанный образец — послушайте внимательно.</span>
      )}
      <audio ref={audio} className="ownv__audio" onTimeUpdate={(e) => {
        const a = e.currentTarget;
        if (stopAt.current !== null && a.currentTime >= stopAt.current) {
          a.pause();
          stopAt.current = null;
        }
      }} onError={() => {
        const i = playing.current;
        if (i !== null) setDead((prev) => new Set(prev).add(i));
        playing.current = null;
      }} />
    </div>
  );
}

const suggestionKey = (status: OwnerVoiceStatus | null) =>
  status?.suggestion ? `${status.suggestion.date}:${status.suggestion.meetings.join(",")}` : "none";

/** Настройки «Звук» → «Мой голос»: что записано, «Перезаписать», «Удалить», «Найти по прошлым встречам». */
export function OwnerVoiceRow({ endpoint, device, pollMs }: {
  endpoint: Endpoint;
  /** Микрофон из черновика настроек (null — системный). */
  device: string | null;
  pollMs?: number;
}) {
  const voice = useOwnerVoice(endpoint, { pollMs });
  const [open, setOpen] = useState(false);
  const [confirmNode, confirm] = useConfirm();
  const samples = voice.status?.samples ?? [];
  // «Перезаписать» — только если запись заменит образец этого микрофона;
  // с другим микрофоном появится второй образец.
  const enrolled = samples.some((s) => s.source === "enroll" && (device === null || s.device === device));
  const remove = async (s: OwnerVoiceSample) => {
    if (s.source === "enroll" && !(await confirm({
      title: "Удалить записанный образец голоса?",
      message: "Расшифровка перестанет отличать ваш голос с этого микрофона от голосов людей рядом, "
        + "пока вы не запишете образец снова.",
      confirmLabel: "Удалить", cancelLabel: "Оставить",
    }))) return;
    await voice.remove(s.id);
  };
  const take = voice.status?.take;
  const deriving = !!voice.status?.derive?.running;
  const note = deriveNote(voice.status);
  const failed = !deriving ? voice.status?.derive?.error ?? null : null;
  // Запись готова — свернуть: в строке уже виден новый образец.
  useEffect(() => { if (take?.state === "done") setOpen(false); }, [take?.state]);
  return (
    <div role="group" aria-label="Мой голос">
      <Row label="Мой голос" stack
        hint="Чтобы отличать ваш голос от людей рядом с вами, которых слышит ваш микрофон"
        help={(
          <HelpTip label="Зачем записывать свой голос" title="Мой голос">
            <TipLine>По образцу голоса расшифровка отличает вас от людей, которые сидят рядом и попадают в ваш микрофон.</TipLine>
            <TipLine>{PRIVACY_NOTE}</TipLine>
          </HelpTip>
        )}>
        <div className="ownv__samples">
          {voice.status && samples.length === 0 && <span className="muted">не записан</span>}
          {samples.map((s) => (
            <span key={s.id} className="ownv__sample">
              <span>{sampleText(s)}</span>
              <Button onClick={() => void remove(s)} disabled={voice.busy}
                aria-label={`Удалить образец: ${sampleText(s)}`}>Удалить</Button>
            </span>
          ))}
          {!open && (
            <Button onClick={() => setOpen(true)} disabled={!voice.status}>
              {enrolled ? "Перезаписать" : "Записать"}
            </Button>
          )}
          {!open && (
            <Tip content="Поискать ваш голос в последних звонках — без записи образца">
              <Button variant="ghost" onClick={() => voice.derive()}
                disabled={!voice.status || voice.status.recording || !voice.status.ready || voice.busy
                  || voice.pending}>
                Найти по прошлым встречам
              </Button>
            </Tip>
          )}
          {deriving && <span className="muted" aria-live="polite">Ищу ваш голос в последних встречах…</span>}
          {deriving && voice.status?.derive?.job && (
            <Button variant="ghost" onClick={() => voice.stopDerive()}>Остановить поиск</Button>
          )}
          {!open && voice.error && <span className="error">{voice.error}</span>}
        </div>
      </Row>
      {/* Новое предложение — новая карточка: погашенные ▶ прежнего не переносятся. */}
      <OwnerVoiceFound key={suggestionKey(voice.status)} endpoint={endpoint} voice={voice} />
      {note && <p className="muted ownv__note">{note}</p>}
      {failed && <p className="error ownv__note" role="alert">Поиск не удался: {failed}</p>}
      {open && <OwnerVoiceRecorder voice={voice} device={device} />}
      {confirmNode}
    </div>
  );
}
