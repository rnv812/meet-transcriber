/**
 * Вопрос ассистенту по идущей встрече. Ответ — под полем (модель отвечает до
 * нескольких минут). Поле фокус не берёт: панель поверх звонка не должна
 * перехватывать клавиатуру, пока человек сам не щёлкнул в поле.
 */

import { useState, type KeyboardEvent } from "react";

import { Markdown } from "../lib/markdown";
import { Button } from "../ui/Button";
import type { LiveReply } from "./useLive";
import "./live.css";

export const MISSED_QUESTION = "Что я пропустил за последние минуты?";

export function LiveAsk({ reply, onAsk, disabled = false }: {
  reply: LiveReply;
  onAsk: (question: string) => void | Promise<void>;
  /** Режим кончается — спрашивать уже некого. */
  disabled?: boolean;
}) {
  const [text, setText] = useState("");
  const blocked = disabled || reply.pending;

  const submit = () => {
    const question = text.trim();
    if (!question || blocked) return;
    setText("");
    void onAsk(question);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
    e.preventDefault();
    submit();
  };

  return (
    <div className="live-ask">
      <form className="live-ask__row" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <input
          className="live-ask__input" aria-label="Вопрос ассистенту" placeholder="Спросите о встрече"
          value={text} disabled={blocked}
          onChange={(e) => setText(e.target.value)} onKeyDown={onKeyDown}
        />
        <Button type="submit" variant="primary" disabled={blocked || !text.trim()}>Спросить</Button>
      </form>
      <Button className="live-ask__missed" disabled={blocked} onClick={() => void onAsk(MISSED_QUESTION)}>
        Что я пропустил?
      </Button>
      {reply.question && (
        <div className="live-ask__reply" aria-live="polite">
          <div className="live-ask__q">{reply.question}</div>
          {reply.pending && (
            <div className="live-ask__stage" role="status">
              <span className="live-dot" aria-hidden="true" />Модель думает…
            </div>
          )}
          {reply.answer !== null && <Markdown source={reply.answer} className="live-ask__a" />}
          {reply.error && <div className="live-ask__error" role="alert">{reply.error}</div>}
        </div>
      )}
    </div>
  );
}
