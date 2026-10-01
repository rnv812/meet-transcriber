/** Общее для вкладок «Итоги» и «Вопросы»: кто ответит, подсказка, ступень ожидания. */

import { useEffect, useRef, useState } from "react";
import { getAssistant, type Endpoint } from "../../lib/api";
import type { AssistantInfo, Job } from "../../lib/types";

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

/** Обновлений списка задач без «своей» задачи — после этого она считается потерянной. */
const LOST_UPDATES = 2;
/** …или столько времени без неё (поток событий молчит). */
const LOST_MS = 10_000;

type Track = { seen: boolean; misses: number; jobs: Job[]; timer: ReturnType<typeof setTimeout> };

/**
 * Поставленные задачи, которых в `/jobs` больше нет или так и не появилось:
 * резидент перезапустился (задачи живут в его памяти) или задача ушла за
 * предел листинга. Такая задача не закончится на глазах у окна — `onLost`
 * зовётся один раз, и ждать её дальше незачем.
 *
 * IMPORTANT: обновление списка — это новая ссылка `jobs`, поэтому список не
 * должен пересоздаваться на каждом рендере (умолчание `[]` в пропсах — ошибка).
 */
export function useLostJobs(ids: readonly string[], jobs: Job[], onLost: (id: string) => void): void {
  const tracks = useRef(new Map<string, Track>());
  const onLostRef = useRef(onLost);
  onLostRef.current = onLost;
  const key = ids.join("\n");

  useEffect(() => {
    const all = tracks.current;
    const drop = (id: string) => {
      const t = all.get(id);
      if (!t) return;
      clearTimeout(t.timer);
      all.delete(id);
      onLostRef.current(id);
    };
    const wanted = new Set(key ? key.split("\n") : []);
    for (const [id, t] of all) {
      if (!wanted.has(id)) { clearTimeout(t.timer); all.delete(id); }
    }
    const present = new Set(jobs.map((j) => j.id));
    const lost: string[] = [];
    for (const id of wanted) {
      let t = all.get(id);
      if (!t) {
        const timer = setTimeout(() => { if (all.get(id)?.seen === false) drop(id); }, LOST_MS);
        t = { seen: false, misses: 0, jobs, timer };
        all.set(id, t);
      }
      if (present.has(id)) {
        t.seen = true;
        clearTimeout(t.timer);
      } else if (t.seen) {
        lost.push(id);
      } else if (t.jobs !== jobs) {
        t.jobs = jobs;
        if (++t.misses >= LOST_UPDATES) lost.push(id);
      }
    }
    lost.forEach(drop);
  }, [key, jobs]);

  useEffect(() => () => {
    for (const t of tracks.current.values()) clearTimeout(t.timer);
    tracks.current.clear();
  }, []);
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
