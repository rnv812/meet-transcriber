/**
 * «Подсказки»: что стоит спросить, риски, вопросы без ответа, термины,
 * следующие шаги. У каждой — момент встречи (щелчок — к реплике в ленте) и
 * действия: закрепить, скрыть (больше не вернётся), спросить об этом,
 * скопировать. Порядок — как у ассистента: новые в конце, ничего не прыгает.
 */

import { useState } from "react";
import { Sparkles } from "lucide-react";

import { clock } from "../lib/format";
import type { LiveHint } from "../lib/types";
import { CloseIcon, CopyIcon, PinIcon } from "./icons";
import { KIND_LABEL, hintKey } from "./liveModel";
import type { HintAction } from "./useLive";
import "./live.css";

const COPIED_MS = 1500;

export function LiveHints({ hints, fresh, enabled = true, error = null, onAction, onAsk, onTime, askTitle }: {
  hints: LiveHint[];
  /** Ключи `hintKey` недавно появившихся или изменённых. */
  fresh: Set<string>;
  /** false — режим «Только сводка». */
  enabled?: boolean;
  /** Действие с подсказкой не дошло — текст у неё. */
  error?: { id: string; text: string } | null;
  onAction: (id: string, action: HintAction) => void;
  onAsk: (hint: LiveHint) => void;
  onTime: (seconds: number) => void;
  /** Подсказка у «Спросить об этом» (в карточке вопрос уходит агенту). */
  askTitle?: string;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  if (!enabled) {
    return (
      <p className="live-empty muted">
        Подсказки выключены: в настройках ассистента выбрано «Только сводка».
      </p>
    );
  }
  if (hints.length === 0) {
    return <p className="live-empty muted">Подсказки появятся, когда в разговоре будет за что зацепиться.</p>;
  }
  const copy = (h: LiveHint) => {
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(h.text).then(() => {
      setCopied(h.id);
      setTimeout(() => setCopied((cur) => (cur === h.id ? null : cur)), COPIED_MS);
    }).catch(() => {});
  };
  return (
    <ul className="live-hints" aria-label="Подсказки">
      {hints.map((h) => (
        <li key={h.id} className={`live-hint live-hint--${h.kind}${fresh.has(hintKey(h)) ? " is-fresh" : ""}${h.pinned ? " is-pinned" : ""}`}>
          <div className="live-hint__head">
            <span className="live-hint__kind">{KIND_LABEL[h.kind] ?? h.kind}</span>
            <button type="button" className="live-hint__time num" title="Перейти к реплике в ленте"
              aria-label={`Момент ${clock(h.source_t)}`} onClick={() => onTime(h.source_t)}>
              {clock(h.source_t)}
            </button>
            <span className="live-hint__actions">
              <button type="button" className="icon-btn live-hint__pin" aria-pressed={h.pinned}
                aria-label={h.pinned ? "Открепить" : "Закрепить"}
                title={h.pinned ? "Открепить" : "Закрепить: подсказка не уйдёт сама"}
                onClick={() => onAction(h.id, h.pinned ? "unpin" : "pin")}>
                <PinIcon />
              </button>
              <button type="button" className="icon-btn" aria-label="Копировать"
                title={copied === h.id ? "Скопировано" : "Копировать текст"} onClick={() => copy(h)}>
                <CopyIcon />
              </button>
              <button type="button" className="icon-btn" aria-label="Скрыть"
                title="Скрыть: эта подсказка больше не появится" onClick={() => onAction(h.id, "dismiss")}>
                <CloseIcon />
              </button>
            </span>
          </div>
          <div className="live-hint__text">{h.text}</div>
          {h.why && <div className="live-hint__why muted">{h.why}</div>}
          <div className="live-hint__foot">
            {h.ref && <span className="live-hint__ref muted" title={h.ref}>База знаний: {h.ref}</span>}
            <button type="button" className="live-chip" title={askTitle} onClick={() => onAsk(h)}>
              {askTitle && <Sparkles size={12} strokeWidth={1.75} aria-hidden="true" />}Спросить об этом
            </button>
            {copied === h.id && <span className="muted live-hint__copied" role="status">Скопировано</span>}
          </div>
          {error?.id === h.id && <div className="live-hint__error" role="alert">{error.text}</div>}
        </li>
      ))}
    </ul>
  );
}
