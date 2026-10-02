/**
 * Когда человек последний раз смотрел на ассистента — для «Что я пропустил?».
 *
 * Отметка — время (секунды записи) последней реплики в миг, когда человек
 * перестал смотреть: свернул панель или ушёл из окна в звонок. «Что я
 * пропустил?» берёт отметку и ставит новую на «сейчас»: следующий раз —
 * только то, что после ответа. Отметки ещё нет — null (ассистент возьмёт
 * последние минуты).
 */

import { useCallback, useEffect, useRef } from "react";

import type { LiveQuick } from "../lib/types";
import type { FeedLine, Live } from "./useLive";

export function useLastLook(lines: FeedLine[], looking: boolean): () => number | null {
  const latest = useRef<number | null>(null);
  latest.current = lines.at(-1)?.t ?? null;
  const mark = useRef<number | null>(null);
  const was = useRef(looking);

  useEffect(() => {
    if (was.current && !looking) mark.current = latest.current;
    was.current = looking;
  }, [looking]);

  useEffect(() => {
    const away = () => { if (was.current) mark.current = latest.current; };
    window.addEventListener("blur", away);
    return () => window.removeEventListener("blur", away);
  }, []);

  return useCallback(() => {
    const since = mark.current;
    mark.current = latest.current;
    return since;
  }, []);
}

/**
 * Вопрос ассистенту из окна: свой текст или быстрое действие. «Что я
 * пропустил?» уходит с отметкой «когда смотрел» (`since_t`).
 */
export function useLiveAsk(live: Live, looking: boolean) {
  const since = useLastLook(live.lines, looking);
  const { ask } = live;
  return useCallback((question: string, quick?: LiveQuick) => {
    if (!quick) return ask(question);
    if (quick !== "missed") return ask("", { quick });
    const t = since();
    return ask("", t === null ? { quick } : { quick, since_t: t });
  }, [ask, since]);
}
