/** Общее для вкладок «Итоги» и «Вопросы»: кто ответит, подсказка, ступень ожидания. */

import { useEffect, useState } from "react";
import { getAssistant, type Endpoint } from "../../lib/api";
import type { AssistantInfo } from "../../lib/types";

/** Пока резидент проверяет вход в CLI (`checking`), спрашиваем снова через паузу. */
const RECHECK_MS = 1500;

/** Кто ответит и куда класть заметки. null — ещё не знаем (или резидент старый). */
export function useAssistant(endpoint: Endpoint): AssistantInfo | null {
  const [info, setInfo] = useState<AssistantInfo | null>(null);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      getAssistant(endpoint).then((data) => {
        if (!live) return;
        setInfo(data);
        if (data.checking) timer = setTimeout(load, RECHECK_MS);
      }).catch(() => {});
    };
    load();
    return () => { live = false; clearTimeout(timer); };
  }, [endpoint]);
  return info;
}

/** Провайдера точно нет: не «ещё проверяется» и не «неизвестно». */
export const noProvider = (info: AssistantInfo | null): boolean =>
  info !== null && !info.provider && !info.checking;

export function ProviderHint({ onOpenSettings }: { onOpenSettings?: (section: string) => void }) {
  return (
    <div className="assist__hint">
      <span>Подключите Claude Code или Codex в настройках</span>
      {onOpenSettings && (
        <button type="button" className="link-btn" onClick={() => onOpenSettings("assistant")}>
          Открыть настройки
        </button>
      )}
    </div>
  );
}

export function ThinkingStage() {
  return (
    <div className="assist__stage" role="status">
      <span className="assist__pulse" aria-hidden="true" />
      Модель думает…
    </div>
  );
}
