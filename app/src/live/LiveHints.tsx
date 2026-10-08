/**
 * «Подсказки»: что стоит спросить, риски, вопросы без ответа, термины,
 * следующие шаги. У каждой — момент встречи (щелчок — к реплике в ленте) и
 * действия: закрепить, скрыть (больше не вернётся), спросить об этом,
 * скопировать. Порядок — как у ассистента: новые в конце, ничего не прыгает.
 *
 * «Вам вопрос» (к владельцу обратились и ждут ответа) — наверху, с акцентной
 * полосой и черновиком «Что ответить: …»; «Спросить агента» сразу задаёт
 * вопрос о том, что ответить. Появление отмечено коротким всплеском — кроме
 * режима «Не отвлекать» (тогда без анимации, но всё равно наверху).
 */

import { useEffect, useRef, useState } from "react";
import { Copy, Pin, Sparkles, X } from "lucide-react";

import { clock } from "../lib/format";
import type { LiveHint } from "../lib/types";
import { Icon } from "../ui/Icon";
import { IconButton } from "../ui/IconButton";
import { Truncate } from "../ui/Truncate";
import { KIND_LABEL, hintKey, isUrgent, orderHints } from "./liveModel";
import "./live.css";

const COPIED_MS = 1500;

export function LiveHints({
  hints, fresh, enabled = true, error = null, onAction, onAsk, onAskUrgent, onTime, askTitle, quiet = false,
}: {
  hints: LiveHint[];
  /** «Не отвлекать»: новый «Вам вопрос» всё равно показывается, но без плавной прокрутки. */
  quiet?: boolean;
  /** Ключи `hintKey` недавно появившихся или изменённых. */
  fresh: Set<string>;
  /** false — режим «Только сводка». */
  enabled?: boolean;
  /** Действие с подсказкой не дошло — текст у неё. */
  error?: { id: string; text: string } | null;
  onAction: (id: string, action: "pin" | "unpin" | "dismiss") => void;
  onAsk: (hint: LiveHint) => void;
  /** «Спросить агента» у «Вам вопрос»; нет — как «Спросить об этом». */
  onAskUrgent?: (hint: LiveHint) => void;
  onTime: (seconds: number) => void;
  /** Подсказка у «Спросить об этом» (в карточке вопрос уходит агенту). */
  askTitle?: string;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  // Новый «Вам вопрос» — в поле зрения (он первый в списке): ответа ждут сейчас.
  const urgentEl = useRef<HTMLLIElement>(null);
  const urgentId = orderHints(hints).find(isUrgent)?.id ?? null;
  const seenUrgent = useRef<string | null>(urgentId);
  /** Новый «Вам вопрос» — вслух для экранного диктора (сам список не live-регион). */
  const [announce, setAnnounce] = useState("");
  const urgentText = hints.find((h) => h.id === urgentId)?.text ?? "";
  useEffect(() => {
    if (!urgentId || urgentId === seenUrgent.current) return;
    seenUrgent.current = urgentId;
    setAnnounce(`Вам вопрос: ${urgentText}`);
    urgentEl.current?.scrollIntoView?.({ block: "nearest", behavior: quiet ? "auto" : "smooth" });
  }, [urgentId, urgentText, quiet]);
  const announcer = <div className="sr-only" role="alert">{announce}</div>;
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
    navigator.clipboard.writeText(isUrgent(h) && h.reply ? h.reply : h.text).then(() => {
      setCopied(h.id);
      setTimeout(() => setCopied((cur) => (cur === h.id ? null : cur)), COPIED_MS);
    }).catch(() => {});
  };
  return (
    <>
    {announcer}
    <ul className="live-hints" aria-label="Подсказки">
      {orderHints(hints).map((h) => (
        <li key={h.id} ref={h.id === urgentId ? urgentEl : undefined} className={`live-hint live-hint--${h.kind}${fresh.has(hintKey(h)) ? (isUrgent(h) ? " is-fresh is-urgent-new" : " is-fresh") : ""}${h.pinned ? " is-pinned" : ""}`}>
          <div className="live-hint__head">
            <span className="live-hint__kind">{KIND_LABEL[h.kind] ?? h.kind}</span>
            <button type="button" className="live-hint__time num" title="Перейти к реплике в ленте"
              aria-label={`Момент ${clock(h.source_t)}`} onClick={() => onTime(h.source_t)}>
              {clock(h.source_t)}
            </button>
            <span className="live-hint__actions">
              <IconButton icon={Pin} size="xs" className="live-hint__pin" pressed={h.pinned}
                label={h.pinned ? "Открепить" : "Закрепить"}
                tooltip={h.pinned ? "Открепить" : "Закрепить: подсказка не уйдёт сама"}
                onClick={() => onAction(h.id, h.pinned ? "unpin" : "pin")} />
              <IconButton icon={Copy} size="xs" label="Копировать"
                tooltip={copied === h.id ? "Скопировано" : isUrgent(h) && h.reply ? "Копировать черновик ответа" : "Копировать текст"}
                onClick={() => copy(h)} />
              <IconButton icon={X} size="xs" label="Скрыть"
                tooltip="Скрыть: подсказка больше не появится (несколько секунд её можно вернуть)"
                onClick={() => onAction(h.id, "dismiss")} />
            </span>
          </div>
          <div className="live-hint__text">{h.text}</div>
          {isUrgent(h) && h.reply && (
            <div className="live-hint__reply"><span className="live-hint__reply-label">Что ответить:</span>{h.reply}</div>
          )}
          {h.why && <Truncate as="div" className="live-hint__why muted">{h.why}</Truncate>}
          <div className="live-hint__foot">
            {h.ref && <Truncate className="live-hint__ref muted" text={`База знаний: ${h.ref}`}>База знаний: {h.ref}</Truncate>}
            {isUrgent(h) ? (
              <button type="button" className="live-chip" title="Спросить агента, что ответить"
                onClick={() => (onAskUrgent ?? onAsk)(h)}>
                <Icon as={Sparkles} size="sm" />Спросить агента
              </button>
            ) : (
              <button type="button" className="live-chip" title={askTitle} onClick={() => onAsk(h)}>
                {askTitle && <Icon as={Sparkles} size="sm" />}Спросить об этом
              </button>
            )}
            {copied === h.id && <span className="muted live-hint__copied" role="status">Скопировано</span>}
          </div>
          {error?.id === h.id && <div className="live-hint__error" role="alert">{error.text}</div>}
        </li>
      ))}
    </ul>
    </>
  );
}
