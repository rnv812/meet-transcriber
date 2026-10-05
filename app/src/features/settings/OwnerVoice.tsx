/**
 * «Мой голос»: образец голоса владельца — по нему расшифровка отличает вас от
 * людей, которые сидят рядом и попадают в ваш микрофон.
 *
 * Запись ~25 с идёт в резиденте (`POST /owner-voice/record`): подпроцесс пишет
 * микрофон, задача строит отпечаток и проверяет качество. Окно опрашивает
 * `GET /owner-voice`, пока попытка идёт, и показывает итог словами.
 * Хранится только отпечаток голоса, звук удаляется сразу после разбора.
 *
 * Используется в мастере первого запуска (шаг «Ваш голос») и в настройках «Звук».
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { deleteOwnerVoice, type Endpoint, getOwnerVoice, recordOwnerVoice } from "../../lib/api";
import { errorText } from "../../lib/format";
import type { OwnerVoiceSample, OwnerVoiceStatus } from "../../lib/types";
import { Button } from "../../ui/Button";
import { useConfirm } from "../../ui/ConfirmDialog";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Row } from "./Section";

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
  status?.take?.state === "recording" || status?.take?.state === "analyzing";

/**
 * Состояние образца у резидента: загрузка, опрос во время записи, запись и
 * удаление. `watchReady` — перепроверять готовность, пока её нет (мастер:
 * модель разделения на спикеров могла ещё качаться).
 */
export function useOwnerVoice(endpoint: Endpoint, { pollMs = POLL_MS, readyPollMs = READY_POLL_MS, watchReady = false }: {
  pollMs?: number; readyPollMs?: number; watchReady?: boolean;
} = {}) {
  const [status, setStatus] = useState<OwnerVoiceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);

  const reload = useCallback(async () => {
    try {
      const next = await getOwnerVoice(endpoint);
      if (live.current) { setStatus(next); setError(null); }
    } catch (e) {
      if (live.current) setError(errorText(e));
    }
  }, [endpoint]);

  useEffect(() => { void reload(); }, [reload]);

  const polling = active(status);
  const waiting = watchReady && !!status && !status.ready && !polling;
  useEffect(() => {
    if (!polling && !waiting) return;
    const timer = setTimeout(() => void reload(), polling ? pollMs : readyPollMs);
    return () => clearTimeout(timer);
  }, [polling, waiting, status, reload, pollMs, readyPollMs]);

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

  return { status, error, starting, busy: starting || polling, record, remove, reload };
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

/** Настройки «Звук» → «Мой голос»: что записано, «Перезаписать», «Удалить». */
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
              <Button size="sm" onClick={() => void remove(s)} disabled={voice.busy}
                aria-label={`Удалить образец: ${sampleText(s)}`}>Удалить</Button>
            </span>
          ))}
          {!open && (
            <Button size="sm" onClick={() => setOpen(true)} disabled={!voice.status}>
              {enrolled ? "Перезаписать" : "Записать"}
            </Button>
          )}
          {!open && voice.error && <span className="error">{voice.error}</span>}
        </div>
      </Row>
      {open && <OwnerVoiceRecorder voice={voice} device={device} />}
      {confirmNode}
    </div>
  );
}
