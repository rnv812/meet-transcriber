/**
 * Запись образца голоса в один щелчок — оттуда, где видно, что он нужен.
 *
 * Без образца голоса владельца микрофон не делится на голоса: на встрече за
 * одним ноутбуком и сосед подписан «Вы». `OwnerVoiceNudge` — заметная строка
 * в окне ассистента (шапка ленты) и в карточке записи без разделения
 * микрофона: «Запишите образец своего голоса — прочитайте вслух короткий
 * текст (25 с)». «Записать образец» сразу открывает `OwnerVoiceDialog` с
 * текстом для чтения — тот же рекордер, что в мастере и в «Настройки → Звук →
 * Мой голос» (`OwnerVoiceRecorder`), а не переход в настройки.
 *
 * Пока идёт запись встречи, резидент образец не пишет (микрофон занят, чтение
 * попало бы во встречу): окно показывает текст и причину и само включает
 * кнопку, как только запись закончится (`watchBusy`). Строку можно скрыть —
 * до перезапуска окна (sessionStorage); ничего она не блокирует.
 */

import { X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Endpoint } from "../../lib/api";
import type { LiveMic } from "../../lib/types";
import { Button } from "../../ui/Button";
import { IconButton } from "../../ui/IconButton";
import "../../ui/primitives.css";
import { OwnerVoiceRecorder, POLL_MS, READY_POLL_MS, sentence, useOwnerVoice } from "./OwnerVoice";
import "./ownv.css";

export const NUDGE_TEXT = "Запишите образец своего голоса — прочитайте вслух короткий текст (25 с)";
/** Ключ «строку скрыли» в sessionStorage: до перезапуска окна. */
export const NUDGE_HIDDEN_KEY = "meet.ownerVoiceNudge.hidden";

let hiddenFallback = false;

/** Скрыта ли строка в этом сеансе окна (sessionStorage недоступен — в памяти). */
export function nudgeHidden(): boolean {
  try {
    return window.sessionStorage.getItem(NUDGE_HIDDEN_KEY) === "1" || hiddenFallback;
  } catch {
    return hiddenFallback;
  }
}

export function hideNudge(): void {
  hiddenFallback = true;
  try {
    window.sessionStorage.setItem(NUDGE_HIDDEN_KEY, "1");
  } catch {
    // приватный режим или запрет — хватит памяти до перезапуска окна
  }
}

/** Для тестов: снова показывать строку. */
export function resetNudge(): void {
  hiddenFallback = false;
  try {
    window.sessionStorage.removeItem(NUDGE_HIDDEN_KEY);
  } catch {
    // нечего сбрасывать
  }
}

/** Ведущая строка окна ассистента. */
export const LIVE_NUDGE_LEAD = "Микрофон не делится на голоса.";

/** Живой сеанс делит микрофон по голосам, а образца голоса нет — предложить записать. */
export function wantsOwnerSample(mic: LiveMic | null | undefined): boolean {
  return !!mic && mic.split && !mic.owner_profile;
}

/** Окно «Мой голос»: текст для чтения и запись образца (~25 с). */
export function OwnerVoiceDialog({ endpoint, onClose, onDone, pollMs = POLL_MS, readyPollMs = READY_POLL_MS }: {
  endpoint: Endpoint;
  onClose: () => void;
  /** Образец записан в этом окне (не прежняя попытка, которую резидент ещё помнит). */
  onDone?: () => void;
  pollMs?: number;
  readyPollMs?: number;
}) {
  const voice = useOwnerVoice(endpoint, { pollMs, readyPollMs, watchReady: true, watchBusy: true });
  const { status } = voice;
  const titleId = useId();
  const box = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const done = status?.take?.state === "done";
  // Попытка, которую резидент помнил до открытия окна, — не наша.
  const seenSample = useRef<string | null | undefined>(undefined);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;
  const sampleId = status?.take?.sample_id ?? null;
  useEffect(() => {
    if (!status) return;
    if (seenSample.current === undefined) {
      seenSample.current = status.take?.state === "done" ? sampleId : null;
      return;
    }
    if (done && sampleId !== seenSample.current) {
      seenSample.current = sampleId;
      doneRef.current?.();
    }
  }, [status, done, sampleId]);

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopImmediatePropagation();
      closeRef.current();
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      if (before && before !== document.body && document.contains(before)) before.focus();
    };
  }, []);

  return createPortal(
    <div className="confirm-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={box} className="confirm ownv-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}
        tabIndex={-1}>
        <div className="ownv-dialog__head">
          <h3 className="ownv-dialog__title" id={titleId}>Мой голос</h3>
          <IconButton icon={X} size="xs" label="Закрыть" onClick={onClose} />
        </div>
        <p className="muted ownv-dialog__lead">
          По образцу голоса расшифровка отличает вас от людей, которые сидят рядом и попадают в ваш микрофон.
        </p>
        {!status ? (
          voice.error ? <p className="error" role="alert">{voice.error}</p> : <p className="muted">Загрузка…</p>
        ) : !status.ready ? (
          <p className="muted">{sentence(status.reason ?? "Записать образец сейчас нельзя")}</p>
        ) : (
          <OwnerVoiceRecorder voice={voice} device={null} />
        )}
        <div className="ownv-dialog__bar">
          {done
            ? <Button variant="primary" onClick={onClose}>Готово</Button>
            : <Button onClick={onClose} disabled={status?.take?.state === "recording"}>Закрыть</Button>}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * Строка «запишите образец»: `lead` — почему (микрофон не разделён), кнопка
 * открывает окно записи сразу. Записали образец — строка уходит.
 */
export function OwnerVoiceNudge({ endpoint, lead, className = "", pollMs, readyPollMs }: {
  endpoint: Endpoint;
  lead: string;
  className?: string;
  pollMs?: number;
  readyPollMs?: number;
}) {
  const [hidden, setHidden] = useState(nudgeHidden);
  const [open, setOpen] = useState(false);
  const [recorded, setRecorded] = useState(false);
  const saved = useRef(false);
  if (hidden || recorded) return null;
  return (
    <div className={`ownv-nudge ${className}`.trim()} role="note" aria-label="Образец голоса">
      <span className="ownv-nudge__text">
        <span className="ownv-nudge__lead">{lead}</span> {NUDGE_TEXT}
      </span>
      <span className="ownv-nudge__actions">
        <Button size="xs" variant="primary" onClick={() => setOpen(true)}>Записать образец</Button>
        <IconButton icon={X} size="xs" label="Скрыть до перезапуска"
          onClick={() => { hideNudge(); setHidden(true); }} />
      </span>
      {open && (
        <OwnerVoiceDialog endpoint={endpoint} pollMs={pollMs} readyPollMs={readyPollMs}
          onDone={() => { saved.current = true; }}
          onClose={() => { setOpen(false); if (saved.current) setRecorded(true); }} />
      )}
    </div>
  );
}
