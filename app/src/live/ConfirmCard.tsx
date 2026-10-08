/**
 * Карточка подтверждения Meet: что агент хочет выполнить — из настоящего вызова, не из его текста.
 * Резидент присылает вызов целиком (`args`, пробелы и переводы строк — видимыми пометками, невидимые
 * символы запрещены) и для длинного — начало и конец (`preview`, середина — пометкой «скрыто: …»),
 * так что хвост виден всегда. «Показать полностью» — по желанию. Ждёт решения (`open`) — кнопки
 * прямо в карточке: «Разрешить один раз», «Разрешать такое до конца встречи» (если Meet её
 * предлагает) и «Отклонить» (Esc); решена или срок вышел — итог словом.
 *
 * С 0.4 карточка с `tool_use_id` стоит в строке своего вызова (ход работы, `ToolRows`):
 * `inline` — без своего заголовка (вызов уже назван строкой), только вопрос и кнопки.
 */

import { ShieldQuestion } from "lucide-react";
import { type KeyboardEvent, useState } from "react";

import type { ChatMessage } from "../lib/types";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { Tip } from "../ui/Tip";
import type { Chat } from "./useChat";

/** Решение по карточке подтверждения — словом. */
export const CARD_DECIDED: Record<string, string> = {
  allow: "Разрешено один раз", allow_meeting: "Разрешено до конца встречи", deny: "Отклонено", timeout: "Время вышло — не выполнено",
  cancelled: "Отменено", expired: "Не дождались ответа — не выполнено",
};

export function ConfirmCard({ m, chat, disabled, open, inline = false }: {
  m: ChatMessage; chat: Chat; disabled: boolean; open: boolean; inline?: boolean;
}) {
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState(false);
  const args = m.args ?? "";
  const decide = async (allow: boolean, meeting = false) => {
    if (busy) return;
    setBusy(true);
    try { await chat.confirm(m.id, allow, meeting); } finally { setBusy(false); }
  };
  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if (open && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); void decide(false); }
  };
  return (
    <section className={`chat-card${open ? " chat-card--open" : ""}${inline ? " chat-card--inline" : ""}`} role="group"
      aria-label={`Ассистент хочет выполнить: ${m.title ?? m.tool ?? ""}`} onKeyDown={onKey}>
      <div className="chat-card__title">
        <Icon as={ShieldQuestion} size="sm" className="chat-card__icon" />
        <span>
          {inline ? "Нужно ваше решение: " : "Ассистент хочет выполнить: "}<b>{m.title ?? m.tool}</b>
          {m.size && <span className="chat-card__size"> · {m.size}</span>}
        </span>
      </div>
      {(m.warnings ?? []).map((w) => <div key={w} className="chat-card__warn" role="note">{w}</div>)}
      {args && <pre className="chat-card__args" dir="ltr">{m.preview && !full ? m.preview : args}</pre>}
      {m.preview && (
        <Button variant="link" className="chat-card__more" aria-expanded={full} onClick={() => setFull(!full)}>
          {full ? "Свернуть" : "Показать полностью"}
        </Button>
      )}
      {open ? (
        // «Разрешить один раз» — сильная без цвета; «до конца встречи» (шире всего) — тише; «Отклонить» (Esc) — контур.
        <div className="chat-card__actions">
          <Button variant="mono" className="chat-card__allow" disabled={disabled || busy}
            onClick={() => void decide(true)}>
            Разрешить один раз
          </Button>
          {m.grant && (
            <Tip content={`Дальше до конца встречи без вопросов: ${m.grant.label}`}>
              <Button variant="ghost" className="chat-card__allow-meeting" disabled={disabled || busy}
                onClick={() => void decide(true, true)}>
                Разрешать такое до конца встречи
              </Button>
            </Tip>
          )}
          <Button className="chat-card__deny" disabled={disabled || busy} onClick={() => void decide(false)}>
            Отклонить
          </Button>
        </div>
      ) : (
        <div className="chat-card__done">{m.decision ? CARD_DECIDED[m.decision] ?? m.decision : CARD_DECIDED.expired}</div>
      )}
    </section>
  );
}
