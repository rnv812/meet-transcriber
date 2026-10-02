/**
 * «Спросить»: история вопросов по идущей встрече, быстрые действия и поле.
 *
 * История — у ассистента (`qa`): вопрос в ней сразу, ответ приходит позже,
 * пока ждём — «Модель думает…»; ответ, который пишется (`partial`), виден по
 * мере генерации простым текстом, готовый — Markdown'ом на том же месте.
 * Ответы — Markdown; таймкоды в них — кнопки
 * перехода к реплике в ленте (`onTime`). Поле фокус не берёт: панель поверх
 * звонка не должна перехватывать клавиатуру, пока человек сам не щёлкнул.
 * Текст поля можно задать снаружи (`draft`) — «Спросить об этом» у подсказки.
 */

import { useLayoutEffect, useRef, useState } from "react";

import { Markdown } from "../lib/markdown";
import type { LiveQa, LiveQuick } from "../lib/types";
import { Button } from "../ui/Button";
import "./live.css";

export const QUICK_ACTIONS: { id: LiveQuick; label: string }[] = [
  { id: "missed", label: "Что я пропустил?" },
  { id: "decisions", label: "Какие решения уже приняты?" },
  { id: "reply", label: "Что мне ответить?" },
  { id: "brief", label: "Кратко за 1 минуту" },
];

/** Насколько от низа ещё считается «внизу». */
const BOTTOM_SLACK_PX = 24;

export function LiveAsk({ qa, asking = false, error = null, onAsk, disabled = false, draft, onDraft, onTime }: {
  qa: LiveQa[];
  /** Запрос ушёл, история его ещё не показала. */
  asking?: boolean;
  /** Вопрос не дошёл до ассистента. */
  error?: string | null;
  onAsk: (question: string, quick?: LiveQuick) => void | Promise<void>;
  /** Режим кончается — спрашивать уже некого. */
  disabled?: boolean;
  /** Текст поля снаружи (вместе с `onDraft`), иначе поле своё. */
  draft?: string;
  onDraft?: (text: string) => void;
  onTime?: (seconds: number) => void;
}) {
  const [own, setOwn] = useState("");
  const text = draft ?? own;
  const setText = onDraft ?? setOwn;
  const waiting = asking || qa.some((it) => it.pending);
  const blocked = disabled || waiting;
  const box = useRef<HTMLOListElement>(null);
  const follow = useRef(true);

  const submit = () => {
    const question = text.trim();
    if (!question || blocked) return;
    setText("");
    void onAsk(question);
  };

  // Новый вопрос или ответ — к низу истории, если человек не листает её вверх.
  const last = qa.at(-1);
  const sig = `${qa.length}:${last?.pending ? "p" : "d"}:${last?.partial?.length ?? 0}:${asking}`;
  useLayoutEffect(() => {
    const el = box.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [sig]);
  const onScroll = () => {
    const el = box.current;
    if (el) follow.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
  };

  return (
    <div className="live-ask">
      {(qa.length > 0 || asking) && (
        <ol ref={box} className="live-ask__history" aria-label="Вопросы и ответы" onScroll={onScroll}>
          {qa.map((it) => (
            <li key={it.id} className="live-ask__item">
              <div className="live-ask__q">{it.q}</div>
              {it.pending && !it.partial && (
                <div className="live-ask__stage" role="status">
                  <span className="live-dot" aria-hidden="true" />Модель думает…
                </div>
              )}
              {it.pending && it.partial && (
                <div className="live-ask__a live-ask__a--streaming" aria-live="polite" aria-busy="true">
                  {it.partial}
                </div>
              )}
              {it.a !== null && <Markdown source={it.a} className="live-ask__a" onTime={onTime} />}
              {it.error && <div className="live-ask__error">{it.error}</div>}
            </li>
          ))}
          {asking && !qa.some((it) => it.pending) && (
            <li className="live-ask__item">
              <div className="live-ask__stage" role="status">
                <span className="live-dot" aria-hidden="true" />Модель думает…
              </div>
            </li>
          )}
        </ol>
      )}
      {error && <div className="live-ask__error" role="alert">{error}</div>}
      <div className="live-ask__quick" role="group" aria-label="Быстрые вопросы">
        {QUICK_ACTIONS.map((q) => (
          <button key={q.id} type="button" className="live-chip" disabled={blocked}
            onClick={() => void onAsk(q.label, q.id)}>
            {q.label}
          </button>
        ))}
      </div>
      <form className="live-ask__row" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {/* Однострочное поле: Enter отправляет форму сам (неявная отправка). */}
        <input
          className="live-ask__input" aria-label="Вопрос ассистенту" placeholder="Спросите о встрече"
          value={text} disabled={disabled}
          onChange={(e) => setText(e.target.value)}
        />
        <Button type="submit" variant="primary" disabled={blocked || !text.trim()}>Спросить</Button>
      </form>
    </div>
  );
}
